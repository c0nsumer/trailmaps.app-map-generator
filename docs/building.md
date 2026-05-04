# Building

How the build pipeline works, the CLI flags it accepts, the caching
behaviour you can rely on, and the tools that wrap it for everyday
use. Everything in this document runs on your local machine; for
hosting the output, see [`deployment.md`](deployment.md).

## Contents

- [Prerequisites](#prerequisites)
- [Building a map](#building-a-map)
- [Build options](#build-options)
- [Local preview](#local-preview)
- [Convenience wrapper: build_and_deploy.sh](#convenience-wrapper-build_and_deploysh)
- [Validate a config without building](#validate-a-config-without-building)
- [Re-aligning a production config: clean_config.py](#re-aligning-a-production-config-clean_configpy)
- [Data cache](#data-cache)
- [Local .osm file support](#local-osm-file-support)
- [Vendor bundling](#vendor-bundling)
- [Font trimming](#font-trimming)
- [Project structure](#project-structure)

## Prerequisites

- Python 3.9 or newer (tested through 3.14) with
  `pip install -r requirements.txt`. Versions are pinned for
  reproducible builds; see the file header for upgrade notes.
- [`pmtiles`](https://github.com/protomaps/go-pmtiles) CLI:
  `go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest`.
- Self-hosted [Protomaps basemap assets](https://github.com/protomaps/basemaps-assets/releases)
  (fonts + sprites) extracted into `assets/`.
- Optional: [`potrace`](http://potrace.sourceforge.net/) for Safari
  pinned-tab SVG icon generation.
  - macOS: `brew install potrace`
  - Linux: `apt install potrace`
  - Windows: `choco install potrace` or download from
    [potrace.sourceforge.net](http://potrace.sourceforge.net/#downloading)
  - If not installed, the build skips `safari-pinned-tab.svg` with a
    warning. Everything else works normally.

## Building a map

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Build the RAMBA map (first run fetches all data)
python scripts/build.py configs/ramba/ramba.yaml

# Preview locally
python scripts/serve.py build/ramba
# Open http://localhost:8090
```

A first-ever build of a new map takes ~5 to 10 minutes (downloads
basemap, terrain, sprites). Subsequent rebuilds with cached data
finish in under 30 seconds.

## Build options

```bash
python scripts/build.py configs/ramba/ramba.yaml                 # Full build (uses caches)
python scripts/build.py configs/ramba/ramba.yaml --force          # Re-fetch everything (clears Overpass cache)
python scripts/build.py configs/ramba/ramba.yaml --trails         # Re-fetch trail + POI data (uses Overpass cache)
python scripts/build.py configs/ramba/ramba.yaml --skip-terrain   # Skip terrain tile generation
python scripts/build.py configs/ramba/ramba.yaml --skip-basemap   # Skip basemap extraction
```

- `--force` clears the Overpass API response cache (`cache/`) and
  re-fetches all data from OSM, re-extracts basemap and terrain
  tiles.
- `--trails` re-runs the trail and POI data pipeline but reuses
  cached Overpass API responses if available. Useful for
  re-processing data after changing YAML options like
  `dashed_relations`, `relation_colors`, `winter_relations`,
  `summer_relations`, or `custom_routes` without hitting the
  Overpass API again.
- `--skip-terrain` and `--skip-basemap` skip the corresponding tile
  extraction steps. Useful for faster rebuilds when only templates or
  config options have changed.

The basemap extraction automatically detects the latest available
[Protomaps planet build](https://maps.protomaps.com/builds/), so
there's no URL to update by hand. You can override this by setting
the `PROTOMAPS_PLANET_URL` environment variable.

Flags can be combined: `--trails --skip-basemap --skip-terrain`
re-processes trail data and rebuilds templates without touching
tiles.

### Expected build times

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

## Local preview

A development server with HTTP Range request support lives at
`scripts/serve.py`:

```bash
python scripts/serve.py build/ramba
# Open http://localhost:8090
```

This is the fastest way to test changes without a production deploy.
The server honours Range requests properly so PMTiles work end-to-end.

## Convenience wrapper: build_and_deploy.sh

`tools/build_and_deploy.sh` validates every config first, then builds
and (optionally) deploys via `rsync`. Run `./tools/build_and_deploy.sh
--help` for full usage. Common patterns:

```bash
# Build and deploy every map under configs/ (excluding configs/example/)
./tools/build_and_deploy.sh

# Build and deploy a subset
./tools/build_and_deploy.sh ramba dte

# Build but skip deploy
./tools/build_and_deploy.sh --build-only ramba

# Re-fetch all data and rebuild
./tools/build_and_deploy.sh --force ramba

# Pass extra flags through to build.py
./tools/build_and_deploy.sh ramba -- --skip-basemap --skip-terrain
```

The deploy destination is the `DEFAULT_DEPLOY_DEST` constant at the
top of the script; override per-run via `--dest <ssh-path>`.

See [`tools/README.md`](../tools/README.md) for the full option table.

## Validate a config without building

```bash
./tools/build_and_deploy.sh --validate-only          # all configs
./tools/build_and_deploy.sh --validate-only ramba    # just one
```

Or invoke the validator directly:

```bash
python scripts/validate_config.py configs/ramba/ramba.yaml
```

Validation is fast and catches every YAML / value error in one pass
before any expensive fetch or build work starts. The validator
checks:

- Top-level key spelling against `KNOWN_KEYS` (suggests close matches
  for typos).
- Value types and allowed enums (`default_labels`, `color_by`,
  `distance_units`, `default_color_scheme`, `reverse_days` tokens,
  custom-route geometry types, etc.).
- Asset file existence (`logo:`, `icon:`, `osm_file:`,
  `custom_routes[].geometry`).
- Custom-route bucket sanity (at least one of summer / winter /
  emergency must be true; ID must not collide with any OSM relation
  ID in the config).
- Slug must equal the parent folder name and match `[a-z0-9_-]+`.

## Re-aligning a production config: clean_config.py

Production configs accumulate cruft over time as they're maintained
by hand: keys reordered, comments edited, sections renamed, drift
from the template's structure. `tools/clean_config.py` produces a
sibling `<input>-cleaned.yaml` that adopts the canonical template's
structure (section dividers, key ordering, default-value
documentation comments) while preserving every value the production
file explicitly set.

```bash
# Default template (configs/example/reference-minimal.yaml)
python tools/clean_config.py configs/potoloo/potoloo.yaml

# Custom template (e.g. the verbose annotated reference)
python tools/clean_config.py configs/foo/foo.yaml \
    --template configs/example/reference.yaml
```

The original file is never modified. Review the cleaned output and
swap it in manually when satisfied. See
[`tools/README.md`](../tools/README.md) for behaviour and
output-formatting notes.

## Data cache

The build pipeline caches Overpass API responses in the `cache/`
directory to avoid redundant network requests. **Cached data is never
automatically updated.** Subsequent builds reuse existing cache files
indefinitely until you explicitly clear them.

### Checking cache age

When the build runs, it logs the date and age of each cached
response it uses:

```
Using cached response (2026-04-07 22:45, 2d ago): cache/overpass_798bc0f14a88.json
```

You can also check cache ages manually:

```bash
ls -la cache/
```

### Refreshing cached data

To update the cached OSM data (e.g. after trail edits in
OpenStreetMap):

- **`--force`** clears the entire `cache/` directory and re-fetches
  all Overpass data from scratch. Also re-extracts basemap and
  terrain tiles.
- **`--trails`** re-runs the trail and POI data pipeline. Reuses
  existing cached Overpass responses if present; only fetches data
  that isn't already cached.

To force a full refresh of just trail data, delete the relevant cache
files manually and run with `--trails`:

```bash
rm cache/overpass_*.json
python scripts/build.py configs/ramba/ramba.yaml --trails --skip-basemap --skip-terrain
```

### Build and data dates

The About modal shows both the build date and the date of the cached
data source, so visitors can see how current the trail information
is.

## Local .osm file support

Instead of fetching data from the Overpass API, you can build maps
from a local `.osm` XML file. This is useful for:

- Non-public trail data maintained locally in JOSM.
- Offline map generation without internet access.
- Testing edits before uploading to OpenStreetMap.

Add `osm_file` to your config. The path is resolved relative to the
config's directory, so a bare filename like `osm.osm` picks up the
file sitting next to the YAML:

```yaml
# configs/mytrails/mytrails.yaml
osm_file: osm.osm              # resolves to configs/mytrails/osm.osm
root_relation_id: 12345678     # still required
```

The `.osm` file must contain the root relation, any child relations
it references, their member ways, and all referenced nodes with
coordinates (this is the default when saving from JOSM).

All other config options (`extra_relations`, `clipped_relations`,
`winter_relations`, `summer_relations`,
`emergency_access_relations`, `dashed_relations`, etc.) work the
same way: they reference IDs found in the file instead of Overpass.

### Downloading from Overpass

You can download a complete `.osm` file for a super-relation from the
Overpass API. This fetches the super-relation, all child relations,
their member ways, and all referenced nodes with full geometry:

```bash
curl -o configs/mytrails/osm.osm "https://overpass-api.de/api/interpreter" \
  --data-urlencode "data=[out:xml][timeout:300];
    relation(12345678);
    rel(r);
    (._; way(r););
    (._;>;);
    out meta;"
```

Replace `12345678` with your super-relation ID. The query works as
follows:

1. `relation(12345678)`: fetches the super-relation.
2. `rel(r)`: fetches all child relations.
3. `way(r)`: fetches all ways referenced by those relations.
4. `(._;>;)`: recursively resolves all node references to get
   coordinates.
5. `out meta`: outputs full XML with coordinates and metadata.

The resulting file can be opened and edited in JOSM, then used
directly with the build pipeline.

You can preview what the parser finds without building:

```bash
python scripts/osm_parser.py configs/mytrails/osm.osm 12345678
```

## Vendor bundling

All JavaScript and CSS dependencies (MapLibre GL JS, PMTiles,
Protomaps basemaps) are downloaded from their CDNs at build time and
bundled into `vendor/` in the output directory. The generated map has
**no runtime CDN dependency**; everything is served from your own
server. This ensures the map continues to work even if upstream CDNs
go offline or change.

Vendor libraries are bundled regardless of the `pwa` setting.

## Font trimming

The Protomaps basemap assets include fonts covering every world
script (Latin, CJK, Devanagari, etc.) across 256 Unicode range files
per font face: roughly 20 MB total. Most maps only need a small
subset of these.

The build pipeline automatically trims fonts by scanning the basemap
tiles, trail data, and POI data for every text character that
actually appears, then copying only the PBF glyph ranges containing
those characters. Script-specific font faces (e.g. Devanagari) are
only included when the map data contains characters from that
script.

This is fully data-driven: a US trail map gets only Latin ranges,
while a map in Japan would automatically include CJK ranges. No
configuration is needed.

You can preview font trimming results without building:

```bash
python scripts/font_trimmer.py build/ramba/
```

## Project structure

```
configs/
  <slug>/
    <slug>.yaml         The map's config
    logo.<ext>          Optional: source logo
    icon.<ext>          Optional: source square icon for favicons + PWA icons
    osm.osm             Optional: offline OSM snapshot
    *.geojson           Optional: custom-route geometries (one per custom_routes entry)
  example/
    reference-minimal.yaml  Canonical-order skeleton; copy this to start a new map
    reference.yaml          Verbose annotated reference (same key order)

scripts/
  build.py            Build orchestrator
  fetch_trails.py     OSM trail data via Overpass API or local .osm file
  fetch_pois.py       Trail markers (guideposts + emergency-access points, merged), features, toilets, drinking water
  osm_parser.py       Parser for local .osm XML files
  fetch_basemap.py    Protomaps basemap PMTiles extraction
  fetch_terrain.py    Mapterhorn terrain PMTiles extraction
  generate_icons.py   Icon generation from source image (Pillow)
  font_trimmer.py     Automatic font subsetting based on map data
  validate_config.py  Pre-flight YAML validation
  serve.py            Dev server with Range request support
  compute_route_stats.py     Per-route distance + USGS 3DEP elevation
  compare_elevation_sources.py  Diagnostic for elevation source comparisons

templates/
  index.html          Map viewer page
  app.js              MapLibre GL JS application
  style.css           Theme styles (light + dark via [data-color-scheme])
  sw.js               Service worker template for offline / PWA support

assets/
  fonts/              Protomaps basemap fonts (PBF glyph ranges, auto-trimmed at build time)
  sprites/            Protomaps basemap sprites (PNG + JSON, all flavours)

build/<slug>/         Generated output (deployable static site)
cache/                Cached Overpass API responses

tools/
  build_and_deploy.sh Convenience wrapper: validate then build then optional rsync deploy
  clean_config.py     Re-align a production YAML against the canonical template
  README.md           Tool documentation

docs/                 Documentation (this folder)
```
