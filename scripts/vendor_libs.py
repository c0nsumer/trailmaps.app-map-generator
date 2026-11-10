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

# SHA-256 of each file above, checked after a download and on every cache
# read. The bytes are the app, and unpkg is the only party vouching for
# them; these were also checked against the npm registry tarballs (its
# dist.integrity), an independent source. To bump a pin, change the URL
# AND the hash here together: fetch the new file, compare it against the
# registry tarball for that version, then paste its `sha256sum`. A build
# fails on any mismatch, so a forgotten hash cannot ship quietly.
VENDOR_SHA256 = {
    "maplibre-gl.css": "8e2dbbab312dc57656fbb76e9fa5308c75c9d7c7ba5808a7d55bcdb64cc813fa",
    "maplibre-gl.mjs": "454e0bbb16c721cfbf952a92ff3e4362130346ed59a5bff56ae5d6fbebb651f7",
    "maplibre-gl-shared.mjs": "0996a0ff2ecb2807afc3f053992539ff7fe45c1678a02304b2b33120136f81f3",
    "maplibre-gl-worker.mjs": "7d5ebf88ec25a72cc48cc320d374f41d921e5105d2f1fc774d463a4b68164227",
    "pmtiles.js": "36bcbe1ba97cc07b3fc90cee9cba11729b04e25ec8790cf65a0787d5b38e091b",
    "basemaps.js": "551864f01da1bfe3005e99987197f22f56d0c35f9a10fa1727a889e2434af16d",
    "maplibre-contour.js": "055e45d0a76aaeaaf5a96765b8ce524abba3272456be75e715081e235ebd1262",
    "uqr.mjs": "7f0e61c2f13bb3724edee7bfb876e13c54ac9ee4fcfa283c9fa93cfb1241c325",
    "maplibre-gl-lanes.js": "80aca0d4531c1056f69faa4508115f7b02f5c2327613a72cc67691ebd633872b",
}
