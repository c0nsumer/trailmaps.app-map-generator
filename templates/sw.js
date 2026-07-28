// Service Worker for the trailmaps.app Map Generator
// Provides offline support: a small priority list is precached in
// the background after install, and everything else the rider
// touches at runtime is cached on-fetch by the handler below.
//
// Config is injected at build time by build.py:
//   SW_CONFIG.CACHE_SCOPE    - the map's slug. Cache Storage is
//                              per-ORIGIN, not per-SW-scope, and
//                              multiple maps are served as paths on
//                              one origin, so every cache name must
//                              carry the slug or one map's cleanup
//                              deletes its neighbors' offline caches
//   SW_CONFIG.CACHE_VERSION  - hash-based cache version string,
//                              computed over EVERY file in the
//                              build (not just precached ones)
//   SW_CONFIG.PRECACHE_URLS  - priority list for background fill
//                              (omits most glyph PBFs - those flow
//                              through cache-on-fetch)
//   SW_CONFIG.PRECACHE_BYTES - {url: size} for the above, so the page
//                              can weight offline progress by bytes
//                              instead of by file count
//   SW_CONFIG.PMTILES_FILES  - list of .pmtiles filenames for
//                              Range request handling

/*__SW_CONFIG__*/

// All of THIS map's caches share the slug-scoped prefix; cleanup
// below only ever deletes within it. localStorage learned this same
// origin-not-scope lesson long ago (see the `<slug>.mtb.*` key
// namespace in app.js).
const CACHE_PREFIX = `trail-map-${SW_CONFIG.CACHE_SCOPE}-`;
const CACHE_NAME = `${CACHE_PREFIX}${SW_CONFIG.CACHE_VERSION}`;

// Pre-slug cache names were `trail-map-<12 hex chars>` with no map
// identity. They can't be attributed to a map, so the first slugged
// version to clean up deletes them all - a one-time migration cost
// (the affected maps re-precache on their next online visit), versus
// the old behavior which cross-deleted on EVERY cleanup. Anchored so
// slugged names can never match.
const LEGACY_CACHE_RE = /^trail-map-[0-9a-f]{12}$/;

// ============================================================
// Install - complete immediately, precache in background
// ============================================================
// Install used to block on cache.addAll(PRECACHE_URLS), which on
// first visit pulled ~20 MB across hundreds of files (full PMTiles,
// every glyph PBF, sprites, etc.) in parallel with MapLibre's own
// foreground rendering requests. On constrained connections this
// produced a long tail of "blocked" HTTP/3 streams contending with
// the critical-path resources and degraded first paint badly.
//
// New design: install completes essentially immediately, then a
// background precache trickles through PRECACHE_URLS one request
// at a time. The fetch handler below also writes runtime fetches
// to the cache, so any asset the rider actually touches becomes
// available offline as a side effect of normal use. Combined, this
// means first paint is unblocked, and offline coverage grows
// progressively (visited areas immediately via cache-on-fetch;
// unvisited areas as the background precache catches up, typically
// within tens of seconds to a couple of minutes depending on
// connection speed).
//
// We deliberately do NOT call skipWaiting() here. A new SW installs
// but stays in the "waiting" state until either the rider taps
// "Reload" on the update toast (which posts SKIP_WAITING - see the
// message handler below) or every old-SW client closes. This is the
// standard double-buffered update model, and it matters most on poor
// connectivity:
//
//   skipWaiting() on install would activate the new SW immediately,
//   and the activate handler below deletes the previous cache. The
//   new cache is still empty at that moment (backgroundPrecache is
//   fire-and-forget and the large files - trails.geojson, the
//   PMTiles - haven't downloaded yet). On a flaky cellular link the
//   tiny sw.js squeaks through and triggers the swap, but the big
//   files then stall, so the page is left fetching required data
//   (loadTrails) from an empty cache over a dead network: blank
//   basemap, no trail lines, indefinite hang. Leaving the new SW
//   waiting keeps the OLD SW in control and its OLD cache intact, so
//   the map keeps loading from cache regardless of connectivity; the
//   update applies only once the page has verified this cache's core
//   files are present (the silent swap: CORE_STATUS below), when the
//   rider taps Reload on the mid-session toast, or on the next cold
//   launch.
//
// backgroundPrecache() still runs at install time, filling the NEW
// cache alongside the old one, so by the time the rider reloads the
// new version is already warm.
self.addEventListener("install", (event) => {
    // Fire-and-forget. Intentionally NOT inside event.waitUntil so
    // it cannot delay install completion.
    backgroundPrecache();
});

