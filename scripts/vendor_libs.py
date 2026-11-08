"""Third-party browser libraries every map bundles into vendor/.

Lives in its own file so the app version (template_inject
._engine_app_version) can key off it alongside templates/: a pin bump
changes code riders run, so it must advance the About version and the
gallery date the same way a templates/ commit does.
"""

# CDN libraries to bundle locally for offline/PWA support.
# Update versions here when upgrading dependencies.
VENDOR_LIBS = {
    # MapLibre GL JS 6 is ESM only: an entry module, a chunk it shares
    # with its worker, and the worker, which the entry starts as a module
    # worker from a URL beside itself. All three must sit in vendor/
    # under their upstream names, because they import each other by
    # those names. index.html loads the entry with a module script that
    # sets window.maplibregl for the classic scripts after it. A file
    # missing here still works online and fails offline, since the
    # service worker precaches only what the build wrote to vendor/.
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl.css",
    "maplibre-gl.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl.mjs",
    "maplibre-gl-shared.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl-shared.mjs",
    "maplibre-gl-worker.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl-worker.mjs",
    "pmtiles.js": "https://unpkg.com/pmtiles@4.4.1/dist/pmtiles.js",
    "basemaps.js": "https://unpkg.com/@protomaps/basemaps@5.7.2/dist/basemaps.js",
    # Client-side contour isolines from the terrain raster-dem
    # (templates/app.js addContourLayers). Upstream ships index.min.js;
    # renamed here to match the vendor/ one-lib-one-name convention.
    # 0.1.1 is the floor: 0.1.0 returned its cached ArrayBuffer itself,
    # MapLibre detached it on transfer to its worker, and the next cache
    # hit lost that tile's contours on the worker:false path this app
    # uses (fixed in onthegomap/maplibre-contour#437).
    "maplibre-contour.js": "https://unpkg.com/maplibre-contour@0.1.1/dist/index.min.js",
    # QR code encoder for the Options Share sheet (renderQrSheet in
    # app.js): ES module, about 27 KB, MIT, Project Nayuki's generator
    # as packaged by Anthony Fu. index.html imports it and sets
    # window.uqr; the QR row stays hidden when it is missing.
    "uqr.mjs": "https://unpkg.com/uqr@0.1.3/dist/index.mjs",
    # Draws every route, so a page without it cannot start. The
    # classic-script build (global maplibreLanes) with its workers
    # inlined, so it needs no sibling files. 1.3.0 is the floor: the
    # inside-corner bevel that ended the low-zoom lane spike, the
    # loop-removal cut anchor, every route at every overview zoom, and
    # the dash phase fixed to the map.
    "maplibre-gl-lanes.js": "https://unpkg.com/maplibre-gl-lanes@1.4.1/dist/maplibre-gl-lanes.js",
}
