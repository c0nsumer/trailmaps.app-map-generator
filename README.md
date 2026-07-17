# trailmaps.app Map Generator

This framework generates static mountain bike trail maps from OpenStreetMap data
and other open data sets. Each map is plain HTML, CSS, and JavaScript that can
be self-hosted without a database, works offline, and can be installed as a PWA.

Each map is described by a single YAML file, then the build pipeline fetches
trail data from OSM, extracts regional basemap and terrain tiles, bundles every
dependency locally, and emits a complete static site you can deploy anywhere. No
external tile services or API keys are needed at runtime.

This is the framework that generates most of the maps at
[trailmaps.app](https://trailmaps.app).

## Screenshot
![Bloomer + Clinton River Oaks Map as of 2026-Jul-05](docs/screenshots/screenshot_bloomer_2026-jul-05.png?raw=true
"Bloomer + Clinton River Oaks Map as of 2026-Jul-05") *Bloomer + Clinton River
Oaks Map as of 2026-Jul-05*

## Map / Generator Features

- Builds standalone static site under `build/<slug>/`, deployable to any HTTP
  server that supports Range requests (Caddy, nginx, Apache).
- Optional: A fully installable Progressive Web App: the map is usable offline
  after the first visit, including PMTiles range requests served from cache.
- Self-hosted basemap tiles (Protomaps), optional terrain hillshade
  (Mapterhorn), and optional custom raster basemaps.
- On-map controls: Locate, Reset View, and Options buttons (top-right), plus
  the routes panel (bottom-right):
  - Locate: show the user's position from the device GNSS sensors.
  - Reset View: return to the map's initial framing.
  - Options: toggle labels, color scheme, season, and POI layers; install the
    PWA; open About.
  - Routes panel: a collapsible key pairing each route's color with its name
    and stats. Tap a row to highlight that route, or use its Search row to find
    routes, trails, and POIs on the map.
- Optional per-route distance and USGS 3DEP elevation gain / loss (US only).
- Trail markers, trailheads, parking, features, toilets, drinking water as
  configurable POI layers; direction arrows on one-way ways; per-route dash
  patterns; per-trail IMBA difficulty symbols.
- Compass heading indicator: on devices with a compass, the Locate dot grows
  a small cone showing which way the rider is facing. Permission-gated on iOS
  (asked once, on the Locate tap); reads only the direction fields each GPS
  fix already carries; the app keeps no location history.
- Light and dark color schemes; per-map accent color (manual or auto-derived
  from the logo); per-map branding via a logo and icon, plus optional secondary
  event / sponsor logos stacked under the primary.
- Summer and Winter modes, so systems with groomed winter routes can present
  each season's network separately.

## Stack

- [MapLibre GL JS](https://maplibre.org) for vector + raster rendering
  (BSD-3-Clause).
- [PMTiles](https://docs.protomaps.com/pmtiles/) for self-hosted tile delivery
  without a tile server (BSD).
- [Protomaps basemap](https://protomaps.com) for the default vector basemap
  (BSD; built from OpenStreetMap data).
- [Overpass API](https://overpass-api.de/) for fetching OSM trail relations at
  build time.
- Python 3.11 or newer (tested through 3.14) for the build pipeline.

## Quick start

```bash
# Set up the venv (one-time)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install pmtiles somewhere in your path.

# Build a map
python scripts/build.py configs/example/example.yaml

# Preview locally
python scripts/serve.py build/example
# Open http://localhost:8090
```

A first-ever build of a new map takes ~5 to 10 minutes (downloads basemap,
terrain, sprites). Subsequent rebuilds with cached data finish in under 30
seconds. See [`docs/building.md`](docs/building.md) for the full pipeline, CLI
flags, and caching behavior.

The build can also be invoked as a package, which lets the caller redirect
output and cache to arbitrary paths without touching the config file:

```bash
python -m map_generator build configs/example/example.yaml \
    --output-dir /tmp/example-build \
    --cache-dir  /tmp/example-cache
```

Both entry points are interchangeable; the package form just forwards flags
to `scripts/build.py`. Omit `--output-dir` / `--cache-dir` and the legacy
`build/<slug>/` and `cache/` layout under the repo root is used.

## Bring your own configs

The engine itself does not ship any specific trail data. The only configs
tracked in this repo are:

- [`configs/example/`](configs/example/) : a worked example with a small
  config, an icon, and a logo. Build it as a smoke test, or copy it to
  start a new map.
- [`configs/reference/`](configs/reference/) : the canonical schema
  references. `reference-minimal.yaml` is a terse skeleton for new maps;
  `reference.yaml` is annotated with every supported key.

The `configs/*` glob is gitignored, with explicit exceptions only for
those two folders. Any `configs/<slug>/` you create stays private to your
checkout. Point the engine at a config anywhere on disk via the positional
`config` argument plus `--output-dir` / `--cache-dir` to keep your data
fully outside this repo.

The trailmaps.app maps that originally lived in this repo now live in a
separate private orchestrator that drives this engine as a CLI tool.

## Creating a new basic map with PWA support

1. Map your trails in OpenStreetMap as route relations under a relation or
   super-relation (or pick an existing one).
2. Create `configs/<slug>/` (the folder name must match your chosen slug
   exactly). Copy
   [`configs/reference/reference-minimal.yaml`](configs/reference/reference-minimal.yaml)
   into it as `configs/<slug>/<slug>.yaml`. Set `name`, `slug`, `title`, and a
   geometry source. This is usually `relations:` (a non-empty list of OSM
   relation IDs; each entry may be a leaf route relation or a super-relation);
   a route-only or event map can use `custom_routes` / `event_mode.routes`
   instead. Uncomment and adjust any other keys you want to customize.
3. Drop your `logo.<ext>` and `icon.<ext>` source files into the same
   `configs/<slug>/` folder and reference them by bare filename (e.g.
   `logo: logo.webp`, `icon: icon.png`). Either key alone is fine; the framework
   uses one as a fallback for the other. The exception is SVG logos, which
   can't be rasterized into icons, so set `icon:` to a PNG/WebP in that case.
4. Run `python scripts/build.py configs/<slug>/<slug>.yaml` to generate the
   output.
5. Deploy `build/<slug>/` to your server. See
   [`docs/deployment.md`](docs/deployment.md) for a recommended Caddy
   configuration and PWA hosting notes.

The annotated reference at
[`configs/reference/reference.yaml`](configs/reference/reference.yaml) explains
every supported key in detail; the terse skeleton at
[`configs/reference/reference-minimal.yaml`](configs/reference/reference-minimal.yaml)
is useful to copy to start a new map.

## Documentation

| Document | What's in it |
|---|---|
| [`docs/configuration.md`](docs/configuration.md) | Full YAML config reference. Every key, every accepted value, and deep dives on route buckets, custom routes, direction schedules, dash patterns, the About / Welcome modals, base layers, logo / icon assets, and privacy posture. |
| [`docs/building.md`](docs/building.md) | Build pipeline: prerequisites, CLI flags, the `build_and_deploy.sh` and `clean_config.py` helpers, the data cache, local `.osm` file support, vendor bundling, font trimming, and the project layout. |
| [`docs/deployment.md`](docs/deployment.md) | Hosting the output: Caddy config, service worker update cadence, PWA install behavior by platform, PMTiles Range requests, Open Graph share previews. |
| [`docs/elevation.md`](docs/elevation.md) | USGS 3DEP elevation: the data source, why both gain and loss are shown, accuracy caveats, and why the numbers won't match Strava or a GPS. |
| [`docs/event-mode.md`](docs/event-mode.md) | Event-specific maps (races, group rides). Feature one or more routes prominently while every other trail renders as muted context. POIs unchanged. |
| [`docs/osm-mapping.md`](docs/osm-mapping.md) | Mapping and tagging trail systems in OpenStreetMap: the standard tags this renderer reads (route relation `name` / `colour` / `ref`, way-level `mtb:scale:imba` / `oneway`, POI categories), why "tagging for the renderer" is the wrong frame, and practical advice for trail-system mappers. |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Common build and runtime issues, and known cosmetic upstream warnings. |

## Privacy

This framework is entirely client-side: there are no cookies, no analytics, no
server-side tracking, and no third-party scripts. The map renders from your own
static server, and all visitor interactions stay in the browser.

The app uses `localStorage` to persist a small set of functional UI preferences across
visits, each key prefixed with the map's slug (for example,
`<slug>.mtb.colorScheme`):

- `mtb.seasonMode`: "summer" or "winter"
- `mtb.emergencyOn`: boolean (Emergency overlay)
- `mtb.poi.<kind>`: one boolean per POI category (parking, trailheads, hubs,
  features, markers, toilets, drinking_water)
- `mtb.labels`: "routes", "trails", or "none"
- `mtb.difficulty`: boolean (IMBA difficulty symbols)
- `mtb.directionArrows`: boolean (direction arrows)
- `mtb.colorScheme`: "light", "dark", or "auto"
- `mtb.fabsLabeled`: boolean (text labels on the on-map buttons)
- `mtb.routePanelExpanded`: boolean (routes panel expanded/collapsed)
- `mtb.welcomed`: boolean (welcome modal dismissal)

Nothing else is stored. No identifiers, no geolocation traces, no analytics
payloads.

## Author

Built and maintained by **Steve Vigneau**
([steve@nuxx.net](mailto:steve@nuxx.net) / [nuxx.net](https://nuxx.net)).

And yes, this was developed using Anthropic's Claude.

## Credits

This project depends on the work of many open-source projects and public data
sources. Each is credited in the in-app About modal of every generated map, and
their licenses and origins are listed here:

| Component | Used for | License |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | Trail data, basemap source data | ODbL 1.0 |
| [Protomaps](https://protomaps.com) (basemap tiles + JS) | Self-hosted vector basemap delivery | BSD-3-Clause (code), CC0 + ODbL (data) |
| [Mapterhorn](https://mapterhorn.com) | Terrain DEM aggregation (USGS 3DEP, EU-DEM, JAXA AW3D30) | Public-domain inputs aggregated under permissive license |
| [USGS 3D Elevation Program (3DEP)](https://www.usgs.gov/3d-elevation-program) | Per-route elevation profiles | Public domain (US government work) |
| [MapLibre GL JS](https://maplibre.org) | Vector + raster map rendering | BSD-3-Clause |
| [PMTiles](https://github.com/protomaps/PMTiles) | Single-file tile archive format and JS reader | BSD-3-Clause |
| [Material Design Icons](https://pictogrammers.com/library/mdi/) (Pictogrammers) | UI iconography (inline SVG) | Apache 2.0 |
| [SIL Open Font License](https://openfontlicense.org/) | Map label fonts (via Protomaps) | OFL 1.1 |
| [Pillow](https://python-pillow.org/) | Build-time icon and logo image processing | HPND |
| [PyYAML](https://pyyaml.org/) | YAML config parsing | MIT |
| [requests](https://requests.readthedocs.io/) | HTTP client for Overpass / 3DEP fetches | Apache 2.0 |
| [shapely](https://shapely.readthedocs.io/) | Geometry processing in build scripts | BSD-3-Clause |
| [pyclipper](https://github.com/fonttools/pyclipper) | Polygon offsetting (parallel route smoothing) | MIT |
| [mapbox-vector-tile](https://github.com/mapbox/mapbox-vector-tile-py) | Vector tile parsing in build scripts | BSD-3-Clause |
| [potrace](http://potrace.sourceforge.net/) (optional) | Safari pinned-tab SVG generation | GPL 2.0 |
| [go-pmtiles](https://github.com/protomaps/go-pmtiles) | PMTiles CLI for tile extraction | BSD-3-Clause |

If you re-deploy this framework, please preserve the in-app About modal credits
unchanged: every component above earned its place by either being free
infrastructure (Overpass), donated public data (OpenStreetMap, USGS), or
thoughtful open-source work (everyone else).