// Sentinel cache entry marking "every PRECACHE_URL is in this cache".
// Written only after a verification pass at the end of a full
// backgroundPrecache run; its absence means the precache was cut
// short and must resume. Never requested by the page, so the fetch
// handler can't serve it to anyone.
const PRECACHE_DONE_URL = "__precache-complete__";

// Re-entrancy guard: install / activate / page pings can overlap
// (e.g. a RESUME_PRECACHE message arriving while the install-time
// run is mid-list) - one runner at a time is enough.
let _precacheRunning = false;

async function backgroundPrecache() {
    // Sequential cache.add (not cache.addAll) so we trickle through
    // the connection one request at a time, leaving bandwidth for
    // foreground MapLibre fetches. cache: "no-cache" forces
    // revalidation against the server via conditional GET - Caddy
    // serves most assets with max-age=86400, so the default fetch
    // could blindly cache yesterday's HTTP-cached bytes for up to
    // 24 h post-deploy. Revalidation gives the same freshness
    // guarantee as the old cache: "reload" but lets UNCHANGED files
    // answer 304 and fill from the browser's HTTP cache instead of
    // re-downloading: a new CACHE_VERSION re-precaches everything,
    // and before this only a one-line app.js change made every
    // installed rider re-pull the full ~20-35 MB build (PMTiles
    // included) over the network on each deploy. Same posture as the
    // fetch handler below.
    //
    // RESUMABLE: the browser is free to terminate this worker while
    // the fire-and-forget loop is still trickling (Chrome ~30 s idle,
    // Safari sooner), and `install` never re-fires for the same SW
    // version - the multi-MB .pmtiles at the tail of PRECACHE_URLS
    // were the most likely casualties, leaving a rider with UI assets
    // cached but no tile archives at the trailhead. So the run is
    // idempotent (already-cached URLs are skipped) and re-triggered
    // from three places: install (here), activate, and a
    // RESUME_PRECACHE ping the page sends on every load. Only after a
    // verification pass confirms every URL is present is the
    // completion sentinel written, which makes subsequent triggers a
    // single cache.match.
    if (_precacheRunning) return;
    _precacheRunning = true;
    try {
        const cache = await caches.open(CACHE_NAME);
        if (await cache.match(PRECACHE_DONE_URL)) {
            // Already complete. Old caches may still exist if the
            // sentinel was written while this worker sat waiting
            // (cleanup is active-only); the post-activation trigger
            // lands here and finishes the job.
            await cleanupOldCaches();
            return;
        }
        for (const url of SW_CONFIG.PRECACHE_URLS) {
            try {
                if (await cache.match(url)) continue; // resume: already cached
                await cache.add(new Request(url, { cache: "no-cache" }));
            } catch (e) {
                // Best-effort. A single failed asset (network blip,
                // stale URL after deploy, etc.) shouldn't abort the
                // rest of the precache. Log so it's visible in DevTools
                // when debugging offline coverage gaps.
                console.warn("SW backgroundPrecache failed:", url, e);
            }
        }
        // Mark complete only when every URL is really present - a
        // failed add above must leave the sentinel absent so the next
        // trigger retries the gaps.
        let complete = true;
        for (const url of SW_CONFIG.PRECACHE_URLS) {
            if (!(await cache.match(url))) {
                complete = false;
                break;
            }
        }
        if (complete) {
            await cache.put(PRECACHE_DONE_URL, new Response("1"));
            await cleanupOldCaches();
        }
    } finally {
        _precacheRunning = false;
    }
}

// Delete previous versions' caches. Deferred until (a) this worker is
// the ACTIVE one: while we sit waiting, the old worker's pages are
// still being served from those caches, and (b) our own precache has
// verified complete, so the old-cache fallbacks in the fetch path
// (regular requests offline, .pmtiles Range requests, the HEAD
// precheck) are no longer needed.
//
// "Not yet active" is detected via the registration slots, NOT an
// identity compare against `self`: registration.active holds a
// ServiceWorker handle while `self` is the global scope, so
// `reg.active !== self` is always true and silently blocked every
// cleanup (the two-caches-forever bug). A worker that isn't active
// occupies reg.installing or reg.waiting itself, so the null checks
// below cover (a), and they also keep an active worker from deleting
// a SUCCESSOR's half-filled cache when a RESUME_PRECACHE ping
// arrives while a newer version installs alongside. The state check
// is belt-and-suspenders where supported (it closes the sliver where
// a just-made-redundant worker's in-flight precache lands between a
// successor's slot transitions).
async function cleanupOldCaches() {
    const reg = self.registration;
    if (reg.waiting || reg.installing) return;
    if (
        self.serviceWorker &&
        self.serviceWorker.state !== "activating" &&
        self.serviceWorker.state !== "activated"
    ) {
        return;
    }
    const keys = await caches.keys();
    await Promise.all(
        keys
            .filter(
                (k) =>
                    (k.startsWith(CACHE_PREFIX) && k !== CACHE_NAME) ||
                    LEGACY_CACHE_RE.test(k)
            )
            .map((k) => caches.delete(k))
    );
}

