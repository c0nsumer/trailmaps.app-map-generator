// MapLibre GL JS 6 is ESM only. This module puts it on window.maplibregl
// for the classic scripts in index.html, which still expect that global.
// A file, not an inline module: Chromium's renderer grows without bound
// resolving a relative import from an inline module when the page URL's
// fragment holds ~100,000 slashes, and an inline script needs a CSP hash.
import * as maplibre from "./vendor/maplibre-gl.mjs";

window.maplibregl = maplibre;
