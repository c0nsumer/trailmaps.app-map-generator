# Building

How the build pipeline works, the CLI flags it accepts, the caching
behavior you can rely on, and the tools that wrap it for everyday
use. Everything in this document runs on your local machine. For
hosting the output, see [`deployment.md`](deployment.md).

## Contents

- [Prerequisites](#prerequisites)
- [Building a map](#building-a-map)
- [Build options](#build-options)
- [Local preview](#local-preview)
- [Convenience wrapper: build_and_deploy.sh](#convenience-wrapper-build_and_deploysh)
  - [Template lint (contributors only)](#template-lint-contributors-only)
- [Validate a config without building](#validate-a-config-without-building)
- [Re-aligning a production config: clean_config.py](#re-aligning-a-production-config-clean_configpy)
- [Data cache](#data-cache)
- [Local .osm file support](#local-osm-file-support)
- [Vendor bundling](#vendor-bundling)
- [Font trimming](#font-trimming)
- [Project structure](#project-structure)

## Prerequisites

- Python 3.11 or newer (tested through 3.14) with
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
- Optional, contributors only: Node.js for the template lint (see
  [Template lint](#template-lint-contributors-only)). **Not needed to
  build maps.** The `package.json` at the repo root is dev tooling,
  not a build dependency. There is no JS build step.

## Building a map

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Build the example map (first run fetches all data)
python scripts/build.py configs/example/example.yaml

# Preview locally
python scripts/serve.py build/example
# Open http://localhost:8090
```

A first-ever build of a new map takes ~5 to 10 minutes (downloads
basemap, terrain, sprites). Subsequent rebuilds with cached data
finish in under 30 seconds.

## Build options

```bash
python scripts/build.py configs/example/example.yaml                 # Full build (uses caches)
python scripts/build.py configs/example/example.yaml --refresh        # Re-fetch all remote data (OSM, basemap, terrain)
python scripts/build.py configs/example/example.yaml --refresh-trails # Re-fetch trail data from OSM
python scripts/build.py configs/example/example.yaml --refresh-pois   # Re-fetch POI data from OSM
python scripts/build.py configs/example/example.yaml --no-terrain     # Skip terrain tile generation
python scripts/build.py configs/example/example.yaml --no-basemap     # Skip basemap extraction
python scripts/build.py configs/example/example.yaml --dry-run        # Validate + print the plan, write nothing
```

Remote data never updates on its own. The build uses cached
responses regardless of age, so an unflagged rebuild is fully
offline and reproducible. Picking up OSM edits always takes an
explicit `--refresh` flag. Config edits still trigger the relevant
re-fetch automatically: changed relation IDs re-fetch trails, a
changed bbox re-extracts tiles.

- `--refresh` re-fetches all of this map's remote data: trails and
  POIs from Overpass, plus basemap and terrain tiles. It bypasses
  the cached Overpass responses. Other maps' shared-cache entries
  are untouched.
- `--refresh-trails` re-fetches trail data from Overpass. Useful when
  you want to refresh trail geometry or pick up an OSM edit.
  YAML-only changes never need it: per-route style overrides
  (`dashed_relations`, `relation_colors`, `winter_relations`,
  `summer_relations`, `custom_routes`, `event_mode.routes`,
  `event_mode.featured`, `event_mode.background_style`) flow
  through every build's enrichment pass automatically.
- `--refresh-pois` re-fetches OSM POI data (guideposts, toilets,
  drinking water, bicycle repair stations, attractions) from Overpass. YAML-only changes
  never need it: `parking:`, `trailheads:`, `event_mode.pois`,
  and the related color overrides flow through `fetch_pois.py` on
  every build automatically.
- `--force` and `--trails` are deprecated spellings of `--refresh`
  and `--refresh-trails`. They still work but print a note.
- `--no-terrain` and `--no-basemap` skip the corresponding tile
  extraction steps. Useful for faster rebuilds when only templates or
  config options have changed.
- `--dry-run` validates the config and prints what would be fetched and
  generated, then exits: no Overpass calls, no tile downloads, no file
  writes.
- `--output-dir DIR` and `--cache-dir DIR` redirect the build output and
  the data cache away from the repo-relative `build/<slug>/` and `cache/`
  defaults. The package form (`python -m map_generator build …`) forwards
  both unchanged.
- `--no-minify` and `--no-precompress` opt out of the default-on
  minification and `.gz`/`.zst` precompression for fast local iteration.
  Leave both on for deploys (see [Building unminified output](#building-unminified-output-for-local-debug)).
- `--quiet` suppresses step and progress output, leaving only notes,
  warnings, and errors.

The basemap extraction automatically detects the latest available
[Protomaps planet build](https://maps.protomaps.com/builds/), so
there's no URL to update by hand. To override this, set the
`PROTOMAPS_PLANET_URL` environment variable.

### Reviewing what an OSM refresh changed

A build re-fetches trail data on an explicit `--refresh` /
`--refresh-trails`, or automatically when the config's relation IDs or
bbox changed. Any such build compares the new data against the previous
snapshot and prints a summary:

```
OSM data diff vs previous snapshot
    routes added: 1
    trails renamed: 2
    tag changes: 4
    ways 128 → 130, total length 15.73 mi → 15.86 mi (+0.13 mi)
      ~ trail Easy Option (old name) → Easier Option (3 ways)
```

The build writes the full report to
`cache/osm_diff/<slug>/last-refresh.md`, overwriting it each refresh.
The report lists routes added, removed, renamed, recolored, or
reseasoned; trails added, removed, or renamed; per-way `mtb:scale:imba`
and `oneway` changes with `openstreetmap.org/way/<id>` links; and
per-trail length changes. The previous snapshot itself is kept beside
it as `trails.prev.geojson`.

Both live under the cache directory, never under `build/<slug>/`, so there is
no chance of an internal file reaching a deploy.

Two things the diff deliberately does not do:

- **It never diffs merged features.** `merge_consecutive_ways` fuses
  consecutive ways sharing a `(relation membership, name, mtb:scale:imba,
  resolved oneway)` signature, so a way that merely gains a rating moves
  between features. The diff keys on OSM way IDs, which survive the
  merge. A one-tag edit therefore reports as one tag change rather than
  wholesale churn.
- **It never compares vertices.** Contributors nudge geometry constantly.
  Length is the only geometry signal. Per-trail changes under 20 m are
  treated as noise, so real extensions and truncations aren't buried.

Nothing here needs a flag, and nothing changes when no re-fetch happens.
A first build has no previous snapshot and reports nothing.

### OSM data notes

Every build also audits the OSM data for genuine gaps and writes
`cache/osm_diff/<slug>/data-notes.md`. It prints only when it finds
something, so an unremarkable build stays quiet:

```
OSM data notes
    3 possible unconnected way pairs
    3 named trails with no difficulty rating
    3 relations with no colour
```

This is **not** a "make the render look better" checklist.
[Mapping for this framework](osm-mapping.md) is explicit that adding tags to
manipulate a renderer degrades the dataset for every other consumer.
Every check therefore has to stand on its own as a data problem. What
that rules out:

- **Unnamed ways are never flagged.** Connectors and spurs are legitimately
  nameless, and "name it so a label appears" is the anti-pattern itself.
- **Missing difficulty ratings are only reported on maps with *partial*
  coverage.** If nothing on the map is rated, nobody has tagged difficulty
  there. Listing every named trail would be asking for tags purely so this
  renderer has something to draw. (The Difficulty control auto-hides on such
  maps anyway.) An out-of-range value like `mtb:scale:imba=7` is always
  reported, because that's wrong on its own terms.
- **Parking, trailheads, and hubs are never checked for distance from a
  trail.** They are curator-placed, and being off-trail is the point of a
  parking lot. Only guideposts and emergency-access points, which are
  definitionally on the trail, get the distance check.
- **`oneway:bicycle` coverage is not checked**, although it would be useful.
  The snapshot stores the resolved `oneway` value, so which tag it came from
  isn't recoverable without changing what `fetch_trails` emits. Better to omit
  a check than imply coverage that isn't there.

The most valuable check is **possible unconnected ways**: two of a route's ways
ending within 10 m of each other without sharing a node, so they look joined
but aren't. That's what makes a loop fail to close for elevation. It also
breaks routing for every other consumer of the data. Each finding links to
the spot on openstreetmap.org. Ordinary branch junctions share a node exactly and
are excluded, so the check stays quiet on healthy data.

The audit reads the pre-enrichment snapshot, so custom routes (not OSM's to
fix) and the subway-style parallel-route expansion never reach it.

Flags can be combined: `--refresh-trails --no-basemap --no-terrain`
re-processes trail data and rebuilds templates without touching
tiles.

### Expected build times

- First-ever build of a new map: 5 to 10 min (downloads basemap,
  terrain, sprites).
- Re-build with cached data, no `--refresh`: under 30 seconds.
- Build with `show_route_elevation: true` and a fresh cache: extra
  ~30 sec to 2 min for USGS 3DEP API calls (one batch per route at
  25m sampling; auto-retries transient 502s).
- `--refresh` on a large map: 10 to 20 min.

If a build takes much longer, the slowest steps are usually terrain
extraction (Mapterhorn HTTP fetches over a wide bbox) and Overpass
(depends on relation size + Overpass server load).

## Local preview

A development server with HTTP Range request support lives at
`scripts/serve.py`:

```bash
python scripts/serve.py build/example
# Open http://localhost:8090 (change the port with --port/-p)
```

This is the fastest way to test changes without a production deploy.
The server honors Range requests properly so PMTiles work end-to-end.

## Convenience wrapper: build_and_deploy.sh

`tools/build_and_deploy.sh` is a convenience wrapper for the common
SSH/rsync deploy workflow. It validates every config first. It then
builds via `scripts/build.py` and `rsync`s each map to a remote
host. It is one of several ways to deploy. If you deploy via
a different mechanism (S3, Netlify, GitHub Pages, manual upload),
run `python scripts/build.py <config>` directly and ship the
resulting `build/<slug>/` tree. The output is production-quality by
default, with no flag needed: minified `app.js` / `style.css`, a
content-hashed service worker, and a trimmed font set. See
[`deployment.md`](deployment.md) for recipes targeting other
static hosts.

Run `./tools/build_and_deploy.sh --help` for full usage. Common
patterns:

```bash
# Build and deploy every map under configs/ (excluding configs/reference/)
./tools/build_and_deploy.sh

# Build and deploy a subset
./tools/build_and_deploy.sh example northpark

# Build but skip deploy
./tools/build_and_deploy.sh --build-only example

# Re-fetch all data and rebuild
./tools/build_and_deploy.sh --refresh example

# Pass extra flags through to build.py
./tools/build_and_deploy.sh example -- --no-basemap --no-terrain
```

The script reads the deploy destination from the
`TRAILMAPS_DEPLOY_DEST` environment variable. Set it in your shell rc
once:

```bash
# in ~/.zshrc or ~/.bashrc
export TRAILMAPS_DEPLOY_DEST=user@host:/var/www/your-maps
```

Day-to-day runs then need no extra flags. Override per run with
`--dest <ssh-path>`. If neither the env var nor `--dest` is set, the
script errors out with a clear hint rather than shipping to a wrong
or empty target.

See [`tools/README.md`](../tools/README.md) for the full option table.

### Building unminified output for local debug

The default build path produces minified `app.js` and `style.css`
(faster page loads, smaller cache footprint). If you need readable
output for in-browser debugging:

```bash
# Direct: produces unminified output in build/<slug>/
python scripts/build.py configs/<slug>/<slug>.yaml --no-minify

# Through the wrapper (rare; usually you'd call build.py directly):
./tools/build_and_deploy.sh --build-only <slug> -- --no-minify
```

The original `app.js` / `style.css` sources under `templates/`
are unminified in the repo, so most debug work is on those rather
than on the build output anyway.

### Template lint (contributors only)

The runtime templates (`templates/app.js`, `templates/sw.js`) are
plain JavaScript with no build step, so nothing ever compiles them.
A reference to an identifier that doesn't exist parses fine, ships
silently, and throws at runtime. (Say, a refactor removed a helper
another code path still calls.) One runtime exception can take out
the whole app boot. An ESLint pass with only the `no-undef` rule
enabled catches that class of mistake statically.

This is optional dev tooling for people editing the templates. It is
**never required to build maps**, and the Python test suite passes
without it.

```bash
# One-time setup (either package manager works)
corepack pnpm install    # or: npm install

# Run it
corepack pnpm lint       # or: npm run lint
```

Once installed, it also runs automatically as part of
`python -m pytest scripts/tests/` (via `test_eslint.py`). On machines
without Node.js or without the install step, that test skips cleanly.
The rule set is deliberately minimal (`no-undef` only, configured in
`eslint.config.mjs`), so it never argues about style. `CONFIG` and
`SW_CONFIG` are declared there as known globals because the build
injects them into the templates at build time.

## Validate a config without building

```bash
./tools/build_and_deploy.sh --validate-only          # all configs
./tools/build_and_deploy.sh --validate-only example    # just one
```

Or invoke the validator directly:

```bash
python scripts/validate_config.py configs/example/example.yaml
```

Validation is fast and catches every YAML / value error in one pass
before any expensive fetch or build work starts. The validator
checks:

- Top-level key spelling against `KNOWN_KEYS`, and nested-dict keys
  against each block's allowed set (both suggest close matches for
  typos).
- Value types and allowed enums (`default_labels`, `color_by`,
  `distance_units`, `default_color_scheme`, `reverse_days` tokens,
  etc.).
- Asset file existence (`logo:`, `icon:`, `osm_file:`,
  `custom_routes[].geometry`).
- Custom-route bucket sanity (at least one of summer / winter /
  emergency must be true; ID must not collide with any OSM relation
  ID in the config).
- Slug format: must match `[a-z0-9_-]+`.

## Re-aligning a production config: clean_config.py

Production configs accumulate cruft over time as they're maintained
by hand: keys reordered, comments edited, sections renamed, drift
from the template's structure. `tools/clean_config.py` produces a
sibling `<input>-cleaned.yaml` that adopts the canonical template's
structure (section dividers, key ordering, default-value
documentation comments). It preserves every value the production
file explicitly set.

```bash
# Default template (configs/reference/reference-minimal.yaml)
python tools/clean_config.py configs/example/example.yaml

# Custom template (e.g. the verbose annotated reference)
python tools/clean_config.py configs/foo/foo.yaml \
    --template configs/reference/reference.yaml
```

The original file is never modified. Review the cleaned output and
swap it in manually when satisfied. See
[`tools/README.md`](../tools/README.md) for behavior and
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

- **`--refresh`** re-fetches all of this map's remote data. Trail
  and POI queries bypass their cached Overpass responses. Other
  maps' shared-cache entries are untouched. The build also
  re-extracts basemap and terrain tiles.
- **`--refresh-trails`** re-fetches just the trail data from
  Overpass.
- **`--refresh-pois`** re-fetches just the OSM POI data from
  Overpass.

To refresh trail data without touching tiles:

```bash
python scripts/build.py configs/example/example.yaml --refresh-trails --no-basemap --no-terrain
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

Add `osm_file` to your config. The path resolves relative to the
config's directory. A bare filename like `osm.osm` picks up the
file sitting next to the YAML:

```yaml
# configs/mytrails/mytrails.yaml
osm_file: osm.osm              # resolves to configs/mytrails/osm.osm
relations: [12345678]          # still required
```

The `.osm` file must contain every entry in `relations`, all of their
child relations (when any entry is a super-relation), every member
way, and all referenced nodes with coordinates. This is the default
when saving from JOSM.

All other config options (`clipped_relations`, `winter_relations`,
`summer_relations`, `emergency_access_relations`, `dashed_relations`,
etc.) work the same way: they reference IDs found in the file instead
of Overpass.

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

You can open and edit the resulting file in JOSM, then use it
directly with the build pipeline.

You can preview what the parser finds without building:

```bash
python scripts/osm_parser.py configs/mytrails/osm.osm 12345678
```

## Vendor bundling

The build downloads all JavaScript and CSS dependencies (MapLibre GL
JS, PMTiles, Protomaps basemaps) from their CDNs and bundles them
into `vendor/` in the output directory. The generated map has
**no runtime CDN dependency**. Everything is served from your own
server. This ensures the map continues to work even if upstream CDNs
go offline or change.

Vendor libraries are bundled regardless of the `pwa` setting.

## Font trimming

The Protomaps basemap assets include fonts covering every world
script (Latin, CJK, Devanagari, etc.) across 256 Unicode range files
per font face. That is roughly 20 MB total. Most maps only need a
small subset of these.

The build pipeline trims fonts automatically. It scans the basemap
tiles, trail data, and POI data for every text character that
actually appears. It then copies only the PBF glyph ranges containing
those characters. Script-specific font faces (e.g. Devanagari) are
included only when the map data contains characters from that
script.

This is fully data-driven: a US trail map gets only Latin ranges,
while a map in Japan would automatically include CJK ranges. No
configuration is needed.

You can preview font trimming results without building:

```bash
python scripts/font_trimmer.py build/example/
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
    example.yaml        A worked example map (build it as a smoke test)
    example_logo.png    Example logo and icon assets
  reference/
    reference-minimal.yaml  Bare template; copy this to start a new map
    reference.yaml          Annotated reference, same key order

scripts/            (abridged; supporting modules not listed)
  build.py            Build orchestrator
  fetch_trails.py     OSM trail data via Overpass API or local .osm file
  fetch_pois.py       Trail markers (guideposts + emergency-access points, merged), features, toilets, drinking water, bicycle repair stations
  osm_parser.py       Parser for local .osm XML files
  fetch_basemap.py    Protomaps basemap PMTiles extraction
  fetch_terrain.py    Mapterhorn terrain PMTiles extraction
  generate_icons.py   Icon generation from source image (Pillow)
  font_trimmer.py     Automatic font subsetting based on map data
  validate_config.py  Pre-flight YAML validation
  serve.py            Dev server with Range request support
  compute_route_stats.py     Per-route distance + USGS 3DEP elevation
  osm_diff.py         Diff a trail re-fetch against the previous snapshot
  tagging_report.py   OSM data-quality notes (gaps, not style preferences)

templates/
  index.html          Map viewer page
  app.js              MapLibre GL JS application
  style.css           Theme styles (light + dark via [data-color-scheme])
  sw.js               Service worker template for offline / PWA support

assets/
  fonts/              Protomaps basemap fonts (PBF glyph ranges, auto-trimmed at build time)
  sprites/            Protomaps basemap sprites (PNG + JSON, all flavors)

build/<slug>/         Generated output (deployable static site)
cache/                Cached Overpass API responses
  osm_diff/<slug>/    Previous trail snapshot, refresh diff, OSM data notes

tools/
  build_and_deploy.sh Convenience wrapper: validate then build then optional rsync deploy
  clean_config.py     Re-align a production YAML against the canonical template
  list_relations.py   Diagnostic: list the OSM relations a map is built from
  README.md           Tool documentation

docs/                 Documentation (this folder)
```
