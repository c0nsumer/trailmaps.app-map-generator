# MTB Map Framework

A reusable framework for generating self-hosted, static trail maps from OpenStreetMap data. No external tile services or API keys needed at runtime.

## How It Works

Write a YAML config file describing your trail system. The build pipeline fetches trail data from OpenStreetMap, extracts regional basemap and terrain tiles, and produces a complete static site you can deploy anywhere.

**Stack:** MapLibre GL JS, Protomaps basemap, PMTiles, Overpass API

### Terminology

This framework uses two terms with specific meanings:

- **Trail** — an individual OSM way: one continuous line segment a rider follows on the ground.
- **Route** — a named grouping of one or more trails, typically a loop, a signed route, or a cross-country path. Routes come from OSM relations (the standard case) or from a [custom routes](#custom-routes) config block (for race courses, event loops, or anything OSM won't accept).

YAML config keys like `extra_relations`, `dashed_relations`, `relation_colors`, `winter_relations`, `summer_relations`, `emergency_access_relations`, `clipped_relations`, and `root_relation_id` all take OSM relation IDs as input, so they keep "relation" in the name. Internally and in the UI, those are the framework's "routes."

## Features

### Self-hosted and offline-capable

- **Fully self-hosted** — all tiles, fonts, sprites, and vendor libraries served from your own infrastructure; zero runtime CDN or API dependencies
- **Locally bundled libraries** — MapLibre GL JS, PMTiles, and Protomaps basemap JS/CSS are downloaded from CDNs at build time and bundled into the output; required for PWA offline support and eliminates external runtime dependencies
- **Progressive Web App** — installable PWA with service worker for complete offline use after first visit; PMTiles range requests served from cache; install row shown in the bottom sheet on supported browsers, with an iOS Add-to-Home-Screen hint for Safari
- **Config-driven** — create a new map by writing a single YAML file; no code changes needed
- **Static output** — builds to a plain directory of files deployable to any static server supporting HTTP Range requests (Caddy, nginx, Apache)

### Map display

- **Auto-fit zoom** — initial view automatically fits the trail bounding box to any screen size
- **Parallel line offsets** — shared trail segments rendered as parallel coloured lines (tube-map style) with smooth Chaikin corner-cutting; offsets recalculate dynamically when trails are hidden
- **Terrain hillshade** — optional Mapterhorn terrain tiles with client-side hillshade rendering
- **Custom base layers** — add external raster tile layers (satellite, aerial, topo) with optional authentication headers; trail overlays and interactive features work on top. When no custom layers are configured, the basemap selector is hidden entirely
- **Light theme** — single light theme tuned for outdoor readability; no dark-mode variant to maintain

### Bottom-sheet UI

A single bottom sheet is the home for all controls. Two states:

- **Peek** (always visible): map title plus a horizontal row of labelled icon toggles — Season (Summer ↔ Winter), Parking, Trailheads, Features, Difficulty, Markers, and Locate. Buttons for categories the map doesn't carry are auto-hidden so each map only shows what it has.
- **Expanded** (~70% of viewport): a combined routes-and-trails finder, map-options (Labels, Emergency Access Routes when relevant, Basemap picker when configured), an Install-as-app row (when applicable), and the About button.

Drag the handle up/down, tap the peek area, swipe down to close, or press Escape on desktop. Safe-area insets keep the sheet clear of the iOS home bar.

### Trail data and styling

- **Non-exclusive bucket model** — every route carries three independent flags (`summer`, `winter`, `emergency`); Summer and Winter are a mode switch (one at a time), Emergency is an additive overlay. A route can sit in any combination of the three buckets — see [Route Buckets](#route-buckets)
- **Combined routes + trails finder** — a search input plus a sectioned list (Routes above, Trails below) that always mirrors what's currently visible on the map; tapping a row highlights it without hiding anything else
- **Highlight layers** — four overlay layers render a glow+stroke emphasis when a route or trail is picked. Route highlights use the route's own colour; trail highlights use amber so they read consistently across differently-coloured parent routes
- **Trail colouring** — colour by route (OSM `colour` tag per relation, overridable via `relation_colors`) or by IMBA difficulty rating (`mtb:scale:imba` per way)
- **Trail difficulty symbols** — optional IMBA difficulty rating symbols (`mtb:scale:imba` 0–5) rendered along trail segments with a toggleable checkbox. Defaults to **on** when the build generates the sprite; state persists in localStorage
- **Direction arrows** — arrows along ways tagged `oneway=yes`/`oneway=-1`/`oneway=reversible`; optional day-of-week direction schedule via `default_direction_schedule` (system-wide) or `direction_schedules` (per-relation overrides), required for `reversible` ways. Always rendered (no toggle) and tuned small enough to read as a subtle cue rather than a dominant visual
- **Dashed line styles** — config-driven dash patterns per route (dots, dashes, etc.) with round or square caps and optional two-colour alternating dashes
- **Colour overrides** — override OSM `colour` tag per route from config
- **Trail labels** — switchable between route names, individual trail names, or off
- **Clipped routes** — include long-distance routes (rail trails, XC ski trails) clipped to the map's bounding box, with continuation arrowheads at every cut endpoint pointing in the direction the route keeps going off-map
- **Custom (non-OSM) routes** — declare race courses, event loops, or any routes OSM won't accept inline in the config with a local GeoJSON file reference; they participate in buckets, the finder, and highlights just like OSM routes — see [Custom routes](#custom-routes)

### Points of interest

- **Trail markers** — toggleable merged layer covering OSM guideposts (`tourism=information` + `information=guidepost`) and emergency-access points (`highway=emergency_access_point`). Both share one chip style so they read as a single "trail marker" concept; when a node carries neither `ref` nor `name`, the chip falls back to `#`. Fill, text, and outer halo colours are independently configurable.
- **Trailheads** — YAML-defined trailhead markers with popup and driving directions link; fill, text, and halo colours configurable.
- **Parking** — YAML-defined parking markers with popup and platform-aware driving directions (Apple Maps in Safari, Google Maps elsewhere); fill, text, and halo colours configurable.
- **Features** — toggleable feature markers (OSM `tourism=attraction` nodes) rendered as a coloured dot inside a white ring; both dot colour and ring colour configurable.
- **Proximity filtering** — trail-marker and feature markers are filtered by distance (10 m) to trail geometry, so a node off-trail but near a road won't clutter the map.

### Build pipeline

- **Automatic icon generation** — all favicon and PWA icon variants generated from a single square source image (see [Logo and Icon Assets](#logo-and-icon-assets))
- **Font trimming** — Protomaps font ranges automatically subset to only the Unicode ranges present in the map data; a US trail map ships only Latin glyphs
- **Parallel Overpass queries** — all public Overpass API servers queried simultaneously; the first response wins, with automatic retry and timeout escalation
- **Local .osm support** — build from a local JOSM-exported `.osm` file instead of the Overpass API for offline or pre-upload testing
- **Basemap path/POI suppression** — optionally hide path, track, and POI labels from the Protomaps basemap to avoid conflicts with trail casings

### Mobile and UX

- **Mobile-first** — bottom sheet thumb-reachable on phones, roomier on desktop; tap targets ≥ 44 px; safe-area insets on iOS/Android
- **Geolocation** — locate-me button for on-trail use; off-screen indicator and toast when the device is outside the map area
- **Per-origin state persistence** — season mode, Emergency toggle, POI toggles, Labels, and Difficulty state all persist in localStorage under `mtb.*` keys (see [Privacy](#privacy))
- **About This Map modal** — button at the bottom of the expanded sheet opens a dialog with a configurable description, links, author info, and auto-generated data/build version info and credits

## Quick Start

### Prerequisites

- Python 3.9 or newer (tested through 3.14) with `pip install -r requirements.txt` (versions are pinned for reproducible builds; see the file header for upgrade notes)
- [`pmtiles`](https://github.com/protomaps/go-pmtiles) CLI (`go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest`)
- Self-hosted [Protomaps basemap assets](https://github.com/protomaps/basemaps-assets/releases) (fonts + sprites) in `assets/`
- Optional: [`potrace`](http://potrace.sourceforge.net/) for Safari pinned tab SVG icon generation
  - macOS: `brew install potrace`
  - Linux: `apt install potrace`
  - Windows: `choco install potrace` or download from [potrace.sourceforge.net](http://potrace.sourceforge.net/#downloading)
  - If not installed, the build skips `safari-pinned-tab.svg` with a warning — everything else works normally

### Build a Map

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

### Deploy

Copy the `build/<slug>/` directory to any static file server. The server must support HTTP Range requests for PMTiles (Caddy, nginx, Apache all do). No special CORS or rewrite rules are needed — a standard `file_server` config is sufficient.

Recommended Caddy config serving maps at `mytrailmaps.com`:

```caddyfile
mytrailmaps.com {
    root * /var/www/mytrailmaps.com
    encode zstd gzip
    file_server

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    }

    # Entry point + service worker — always revalidate so users pick up new
    # builds promptly. `/` and `/index.html` are listed separately because
    # Caddy's `path` matcher is exact-match: a request to the bare root has
    # URI path `/` (file_server resolves it to index.html only internally),
    # and an explicit /index.html link bypasses that. Without an explicit
    # Cache-Control on HTML, browsers fall back to heuristic caching (~10%
    # of the file's age), which is unpredictable and almost always wrong
    # for a PWA's entry point. Browsers already cap sw.js at 24h per spec,
    # but explicit no-cache keeps behavior consistent across browsers and
    # ensures every load checks for a new service worker.

    @nocache path / /index.html /sw.js
    header @nocache Cache-Control "no-cache"

    # Cache static assets for 1 day — long enough for a trail ride, short
    # enough that rebuilds propagate quickly. app.js and style.css change
    # every build but keep the same filename (no content hash), so a user
    # on a stale cache won't see new code until the cache entry expires or
    # the service worker swaps assets.

    @immutable path *.pmtiles *.pbf *.js *.css *.png *.webp *.ico *.svg *.json

    # Pick one of these two:

    # Production use: one-day cache.
    header @immutable Cache-Control "public, max-age=86400"

    # Development / map testing: no caching.
    # header @immutable Cache-Control "no-store"

    handle_errors {
        respond "{err.status_code} {err.status_text}"
    }

    log {
        output file /var/log/caddy/access.log
    }
}
```

## Configuration

Each map lives in its own folder under `configs/`: `configs/<slug>/<slug>.yaml` plus all of its asset files (logo, icon, optional offline OSM snapshot, optional custom-route GeoJSONs). Copy the whole folder to start a new map — everything the framework needs for that map is self-contained.

Two reference templates ship together under `configs/example/`:

- **`configs/example/example-minimal.yaml`** — every supported key listed in canonical order, commented out when the value matches the house default. Copy this as the starting point for a new map, then uncomment only the lines you need to customise.
- **`configs/example/example.yaml`** — same layout, but every key is uncommented with an inline comment explaining what it does and what the default is. Reference it to understand what a knob does without re-reading this page.

Each real map config follows the same canonical order — scan any of them (e.g. `configs/ramba/ramba.yaml`, `configs/dte/dte.yaml`) to see the paradigm in practice. Non-default lines are uncommented; defaults stay commented as placeholders. Descriptive comments live in `example.yaml`, not in per-map configs.

Minimal map config:

```yaml
name: My Trails
slug: mytrails
title: "My Trails Map"
root_relation_id: 12425503
```

### Config Reference

#### Identity

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | Yes | — | Short name used in build logs and as the PWA icon label on mobile home screens |
| `slug` | Yes | — | URL-safe identifier. Used for the map's config folder (`configs/<slug>/`), the build output directory (`build/<slug>/`), and the deploy destination subdirectory. Must match [a-z0-9_-]+ and match the folder name holding the YAML. |
| `title` | Yes | — | Full page title shown in browser tab and PWA install dialogs |

#### Data Sources

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `root_relation_id` | Yes | — | OSM relation ID anchoring this map. Either a super-relation (its child relations become routes) or a single route relation (used directly as the only route — for small parks with no super-relation wrapper). |
| `osm_file` | No | — | Path to local `.osm` XML file; when set, uses this instead of the Overpass API |
| `extra_relations` | No | `[]` | Additional OSM relation IDs to include that aren't children of the root super-relation |
| `clipped_relations` | No | `[]` | OSM relation IDs to include but clip to the core trail bounding box (e.g., rail trails) |

#### Route Buckets

See [Route Buckets](#route-buckets) for how Summer / Winter / Emergency flags are computed from these lists plus OSM tags.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `winter_relations` | No | `[]` | Relation IDs to flag `winter=true`. Use for winter-only routes not already tagged `seasonal=winter` in OSM (e.g., snowshoe relations). Being in this list removes the route from Summer unless also in `summer_relations`. |
| `summer_relations` | No | `[]` | Relation IDs to flag `summer=true`. Use to re-add a route to Summer that would otherwise be pulled out of it (OSM `seasonal=winter` year-round routes like a Snow Bike Route, emergency routes also used year-round). Overlap with `winter_relations` is how you express a route that lives in both buckets. |
| `emergency_access_relations` | No | `[]` | Relation IDs to flag `emergency=true`. Rendered only when the Emergency Access toggle is on, regardless of season mode. |

#### Custom Routes

See [Custom routes](#custom-routes) for details.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `custom_routes` | No | `[]` | List of user-defined non-OSM routes with inline metadata (id, name, colour, bucket flags) and a GeoJSON geometry file reference |

#### Style Overrides

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `color_by` | No | `"relation"` | How trail lines are coloured: `"relation"` (by route colour from OSM `colour` tag, optionally overridden via `relation_colors`) or `"difficulty"` (by per-trail IMBA `mtb:scale:imba` rating, using the fixed IMBA palette — `relation_colors` does not apply to trail polylines in this mode) |
| `default_trail_color` | No | `"#808080"` | Fallback trail colour. In `relation` mode: used when a relation has no OSM `colour` tag. In `difficulty` mode: used for segments with no `mtb:scale:imba` tag. Accepts a CSS colour string or an object with `color`, `pattern` (dash array), and `cap` (`"round"` or `"square"`) for dashed uncoloured trails |
| `dashed_relations` | No | `{}` | Map of relation ID to dash config (see [Dash Patterns](#dash-patterns)) |
| `relation_colors` | No | `{}` | Map of relation ID to CSS colour (hex, named, `rgb()`, `rgba()`, `hsl()`); overrides OSM `colour` tag. Only takes effect on trail polylines when `color_by: relation` (the default); under `color_by: difficulty` the override is used only for the route swatch in the finder. |
| `marker_color` | No | `"#795548"` | Trail-marker chip fill (merged guideposts + emergency access) |
| `marker_text_color` | No | `"white"` | Trail-marker glyph (`ref` / `name` / fallback `#`) colour |
| `marker_border_color` | No | `"white"` | Trail-marker outer halo border colour |
| `parking_color` | No | `"#2980b9"` | Parking chip fill |
| `parking_text_color` | No | `"white"` | Parking glyph (`P`) colour |
| `parking_border_color` | No | `"white"` | Parking outer halo border colour |
| `trailhead_color` | No | `"#27ae60"` | Trailhead chip fill |
| `trailhead_text_color` | No | `"white"` | Trailhead glyph (`TH`) colour |
| `trailhead_border_color` | No | `"white"` | Trailhead outer halo border colour |
| `feature_color` | No | `"#8e44ad"` | Feature marker inner dot colour |
| `feature_ring_color` | No | `"#ffffff"` | Feature marker outer ring colour |

Each POI type follows the same three-knob pattern (fill + glyph/dot + halo/ring). Values flow to CSS custom properties on `:root` so the peek-bar swatch, the on-map marker, and any popup badge all read the same hex — one source of truth per colour.

#### Map View

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `bbox` | No | auto | Bounding box `[west, south, east, north]` used for the **initial view fit**; auto-computed from trail geometry with a ~3% proportional buffer if omitted |
| `pan_padding` | No | `0.5` | How much looser the pan wall is than `bbox`, as a fraction of the bbox's greater dimension added on each side. `0.5` ≈ 4× the pannable area; `0` pins the pan wall to `bbox` (the pre-knob behaviour). Also expands the basemap + terrain PMTiles extraction footprint so tiles fill the whole envelope |
| `pan_bbox` | No | computed | Explicit pan envelope `[west, south, east, north]`; overrides `pan_padding` when set. Usually unnecessary — use `pan_padding` unless the auto-symmetric expansion is wrong for your site (e.g. you want asymmetric pan room to cover a parking lot north of the trails but nothing south) |
| `center` | No | auto | Map centre `[lon, lat]`; auto-computed from bbox midpoint if omitted |
| `zoom` | No | `14` | Initial zoom level |
| `min_zoom` | No | `10` | Minimum zoom level |
| `max_zoom` | No | `18` | Maximum zoom level |
| `basemap_maxzoom` | No | `15` | Max zoom for basemap tile extraction |
| `terrain_maxzoom` | No | `13` | Max zoom for terrain tile extraction |

**`bbox` vs. `pan_bbox`.** These have distinct jobs. `bbox` frames the trails on first paint — tight feels intentional. `pan_bbox` (derived from `bbox` + `pan_padding`, or set explicitly) is the MapLibre `maxBounds`: the wall the user hits when panning. Because MapLibre's `maxBounds` clamps the *map centre* (not the viewport edge), a mobile user zoomed in will only see roughly half a viewport past a tight bbox, which feels cramped. A `pan_padding` of `0.5` quadruples the pannable area and expands basemap/terrain extraction to match, so panning to the edge still shows real map.

**Tuning `pan_padding`.** The default (`0.5`) is aimed at a mobile user zoomed into a trail detail who wants to see the surrounding roads and landmarks. If that still feels tight after real-world testing, bump it per-map — `1.0` gives roughly 2× the pan room (~9× area), useful for systems where the access road or parking lot you drove in on lives well outside the trail footprint. If empty basemap around the trails feels distracting, or PMTiles size matters more than pan room, drop to `0.25` or lower. Setting `0` pins the pan wall to `bbox` and restores the pre-knob tight-pan behaviour. Each step up also inflates the basemap + terrain PMTiles since extraction expands to match — a `0.5` default adds ~30% to the build size on a compact system, `1.0` roughly doubles it. For per-side asymmetry (more room north than south, say), skip `pan_padding` and set `pan_bbox` directly.

#### Direction Schedules

See [Direction Arrows](#direction-arrows) for the full model.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `default_direction_schedule` | No | `{}` | System-wide direction schedule applied to every route: `{reverse_days: [...]}`. |
| `direction_schedules` | No | `{}` | Per-relation overrides keyed by relation ID → `{reverse_days: [...]}`. An entry with an empty `reverse_days` opts that one route out of the default. **Required** (via either key) for any way tagged `oneway=reversible` to render. |

#### PWA

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `pwa` | No | `true` | Enable PWA support (service worker, offline caching, install row). When false, no service worker or install UI is generated. Vendor libraries are always bundled locally regardless of this setting |
| `pwa_install_prompt` | No | `false` | When `true`, surface PWA install affordances on platforms that support them. On Chrome/Android: the page does **not** call `preventDefault()` on `beforeinstallprompt` so Chrome's native mini-infobar appears, AND the custom Install button (in the expanded sheet, above About) is visible alongside it as a persistent fallback for second-visit installs. On iOS Safari: the Install button opens manual Add-to-Home-Screen instructions. When `false` (the default), no `beforeinstallprompt` handler is registered at all — silences Chrome's `"Banner not shown: beforeinstallpromptevent.preventDefault() called"` console warning — and the custom Install button is hidden everywhere. Use `false` for maps that don't want install promotion (e.g. personal/family maps); use `true` for production/community maps where install is encouraged. Requires `pwa: true`. |

#### Build-time Data Gates

These gate **data fetching and build-time asset generation**, not UI visibility. They exist so a map that doesn't need a given data type can skip the Overpass query or sprite generation entirely. The corresponding UI toggle is hidden automatically when the underlying data or sprite is absent.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `show_markers` | No | `true` | When false, skips the Overpass query for trail markers (guideposts + emergency-access points, merged into one category). Hides the peek-bar Markers toggle too. |
| `show_parking` | No | `true` | When false, parking markers from the config are not rendered |
| `show_trailheads` | No | `true` | When false, trailhead markers from the config are not rendered |
| `show_features` | No | `true` | When false, skips the Overpass query for `tourism=attraction` feature nodes |
| `poi_proximity_m` | No | `50` | Distance in meters from the nearest visible trail within which a feature or trail-marker POI is allowed to render. Tight values (~10 m) hide POIs that aren't directly on the trail; loose values (~75 m+) include nearby attractions but risk surfacing bbox-incidental POIs. The peek-bar Features toggle auto-hides if no feature POI passes this distance check. |
| `show_terrain` | No | `true` | When false, terrain tiles are not fetched and the hillshade layer is omitted |
| `show_difficulty` | No | `false` | When true, generates the IMBA difficulty sprite and enables the in-UI toggle; when false, no sprite is generated and no difficulty symbols appear. The toggle defaults **on** when enabled and persists state via localStorage. |
| `show_routes` | No | `true` | When false, hides the Routes section of the Finder and removes "Routes" from the Labels dropdown. Useful for maps that have no curated route relations. |
| `show_trails` | No | `true` | When false, hides the Trails section of the Finder and removes "Trails" from the Labels dropdown. Useful for systems where routes and trails overlap so heavily that listing trails adds noise (e.g. DTE). If both `show_routes` and `show_trails` are false, the whole Finder section disappears and the Labels row is hidden. |
| `suppress_path_labels` | No | `false` | Hide path/track/footway labels from the Protomaps basemap (does not affect custom base layers) |
| `suppress_basemap_pois` | No | `false` | Hide POI labels (tourism, attractions, viewpoints) from the Protomaps basemap (does not affect custom base layers) |
| `show_route_distance` | No | `false` | When true, computes per-route distance at build time (sum of haversine segment lengths) and surfaces it in the Finder rows + highlight chip. Cheap; no data dependency. Display units are governed by `distance_units` (Display section). |
| `show_route_elevation` | No | `false` | When true, samples elevation along each route via opentopodata.org's free SRTM30m endpoint at build time and computes both positive elevation gain (climb) and absolute negative gain (loss/descent) in one pass — surfaces in the Finder + highlight chip as `↑gain / ↓loss`. The pair lets loops read as balanced (gain ≈ loss) and asymmetric/one-way routes communicate their direction through the difference, without us having to claim we know intended traversal direction. Per-route results are cached in `cache/route_stats/`. On HTTP 429 rate limits, the build automatically retries with backoff (60/90/120 s) before giving up; if all retries are exhausted, missing routes fall back to no-data and fill in on the next build via cache. Display units governed by `distance_units`. |

#### Display

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `default_labels` | No | `"routes"` | Initial label mode: `"routes"` (route names), `"trails"` (trail names), or `"none"`. The in-UI select reflects `show_routes` / `show_trails`; options for hidden categories are removed. |
| `map_dim_on_highlight` | No | `true` | When a route or trail is highlighted (via Finder tap or in-map click), dim every non-highlighted route/trail. Set `false` to keep the rest of the network at full saturation. |
| `url_hash` | No | `false` | When `true`, write `#zoom/lat/lon` to the URL hash as the user pans/zooms, and honour any hash on page load — enables shareable deep-links and reload-preserved position. Default `false` drops the hash entirely: return visitors always open framed on `bbox`, URLs stay clean, and the last-viewed location doesn't leak via address bar / screenshots / screen-shares. See [Privacy](#privacy) for the trade-off. |
| `distance_units` | No | `"mi"` | Units used for **all** distance and elevation values shown in the app — off-screen-indicator distance pill, route stats in the Finder (gated by `show_route_distance` / `show_route_elevation`), highlight chip, any future distance display. The underlying data is always meters; this only affects render-time formatting. `"mi"` → ft + decimal mi (and ft for elevation gain). `"km"` → m + decimal km (and m for elevation gain). The mixed elevation convention (ft for `mi`, m for `km`) matches what riders in each unit system actually expect. |
| `share_button` | No | `true` | Show the **Share this view** button in the expanded sheet (above the Install button). Tapping it captures the current view (zoom + center) and any highlighted route/trail as a deep-link URL, then surfaces it via the Web Share API (mobile native share sheet) or `navigator.clipboard.writeText()` fallback (desktop). Recipient opens the URL → the runtime restores the view + highlight and strips the hash. The shared URL works regardless of `url_hash` — Share is a deliberate, consensual one-shot, not ambient URL writing. Set `false` to remove the button entirely (the section is stripped from `index.html` at build time) for private/family maps where the affordance is unwanted. Open Graph + Twitter Card meta tags are always emitted (no separate gate) so the share preview cards render polished on Android. |

#### Base Layers

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `base_layers` | No | `[]` | Additional raster base layers. When empty, the basemap selector is hidden in the UI; when populated, the selector appears with "Default" (Protomaps light) and each configured layer. See [Base Layers](#base-layers). |

#### Branding

See [Asset layout convention](#asset-layout-convention) for where these files live.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `logo` | No | — | Path (repo-root-relative) to logo image (any web format: PNG, WebP, JPEG). Resampled at build time to fit a 200×80 px box (map overlay) and a 140×56 px box (About modal). If omitted, the `icon:` source is used as the logo automatically. |
| `icon` | No | — | Path (repo-root-relative) to square source image (PNG/WebP, ≥256 px) for automatic icon generation. Also used as the logo source if `logo:` is omitted. |

#### User-supplied Points

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `trailheads` | No | `[]` | List of trailhead locations (see [Trailhead Entries](#trailhead-entries)) |
| `parking` | No | `[]` | List of parking locations (see [Parking Entries](#parking-entries)) |

#### About This Map

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `about` | No | none | Object with optional `description`, `more_information`, `author`, `extra_links` keys (see [About This Map](#about-this-map)) |

#### Output

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `output_dir` | No | `build/<slug>` | Custom output directory path |

### Trailhead Entries

Trailheads are shown as green "TH" markers by default (configurable via `trailhead_color`, `trailhead_text_color`, `trailhead_border_color`). Each entry supports:

| Key | Required | Description |
|-----|----------|-------------|
| `name` | Yes | Display name shown in popup |
| `coordinates` | Yes | `[longitude, latitude]` |
| `directions_url` | No | Custom directions URL; if omitted, auto-generates based on browser |

**Tip:** If a trailhead has parking, use a single trailhead entry with a `directions_url` rather than adding both a trailhead and a parking entry at the same location. Use separate parking entries only for lots that aren't at a trailhead (e.g., overflow parking down the road).

### Parking Entries

Parking areas are shown as blue "P" markers by default (configurable via `parking_color`, `parking_text_color`, `parking_border_color`). Each entry supports:

| Key | Required | Description |
|-----|----------|-------------|
| `name` | Yes | Display name shown in popup |
| `coordinates` | Yes | `[longitude, latitude]` |
| `directions_url` | No | Custom directions URL; if omitted, auto-generates a link based on browser (see below) |

When `directions_url` is omitted, the app auto-detects the browser and generates the appropriate link:

| Browser | UA identifier | Result |
|---|---|---|
| Safari (any platform) | `Safari` | Apple Maps |
| Chrome on iOS | `CriOS` | Google Maps |
| Firefox on iOS | `FxiOS` | Google Maps |
| Chrome on desktop | `Chrome` | Google Maps |
| Edge | `Edg` | Google Maps |
| Firefox on desktop | `Firefox` | Google Maps |
| Opera | `OPR` | Google Maps |

### About This Map

The bottom sheet's expanded state includes an **About this map** button (below the Install row when installable). Clicking it opens a modal with information about the map. The modal header shows the map **title** on the left and, when `logo:` (or `icon:` as fallback) is configured, the brand **logo** on the right. The `about` YAML block is optional; when omitted, the modal still renders the always-on version and credits sections.

```yaml
about:
  description: |
    Free-form multi-line description. Line breaks in a YAML `|` block are
    preserved in the modal.
  more_information:
    - label: "Trail Association"
      url: "https://example.org"
    - label: "Trail Conditions"
      url: "https://example.org/conditions"
  author:
    name: "Your Name"
    url: "https://yoursite.example.com"
  extra_links:
    - label: "Source on GitHub"
      url: "https://github.com/you/your-fork"
```

| Key | Required | Description |
|-----|----------|-------------|
| `description` | No | Paragraph of prose shown near the top of the modal. `\|`-style multi-line blocks preserve newlines. |
| `more_information` | No | List of `{label, url}` entries rendered under a "More Information" header — typically the trail association, conditions page, social media. |
| `author` | No | Single `{name, url}` object rendered under an "Author" header. `url` optional; if omitted, the name is plain text. |
| `extra_links` | No | List of `{label, url}` entries rendered under an "Additional Links" header — source repo, related maps, docs, etc. |

The modal also always shows:

- **Versions** — `Trail data: <date HH:MM>` (from the trails file mtime / `_data_date`) and `App built: <date HH:MM>` (set at build time); both timestamps use the build machine's local time
- **Credits** — OpenStreetMap contributors (trail data), Protomaps (basemap tiles), and a link back to the `mtb-map-framework` source repo

Any section whose source data is absent is omitted entirely; the headers only appear when their content does.

### Base Layers

Custom raster tile layers appear in the basemap selector alongside the default Protomaps light basemap. When no base layers are configured, the selector is hidden entirely. Each entry supports:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `id` | Yes | — | Unique identifier (used internally) |
| `name` | Yes | — | Display name in the basemap dropdown |
| `url` | Yes | — | Tile URL template with `{z}`, `{x}`, `{y}` placeholders |
| `attribution` | No | `""` | HTML attribution string |
| `tile_size` | No | `256` | Tile size in pixels |
| `max_zoom` | No | — | Maximum zoom level for the tile source |
| `headers` | No | — | Map of HTTP headers for authenticated tile requests |

Example with satellite imagery:

```yaml
base_layers:
  - id: satellite
    name: "Satellite"
    url: "https://tiles.example.com/{z}/{x}/{y}.jpg"
    attribution: "&copy; Example Imagery"
    max_zoom: 19

  - id: usgs-topo
    name: "USGS Topo"
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
    attribution: "&copy; USGS"
    tile_size: 256

  - id: premium-imagery
    name: "Premium Imagery"
    url: "https://api.example.com/tiles/{z}/{x}/{y}.png"
    attribution: "&copy; Premium Provider"
    headers:
      Authorization: "Bearer YOUR_API_TOKEN"
```

When a custom layer is selected, the Protomaps vector basemap is replaced with the raster tile layer. Trail overlays, hillshade, and all interactive features continue to work on top of the raster basemap.

### Dash Patterns

The `dashed_relations` config supports two formats.

**Simple format** — a `[dash, gap]` array with values in line-width multiples:

```yaml
dashed_relations:
  13213211: [0, 2]  # dots
  55555555: [4, 2]  # long dashes
```

Common patterns: `[0, 2]` dots, `[2, 2]` short dashes, `[4, 2]` long dashes, `[6, 2]` extra-long dashes.

**Object format** — for more control over cap style and colours:

```yaml
dashed_relations:
  13213211:
    pattern: [4, 2]                  # dash pattern (required)
    cap: square                      # "round" (default) or "square" line ends
    colors: ["#000000", "#FF0000"]   # two-colour alternating dashes
```

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `pattern` | Yes | — | `[dash, gap]` in line-width multiples |
| `cap` | No | `"round"` | Line cap style: `"round"` or `"square"` |
| `colors` | No | — | One or two CSS colours. One colour overrides the route's normal colour; two colours produce alternating dash colours (see below). |

Both formats can be mixed in the same config. Dashed relations are rendered without line offsets (centred on the geometry) to avoid oval distortion on curves.

#### Alternating-colour dashes

Two colours in `colors` produce dashes of colour A interleaved with colour B along the trail — useful for trails that share signage from two routes, hazard stripes, emergency-access markings, or any case where one solid colour isn't enough.

How it works under the hood: the framework draws **two overlaid line layers**. The bottom layer is solid colour B (no dashes). The top layer is dashed colour A. The gaps in the top layer reveal solid colour B beneath, producing the alternation.

This means **colour A is "the dash" and colour B is "what shows in the gap"**. Pattern sizing controls the visual proportion directly:

```yaml
dashed_relations:
  12345678:
    pattern: [4, 4]                  # equal dash and gap
    colors: ["#000000", "#ff0000"]   # colour A = black, colour B = red
    cap: square
```

| Pattern | colors `[black, red]` produces |
|---|---|
| `[4, 4]` | Equal-width black and red segments |
| `[6, 2]` | Mostly black, with narrow red showing through 2-wide gaps |
| `[2, 6]` | Mostly red, with narrow 2-wide black dashes on top |
| `[0, 2]` (dots) | Solid red line with **black dots** on top (because dash width is 0) |
| `[4, 0]` | Solid black line — no gap means colour B never shows |

Pattern values are in line-width multiples, so the absolute size scales naturally with zoom.

**Cap style interacts with the effect.** With `cap: round` (the default) the dashes have semicircular ends that bulge slightly past the dash bounds and visually soften the boundary between A and B. With `cap: square` (or `butt`) the boundaries are crisp — generally what you want for an obvious alternating look:

```yaml
dashed_relations:
  12345678:
    pattern: [4, 4]
    colors: ["#000000", "#ffffff"]   # crisp black/white hazard stripe
    cap: square
```

**One colour vs. two.** A single-element `colors: ["#ff0000"]` overrides the route's colour entirely (equivalent to setting `relation_colors`) and applies dashes from `pattern`. Two elements activates the alternating-colour path. Three or more colours are not supported — extra entries are ignored.

**Why not just two dashed layers with offset patterns?** MapLibre's `line-dasharray` has no phase/offset parameter. You can't shift one layer's dashes by half a period to interleave them with another's. For a symmetric pattern like `[3, 3]`, the "inverted" pattern is the same `[3, 3]`, and a second dashed layer would paint in the same positions as the first, leaving the gaps transparent. The solid-underlay approach sidesteps this entirely and works for any pattern.

**Interaction with other features.** Alternating-colour dashes work with direction arrows, labels, and the route visibility rules exactly like any other dashed route. They are *not* compatible with `color_by: difficulty` — when difficulty colouring is active, IMBA-rated segments use the difficulty palette and `colors` is ignored on those segments.

### Trail Difficulty

When `show_difficulty: true`, the map displays IMBA trail difficulty rating symbols along trail segments. Ratings are read from the `mtb:scale:imba` OSM tag on individual ways. Segments without this tag show no symbols.

| Rating | Symbol | Colour |
|--------|--------|-------|
| 0 | Circle | White (easiest) |
| 1 | Circle | Green (easy) |
| 2 | Square | Blue (intermediate) |
| 3 | Diamond | Black (difficult) |
| 4 | Double diamond | Black (expert) |
| 5 | Double diamond | Orange (pro) |

The symbols use ski-trail-style iconography, are always oriented vertically (not rotated to follow the trail line), and include a white halo for visibility against any background. The Difficulty toggle appears under "Show on map" in the expanded bottom sheet; it defaults **on** when the build generates the sprite, and the state persists in localStorage (`mtb.difficulty`).

Difficulty symbols only appear on segments where the trail casing is visible — hiding a trail (e.g., by switching season) also hides its difficulty symbols.

#### Difficulty is per-trail (way), not per-route (relation)

The `mtb:scale:imba` tag is read from individual **ways** only. Tags on the parent **relation** are ignored — including for `color_by: difficulty` colouring.

This matches OpenStreetMap's tagging convention (`mtb:scale:imba` is a per-way tag) and reflects the reality that real trails often vary in difficulty along their length:

| Way tag | Relation tag | What renders |
|--|--|--|
| `4` | `2` | Black diamond (4) — way value wins |
| (none) | `2` | Nothing — segment is unrated; in `color_by: difficulty` it falls back to `default_trail_color` |
| `4` | (none) | Black diamond (4) |

### Direction Arrows

The map renders direction-of-travel arrows along ways tagged `oneway=yes`, `oneway=-1`, or `oneway=reversible` in OpenStreetMap. Two-way ways get no arrows. Arrows scale with zoom, follow the line's bearing (rotate with the map, not the screen), and are always on. They're sized to read as a subtle directional cue rather than compete with the trail casing.

Arrow rendering is **per-way**, not per-route — same model as IMBA difficulty. The `oneway` tag is read from individual ways; relations themselves don't have a direction, so they don't carry the tag.

| Way tag | What renders |
|--|--|
| `oneway=yes` | Arrows along the way's digitised direction |
| `oneway=-1` | Arrows along the reverse direction (normalised at build time) |
| `oneway=reversible` | Arrows that **must** flip on a schedule — see below |
| `oneway=no` or unset | No arrows |

#### Direction Schedules (Day-of-Week / Date-Parity Reversal)

Some trail systems are signed as one-way with the direction alternating by day of week (e.g. clockwise Mon/Wed/Fri, counter-clockwise Tue/Thu/Sat) or by calendar-date parity (one direction on even days, the other on odd) — OSM has no canonical schema for the schedule itself. The framework supplies it via two layered keys:

**`default_direction_schedule`** — system-wide default. Use this when the whole trail system shares one schedule (the common case at most parks):

```yaml
default_direction_schedule:
  reverse_days: [tuesday, thursday, saturday]
```

Every route in the map adopts this schedule unless overridden.

**`direction_schedules`** — per-relation overrides. Use this only when one route differs from the system-wide default, or to opt a route out:

```yaml
direction_schedules:
  12425503:
    reverse_days: [sunday]            # this route uses a different schedule
  98765432:
    reverse_days: []                  # this route opts out of the default
```

Rules:

- Keys are OSM relation IDs. The relation is purely a grouping handle for "the ways under this relation share this schedule" — relations themselves don't have direction.
- `reverse_days` lists day tokens (case-insensitive; `monday`, `Mon`, `MONDAY`, and `mo` all parse). Alongside the seven weekday names, two parity tokens are accepted: **`even_days`** matches every even calendar date (2, 4, 6 …) and **`odd_days`** matches odd dates (1, 3, 5 …). Parity is calendar-date parity (`getDate() % 2`), so month boundaries such as Mar 31 → Apr 1 can produce two same-parity days in a row; that's expected. Weekday and parity tokens can coexist in one list — any match triggers reversal ("today is Monday OR today is even").
- A per-relation entry in `direction_schedules` always wins over `default_direction_schedule`. An entry with `reverse_days: []` is the way to opt one route out of the default.
- On a reverse day, every way that is (a) OSM-tagged `oneway=yes`/`oneway=-1`/`oneway=reversible` **and** (b) a member of a relation whose effective schedule lists today has its arrow rotated 180°.
- Setting a schedule (default or per-relation) does **not** make untagged ways one-way — OSM tagging still controls which ways get arrows. The schedule is purely a rotation hook.

#### `oneway=reversible` is Required to Pair with a Schedule

`oneway=reversible` means "the direction is alternating per ground signage" — a way with this tag and no schedule cannot be rendered correctly (we'd silently pick OSM digitisation order, which would be wrong half the time). The build therefore **fails** if any way is tagged `oneway=reversible` and no schedule (via `default_direction_schedule` or a `direction_schedules` entry on a parent relation) covers it. The error lists each offending way with a clickable OSM URL and its parent relation IDs so you can pick where to attach the schedule.

By contrast, `oneway=yes` ways have an inherent direction in OSM, so a schedule for them is optional — without one they render statically forward; with one they flip on the configured days.

The day-of-week and calendar date are read from the visitor's local clock at page load and rechecked every 5 minutes (so a tab left open across midnight stays correct).

## Route Buckets

Every route in the map carries three independent boolean flags:

- `summer` — rideable without snow
- `winter` — rideable / groomed only with snow (fatbike, snowshoe)
- `emergency` — a service, rescue, or access route

**The flags are not mutually exclusive.** A single route can sit in one bucket, two, or all three. The canonical example: a Snow Bike Route groomed for fatbikes in winter *and* rideable on knobbies in summer would carry `summer=true, winter=true` — so it stays visible when you switch from Summer to Winter mode and back.

### UI behaviour

- **Summer / Winter is a mode switch.** The bottom sheet has a segmented Season control with two options. The app is in one mode at a time. Summer mode renders routes with `summer: true`; Winter mode renders routes with `winter: true`.
- **Emergency is an additive overlay.** A separate Emergency toggle (also in the bottom sheet) adds routes with `emergency: true` on top of whatever mode is currently active, without changing the mode. Toggling Emergency off hides those routes again but leaves the mode untouched.
- **First-visit default is Summer.** The user's explicit choice persists in localStorage (`mtb.seasonMode`, `mtb.emergencyOn`) and restores on subsequent visits. No month-based auto-detection.

### How the flags are computed

At build time, for each route the three flags are computed from OSM tags plus the three additive config lists:

```
winter    = OSM tag seasonal=winter   OR  id in winter_relations
emergency = id in emergency_access_relations
summer    = id in summer_relations   OR  (not winter AND not emergency)
```

Concretely:

| Route source | `summer` | `winter` | `emergency` |
|---|---|---|---|
| Regular route (no OSM tag, not in any list) | true | false | false |
| OSM `seasonal=winter` | false | true | false |
| In `winter_relations` only | false | true | false |
| In `emergency_access_relations` only | false | false | true |
| In `winter_relations` + `summer_relations` (the SBR pattern) | true | true | false |
| OSM `seasonal=winter` + in `summer_relations` | true | true | false |
| In `winter_relations` + `emergency_access_relations` | false | true | true |
| In all three lists | true | true | true |

The rule to remember: **being categorised Winter or Emergency removes a route from Summer by default.** `summer_relations` is the opt-back-in list for year-round routes that would otherwise sit only in Winter or Emergency.

Custom routes (see below) carry their three flags inline as YAML booleans and follow the same rules.

## Custom Routes

Some routes can't live in OSM — race courses, event loops, demo routes, or any transient route that doesn't match permanent signed infrastructure on the ground. The framework supports these as first-class citizens via the `custom_routes` config key.

```yaml
custom_routes:
  - id: race-2025                                     # string id, unique within the map
    name: "2025 Copper Harbor Epic"
    color: "#ff00ff"
    summer: true
    winter: false
    emergency: false
    geometry: race-2025.geojson                       # path relative to configs/<slug>/
    # dashed: false                                   # optional
    # description: "Start/finish at the lodge. 20 mi."  # optional
    # trail_name_field: name                          # optional: GeoJSON property to use as per-segment trail_name
```

### Rules

- **`id`** is a string. It must be unique within the map and must not collide with any OSM relation ID in the config (`extra_relations`, `winter_relations`, etc.). Best practice: use a hyphenated slug like `race-2025` or `demo-loop` so it's visually distinct from the numeric OSM ids.
- **`color`** overrides any `relation_colors` / `default_trail_color` lookup for this route. It also becomes the swatch colour in the finder and the glow colour when the route is highlighted.
- **`summer`, `winter`, `emergency`** are three independent booleans — same semantics as OSM routes. Any combination is valid; at least one must be true (a custom route that's invisible in all modes is rejected at validation time). Defaults if all three are omitted: `summer: true, winter: false, emergency: false`.
- **`geometry`** is a path to a GeoJSON file, resolved relative to the config's directory (`configs/<slug>/`). A bare filename like `race-2025.geojson` picks up the file sitting next to the YAML. Absolute paths are passed through unchanged (useful for shared assets outside the repo). The file must be a FeatureCollection or a single Feature, containing LineString or MultiLineString geometry only. Points, Polygons, and other geometry types are rejected with a clear error.
- **`trail_name_field`** is optional. If the GeoJSON features carry a per-feature property whose value should become the trail name (so individual named segments of the custom route show up in the trail finder), name that property here. If omitted, features pass through with no per-segment trail name.

### Generating custom-route GeoJSON

The `geometry` file is a plain GeoJSON FeatureCollection or Feature containing LineString / MultiLineString geometry. Common sources:

- **Hand-drawn** — [geojson.io](https://geojson.io). Draw the route in a browser, export GeoJSON.
- **GPS recording (GPX)** — `gpsbabel -i gpx -f ride.gpx -o geojson -F ride.geojson`, or `ogr2ogr -f GeoJSON ride.geojson ride.gpx tracks`.
- **OSM XML extract (.osm)** — `ogr2ogr -f GeoJSON route.geojson route.osm lines`. Useful if you've drafted the route in JOSM without uploading to OSM.
- **QGIS** — draw/edit a LineString layer and export as GeoJSON (EPSG:4326).

If your GeoJSON features carry a `name` (or similar) property per segment, add `trail_name_field: name` in the config so those segments appear in the trail finder alongside OSM-sourced trails.

### Participation in buckets, the finder, and highlights

Custom routes are indistinguishable from OSM routes in every runtime behaviour:

- They follow the bucket model via their inline `summer`/`winter`/`emergency` flags.
- They appear in the **Routes** section of the finder (filtered to currently-visible routes just like OSM routes).
- Tapping a custom-route row in the finder highlights the whole route in its own colour (same as any OSM route).
- If `trail_name_field` points at per-segment names, those trails also appear in the **Trails** section of the finder and can be highlighted individually.

## Trail Finder

The expanded bottom sheet contains a combined routes-and-trails finder with one search input and two sections:

```
 🔍 Search routes & trails

ROUTES
  ●   Blue Loop              12 mi
  ●   Red Loop                8 mi
  ●   Race 2025              20 mi

TRAILS
      Alpine Traverse        Blue
      Bear Creek             Blue, Red
      Birch Hollow           Red
      …
```

- **One scrollable list, two section headers.** Routes on top, trails below.
- **Single search input** filters both sections (case-insensitive substring match against route names and trail names).
- **The list always mirrors what's currently visible on the map.** In Summer mode with Emergency off, you see summer routes and the trails that belong to them. Toggle Emergency on and emergency routes/trails appear in the list. Switch to Winter mode and the list live-refilters to winter content.
- **Route rows** show a coloured swatch in the route's own colour plus the name. OSM and custom routes appear together, indistinguishable in behaviour.
- **Trail rows** show the trail name and the parent route(s) underneath.
- **Tapping a row** highlights it on the map (glow + stroke in the route's own colour for routes, or amber for trails), pans/zooms to its extent, collapses the sheet, and shows a floating chip at the top of the map (`✕ Highlighting: <name>`). Tap the chip to clear.
- **One thing highlighted at a time.** Picking a new row replaces the previous highlight. Everything else stays visible — the highlight is purely additive emphasis.

## Privacy

This framework is entirely client-side: there are no cookies, no analytics, no server-side tracking, and no third-party scripts. The map renders from your own static server, and all visitor interactions stay in the browser.

The app uses `localStorage` to persist purely-functional UI preferences across visits:

- `mtb.seasonMode` — "summer" or "winter"
- `mtb.emergencyOn` — boolean
- `mtb.poi.<kind>` — one boolean per POI category (parking, trailheads, features, markers)
- `mtb.labels` — "routes", "trails", or "none"
- `mtb.difficulty` — boolean (IMBA difficulty symbols)

Nothing else is stored. No identifiers, no geolocation traces, no analytics payloads.

**URL hash (`url_hash`, default `false`).** Off by default: no `#zoom/lat/lon` fragment is written, return visitors always land on the default bbox-framed view, and no shareable deep-links exist. To opt in for a particular deployment, set `url_hash: true` in the config — the URL then gains a `#zoom/lat/lon` fragment that updates live as the user pans/zooms. That fragment never leaves the browser via network traffic (URL fragments are not sent to the server), but it *is* visible in the address bar, included in screenshots, visible during screen-shares, and captured by anyone the user sends the URL to. On a trail map this usually isn't sensitive — riders share "look at this section" links routinely — but a user who was just zoomed in on their home address when they share the map URL is implicitly sharing that location too. The default-off posture protects users who never asked to share their position; opt in only when the shareable-link convenience clearly outweighs the leak.

Under EU law, this usage falls under the ePrivacy Directive Article 5(3) "strictly necessary" exemption (the same category as a shopping cart's session state) and does not require a consent banner. The stored values are not personal data under GDPR: no cross-site linkage, no profile building, no server transmission. If you self-host this framework for a public trail system, you can link this section from your About modal's `more_information` or `extra_links` if you want to reassure visitors.

## Asset Layout Convention

Every map lives in its own folder under `configs/`, where the folder name matches the config's `slug` field verbatim:

```
configs/<slug>/
  <slug>.yaml                # the map's config file (name matches the folder)
  logo.<ext>                 # optional: source logo (png / webp / svg)
  icon.<ext>                 # optional: square source image for favicon + PWA icons
  osm.osm                    # optional: offline OSM snapshot for `osm_file:`
  <route-id>.geojson         # optional: one file per entry in custom_routes
```

Each folder is self-contained — everything the build needs for that map lives in one place. Copy `configs/ramba/` somewhere else, rename it, and you have the scaffold of a new map.

**Asset paths in the config are resolved relative to the config's directory.** Bare filenames like `logo: logo.webp` and `geometry: race-2025.geojson` pick up files sitting next to the YAML. Absolute paths are passed through unchanged (useful for shared assets kept outside the repo).

The two example templates share one folder:

```
configs/example/
  example.yaml               # verbose, every key uncommented with inline comments
  example-minimal.yaml       # canonical-order skeleton, most keys commented out
```

**Only source artefacts live in `configs/<slug>/`.** Build-time-generated files (PWA icons, manifests, favicons, vendor libraries) land in `build/<slug>/...` and are never committed. The validator checks that each referenced asset file exists and fails fast with a clear error naming the config and the missing file.

If you're creating a new map, start by copying `configs/example/example-minimal.yaml` into a new `configs/<your-slug>/<your-slug>.yaml`, drop `logo.webp` / `icon.png` / any custom-route GeoJSONs into the same folder, and reference them by bare filename in the config.

## Build Options

```bash
python scripts/build.py configs/ramba/ramba.yaml                 # Full build (uses caches)
python scripts/build.py configs/ramba/ramba.yaml --force          # Re-fetch everything (clears Overpass cache)
python scripts/build.py configs/ramba/ramba.yaml --trails         # Re-fetch trail + POI data (uses Overpass cache)
python scripts/build.py configs/ramba/ramba.yaml --skip-terrain   # Skip terrain tile generation
python scripts/build.py configs/ramba/ramba.yaml --skip-basemap   # Skip basemap extraction
```

- `--force` clears the Overpass API response cache (`cache/`) and re-fetches all data from OSM, re-extracts basemap and terrain tiles.
- `--trails` re-runs the trail and POI data pipeline but reuses cached Overpass API responses if available. Useful for re-processing data after changing YAML options like `dashed_relations`, `relation_colors`, `winter_relations`, `summer_relations`, or `custom_routes` without hitting the Overpass API again.
- `--skip-terrain` and `--skip-basemap` skip the corresponding tile extraction steps. Useful for faster rebuilds when only templates or config options have changed.

The basemap extraction automatically detects the latest available [Protomaps planet build](https://maps.protomaps.com/builds/) — no need to update a URL manually. You can override this by setting the `PROTOMAPS_PLANET_URL` environment variable.

Flags can be combined: `--trails --skip-basemap --skip-terrain` re-processes trail data and rebuilds templates without touching tiles.

A convenience wrapper at `tools/build_and_deploy.sh` validates every config first, then builds and (optionally) deploys over `rsync`. Run `./tools/build_and_deploy.sh --help` for usage; `--validate-only` is fast and catches YAML errors without any fetch or build work.

## Data Cache

The build pipeline caches Overpass API responses in the `cache/` directory to avoid redundant network requests. **Cached data is never automatically updated.** Subsequent builds reuse existing cache files indefinitely until you explicitly clear them.

### Checking cache age

When the build runs, it logs the date and age of each cached response it uses:

```
Using cached response (2026-04-07 22:45, 2d ago): cache/overpass_798bc0f14a88.json
```

You can also check cache ages manually:

```bash
ls -la cache/
```

### Refreshing cached data

To update the cached OSM data (e.g., after trail edits in OpenStreetMap):

- **`--force`** — clears the entire `cache/` directory and re-fetches all Overpass data from scratch. Also re-extracts basemap and terrain tiles.
- **`--trails`** — re-runs the trail and POI data pipeline. Reuses existing cached Overpass responses if present; only fetches data that isn't already cached.

To force a full refresh of just trail data, delete the relevant cache files manually and run with `--trails`:

```bash
rm cache/overpass_*.json
python scripts/build.py configs/ramba/ramba.yaml --trails --skip-basemap --skip-terrain
```

### Build and data dates

The About modal shows both the build date and the date of the cached data source, so visitors can see how current the trail information is.

## Local .osm File Support

Instead of fetching data from the Overpass API, you can build maps from a local `.osm` XML file. This is useful for:

- Non-public trail data maintained locally in JOSM
- Offline map generation without internet access
- Testing edits before uploading to OpenStreetMap

Add `osm_file` to your config. The path is resolved relative to the config's directory, so a bare filename like `osm.osm` picks up the file sitting next to the YAML:

```yaml
# configs/mytrails/mytrails.yaml
osm_file: osm.osm              # resolves to configs/mytrails/osm.osm
root_relation_id: 12345678     # still required
```

The `.osm` file must contain the root relation, any child relations it references, their member ways, and all referenced nodes with coordinates (this is the default when saving from JOSM).

All other config options (`extra_relations`, `clipped_relations`, `winter_relations`, `summer_relations`, `emergency_access_relations`, `dashed_relations`, etc.) work the same way — they reference IDs found in the file instead of Overpass.

### Downloading from Overpass

You can download a complete `.osm` file for a super-relation from the Overpass API. This fetches the super-relation, all child relations, their member ways, and all referenced nodes with full geometry:

```bash
curl -o configs/mytrails/osm.osm "https://overpass-api.de/api/interpreter" \
  --data-urlencode "data=[out:xml][timeout:300];
    relation(12345678);
    rel(r);
    (._; way(r););
    (._;>;);
    out meta;"
```

Replace `12345678` with your super-relation ID. The query works as follows:

1. `relation(12345678)` — fetches the super-relation
2. `rel(r)` — fetches all child relations
3. `way(r)` — fetches all ways referenced by those relations
4. `(._;>;)` — recursively resolves all node references to get coordinates
5. `out meta` — outputs full XML with coordinates and metadata

The resulting file can be opened and edited in JOSM, then used directly with the build pipeline.

You can preview what the parser finds without building:

```bash
python scripts/osm_parser.py configs/mytrails/osm.osm 12345678
```

## Logo and Icon Assets

The framework uses two separate image assets configured via `logo` and `icon`. They serve different purposes and have different requirements.

### Logo (`logo`)

The logo is displayed as an overlay in the bottom-left corner of the map and at the top-right of the **About this map** modal header. At build time the framework opens the source with Pillow, picks the binding axis from the source's aspect ratio, resamples to ~2× the display size with LANCZOS for retina sharpness, and writes a single normalised `logo.webp` into the output. Source files can be PNG, WebP, JPEG, or any format Pillow can open; SVGs are not currently rasterised and should be pre-converted.

| Property | Detail |
|----------|--------|
| **Purpose** | Map overlay branding; also shown in the About modal header |
| **Map overlay render** | Inside a 200×80 px bounding box on desktop, 150×60 px on mobile |
| **About modal render** | Inside a 140×56 px box on desktop, 100×40 px on mobile |
| **Binding axis** | Wide wordmarks (aspect ≥ 2.5:1) land at 200 px wide; square or tall logos land at 80 px tall |
| **Pre-resize target** | Source is resampled to ~2× the render size (max longer side ~400 px on desktop). Never upscaled — smaller sources are preserved. |
| **Recommended source** | At least 2× the expected render size on its long side (e.g. 400+ px wide for a wordmark); higher is fine, the framework resizes down |
| **Recommended format** | Any Pillow-readable raster format; output is always WebP |
| **Transparency** | Supported — the logo floats over the map with a subtle drop shadow |

The logo can be any shape — the bounding-box render handles wordmarks, square badges, and tall marks cleanly. Wide horizontal wordmarks produce the most brand-prominent result in the lower-left overlay.

#### Colour guidance

The logo is displayed on top of the map. For best legibility across varying terrain colours, use dark artwork on a transparent background (e.g., a black or near-black wordmark). Multi-colour or photographic logos work too, but design them with a built-in outline or soft background if you need to ensure contrast against busy map areas.

If `logo` is omitted but `icon` is set, the icon source is used as the logo automatically — square icons render as ~80×80 badges in the overlay and ~56×56 in the About modal. If neither `logo` nor `icon` is set, the logo overlay is hidden and the About modal shows only the text title in its header.

### Icon (`icon`)

The icon is a single square source image used to generate all favicon and PWA icon variants at build time. It is **also used as the logo source when `logo:` is omitted**, so a map that only needs a single brand asset can configure `icon:` alone.

| Property | Detail |
|----------|--------|
| **Purpose** | Favicons, PWA home screen icon, browser tab icon; logo fallback when `logo:` is not set |
| **Required format** | PNG or WebP |
| **Required dimensions** | Square, at least 256×256 pixels |
| **Recommended dimensions** | 512×512 or 1024×1024 for best quality at all sizes |
| **Transparency** | Supported on most platforms; the Apple touch icon variant is composited onto a white background since iOS does not support transparent home screen icons |

The build generates the following files from the source image:

| File | Size | Notes |
|------|------|-------|
| `icons/apple-touch-icon.png` | 180×180 | Composited on white background for iOS |
| `icons/android-chrome-192x192.png` | 192×192 | Android home screen |
| `icons/android-chrome-256x256.png` | 256×256 | Android home screen (high-DPI) |
| `icons/mstile-150x150.png` | 150×150 | Windows Start tile |
| `icons/favicon-32x32.png` | 32×32 | Standard browser tab icon |
| `icons/favicon-16x16.png` | 16×16 | Small browser tab icon |
| `favicon.ico` | 16, 32, 48 | Multi-resolution ICO for legacy browsers |
| `icons/safari-pinned-tab.svg` | — | Auto-traced silhouette (requires `potrace`) |
| `icons/site.webmanifest` | — | PWA manifest with `name` and `title` from config |

If `icon` is omitted, all icon `<link>` tags are stripped from the HTML output and no manifest is generated.

**Tip:** Use a simple, high-contrast design for the icon — it needs to be recognisable at 16×16 pixels. Avoid fine text or thin lines.

## Vendor Bundling

All JavaScript and CSS dependencies (MapLibre GL JS, PMTiles, Protomaps basemaps) are downloaded from their CDNs at build time and bundled into `vendor/` in the output directory. The generated map has **no runtime CDN dependency** — everything is served from your own server. This ensures the map continues to work even if upstream CDNs go offline or change.

Vendor libraries are bundled regardless of the `pwa` setting.

## PWA / Offline Support

When `pwa: true` (the default), every generated map is a fully installable Progressive Web App that works offline after the first visit. Set `pwa: false` to disable the service worker and install UI while still keeping locally bundled vendor libraries.

**How it works:**

1. **Service worker** — A service worker (`sw.js`) is generated at the end of each build with a precache list of every file in the output. On first visit, all assets are cached. Subsequent visits and offline use are served entirely from the cache.

2. **PMTiles offline** — The service worker handles HTTP Range requests for `.pmtiles` files by slicing from the cached full file. Map tiles work fully offline.

3. **Install row** — An "Install as app" row appears near the bottom of the expanded bottom sheet on supported browsers. On iOS, tapping it reveals a Share → Add to Home Screen hint instead of firing an install prompt.

4. **Cache updates** — Each build produces a unique cache version. On the next visit after a rebuild, the new service worker installs, re-caches all files, and activates immediately. Old caches are automatically cleaned up.

The PWA is transparent — the map works identically in a regular browser tab. Offline capability is purely additive.

## Font Trimming

The Protomaps basemap assets include fonts covering every world script (Latin, CJK, Devanagari, etc.) across 256 Unicode range files per font face — roughly 20 MB total. Most maps only need a small subset of these.

The build pipeline automatically trims fonts by scanning the basemap tiles, trail data, and POI data for every text character that actually appears, then copying only the PBF glyph ranges containing those characters. Script-specific font faces (e.g., Devanagari) are only included when the map data contains characters from that script.

This is fully data-driven — a US trail map gets only Latin ranges, while a map in Japan would automatically include CJK ranges. No configuration is needed.

You can preview font trimming results without building:

```bash
python scripts/font_trimmer.py build/ramba/
```

## Project Structure

```
configs/            YAML config files (one per map)
scripts/
  build.py          Build orchestrator
  fetch_trails.py   OSM trail data via Overpass API or local .osm file
  fetch_pois.py     Trail markers (guideposts + emergency access points, merged) and features from Overpass API or local .osm file
  osm_parser.py     Parser for local .osm XML files
  fetch_basemap.py  Protomaps basemap PMTiles extraction
  fetch_terrain.py  Mapterhorn terrain PMTiles extraction
  generate_icons.py Icon generation from source image (Pillow)
  font_trimmer.py   Automatic font subsetting based on map data
  validate_config.py Pre-flight YAML validation
  serve.py          Dev server with Range request support
templates/
  index.html        Map viewer page
  app.js            MapLibre GL JS application
  style.css         Light-theme styles (single theme, no dark variant)
  sw.js             Service worker template for offline/PWA support
configs/
  <slug>/             Per-map folder: config + all of its asset files
    <slug>.yaml       The map's config
    logo.<ext>        Optional: source logo
    icon.<ext>        Optional: source square icon for favicons + PWA icons
    osm.osm           Optional: offline OSM snapshot
    *.geojson         Optional: custom-route geometries (one per custom_routes entry)
  example/
    example.yaml      Verbose template — every key uncommented with inline comments
    example-minimal.yaml  Canonical-order skeleton; copy this to start a new map
assets/
  fonts/              Protomaps basemap fonts (PBF glyph ranges, auto-trimmed at build time)
  sprites/            Protomaps basemap sprites (PNG + JSON, all flavours)
build/<slug>/         Generated output (deployable static site)
cache/                Cached Overpass API responses
tools/
  build_and_deploy.sh Convenience wrapper: validate → build → optional rsync deploy
```

## Known Issues

- **Firefox console warning**: `WebGL warning: texImage: Alpha-premult and y-flip are deprecated for non-DOM-Element uploads.` This is a cosmetic warning from MapLibre GL JS and does not affect functionality. A [fix has been merged](https://github.com/maplibre/maplibre-gl-js/pull/7128) and will be included in a future MapLibre GL JS release.

## Creating a New Map

1. Map your trails in OpenStreetMap as route relations under a super-relation (or pick an existing one).
2. Create `configs/<slug>/` (the folder name must match your chosen slug exactly). Copy `configs/example/example-minimal.yaml` into it as `configs/<slug>/<slug>.yaml`. Set `name`, `slug`, `title`, `root_relation_id`; uncomment and adjust any other keys you need to customise.
3. Drop your `logo.<ext>` and `icon.<ext>` source files into the same `configs/<slug>/` folder and reference them by bare filename: `logo: logo.webp`, `icon: icon.png`.
4. If you have any non-OSM routes (race courses, event loops), drop their `.geojson` files into `configs/<slug>/` and reference each from `custom_routes` by bare filename. See [Custom routes](#custom-routes) for details.
5. Run `./tools/build_and_deploy.sh --validate-only <slug>` to confirm the config is clean.
6. Run `./tools/build_and_deploy.sh --build-only <slug>` (or `python scripts/build.py configs/<slug>/<slug>.yaml`) to generate the output.
7. Deploy `build/<slug>/` to your server.
