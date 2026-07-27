// Service Worker for the trailmaps.app Map Generator
// Provides offline support: a small priority list is precached in
// the background after install, and everything else the rider
// touches at runtime is cached on-fetch by the handler below.
//
// Config is injected at build time by build.py:
//   SW_CONFIG.CACHE_VERSION  — hash-based cache version string,
//                              computed over EVERY file in the
//                              build (not just precached ones)
//   SW_CONFIG.PRECACHE_URLS  — priority list for background fill
//                              (omits most glyph PBFs — those flow
//                              through cache-on-fetch)
//   SW_CONFIG.PRECACHE_BYTES — {url: size} for the above, so the page
//                              can weight offline progress by bytes
//                              instead of by file count
//   SW_CONFIG.PMTILES_FILES  — list of .pmtiles filenames for
//                              Range request handling

/*__SW_CONFIG__*/

const CACHE_NAME = `trail-map-${SW_CONFIG.CACHE_VERSION}`;

// ============================================================
// Install — complete immediately, precache in background
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
// "Reload" on the update toast (which posts SKIP_WAITING — see the
// message handler below) or every old-SW client closes. This is the
// standard double-buffered update model, and it matters most on poor
// connectivity:
//
//   skipWaiting() on install would activate the new SW immediately,
//   and the activate handler below deletes the previous cache. The
//   new cache is still empty at that moment (backgroundPrecache is
//   fire-and-forget and the large files — trails.geojson, the
//   PMTiles — haven't downloaded yet). On a flaky cellular link the
//   tiny sw.js squeaks through and triggers the swap, but the big
//   files then stall, so the page is left fetching required data
//   (loadTrails) from an empty cache over a dead network: blank
//   basemap, no trail lines, indefinite hang. Leaving the new SW
//   waiting keeps the OLD SW in control and its OLD cache intact, so
//   the map keeps loading from cache regardless of connectivity; the
//   update applies only when the rider explicitly reloads (ideally on
//   a good connection) or next cold launch.
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
// run is mid-list) — one runner at a time is enough.
let _precacheRunning = false;

async function backgroundPrecache() {
    // Sequential cache.add (not cache.addAll) so we trickle through
    // the connection one request at a time, leaving bandwidth for
    // foreground MapLibre fetches. cache: "no-cache" forces
    // revalidation against the server via conditional GET — Caddy
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
    // version — the multi-MB .pmtiles at the tail of PRECACHE_URLS
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
        if (await cache.match(PRECACHE_DONE_URL)) return;
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
        // Mark complete only when every URL is really present — a
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
        }
    } finally {
        _precacheRunning = false;
    }
}

// ============================================================
// Message handler — supports B.7 "Reload" toast button
// ============================================================
// When the user taps "Reload" on the update toast, the page posts
// {type: "SKIP_WAITING"} to this worker (the waiting one). We call
// skipWaiting() so this version becomes active; the page then
// observes `controllerchange` on navigator.serviceWorker and reloads
// itself. Without this handshake, the new SW would stay in the
// "waiting" state until every page using the old SW closed.
self.addEventListener("message", (event) => {
    if (event.data && event.data.type === "SKIP_WAITING") {
        self.skipWaiting();
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

// Answer a PRECACHE_STATUS query with byte-weighted progress.
//
// Why bytes and not a file count: PRECACHE_URLS is ~30 entries, but the
// .pmtiles archives at its tail are most of the payload. Counting files
// would report ~90% while the tile archives — the only thing that makes
// the map usable away from signal — were still absent.
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
// Activate — clean up old caches
// ============================================================
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((keys) =>
                Promise.all(
                    keys
                        .filter((k) => k.startsWith("trail-map-") && k !== CACHE_NAME)
                        .map((k) => caches.delete(k))
                )
            )
            .then(() => self.clients.claim())
    );
    // Resume any precache the install-time run didn't finish (the
    // worker may have been terminated mid-list while this version sat
    // waiting). Fire-and-forget OUTSIDE waitUntil — activation must
    // not block on a ~20 MB background fill.
    backgroundPrecache();
});

// ============================================================
// Fetch — cache-first for same-origin, with Range request
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
    // terrain.pmtiles before adding the source — offline, the GET
    // is cached but a strict match would miss, the fetch would
    // fall through to network, fail, and the terrain layer would
    // never be added. Synthesize a 200 with no body (HEAD has no
    // body anyway) from the GET cache entry so the precheck passes
    // and the subsequent Range requests find their cached blob.
    if (event.request.method === "HEAD") {
        event.respondWith(
            caches.match(event.request, { ignoreMethod: true }).then((cached) => {
                if (cached) {
                    return new Response(null, {
                        status: cached.status,
                        statusText: cached.statusText,
                        headers: cached.headers,
                    });
                }
                return fetch(event.request);
            })
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
    // default fetch honours the browser's HTTP cache — and Caddy
    // ships most assets with `Cache-Control: public, max-age=86400`,
    // so for up to 24 h post-deploy a SW cache miss could blindly
    // return yesterday's HTTP-cached bytes to the page (visible
    // as "I just deployed and clicked Reload but the page still
    // shows the old map until I shift-reload"). Revalidation is
    // ~50 ms of overhead when content hasn't changed (304 response,
    // no body) and free correctness when it has. Same
    // `cache: "no-cache"` posture as backgroundPrecache above.
    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) return cached;
            const networkReq = new Request(event.request, { cache: "no-cache" });
            return fetch(networkReq)
                .then((response) => {
                    // Cache successful same-origin GET responses
                    // only. Skip opaque (cross-origin no-CORS),
                    // partial (206 Range), and error responses —
                    // none of those round-trip cleanly via the
                    // Cache API for offline serving.
                    if (
                        response &&
                        response.status === 200 &&
                        response.type === "basic"
                    ) {
                        const clone = response.clone();
                        caches
                            .open(CACHE_NAME)
                            .then((cache) => cache.put(event.request, clone))
                            .catch(() => { /* best effort */ });
                    }
                    return response;
                });
        })
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
// below misses and we fall back to network — Caddy serves the
// Range request directly. The PMTiles slice is correct either way;
// the only difference is whether the bytes came from cache or
// network. Once the background precache eventually fetches the full
// file, subsequent Range requests slice from cache.
//
// Blob.slice() creates a lightweight view — not a copy.
// ============================================================
async function handleRangeRequest(request) {
    const cache = await caches.open(CACHE_NAME);

    // Match against the URL without Range header
    const fullResponse = await cache.match(new Request(request.url));
    if (!fullResponse) {
        // Not cached — pass through to network
        return fetch(request);
    }

    const blob = await fullResponse.blob();
    const rangeHeader = request.headers.get("Range");
    const match = rangeHeader.match(/bytes=(\d+)-(\d*)/);

    if (!match) {
        // Malformed Range header — return full response
        return new Response(blob, {
            status: 200,
            headers: fullResponse.headers,
        });
    }

    const start = parseInt(match[1]);
    const end = match[2] ? parseInt(match[2]) : blob.size - 1;

    return new Response(blob.slice(start, end + 1), {
        status: 206,
        headers: {
            "Content-Type": "application/octet-stream",
            "Content-Range": `bytes ${start}-${end}/${blob.size}`,
            "Content-Length": end - start + 1,
        },
    });
}
