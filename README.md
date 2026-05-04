# trailmaps.app Map Generator

Generate self-hosted, offline-capable, static mountain bike trail
maps from OpenStreetMap data. Each map is described by a single YAML
file; the build pipeline fetches trail data from OSM, extracts
regional basemap and terrain tiles, bundles every dependency
locally, and emits a complete static site you can deploy anywhere.
No external tile services or API keys are needed at runtime.

The framework powers the maps published at
[trailmaps.app](https://trailmaps.app).

## What you get per map

- A standalone static site under `build/<slug>/`, deployable to any
  HTTP server that supports Range requests (Caddy, nginx, Apache).
- A fully installable Progressive Web App: the map is usable
  offline after the first visit, including PMTiles range requests
  served from cache.
- Self-hosted basemap tiles (Protomaps), optional terrain hillshade
  (Mapterhorn), and optional custom raster basemaps (USGS topo,
  satellite, etc.).
- A floating-chrome viewer with three FAB buttons (Options, Search,
  Locate), a Search overlay (routes, trails, places), and an
  Options overlay (layer toggles, season switch, basemap picker,
  share, install, about).
- Per-route distance and USGS 3DEP elevation gain / loss (US only,
  optional).
- Trail markers, trailheads, parking, features, toilets, drinking
  water as configurable POI layers; direction arrows on one-way
  ways; per-route dash patterns; per-trail IMBA difficulty symbols.
- Light and dark colour schemes; per-map accent colour (manual or
  auto-derived from the logo); per-map branding via a logo and
  icon.

## Stack

- [MapLibre GL JS](https://maplibre.org) for vector + raster
  rendering (BSD-3-Clause).
- [PMTiles](https://docs.protomaps.com/pmtiles/) for self-hosted
  tile delivery without a tile server (BSD).
- [Protomaps basemap](https://protomaps.com) for the default vector
  basemap (BSD; built from OpenStreetMap data).
- [Overpass API](https://overpass-api.de/) for fetching OSM trail
  relations at build time.
- Python 3.9+ for the build pipeline.

## Quick start

```bash
# Set up the venv (one-time)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build a map
python scripts/build.py configs/ramba/ramba.yaml

# Preview locally
python scripts/serve.py build/ramba
# Open http://localhost:8090
```

A first-ever build of a new map takes ~5 to 10 minutes (downloads
basemap, terrain, sprites). Subsequent rebuilds with cached data
finish in under 30 seconds. See [`docs/building.md`](docs/building.md)
for the full pipeline, CLI flags, and caching behaviour.

## Creating a new map

1. Map your trails in OpenStreetMap as route relations under a
   super-relation (or pick an existing one).
2. Create `configs/<slug>/` (the folder name must match your chosen
   slug exactly). Copy
   [`configs/example/reference-minimal.yaml`](configs/example/reference-minimal.yaml)
   into it as `configs/<slug>/<slug>.yaml`. Set `name`, `slug`,
   `title`, `root_relation_id`. Uncomment and adjust any other keys
   you want to customise.
3. Drop your `logo.<ext>` and `icon.<ext>` source files into the
   same `configs/<slug>/` folder and reference them by bare
   filename (e.g. `logo: logo.webp`, `icon: icon.png`). Either
   key alone is fine; the framework uses one as a fallback for the
   other.
4. If you have any non-OSM routes (race courses, event loops),
   drop their `.geojson` files into `configs/<slug>/` and reference
   each from `custom_routes` by bare filename. See
   [`docs/configuration.md`](docs/configuration.md#custom-routes-full-guide)
   for the schema.
5. Run `./tools/build_and_deploy.sh --validate-only <slug>` to
   confirm the config is clean.
6. Run `./tools/build_and_deploy.sh --build-only <slug>` to
   generate the output.
7. Deploy `build/<slug>/` to your server. See
   [`docs/deployment.md`](docs/deployment.md) for a recommended
   Caddy configuration and PWA hosting notes.

The annotated reference at
[`configs/example/reference.yaml`](configs/example/reference.yaml)
explains every supported key in detail; the terse skeleton at
[`configs/example/reference-minimal.yaml`](configs/example/reference-minimal.yaml)
is what you copy to start a new map. Both files stay in identical
canonical key order so you can diff them at any time.

## Documentation

| Document | What's in it |
|---|---|
| [`docs/configuration.md`](docs/configuration.md) | Full YAML config reference. Every key, every accepted value, and deep dives on route buckets, custom routes, direction schedules, dash patterns, the About / Welcome modals, base layers, logo / icon assets, and privacy posture. |
| [`docs/building.md`](docs/building.md) | Build pipeline: prerequisites, CLI flags, the `build_and_deploy.sh` and `clean_config.py` helpers, the data cache, local `.osm` file support, vendor bundling, font trimming, and the project layout. |
| [`docs/deployment.md`](docs/deployment.md) | Hosting the output: Caddy config, service worker update cadence, PWA install behaviour by platform, PMTiles Range requests, Open Graph share previews. |
| [`docs/elevation.md`](docs/elevation.md) | USGS 3DEP elevation deep dive: data source, computation pipeline, why we show both gain and loss, caveats, and why the numbers won't match Strava. |
| [`docs/examples.md`](docs/examples.md) | Real-world configs from production trail systems. (Many sections are placeholders pending curator notes.) |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Common build and runtime issues, and known cosmetic upstream warnings. |

## Privacy

This framework is entirely client-side: there are no cookies, no
analytics, no server-side tracking, and no third-party scripts. The
map renders from your own static server, and all visitor
interactions stay in the browser. A handful of `localStorage` keys
persist UI preferences (season mode, layer toggles, colour scheme,
welcome dismissal); nothing else is stored.

The optional `url_hash: true` setting writes the current view to
the URL fragment (`#zoom/lat/lon`) for shareable deep-links; it's
off by default so a rider's last-viewed location doesn't leak via
the address bar, screenshots, or screen-shares. See
[`docs/configuration.md#privacy`](docs/configuration.md#privacy)
for the full posture and the EU regulatory framing.

## Author

Built and maintained by **Steve Vigneau**
([steve@nuxx.net](mailto:steve@nuxx.net) /
[nuxx.net](https://nuxx.net)).

If you fork this for your own trail system, a credit link back to
the source repo is appreciated but not required.

## Credits

This project depends on the work of many open-source projects and
public data sources. Each is credited in the in-app About modal of
every generated map, and their licences and origins are listed
here:

| Component | Used for | Licence |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | Trail data, basemap source data | ODbL 1.0 |
| [Protomaps](https://protomaps.com) (basemap tiles + JS) | Self-hosted vector basemap delivery | BSD-3-Clause (code), CC0 + ODbL (data) |
| [Mapterhorn](https://mapterhorn.com) | Terrain DEM aggregation (USGS 3DEP, EU-DEM, JAXA AW3D30) | Public-domain inputs aggregated under permissive licence |
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

If you re-deploy this framework, please preserve the in-app About
modal credits unchanged: every component above earned its place by
either being free infrastructure (Overpass), donated public data
(OpenStreetMap, USGS), or thoughtful open-source work (everyone
else).

---

<sub>Developed via pair-programming with Anthropic's Claude. Final
editorial control, design decisions, and accountability rest with
Steve Vigneau.</sub>
