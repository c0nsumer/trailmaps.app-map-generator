// Service Worker for MTB Trail Map Framework
// Provides offline support via precaching all build assets.
//
// Config is injected at build time by build.py:
//   SW_CONFIG.CACHE_VERSION  — hash-based cache version string
//   SW_CONFIG.PRECACHE_URLS  — list of all files to cache
//   SW_CONFIG.PMTILES_FILES  — list of .pmtiles filenames for Range request handling

/*__SW_CONFIG__*/

const CACHE_NAME = `trail-map-${SW_CONFIG.CACHE_VERSION}`;

// ============================================================
// Install — precache all build output
// ============================================================
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches
            .open(CACHE_NAME)
            .then((cache) => cache.addAll(SW_CONFIG.PRECACHE_URLS))
            .then(() => self.skipWaiting())
    );
});

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

    // Standard cache-first strategy
    event.respondWith(
        caches.match(event.request).then((cached) => {
            return cached || fetch(event.request);
        })
    );
});

// ============================================================
// Range request handler for PMTiles
//
// PMTiles uses HTTP Range requests to read tile chunks from the
// archive. The Cache API stores the full file (fetched during
// install via cache.addAll). On a Range request we retrieve the
// full cached response, slice the requested byte range from the
// blob, and return a synthetic 206 Partial Content response.
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
