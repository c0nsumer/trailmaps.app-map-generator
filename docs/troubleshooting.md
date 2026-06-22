# Troubleshooting

Common issues and their fixes, ordered roughly by how often they
come up. Cosmetic upstream warnings live at the bottom under [Known
issues](#known-issues).

## Contents

- [Build fails with "OSM relation not found" or "0 elements"](#build-fails-with-osm-relation-not-found-or-0-elements)
- [Map shows but trails are missing or incomplete](#map-shows-but-trails-are-missing-or-incomplete)
- [Bbox or pan_padding changes don't update the basemap](#bbox-or-pan_padding-changes-dont-update-the-basemap)
- [PMTiles won't load offline](#pmtiles-wont-load-offline)
- [Overpass keeps timing out](#overpass-keeps-timing-out)
- [Console warning: beforeinstallpromptevent.preventDefault() called](#console-warning-beforeinstallpromptevent-preventdefault-called)
- [Off-screen indicator points to the wrong location](#off-screen-indicator-points-to-the-wrong-location)
- ["Updated map available" toast doesn't appear after deploy](#updated-map-available-toast-doesnt-appear-after-deploy)
- [Build is slow](#build-is-slow)
- [Known issues](#known-issues)

## Build fails with "OSM relation not found" or "0 elements"

One of the relation IDs in `relations`, `clipped_relations`,
`winter_relations`, `summer_relations`, or
`emergency_access_relations` refers to an OSM relation that no longer
exists, has been redacted, or is currently unreachable from Overpass.

- **Verify the relation still exists**: open
  `https://www.openstreetmap.org/relation/<id>` in a browser. If you
  get a 404, the relation was deleted or merged. Find its
  replacement and update the YAML.
- **If the relation exists but Overpass returns 0 elements**: the
  relation may have been split or the geometry coverage moved. Try a
  fresh `--force` run to bypass any cached error response.
- **Last resort**: snapshot the relation's data into a local `.osm`
  XML file and switch to `osm_file: osm.osm` (see
  [Local .osm file support](building.md#local-osm-file-support) in
  the building guide). The build will then ignore Overpass entirely
  for that map.

## Map shows but trails are missing or incomplete

Most often a season-bucket filtering issue. Each route belongs to
non-exclusive Summer / Winter / Emergency buckets (see
[Route buckets](configuration.md#route-buckets)).

- The Options Season toggle hides the inactive bucket. If your map
  is showing summer mode, winter-only trails won't appear (and vice
  versa).
- Check `winter_relations` / `summer_relations` /
  `emergency_access_relations` in your YAML. A route in the wrong
  list won't render under the season the rider expects.
- For routes you want visible in BOTH seasons, list them in
  `summer_relations` AND tag them as `seasonal=winter` in OSM (or
  just put them in `summer_relations` to make them year-round).

## Bbox or pan_padding changes don't update the basemap

The basemap and terrain PMTiles are cached by output path, and bbox
changes normally invalidate them automatically. If a stale tile set
persists:

- Run with `--force` to clear all caches and re-extract from
  scratch.
- Or delete `build/<slug>/basemap.pmtiles` and
  `build/<slug>/terrain.pmtiles` manually, then re-run without
  `--no-basemap` / `--no-terrain`.

## PMTiles won't load offline

The browser's HTTP cache is dropping the PMTiles file but the
service worker isn't catching it. Two likely causes:

- **Server doesn't allow Range requests on `.pmtiles`**: PMTiles
  uses HTTP Range requests to read tile chunks. If your reverse
  proxy strips the `Range` header or returns a 200 with the full
  body instead of a 206 Partial Content, the client falls back to
  fetching the whole archive on every tile read. The runtime
  detects this on first cold load and prints
  `[mtb-map] HTTP Range requests not honored...` to the browser
  console (DevTools > Console) with diagnostic detail. After the
  service worker caches the full file, the warning stops firing
  (that's correct behavior, but every new visitor still pays the
  slow first-load cost). Verify manually with:
  `curl -H "Range: bytes=0-1000" -I https://yourserver/path/to/basemap.pmtiles`.
  Should return `206 Partial Content` and `Content-Range`.
- **Service worker not caching `.pmtiles`**: open
  DevTools > Application > Cache Storage > `trail-map-<version>` and confirm
  `basemap.pmtiles` and `terrain.pmtiles` are listed. If not, the
  precache list missed them: rebuild and verify the build log
  mentions both files.

## Overpass keeps timing out

Overpass is a shared public service and can be slow during peak
hours.

- Check [Overpass status](https://overpass-api.de/) before
  retrying.
- Use a local `osm_file:` snapshot. Once you have an `.osm` file for
  a map, builds use that and skip Overpass entirely. Update the
  snapshot when you want to refresh data.
- For very large maps (city-wide trail networks), consider running
  `osmium` locally to extract a subset and using that as your
  `osm_file`.

## Console warning: beforeinstallpromptevent preventDefault() called

The default `pwa_install_prompt: true` registers a
`beforeinstallprompt` handler so we can show our own Install row in
the Options overlay. We deliberately do NOT call `preventDefault()`,
which lets Chrome's native mini-infobar appear, but Chrome logs this
warning anyway because it expects either `preventDefault()` or an
immediate `prompt()` call. The warning is benign and can be ignored.
Set `pwa_install_prompt: false` to opt out of install promotion
entirely (no handler registered, no warning, no Install row).

## Off-screen indicator points to the wrong location

If the location indicator triangle points to where you *aren't*:

- The browser may be returning a cached or inaccurate position. Tap
  Locate to disable, then re-enable to force a fresh GPS read.
- macOS Location Services may be off for the browser. Open System
  Settings > Privacy & Security > Location Services and enable it for
  your browser.
- Inside a building? GPS accuracy degrades to plus or minus 100m or
  worse indoors. The indicator is still doing its job; the
  underlying position estimate is just wide.

## "Updated map available" toast doesn't appear after deploy

The service worker decides when to check for updates. If you've
just deployed and refreshed but no toast appears:

- The browser may have already activated the new SW silently. Check
  DevTools > Application > Service workers. If the active SW
  shows a recent install date matching your deploy, you're already
  on the new version.
- In DevTools > Application > Service workers, check "Update on
  reload" to force a refetch on every page load. Useful while
  iterating; turn it off in normal browsing.
- The toast detection only fires when there's a *prior* SW (i.e.
  this is an UPDATE, not a first install). A fresh browser profile
  or cleared site data won't trigger it.

## Build is slow

Expected times (typical):

- First-ever build of a new map: 5 to 10 min (downloads basemap,
  terrain, sprites).
- Re-build with cached data, no `--force`: under 30 seconds.
- Build with `show_route_elevation: true` and a fresh cache: extra
  ~30 sec to 2 min for USGS 3DEP API calls (one batch per route at
  25m sampling; auto-retries transient 502s).
- `--force` on a large map: 10 to 20 min.

If a build takes much longer, the slowest steps are usually terrain
extraction (Mapterhorn HTTP fetches over a wide bbox) and Overpass
(depends on relation size + Overpass server load).

## Known issues

- **Firefox console warning**: `WebGL warning: texImage: Alpha-premult
  and y-flip are deprecated for non-DOM-Element uploads.` This is a
  cosmetic warning from MapLibre GL JS and does not affect
  functionality. A
  [fix has been merged](https://github.com/maplibre/maplibre-gl-js/pull/7128)
  and will be included in a future MapLibre GL JS release.