// ============================================================
// Message handler - silent update swap + B.7 "Reload" toast
// ============================================================
// SKIP_WAITING is posted to this worker (the waiting one) by two
// paths in app.js: the silent swap (after CORE_STATUS confirms this
// cache's core files are present) and the rider tapping "Reload" on
// the mid-session update toast. Either way we call skipWaiting() so
// this version becomes active; the page then observes
// `controllerchange` on navigator.serviceWorker and reloads itself.
// Without this handshake, the new SW would stay in the "waiting"
// state until every page using the old SW closed.
self.addEventListener("message", (event) => {
    if (event.data && event.data.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
    // Core-readiness query for the silent update swap (see the PWA
    // update block in app.js). The page polls the WAITING worker with
    // this before posting SKIP_WAITING: "core" is every PRECACHE_URL
    // except the .pmtiles archives, i.e. the set the post-swap reload
    // must have from THIS cache (tile archives can fall back to the
    // previous version's cache via handleRangeRequest; the page, app
    // code, and trail data cannot). Answered on a transferred
    // MessagePort, same pattern as PRECACHE_STATUS.
    if (event.data && event.data.type === "CORE_STATUS") {
        const port = event.ports && event.ports[0];
        if (port) reportCoreStatus(port);
    }
    // Page pings this on every load (see the SW-registration block in
    // app.js) so a precache cut short by worker termination resumes.
    // Near-free once complete: one cache.match against the sentinel.
    if (event.data && event.data.type === "RESUME_PRECACHE") {
        backgroundPrecache();
    }
    // Offline-readiness query for the About modal's Offline row. The
    // page transfers a MessagePort and we answer on it, so there's no
    // need to enumerate clients or broadcast to every open tab.
    if (event.data && event.data.type === "PRECACHE_STATUS") {
        const port = event.ports && event.ports[0];
        if (port) reportPrecacheStatus(port);
    }
});

// Answer a CORE_STATUS query: are all non-.pmtiles precache URLs in
// this worker's cache? Resolves false on any doubt; the page treats
// false as "keep waiting" and falls back to the update toast when its
// grace window expires, so a wrong false costs a prompt, never a
// broken swap.
async function reportCoreStatus(port) {
    let coreComplete = false;
    try {
        const cache = await caches.open(CACHE_NAME);
        if (await cache.match(PRECACHE_DONE_URL)) {
            // Full precache verified - core is a subset.
            coreComplete = true;
        } else {
            const pmtiles = new Set(SW_CONFIG.PMTILES_FILES || []);
            coreComplete = true;
            for (const url of SW_CONFIG.PRECACHE_URLS || []) {
                if (pmtiles.has(url)) continue;
                if (!(await cache.match(url))) {
                    coreComplete = false;
                    break;
                }
            }
        }
    } catch (e) {
        console.warn("SW reportCoreStatus failed:", e);
    }
    port.postMessage({ type: "CORE_STATUS", coreComplete });
}

// Answer a PRECACHE_STATUS query with byte-weighted progress.
//
// Why bytes and not a file count: PRECACHE_URLS is ~30 entries, but the
// .pmtiles archives at its tail are most of the payload. Counting files
// would report ~90% while the tile archives - the only thing that makes
// the map usable away from signal - were still absent.
//
// Note the asymmetry this reports on: normal browsing can never cache
// those archives. MapLibre reads them with Range requests, the fetch
// handler below caches only status-200 basic responses, and
// handleRangeRequest passes a miss straight through without storing
// anything. Only backgroundPrecache's full-file cache.add gets them in,
// which is exactly why a rider can have a fully painted map and no
// offline coverage at all.
async function reportPrecacheStatus(port) {
    const urls = SW_CONFIG.PRECACHE_URLS || [];
    const sizes = SW_CONFIG.PRECACHE_BYTES || {};
    let totalBytes = 0;
    for (const url of urls) totalBytes += sizes[url] || 0;

    let cachedBytes = 0;
    let complete = false;
    try {
        const cache = await caches.open(CACHE_NAME);
        if (await cache.match(PRECACHE_DONE_URL)) {
            // The sentinel is only written after a verification pass, so
            // trust it and skip ~30 cache.match round trips. This is the
            // settled state a rider is in almost all of the time.
            complete = true;
            cachedBytes = totalBytes;
        } else {
            for (const url of urls) {
                if (await cache.match(url)) cachedBytes += sizes[url] || 0;
            }
        }
    } catch (e) {
        // Storage unavailable (private mode, quota). Fall through with
        // what we have; the page reads a zero total as "unknown" and
        // keeps its row hidden rather than claiming anything.
        console.warn("SW reportPrecacheStatus failed:", e);
    }
    port.postMessage({
        type: "PRECACHE_STATUS",
        complete,
        cachedBytes,
        totalBytes,
    });
}

// ============================================================
// Activate - claim clients; old-cache cleanup is deferred
// ============================================================
// Old caches are deliberately NOT deleted here (they were, until the
// silent-swap flow landed). They are what makes an early skipWaiting
// safe: at swap time the new cache holds only the core files, and the
// previous version's .pmtiles archives keep serving Range requests
// (see handleRangeRequest) while the new precache trickles in. So a
// rider who auto-updated in the parking lot and immediately lost
// signal still has full offline tile coverage, just briefly from
// last version's archives. cleanupOldCaches() deletes them only once
// the new precache verifies complete.
self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
    // Resume any precache the install-time run didn't finish (the
    // worker may have been terminated mid-list while this version sat
    // waiting). Fire-and-forget OUTSIDE waitUntil - activation must
    // not block on a ~20 MB background fill.
    backgroundPrecache();
});

// ============================================================
// Fetch - cache-first for same-origin, with Range request
// support for PMTiles files
// ============================================================
self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);

    // Only handle same-origin requests
    if (url.origin !== self.location.origin) return;

    // Check if this is a Range request for a PMTiles file
    const isPMTiles = SW_CONFIG.PMTILES_FILES.some((f) =>
        url.pathname.endsWith(f)
    );
    if (isPMTiles && event.request.headers.has("Range")) {
        event.respondWith(handleRangeRequest(event.request));
        return;
    }

    // HEAD requests: satisfy from the GET cache. cache.match by
    // default is method-aware, so a cached GET won't match a HEAD
    // lookup. addTerrainLayers in app.js does a HEAD precheck on
    // terrain.pmtiles before adding the source - offline, the GET
    // is cached but a strict match would miss, the fetch would
    // fall through to network, fail, and the terrain layer would
    // never be added. Synthesize a 200 with no body (HEAD has no
    // body anyway) from the GET cache entry so the precheck passes
    // and the subsequent Range requests find their cached blob.
    // Current cache first, then any older cache: right after a
    // silent swap the new cache may not hold terrain.pmtiles yet,
    // but the previous version's copy can answer the precheck (the
    // Range handler falls back to it the same way).
    if (event.request.method === "HEAD") {
        event.respondWith(
            (async () => {
                const cache = await caches.open(CACHE_NAME);
                const cached =
                    (await cache.match(event.request, { ignoreMethod: true })) ||
                    (await caches.match(event.request, { ignoreMethod: true }));
                if (cached) {
                    return new Response(null, {
                        status: cached.status,
                        statusText: cached.statusText,
                        headers: cached.headers,
                    });
                }
                return fetch(event.request);
            })()
        );
        return;
    }

    // Cache-first with write-on-miss. If the asset is cached, serve
    // it instantly. Otherwise fetch from network AND write the
    // response into the cache for next time. This pairs with the
    // deferred backgroundPrecache() in install: glyph PBFs, sprites,
    // and other lazily-fetched assets that aren't in the precache
    // priority list get cached on first use, so offline coverage
    // grows as the rider explores even before the background
    // precache catches up.
    //
    // The network fallback uses `cache: "no-cache"` to force the
    // browser to revalidate against the server via a conditional
    // GET (If-Modified-Since / If-None-Match). Without this, the
    // default fetch honours the browser's HTTP cache - and Caddy
    // ships most assets with `Cache-Control: public, max-age=86400`,
    // so for up to 24 h post-deploy a SW cache miss could blindly
    // return yesterday's HTTP-cached bytes to the page (visible
    // as "I just deployed and clicked Reload but the page still
    // shows the old map until I shift-reload"). Revalidation is
    // ~50 ms of overhead when content hasn't changed (304 response,
    // no body) and free correctness when it has. Same
    // `cache: "no-cache"` posture as backgroundPrecache above.
    //
    // Lookups are scoped to the CURRENT cache (cache.match, not the
    // global caches.match this used to call). Old caches now outlive
    // activation until the new precache verifies complete, and the
    // global lookup searches caches oldest-first, which would pin a
    // freshly swapped rider to the oldest surviving version. Old
    // caches are consulted only as a last resort when the network
    // fetch itself fails: a slightly stale asset beats a dead page
    // for a rider who updated moments before losing signal.
    event.respondWith(
        (async () => {
            const cache = await caches.open(CACHE_NAME);
            const cached = await cache.match(event.request);
            if (cached) return cached;
            const networkReq = new Request(event.request, { cache: "no-cache" });
            try {
                const response = await fetch(networkReq);
                // Cache successful same-origin GET responses
                // only. Skip opaque (cross-origin no-CORS),
                // partial (206 Range), and error responses -
                // none of those round-trip cleanly via the
                // Cache API for offline serving.
                if (
                    response &&
                    response.status === 200 &&
                    response.type === "basic"
                ) {
                    const clone = response.clone();
                    cache.put(event.request, clone)
                        .catch(() => { /* best effort */ });
                }
                return response;
            } catch (err) {
                const stale = await caches.match(event.request);
                if (stale) return stale;
                throw err;
            }
        })()
    );
});

// ============================================================
// Range request handler for PMTiles
//
// PMTiles uses HTTP Range requests to read tile chunks from the
// archive. The Cache API stores the full file (fetched by the
// backgroundPrecache loop, since each .pmtiles is in
// PRECACHE_URLS). On a Range request we retrieve the full cached
// response, slice the requested byte range from the blob, and
// return a synthetic 206 Partial Content response.
//
// If the full file hasn't been precached yet (rider zoomed into a
// fresh visit before background precache caught up), the cache.match
// below misses and we fall back to network - Caddy serves the
// Range request directly. The PMTiles slice is correct either way;
// the only difference is whether the bytes came from cache or
// network. Once the background precache eventually fetches the full
// file, subsequent Range requests slice from cache.
//
// Blob.slice() creates a lightweight view - not a copy.
// ============================================================
async function handleRangeRequest(request) {
    const cache = await caches.open(CACHE_NAME);

    // Match against the URL without Range header. Current cache
    // first, then any older cache: right after a silent swap the new
    // cache holds only the core files, and the previous version's
    // archives (kept until the new precache verifies complete) cover
    // the gap. A slightly stale basemap tile is indistinguishable
    // from a current one; the trail DATA is already fresh, it comes
    // from the new cache's core files, not from here.
    const fullResponse =
        (await cache.match(new Request(request.url))) ||
        (await caches.match(new Request(request.url)));
    if (!fullResponse) {
        // Not cached anywhere - pass through to network
        return fetch(request);
    }

    const blob = await fullResponse.blob();
    const rangeHeader = request.headers.get("Range");
    const match = rangeHeader.match(/bytes=(\d+)-(\d*)/);

    if (!match) {
        // Malformed Range header - return full response
        return new Response(blob, {
            status: 200,
            headers: fullResponse.headers,
        });
    }

    const start = parseInt(match[1]);
    const end = match[2] ? parseInt(match[2]) : blob.size - 1;

    // Forward the cached response's validators. The pmtiles client
    // reads the archive's header/root directory once and caches it in
    // memory, then relies on ETag/Last-Modified drift across Range
    // responses to notice the archive changed underneath it. After a
    // silent swap, ranges here can migrate from the old version's
    // archive to the new one (backgroundPrecache lands it mid-session,
    // then cleanup deletes the old cache); without validators that
    // switch is invisible and the client slices the new archive at the
    // old directory's offsets - garbage tiles until reload.
    const headers = {
        "Content-Type": "application/octet-stream",
        "Content-Range": `bytes ${start}-${end}/${blob.size}`,
        "Content-Length": end - start + 1,
    };
    for (const name of ["ETag", "Last-Modified"]) {
        const value = fullResponse.headers.get(name);
        if (value) headers[name] = value;
    }

    return new Response(blob.slice(start, end + 1), {
        status: 206,
        headers,
    });
}
