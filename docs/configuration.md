# Configuration

Every map is described by a single YAML file. This document is the canonical
reference for every supported key, plus deep dives on the features that need
more than a one-line description (custom routes, direction schedules, route
buckets, dash patterns, base layers, the About and Welcome modals, logo and icon
assets, and privacy posture).

Two starter YAML files live under `configs/reference/`:

- `reference-minimal.yaml`: the template for a new map. Section headers plus
  every supported key on a commented-out line at its default value. Copy it, set
  the required keys, and uncomment only the lines you want to change.
- `reference.yaml`: the same structure and key order with a one-line comment on
  each key, for quick in-editor lookup. This document holds the full prose.

Both files stay in identical key order, so you can diff them at any time and use
`tools/clean_config.py` to re-align a production config against either template.

## Contents

- [Asset layout convention](#asset-layout-convention)
- [Quick start: minimal map config](#quick-start-minimal-map-config)
- [Config reference](#config-reference)
  - [Identity](#identity)
  - [Data sources](#data-sources)
  - [Custom routes](#custom-routes)
  - [Map view geometry](#map-view-geometry)
  - [Build-time data gates](#build-time-data-gates)
  - [Per-route style overrides](#per-route-style-overrides)
  - [Direction schedules](#direction-schedules)
  - [Display](#display)
  - [Marker and accent colours](#marker-and-accent-colours)
  - [Base layers](#base-layers)
  - [Branding](#branding)
  - [User-supplied points](#user-supplied-points)
  - [PWA](#pwa)
  - [About modal](#about-modal)
  - [Welcome modal](#welcome-modal)
  - [Output](#output)
- [Route buckets](#route-buckets)
- [Custom routes (full guide)](#custom-routes-full-guide)
- [Trail finder](#trail-finder)
- [Trail difficulty](#trail-difficulty)
- [Direction arrows](#direction-arrows)
- [Dash patterns](#dash-patterns)
- [Trailhead and parking entries](#trailhead-and-parking-entries)
- [About this map block](#about-this-map-block)
- [Base layers (full guide)](#base-layers-full-guide)
- [Logo and icon assets](#logo-and-icon-assets)
- [Privacy](#privacy)

## Asset layout convention

Every map lives in its own folder under `configs/`, where the folder name
matches the config's `slug` field verbatim:

```
configs/<slug>/
  <slug>.yaml                 the map's config file (name matches the folder)
  logo.<ext>                  optional: source logo (png / webp / jpeg)
  icon.<ext>                  optional: square source image for favicon + PWA icons
  osm.osm                     optional: offline OSM snapshot for `osm_file:`
  <route-id>.geojson          optional: one file per entry in custom_routes
```

Each folder is self-contained: everything the build needs for that map lives in
one place. Copy `configs/ramba/` somewhere else, rename it, and you have the
scaffold for a new map.

Asset paths in the config are resolved relative to the config's directory. Bare
filenames like `logo: logo.webp` and `geometry: race-2025.geojson` pick up files
sitting next to the YAML. Absolute paths are passed through unchanged (useful
for shared assets kept outside the repo).

The two starter templates share one folder:

```
configs/reference/
  reference-minimal.yaml      canonical-order skeleton, most keys commented out
  reference.yaml              verbose annotated reference; same key order
```

Only source artefacts live in `configs/<slug>/`. Build-time-generated files (PWA
icons, manifests, favicons, vendor libraries) land in `build/<slug>/...` and are
never committed. The validator checks that every referenced asset file exists
and fails fast with a clear error naming the config and the missing file.

## Quick start: minimal map config

The shortest valid config. Every key not shown takes the framework default:

```yaml
name: My Trails
slug: mytrails
title: "My Trails Map"
relations: [12425503]
```

To start a new map, copy `configs/reference/reference-minimal.yaml` into
`configs/<your-slug>/<your-slug>.yaml`, set the four required keys, drop your
`logo.webp` / `icon.png` / any custom-route GeoJSONs into the same folder, and
reference them by bare filename in the config.

## Config reference

### Identity

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | Yes | : | Short name used in build logs and as the PWA icon label on mobile home screens. |
| `slug` | Yes | : | URL-safe identifier. Used for the map's config folder (`configs/<slug>/`), the build output directory (`build/<slug>/`), and the deploy destination subdirectory. Must match `[a-z0-9_-]+` and match the folder name holding the YAML. |
| `title` | Yes | : | Full page title shown in browser tab and PWA install dialogs. |

### Data sources

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `relations` | Yes | : | Non-empty list of OSM relation IDs to render as routes. **Each entry may be a leaf route relation OR a super-relation** (auto-expanded into its child routes one level deep at fetch time; the parent itself is dropped since it has no ways). Order doesn't matter. Multi-system maps just list every entry-point relation. |
| `osm_file` | No | : | Path to local `.osm` XML file; when set, uses this instead of the Overpass API. See [Building](building.md#local-osm-file-support). |
| `clipped_relations` | No | `[]` | OSM relation IDs to include but clip to the core trail bounding box (e.g. rail trails). Super-relations are auto-expanded the same way as `relations`. |
| `event_mode` | No | : | Optional event-mode block. Feature one or more routes prominently while every other trail renders as muted context. See [Event mode](event-mode.md) for the schema and worked examples. |

### Route buckets

See [Route buckets](#route-buckets) for how the Summer / Winter / Emergency
flags are computed from these lists plus OSM tags.

Each list below accepts either leaf route relation IDs OR super-relation IDs. A
super-relation in any of these keys propagates the bucket assignment to every
child route (so listing one super-relation in `winter_relations` marks all its
children as winter without enumerating them).

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `winter_relations` | No | `[]` | Relation IDs to flag `winter=true`. Use for winter-only routes not already tagged `seasonal=winter` in OSM (snowshoe relations, fatbike loops, etc.). Being in this list removes the route from Summer unless also in `summer_relations`. |
| `summer_relations` | No | `[]` | Relation IDs to flag `summer=true`. Use to re-add a route to Summer that would otherwise be pulled out of it (OSM `seasonal=winter` year-round routes like a Snow Bike Route, emergency routes also used year-round). Overlap with `winter_relations` is how you express a route that lives in both buckets. |
| `emergency_access_relations` | No | `[]` | Relation IDs to flag `emergency=true`. Rendered only when the rider toggles the Emergency Access overlay on, regardless of season mode. |

### Custom routes

See [Custom routes (full guide)](#custom-routes-full-guide) for the complete
schema and rules.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `custom_routes` | No | `[]` | List of user-defined non-OSM routes with inline metadata (id, name, colour, bucket flags) and a GeoJSON geometry file reference. |

### Map view geometry

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `bbox` | No | auto | Bounding box `[west, south, east, north]` used for the **initial view fit**; auto-computed from trail geometry with a ~3% proportional buffer if omitted. |
| `pan_padding` | No | `0.5` | How much looser the pan wall is than `bbox`, as a fraction of the bbox's greater dimension added on each side (`0.5` is about 4x the pannable area; `0` pins the wall to `bbox`). Also widens basemap and terrain tile extraction to match. See the notes below. |
| `pan_bbox` | No | computed | Explicit pan envelope `[west, south, east, north]`; overrides `pan_padding` when set. Usually unnecessary: use `pan_padding` unless the auto-symmetric expansion is wrong for your site (e.g. asymmetric pan room to cover a parking lot north of the trails but nothing south). |
| `center` | No | auto | Map centre `[lon, lat]`; auto-computed from bbox midpoint if omitted. |
| `zoom` | No | `14` | Initial zoom level. |
| `min_zoom` | No | `10` | Minimum zoom level. |
| `max_zoom` | No | `18` | Maximum zoom level. |
| `basemap_maxzoom` | No | `15` | Max zoom for basemap tile extraction. |
| `terrain_maxzoom` | No | `13` | Max zoom for terrain tile extraction. |

#### Pan area: `bbox` vs. `pan_bbox`

`bbox` frames the trails on first paint. `pan_bbox` is the wall the rider hits
when panning; it comes from `bbox` widened by `pan_padding`, or you set it
directly. The pan wall is looser than the initial frame because the map clamps
on its centre, so a rider zoomed in near a tight edge would otherwise see little
beyond it. Widening `pan_padding` also expands basemap and terrain extraction to
match, so the edges still show real map and the tile files grow accordingly.

Tune `pan_padding` per map:

- `0.5` (default): room for the surrounding roads and landmarks at trail-detail
  zoom.
- `1.0`: about twice the pan room, for an access road or parking lot well
  outside the trail footprint. Roughly doubles tile size.
- `0.25` or lower: less surrounding basemap, smaller tiles.
- `0`: pins the pan wall to `bbox`.

For asymmetric room (more on one side than another), set `pan_bbox` directly
instead of `pan_padding`.

### Build-time data gates

These gate **data fetching and build-time asset generation**, not UI visibility.
They exist so a map that doesn't need a given data type can skip the Overpass
query or sprite generation entirely. The corresponding UI toggle is hidden
automatically when the underlying data or sprite is absent.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `show_markers` | No | `true` | When false, skips the Overpass query for trail markers (guideposts and emergency-access points, merged) and hides the Markers toggle. |
| `show_features` | No | `true` | When false, skips the Overpass query for `tourism=attraction` feature nodes. |
| `show_parking` | No | `true` | When false, parking markers from the config are not rendered. |
| `show_trailheads` | No | `true` | When false, trailhead markers from the config are not rendered. |
| `show_hubs` | No | `true` | When false, trail-hub markers from the config are not rendered. The Hubs toggle auto-hides when the map defines no hubs. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `show_toilets` | No | `true` | When false, skips the Overpass query for `amenity=toilets` nodes. The Toilets toggle auto-hides when none were found. |
| `show_drinking_water` | No | `true` | When false, skips the Overpass query for `amenity=drinking_water` nodes. The Drinking Water toggle auto-hides when none were found. |
| `show_terrain` | No | `true` | When false, terrain tiles are not fetched and the hillshade layer is omitted. |
| `show_difficulty` | No | `true` | When false, no IMBA difficulty sprite is generated and no symbols appear. The toggle also auto-hides when no way carries an `mtb:scale:imba` value. First-visit state comes from `default_visible` (include `difficulty`, or use `all`); the rider's later choice persists. |
| `show_routes` | No | `true` | When false, hides the Finder's Routes section and the Routes label mode. Use for maps with no curated routes. |
| `show_trails` | No | `true` | When false, hides the Finder's Trails section and the Trails label mode. Use where routes and trails overlap so heavily that listing both adds noise (e.g. DTE). With both `show_routes` and `show_trails` false, the Finder and the Labels control disappear. |
| `show_direction_arrows` | No | `true` | When false, no direction arrows are placed and the toggle is hidden, even if `direction_arrows` is in `forced_visible` (this gate wins). The OSM oneway data stays on features for the finder; only the arrows are suppressed. Use for maps that should never show directional indicators. |
| `suppress_basemap_path_labels` | No | `false` | Hide path / track / footway labels from the Protomaps basemap (custom base layers unaffected). |
| `suppress_basemap_pois` | No | `false` | Hide POI labels and `place=locality` labels (neighbourhoods, clearings, hamlets) from the Protomaps basemap. Higher-tier place labels stay visible. Custom base layers unaffected. |
| `suppress_basemap_oneway_arrows` | No | `false` | Hide the Protomaps basemap's one-way direction arrows (the `roads_oneway` layer) — the arrows it stamps on any `oneway=yes` road or path. Independent of `show_direction_arrows`, which governs the framework's own trail arrows. Custom base layers unaffected. |
| `show_route_distance` | No | `false` | When true, computes per-route distance at build time and shows it in the Finder rows and highlight chip. Units follow `distance_units`. |
| `show_route_elevation` | No | `false` | When true, samples USGS 3DEP at build time for per-route gain and loss. US only. See [`elevation.md`](elevation.md) for the accuracy caveats and why it won't match a phone or GPS. |
| `poi_proximity_m` | No | `50` | Maximum distance (m) from a visible trail at which a feature or trail-marker POI renders. Tight (~10m) keeps only on-trail POIs; loose (~75m+) admits nearby attractions but risks bbox-incidental ones. The Features toggle auto-hides when no feature POI qualifies. |

### Per-route style overrides

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `color_by` | No | `"relation"` | How trail lines are coloured: `"relation"` (by route colour from OSM `colour` tag, optionally overridden via `relation_colors`) or `"trail"` (by per-trail IMBA `mtb:scale:imba` rating, using the fixed IMBA palette: `relation_colors` does not apply to trail polylines in this mode). |
| `default_trail_color` | No | `"#808080"` | Fallback trail colour. In `relation` mode: used when a relation has no OSM `colour` tag. In `trail` mode: used for segments with no `mtb:scale:imba` tag. Accepts a CSS colour string or an object with `color`, `pattern` (dash array), and `cap` (`"round"` or `"square"`) for dashed uncoloured trails. |
| `dashed_relations` | No | `{}` | Map of relation ID to dash config. See [Dash patterns](#dash-patterns). |
| `relation_colors` | No | `{}` | Map of relation ID to CSS colour (hex, named, `rgb()`, `rgba()`, `hsl()`); overrides OSM `colour` tag. Only takes effect on trail polylines when `color_by: relation` (the default); under `color_by: trail` the override is used only for the route swatch in the finder. |

### Direction schedules

See [Direction arrows](#direction-arrows) for the full model.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `direction_schedule` | No | `{}` | When and how arrows flip 180° (day-of-week / date-parity). One hierarchical key: top-level `reverse_days:` is the system-wide schedule applied to every route; nested `per_route:` is a dict of per-relation overrides keyed by OSM relation ID. **Required** (system-wide or per-route) for any way tagged `oneway=reversible` to render. See [Direction schedules](#direction-schedules-day-of-week--date-parity-reversal) for the full schema and examples. |

### Display

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `default_visible` | No | `[]` | First-visit visibility for layer toggles. Three accepted forms: omitted / empty list (everything off; riders opt in via Options); `"all"` (every supported layer on); list of layer names (only those layers on). Valid layer names: `parking`, `trailheads`, `hubs`, `features`, `trail_markers`, `toilets`, `drinking_water`, `difficulty`, `emergency`, `direction_arrows`. Once a rider toggles a layer in Options, their preference persists per-map in `localStorage` and overrides the default on subsequent visits. **Safety note:** maps with one-way trails should normally include `direction_arrows` (or use `"all"`) or list it in `forced_visible`; the build prints a warning if one-way trails exist but `direction_arrows` isn't in either list. |
| `forced_visible` | No | `[]` | Layers rendered ON regardless of `localStorage` or `default_visible`, with their toggle hidden so the rider cannot turn them off. Same forms and layer names as `default_visible`. Use for safety-critical layers (`direction_arrows` on flow trails) or any layer that must always show. Subordinate to the `show_*` gates: a layer suppressed by `show_X: false`, or with no data, has nothing to force on. |
| `default_labels` | No | `"none"` | Initial label mode for first-visit riders: `"routes"` (route names), `"trails"` (trail names), or `"none"`. Defaults to `"none"` so a fresh visit produces a clean map with the rider opting into labels via the Labels segmented control. The in-UI select reflects `show_routes` / `show_trails`; options for hidden categories are removed. |
| `forced_labels` | No | _(unset)_ | Locks the label mode to `"routes"`, `"trails"`, or `"none"` and hides the Labels control, ignoring any persisted preference. Distinct from `default_labels`, which only seeds the initial value. Rejected at build time if it names a hidden category (`"routes"` with `show_routes: false`, or `"trails"` with `show_trails: false`). |
| `default_color_scheme` | No | `"light"` | First-visit colour scheme: `"light"`, `"dark"`, or `"auto"` (follows the rider's OS `prefers-color-scheme`). Riders override via the Options Appearance control; the choice persists per-map. The correct scheme is applied before first paint, so there is no light-to-dark flash. The Protomaps basemap, trail labels, direction arrows, and POI shadows have per-scheme variants; trail line colours are scheme-independent. |
| `invert_logo_dark` | No | `true` | Whether the brand logo auto-inverts in dark mode. The default suits monochrome and limited-palette logos; set `false` when the logo is colourful or photographic and inverting it looks wrong. |
| `map_dim_on_highlight` | No | `true` | When a route or trail is highlighted (via Finder tap or in-map click), dim every non-highlighted route / trail. Name labels stay visible so connecting trails can still be read for wayfinding. Set `false` to keep the rest of the network at full saturation. |
| `scrim_opacity` | No | `0.40` | Opacity (0–1) of the dark scrim used both for the in-map spotlight wash while a route / trail is highlighted (only when `map_dim_on_highlight` is `true`) **and** for the Search / Options / About menu backdrops. One value so the wash and the menus share a consistent density as the rider moves between them. Lower keeps more of the map legible; higher is a stronger dim. |
| `highlight_glow` | No | `true` | Draw a soft amber selection glow beneath the highlighted ribbon so the selected route / trail — dark ones included — reads as "selected" at a glance. Set `false` for the plain outline + stroke ribbon with no glow. |
| `url_hash` | No | `false` | When `true`, write `#zoom/lat/lon` to the URL hash as the rider pans / zooms, and honour any hash on page load: enables shareable deep-links and reload-preserved position. Default `false` drops the hash entirely. See [Privacy](#privacy) for the trade-off. |
| `distance_units` | No | `"mi"` | Units for **all** distance and elevation values. `"mi"`: miles for distance, feet for elevation gain. `"km"`: kilometres for distance, metres for elevation gain. Affects render-time formatting only. |
| `share_button` | No | `true` | Show the **Share this view** row in the Options overlay. It captures the current view and any active highlight — a route, a trail, or a place (a single POI, a name group, or a whole POI category) selected from the Finder — as a deep-link URL and offers it via the native share sheet (mobile) or clipboard (desktop); opening the link restores the view and the highlight. Works regardless of `url_hash`. Set `false` to remove the row for private or family maps. Open Graph and Twitter Card meta tags are emitted regardless, so shared links still preview well. |

### Marker and accent colours

Each POI type follows the same three-knob pattern: fill colour, glyph or dot
colour, halo or ring colour. Values flow to CSS custom properties on `:root` so
the Options swatch, the on-map marker, and any popup badge all read the same
hex: one source of truth per colour. The accent colour works differently: one
base colour resolves into a per-mode light / dark palette (see the `accent_color`
row).

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `marker_color` | No | `"#795548"` | Trail-marker chip fill (merged guideposts + emergency-access points). |
| `marker_text_color` | No | `"white"` | Trail-marker glyph (`ref` / `name` / fallback `#`) colour. |
| `marker_border_color` | No | `"white"` | Trail-marker outer halo border colour. |
| `parking_color` | No | `"#2980b9"` | Parking chip fill. |
| `parking_text_color` | No | `"white"` | Parking glyph (`P`) colour. |
| `parking_border_color` | No | `"white"` | Parking outer halo border colour. |
| `trailhead_color` | No | `"#27ae60"` | Trailhead chip fill. |
| `trailhead_text_color` | No | `"white"` | Trailhead glyph (`TH`) colour. |
| `trailhead_border_color` | No | `"white"` | Trailhead outer halo border colour. |
| `hub_color` | No | `"#f39c12"` | Trail-hub hexagonal chip fill. Default amber/orange chosen to read distinctly from trailhead green, parking blue, and feature purple. |
| `hub_text_color` | No | `"white"` | Trail-hub glyph (`H`) colour. |
| `hub_border_color` | No | `"white"` | Trail-hub outer halo border colour. Rendered as a CSS drop-shadow rather than a CSS border so the halo follows the hexagonal silhouette. |
| `feature_color` | No | `"#8e44ad"` | Feature marker inner dot colour. |
| `feature_ring_color` | No | `"#ffffff"` | Feature marker outer ring colour. |
| `accent_color` | No | `"#1D6FA5"` | UI accent colour: active toggle pill, search input focus ring, link colour, FAB pressed state, segmented-control active fill, etc. From one base colour the build derives a per-mode palette: a deep light-mode shade and a lightened dark-mode shade, each paired with its own text colour (white or near-black, whichever contrasts more), so the accent stays legible in both schemes. `style.css` selects the active pair by `data-color-scheme`. Three accepted forms: omitted (framework default `#1D6FA5`); a 6-digit hex (e.g. `"#FF5733"`), used verbatim as the light shade with the dark shade derived from it, so light mode is unchanged; or the literal `"auto"`, which derives the base from the logo via Pillow (most common saturated colour, cached per source hash), then deepens and saturates it for a vivid light shade and lightens it to clear WCAG AA against the `#1c1c1e` dark sheet. SVG-only logos fall back to the `icon:` raster as the derive source; if neither is raster, `"auto"` falls back to the default. For a curator-chosen accent (explicit hex or successful `"auto"`), the build warns when the light shade fails AA against the white sheet or the dark shade fails AA against the dark sheet (the links / focus-rings role); the on-accent text colour is chosen for contrast automatically and is not part of that check. |

### Base layers

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `base_layers` | No | `[]` | Additional raster base layers. When empty, the basemap selector is hidden in the UI; when populated, the selector appears with "Default" (Protomaps light) and each configured layer. See [Base layers (full guide)](#base-layers-full-guide). |

### Branding

See [Logo and icon assets](#logo-and-icon-assets) for rendering specifics.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `logo` | No | : | Path (config-folder-relative) to logo image (any web format: PNG, WebP, JPEG). Resampled at build time to fit a 200x80 px box (map overlay) and a 140x56 px box (About modal). If omitted, the `icon:` source is used as the logo automatically. |
| `icon` | No | : | Path (config-folder-relative) to source image (PNG / WebP, at least 256 px on the longer side) for automatic icon + PWA-manifest generation. Any aspect ratio works: non-square sources are auto-padded to square (centered, transparent background). If omitted, the `logo:` source is used as the icon source automatically (so most maps only need to set one of the two). |

### User-supplied points

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `trailheads` | No | `[]` | List of trailhead locations. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `parking` | No | `[]` | List of parking locations. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `hubs` | No | `[]` | List of trail-hub locations: named on-trail intersections riders use as wayfinding landmarks ("meet me at Bottle Junction"). Distinct POI type from trailheads. See [Trailhead and parking entries](#trailhead-and-parking-entries). |

### PWA

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `pwa` | No | `true` | Enable PWA support (service worker, offline caching, install row). When false, no service worker or install UI is generated. Vendor libraries are always bundled locally regardless of this setting. See [Deployment](deployment.md#pwa-and-offline-support). |
| `pwa_install_prompt` | No | `true` | When `true` (the default), surface PWA install affordances on platforms that support them. On Chrome / Android: the page does **not** call `preventDefault()` on `beforeinstallprompt` so Chrome's native mini-infobar appears, AND the custom Install action row (in the Options overlay, above About) is visible alongside it as a persistent fallback for second-visit installs. On iOS Safari: the Install action row opens manual Add-to-Home-Screen instructions. Set `false` to suppress install promotion entirely (no `beforeinstallprompt` handler is registered, the custom Install row is hidden everywhere). Use `false` for personal / family maps where install nagging would be unwanted. Requires `pwa: true`. |

### About modal

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `about` | No | none | Object with optional `description`, `curator`, `links` keys. See [About this map block](#about-this-map-block). |

### Welcome modal

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `welcome` | No | framework default | First-visit modal. Three forms: omit (default controls hint), `false` (suppress entirely), or a dict with optional `title` / `body` (plain-text, paragraphs separated by blank lines) / `show_controls_hint` (default `true`). Dismissal persists per-map in `localStorage`. |

### Output

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `output_dir` | No | `build/<slug>` | Custom output directory path. |

## Route buckets

Every route in the map carries three independent boolean flags:

- `summer`: rideable without snow.
- `winter`: rideable / groomed only with snow (fatbike, snowshoe).
- `emergency`: a service, rescue, or access route.

**The flags are not mutually exclusive.** A single route can sit in one bucket,
two, or all three. The canonical example: a Snow Bike Route groomed for fatbikes
in winter AND rideable on knobbies in summer would carry
`summer=true, winter=true`, so it stays visible when you switch from Summer to
Winter mode and back.

### UI behaviour

- **Summer / Winter is a mode switch.** The Options overlay has a segmented
  Season control with two options. The app is in one mode at a time. Summer mode
  renders routes with `summer: true`; Winter mode renders routes with
  `winter: true`.
- **Emergency is an additive overlay.** A separate Emergency Access Routes
  toggle (also in Options) adds routes with `emergency: true` on top of whatever
  mode is currently active, without changing the mode. Toggling Emergency off
  hides those routes again but leaves the mode untouched.
- **First-visit default is Summer.** The rider's explicit choice persists in
  localStorage (`mtb.seasonMode`, `mtb.emergencyOn`) and restores on subsequent
  visits. No month-based auto-detection.

### How the flags are computed

Each route's three flags come from its OSM `seasonal` tag plus the three
additive config lists:

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

The rule to remember: **being categorised Winter or Emergency removes a route
from Summer by default.** `summer_relations` is the opt-back-in list for
year-round routes that would otherwise sit only in Winter or Emergency.

Custom routes carry their three flags inline as YAML booleans and follow the
same rules.

## Custom routes (full guide)

Some routes can't live in OSM: race courses, event loops, demo routes, or any
transient route that doesn't match permanent signed infrastructure on the
ground. The framework supports these as first-class citizens via the
`custom_routes` config key.

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

- **`id`** is a string. It must be unique within the map and must not collide
  with any OSM relation ID in the config (`relations`, `clipped_relations`,
  `winter_relations`, etc.). Best practice: use a hyphenated slug like
  `race-2025` or `demo-loop` so it's visually distinct from the numeric OSM ids.
- **`color`** overrides any `relation_colors` / `default_trail_color` lookup for
  this route. It also becomes the swatch colour in the finder and the glow
  colour when the route is highlighted.
- **`summer`, `winter`, `emergency`** are three independent booleans: same
  semantics as OSM routes. Any combination is valid; at least one must be true
  (a custom route that's invisible in all modes is rejected at validation time).
  Defaults if all three are omitted:
  `summer: true, winter: false, emergency: false`.
- **`geometry`** is a path to a GeoJSON file, resolved relative to the config's
  directory (`configs/<slug>/`). A bare filename like `race-2025.geojson` picks
  up the file sitting next to the YAML. Absolute paths are passed through
  unchanged. The file must be a FeatureCollection or a single Feature,
  containing LineString or MultiLineString geometry only. Points, Polygons, and
  other geometry types are rejected with a clear error.
- **`trail_name_field`** is optional. If the GeoJSON features carry a
  per-feature property whose value should become the trail name (so individual
  named segments of the custom route show up in the trail finder), name that
  property here. If omitted, features pass through with no per-segment trail
  name.

### Generating custom-route GeoJSON

The `geometry` file is a plain GeoJSON FeatureCollection or Feature containing
LineString / MultiLineString geometry. Common sources:

- **Hand-drawn**: [geojson.io](https://geojson.io). Draw the route in a browser,
  export GeoJSON.
- **GPS recording (GPX)**: `gpsbabel -i gpx -f ride.gpx -o geojson -F
  ride.geojson`, or `ogr2ogr -f GeoJSON ride.geojson ride.gpx tracks`.
- **OSM XML extract (.osm)**: `ogr2ogr -f GeoJSON route.geojson route.osm
  lines`. Useful if you've drafted the route in JOSM without uploading to OSM.
- **QGIS**: draw / edit a LineString layer and export as GeoJSON (EPSG:4326).

If your GeoJSON features carry a `name` (or similar) property per segment, add
`trail_name_field: name` in the config so those segments appear in the trail
finder alongside OSM-sourced trails.

### Participation in buckets, the finder, and highlights

Custom routes are indistinguishable from OSM routes in every runtime behaviour:

- They follow the bucket model via their inline `summer` / `winter` /
  `emergency` flags.
- They appear in the **Routes** section of the finder (filtered to
  currently-visible routes just like OSM routes).
- Tapping a custom-route row in the finder highlights the whole route in its own
  colour (same as any OSM route).
- If `trail_name_field` points at per-segment names, those trails also appear in
  the **Trails** section of the finder and can be highlighted individually.

## Trail finder

The Search overlay (opened via the Search FAB) contains a combined routes /
trails / places finder with one search input, type-filter chips, and sectioned
results:

```
[mdi:magnify] Search routes & trails

ROUTES
  [swatch]  Blue Loop              12 mi
  [swatch]  Red Loop                8 mi
  [swatch]  Race 2025              20 mi

TRAILS
            Alpine Traverse        Blue
            Bear Creek             Blue, Red
            Birch Hollow           Red
```

- **One scrollable list, two section headers.** Routes on top, trails below.
- **Single search input** filters both sections (case-insensitive substring
  match against route names and trail names).
- **The list always mirrors what's currently visible on the map.** In Summer
  mode with Emergency off, you see summer routes and the trails that belong to
  them. Toggle Emergency on and emergency routes / trails appear in the list.
  Switch to Winter mode and the list live-refilters to winter content.
- **Route rows** show a coloured swatch in the route's own colour plus the name.
  OSM and custom routes appear together, indistinguishable in behaviour.
- **Trail rows** show the trail name and the parent route(s) underneath.
- **Tapping a row** highlights it on the map (glow + stroke in the route's own
  colour for routes, or amber for trails), pans / zooms to its extent, collapses
  the sheet, and shows a floating chip at the top of the map. Tap the chip to
  clear.
- **One thing highlighted at a time.** Picking a new row replaces the previous
  highlight. Everything else stays visible: the highlight is purely additive
  emphasis.

## Trail difficulty

When `show_difficulty: true`, the map displays IMBA trail difficulty rating
symbols along trail segments. Ratings are read from the `mtb:scale:imba` OSM tag
on individual ways. Segments without this tag show no symbols.

| Rating | Symbol | Colour |
|--------|--------|-------|
| 0 | Circle | White (easiest) |
| 1 | Circle | Green (easy) |
| 2 | Square | Blue (intermediate) |
| 3 | Diamond | Black (difficult) |
| 4 | Double diamond | Black (expert) |
| 5 | Double diamond | Orange (pro) |

The symbols use ski-trail-style iconography, are always oriented vertically (not
rotated to follow the trail line), and include a white halo for visibility
against any background. The Difficulty toggle appears under "What to show" in
the Options overlay; first-visit visibility is controlled by `default_visible`
(include `difficulty` to default on; otherwise off), and the rider's choice
persists in localStorage (`mtb.difficulty`). If no trail in the map carries an
`mtb:scale:imba` value, the toggle is hidden entirely, since there is nothing to
display.

Difficulty symbols only appear on segments where the trail casing is visible:
hiding a trail (e.g. by switching season) also hides its difficulty symbols.

### Difficulty is per-trail (way), not per-route (relation)

The `mtb:scale:imba` tag is read from individual **ways** only. Tags on the
parent **relation** are ignored, including for `color_by: trail` colouring.

This matches OpenStreetMap's tagging convention (`mtb:scale:imba` is a per-way
tag) and reflects the reality that real trails often vary in difficulty along
their length:

| Way tag | Relation tag | What renders |
|---|---|---|
| `4` | `2` | Black diamond (4): way value wins |
| (none) | `2` | Nothing: segment is unrated; in `color_by: trail` it falls back to `default_trail_color` |
| `4` | (none) | Black diamond (4) |

## Direction arrows

The map renders direction-of-travel arrows along ways tagged with either
`oneway:bicycle=*` or `oneway=*` in OpenStreetMap. Two-way ways get no arrows.
Arrows scale with zoom, follow the line's bearing (rotate with the map, not the
screen), and are always on. They're sized to read as a subtle directional cue
rather than compete with the trail casing.

Arrow rendering is **per-way**, not per-route, the same model as IMBA
difficulty. The tag is read from individual ways; relations themselves don't
have a direction, so they don't carry the tag.

**Tag resolution: `oneway:bicycle` wins over `oneway`.** This matters on trails
that ride one-way for bikes but allow foot traffic both ways: the
bicycle-specific tag describes the rule that matters, and a bare `oneway=*`
(often inherited from non-bike use) is the fallback. Either tag takes the same
accepted values:

| Tag value | What renders |
|---|---|
| `yes` | Arrows along the way's digitised direction |
| `-1` | Arrows along the reverse direction (normalised at build time) |
| `reversible` | Arrows that **must** flip on a schedule (see below) |
| `no` or absent | No arrows |

So a way tagged `oneway:bicycle=yes` renders forward arrows even if its generic
`oneway` is unset; `oneway:bicycle=no` suppresses arrows even if `oneway=yes`
exists; and `oneway:bicycle=reversible` requires a schedule the same way bare
`oneway=reversible` does.

### Show / hide on a per-map basis

Three keys interact, from outermost to innermost:

1. `show_direction_arrows: false` suppresses arrows entirely: none are placed,
   the toggle is hidden, and the rider cannot surface them. The OSM oneway tags
   stay on features for the finder; only the arrows are suppressed. Use for maps
   that should never show directional indicators.
2. `forced_visible: [direction_arrows]` forces arrows always-on and hides the
   toggle. Subordinate to `show_direction_arrows`: if arrows are suppressed there
   is nothing to force on. Use for safety-critical maps where wrong-way travel on
   flow trails would be dangerous.
3. `default_visible` controls the toggle's initial state when neither list above
   names `direction_arrows`. Include it (or use `default_visible: all`) for
   arrows ON at first visit; omit it for OFF. The rider can flip the toggle, and
   that choice persists.

The default behaviour (no key set) is: arrows allowed, toggle in Options,
initial state from `default_visible`.

The toggle row also hides when the map has no oneway-tagged trail features, since
no arrows would render anyway. This is a runtime
gate based on the trails data, not a curator-controlled key; it kicks in
regardless of `default_visible` or `forced_visible`.

### Direction schedules (day-of-week / date-parity reversal)

Some trail systems are signed as one-way with the direction alternating by day
of week (e.g. clockwise Mon/Wed/Fri, counter-clockwise Tue/Thu/Sat) or by
calendar-date parity (one direction on even days, the other on odd). OSM has no
canonical schema for the schedule itself. The framework supplies it via the
single `direction_schedule:` key, which has two optional parts: a top-level
`reverse_days:` for the system-wide default, and a nested `per_route:` block for
per-relation overrides.

**System-wide schedule.** The common case for parks where every route
alternates on the same days:

```yaml
direction_schedule:
  reverse_days: [tuesday, thursday, saturday]
```

Every route in the map adopts that schedule.

**Per-route overrides.** Set entries only for routes that differ from the
system-wide schedule. An empty `reverse_days: []` opts a specific route out:

```yaml
direction_schedule:
  reverse_days: [tuesday, thursday, saturday]   # default for every route
  per_route:
    12425503:
      reverse_days: [sunday]                    # this route uses a different schedule
    98765432:
      reverse_days: []                          # this route opts out
```

**Per-route only.** Set just the `per_route:` block when there is no system-wide
default and only specific routes have schedules:

```yaml
direction_schedule:
  per_route:
    12425503:
      reverse_days: [tuesday, thursday, saturday]
```

**Super-relation overrides.** A `per_route:` key whose ID is a super-relation
fans out to every child route, useful for multi-system maps where a whole second
trail system should not reverse:

```yaml
relations:
  - 12345678                          # primary super-relation
  - 99999999                          # super-relation for "the system across town"

direction_schedule:
  reverse_days: [tuesday, thursday, saturday]   # default
  per_route:
    99999999:
      reverse_days: []                # whole second system opts out
    87654321:
      reverse_days: [tuesday]         # one specific child of 99999999 still reverses
```

Rules:

- Keys under `per_route:` are OSM relation IDs. Each may be a leaf route
  relation OR a super-relation; super-relations are auto-expanded to their child
  routes the same way as `relations`.
- The relation is purely a grouping handle for "the ways under this relation
  share this schedule". Relations themselves don't have direction.
- `reverse_days` lists day tokens (case-insensitive; `monday`, `Mon`, and `mo`
  all parse). Two parity tokens are also accepted: `even_days` matches even
  calendar dates and `odd_days` matches odd dates. Weekday and parity tokens can
  coexist in one list, and any match triggers reversal.
- A `per_route` entry always wins over the top-level system-wide
  `reverse_days:`. An entry with `reverse_days: []` is the way to opt one route
  out of the default. **An explicit per-child entry always wins over a
  super-relation entry** that would otherwise fan out to that child.
- On a reverse day, any way whose resolved oneway value (see
  [Tag resolution](#direction-arrows): `oneway:bicycle` first, `oneway` as
  fallback) is `yes`, `-1`, or `reversible`, and that belongs to a relation whose
  schedule lists today, has its arrows rotated 180 degrees.
- Setting a schedule does **not** make untagged ways one-way; OSM tagging still
  controls which ways get arrows. The schedule is purely a rotation hook.

### `reversible` is required to pair with a schedule

A `reversible` resolved oneway value (from either `oneway:bicycle=
reversible` or bare `oneway=reversible`) means "the direction is alternating per
ground signage". A way with this tag and no schedule cannot be rendered
correctly (the build would silently pick OSM digitisation order, which would be
wrong half the time). The build therefore **fails** if any way resolves to
`reversible` and no schedule (via the top-level
`direction_schedule.reverse_days` or a `direction_schedule.per_route` entry on a
parent relation) covers it. The error lists each offending way with a clickable
OSM URL and its parent relation IDs so you can pick where to attach the
schedule.

By contrast, `yes` and `-1` resolved values have an inherent direction in OSM,
so a schedule for them is optional. Without one they render statically forward;
with one they flip on the configured days.

The day-of-week and calendar date are read from the visitor's local clock at
page load and rechecked every 5 minutes (so a tab left open across midnight
stays correct).

## Dash patterns

The `dashed_relations` config supports two formats.

**Simple format**: a `[dash, gap]` array with values in line-width multiples:

```yaml
dashed_relations:
  13213211: [0, 2]  # dots
  55555555: [4, 2]  # long dashes
```

Common patterns: `[0, 2]` dots, `[2, 2]` short dashes, `[4, 2]` long dashes,
`[6, 2]` extra-long dashes.

**Object format**: for more control over cap style and colours:

```yaml
dashed_relations:
  13213211:
    pattern: [4, 2]                  # dash pattern (required)
    cap: square                      # "round" (default) or "square" line ends
    colors: ["#000000", "#FF0000"]   # two-colour alternating dashes
```

| Key | Required | Default | Description |
|---|---|---|---|
| `pattern` | Yes | : | `[dash, gap]` in line-width multiples |
| `cap` | No | `"round"` | Line cap style: `"round"` or `"square"` |
| `colors` | No | : | One or two CSS colours. One colour overrides the route's normal colour; two colours produce alternating dash colours (see below). |

Both formats can be mixed in the same config. Dashed relations are rendered
without line offsets (centred on the geometry) to avoid oval distortion on
curves.

### Alternating-colour dashes

Two colours in `colors` produce dashes of colour A interleaved with colour B
along the trail, useful for trails that share signage from two routes, hazard
stripes, emergency-access markings, or any case where one solid colour isn't
enough.

Colour A is the dash and colour B is what shows in the gap, so pattern sizing
controls the visual proportion directly:

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
| `[4, 0]` | Solid black line: no gap means colour B never shows |

Pattern values are in line-width multiples, so the absolute size scales
naturally with zoom.

**Cap style interacts with the effect.** With `cap: round` (the default) the
dashes have semicircular ends that bulge slightly past the dash bounds and
visually soften the boundary between A and B. With `cap: square` (or `butt`) the
boundaries are crisp, generally what you want for an obvious alternating look:

```yaml
dashed_relations:
  12345678:
    pattern: [4, 4]
    colors: ["#000000", "#ffffff"]   # crisp black/white hazard stripe
    cap: square
```

**One colour vs. two.** A single-element `colors: ["#ff0000"]` overrides the
route's colour entirely (equivalent to setting `relation_colors`) and applies
dashes from `pattern`. Two elements activates the alternating-colour path. Three
or more colours are not supported; extra entries are ignored.

**Interaction with other features.** Alternating-colour dashes work with
direction arrows, labels, and the route visibility rules exactly like any other
dashed route. They are *not* compatible with `color_by: trail`: when difficulty
colouring is active, IMBA-rated segments use the difficulty palette and `colors`
is ignored on those segments.

## Trailhead and parking entries

### Trailheads

Trailheads are shown as green "TH" markers by default (configurable via
`trailhead_color`, `trailhead_text_color`, `trailhead_border_color`). Each entry
supports:

| Key | Required | Description |
|---|---|---|
| `name` | Yes | Display name shown in popup |
| `coordinates` | Yes | `[longitude, latitude]` |
| `directions_url` | No | Custom directions URL; if omitted, auto-generates based on browser |

**Tip:** If a trailhead has parking, use a single trailhead entry with a
`directions_url` rather than adding both a trailhead and a parking entry at the
same location. Use separate parking entries only for lots that aren't at a
trailhead (e.g. overflow parking down the road).

### Trail Hubs

Trail Hubs are *named on-trail intersections*: landmarks riders use for
wayfinding mid-ride ("meet me at the Saddle", "turn right at Bottle
Junction"). Distinct POI type from Trailheads:

- **Visual:** hexagonal "H" chip with the hub's name rendered as a
  permanent inline label below the chip. The hexagonal silhouette reads
  differently from the square TH and P chips at a glance, so riders can
  tell hubs apart even without reading the letter. Default amber/orange
  fill (configurable via `hub_color`, `hub_text_color`, `hub_border_color`).
- **No popup, no directions link.** Riders can't drive to a hub, so a
  "Get Directions" link would only mislead them into routing toward a forest
  junction. The inline name is the entire signal a rider needs.
- **Options toggle:** independent from Trailheads (`Hubs`). Auto-hides
  when no hubs are configured for the map. First-visit visibility is
  controlled by `default_visible` (include `hubs` to default on); the
  rider's choice persists in localStorage (`mtb.poi.hubs`).
- **Search integration:** hubs appear in the Search overlay's POI scope
  alongside trailheads and parking; tap a result to pan and ring-pulse the
  marker (no popup, since there is none).

Each entry supports:

| Key | Required | Description |
|---|---|---|
| `name` | Yes | Display name shown inline under the on-map chip and in search results |
| `coordinates` | Yes | `[longitude, latitude]` |

Example:

```yaml
hubs:
  - name: "Bottle Junction"
    coordinates: [-87.500, 46.510]
  - name: "The Saddle"
    coordinates: [-87.495, 46.515]
```

### Parking

Parking areas are shown as blue "P" markers by default (configurable via
`parking_color`, `parking_text_color`, `parking_border_color`). Each entry
supports:

| Key | Required | Description |
|---|---|---|
| `name` | Yes | Display name shown in popup |
| `coordinates` | Yes | `[longitude, latitude]` |
| `directions_url` | No | Custom directions URL; if omitted, auto-generates a link based on browser (see below) |

When `directions_url` is omitted, the app auto-detects the browser and generates
the appropriate link:

| Browser | UA identifier | Result |
|---|---|---|
| Safari (any platform) | `Safari` | Apple Maps |
| Chrome on iOS | `CriOS` | Google Maps |
| Firefox on iOS | `FxiOS` | Google Maps |
| Chrome on desktop | `Chrome` | Google Maps |
| Edge | `Edg` | Google Maps |
| Firefox on desktop | `Firefox` | Google Maps |
| Opera | `OPR` | Google Maps |

## About this map block

The Options overlay includes an **About this map** action row (below Share and
Install when those are visible). Tapping it opens a modal with information about
the map. The modal header shows the map **title** on the left and, when `logo:`
(or `icon:` as fallback) is configured, the brand **logo** on the right. The
`about` YAML block is optional; when omitted, the modal still renders the
always-on "Built with" section (data + build dates, framework credits).

```yaml
about:
  description: |
    Free-form multi-line description. Line breaks in a YAML `|` block are
    preserved in the modal.
  curator:
    name: "Your Name"
    url: "https://yoursite.example.com"
  links:
    - label: "Trail Association"
      url: "https://example.org"
    - label: "Trail Conditions"
      url: "https://example.org/conditions"
    - label: "Source on GitHub"
      url: "https://github.com/you/your-fork"
```

| Key | Required | Description |
|---|---|---|
| `description` | No | Paragraph of prose shown near the top of the modal. `\|`-style multi-line blocks preserve newlines. |
| `curator` | No | Single `{name, url}` object rendered under a "Map Curator" header. The name appears on the next line, as a hyperlink when `url` is set and plain text otherwise. |
| `links` | No | List of `{label, url}` entries rendered as a bulleted list under a "More info" header: trail-system pages, club pages, a source repo, and so on. YAML order is the render order. |

The modal also always shows a **"Built with"** section at the bottom:

- A framework credit line ("Generated by [trailmaps.app](https://trailmaps.app)
  Map Generator.").
- `Trail data: <date HH:MM>` (from the trails file mtime / `_data_date`) and
  `App built: <date HH:MM>` (set at build time). Both timestamps use the build
  machine's local time.
- One credit line per data source and library: OSM, Protomaps, Material Design
  Icons, MapLibre GL JS, and SIL Open Font License always; Mapterhorn when
  terrain is enabled; USGS 3DEP when route elevation is computed and shown. See
  the framework-level credit list in [`README.md`](../README.md#credits).

Any curator section whose source data is absent is omitted entirely.

## Base layers (full guide)

Custom raster tile layers appear in the basemap selector alongside the default
Protomaps light basemap. When no base layers are configured, the selector is
hidden entirely. Each entry supports:

| Key | Required | Default | Description |
|---|---|---|---|
| `id` | Yes | : | Unique identifier (used internally) |
| `name` | Yes | : | Display name in the basemap dropdown |
| `url` | Yes | : | Tile URL template with `{z}`, `{x}`, `{y}` placeholders |
| `attribution` | No | `""` | HTML attribution string |
| `tile_size` | No | `256` | Tile size in pixels |
| `max_zoom` | No | : | Maximum zoom level for the tile source |
| `headers` | No | : | Map of HTTP headers for authenticated tile requests |

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

When a custom layer is selected, the Protomaps vector basemap is replaced with
the raster tile layer. Trail overlays, hillshade, and all interactive features
continue to work on top of the raster basemap.

## Logo and icon assets

The framework uses two separate image assets configured via `logo` and `icon`.
They serve different purposes and have different requirements.

### Logo (`logo`)

The logo is displayed as an overlay in the bottom-left corner of the map and at
the top-right of the **About this map** modal header. At build time the
framework opens the source with Pillow, picks the binding axis from the source's
aspect ratio, resamples to ~2x the display size with LANCZOS for retina
sharpness, and writes a single normalised `logo.webp` into the output. Source
files can be PNG, WebP, JPEG, or any format Pillow can open; SVGs are not
currently rasterised and should be pre-converted.

| Property | Detail |
|---|---|
| **Purpose** | Map overlay branding; also shown in the About modal header |
| **Map overlay render** | Inside a 200x80 px bounding box on desktop, 150x60 px on mobile |
| **About modal render** | Inside a 140x56 px box on desktop, 100x40 px on mobile |
| **Binding axis** | Wide wordmarks (aspect at least 2.5:1) land at 200 px wide; square or tall logos land at 80 px tall |
| **Pre-resize target** | Source is resampled to ~2x the render size (max longer side ~400 px on desktop). Never upscaled; smaller sources are preserved. |
| **Recommended source** | At least 2x the expected render size on its long side (e.g. 400+ px wide for a wordmark); higher is fine, the framework resizes down |
| **Recommended format** | Any Pillow-readable raster format; output is always WebP |
| **Transparency** | Supported: the logo floats over the map with a subtle drop shadow |

The logo can be any shape: the bounding-box render handles wordmarks, square
badges, and tall marks cleanly. Wide horizontal wordmarks produce the most
brand-prominent result in the lower-left overlay.

#### Colour guidance

The logo is displayed on top of the map. For best legibility across varying
terrain colours, use dark artwork on a transparent background (e.g. a black or
near-black wordmark). Multi-colour or photographic logos work too, but design
them with a built-in outline or soft background if you need to ensure contrast
against busy map areas.

If `logo` is omitted but `icon` is set, the icon source is used as the logo
automatically: square icons render as ~80x80 badges in the overlay and ~56x56 in
the About modal. If neither `logo` nor `icon` is set, the logo overlay is hidden
and the About modal shows only the text title in its header.

### Icon (`icon`)

The icon is a single source image used to generate all favicon and PWA icon
variants at build time. It is **also used as the logo source when `logo:` is
omitted**, so a map that only needs a single brand asset can configure `icon:`
alone.

| Property | Detail |
|---|---|
| **Purpose** | Favicons, PWA home screen icon, browser tab icon; logo fallback when `logo:` is not set |
| **Required format** | PNG or WebP |
| **Required dimensions** | At least 256 px on the longer side |
| **Recommended dimensions** | 512x512 or 1024x1024 for best quality at all sizes |
| **Aspect ratio** | Any: non-square sources are auto-padded to square (centered, transparent background) |
| **Transparency** | Supported on most platforms; the Apple touch icon variant is composited onto a white background since iOS does not support transparent home screen icons |

The build generates the following files from the source image:

| File | Size | Notes |
|---|---|---|
| `icons/apple-touch-icon.png` | 180x180 | Composited on white background for iOS |
| `icons/android-chrome-192x192.png` | 192x192 | Android home screen |
| `icons/android-chrome-256x256.png` | 256x256 | Android home screen (high-DPI) |
| `icons/android-chrome-512x512.png` | 512x512 | Android home screen (Chrome WebAPK) |
| `icons/mstile-150x150.png` | 150x150 | Windows Start tile |
| `icons/favicon-32x32.png` | 32x32 | Standard browser tab icon |
| `icons/favicon-16x16.png` | 16x16 | Small browser tab icon |
| `favicon.ico` | 16, 32, 48 | Multi-resolution ICO for legacy browsers |
| `icons/safari-pinned-tab.svg` | : | Auto-traced silhouette (requires `potrace`) |
| `icons/site.webmanifest` | : | PWA manifest with `name` and `title` from config |

If neither `icon` nor `logo` is set, all icon `<link>` tags are stripped from
the HTML output and no manifest is generated.

**Tip:** Use a simple, high-contrast design for the icon: it needs to be
recognisable at 16x16 pixels. Avoid fine text or thin lines.

## Privacy

A generated map is entirely client-side. It sets no cookies, runs no analytics,
loads no third-party scripts, and makes no network calls beyond fetching its own
static files (HTML, CSS, JS, tiles, fonts) from the server you deploy to. Nothing
a visitor does is reported anywhere.

The app stores a small set of UI preferences in the browser's `localStorage`,
each key prefixed with the map's `slug` so several maps on one origin stay
independent (for example, `<slug>.mtb.colorScheme`):

| Key | Value |
|---|---|
| `mtb.seasonMode` | `"summer"` or `"winter"` |
| `mtb.emergencyOn` | Boolean: Emergency overlay on or off |
| `mtb.poi.<kind>` | Boolean per POI category (`parking`, `trailheads`, `hubs`, `features`, `markers`, `toilets`, `drinking_water`) |
| `mtb.labels` | `"routes"`, `"trails"`, or `"none"` |
| `mtb.difficulty` | Boolean: IMBA difficulty symbols on or off |
| `mtb.directionArrows` | Boolean: direction arrows on or off |
| `mtb.colorScheme` | `"light"`, `"dark"`, or `"auto"` |
| `mtb.fabsLabeled` | Boolean: whether the on-map buttons show text labels |
| `mtb.welcomed` | Boolean: welcome modal already dismissed |

These persist a returning visitor's own choices and are never transmitted. A
visitor can clear them at any time through their browser.

`url_hash` is the one setting that changes what leaves the browser, and only when
the visitor chooses to share. With `url_hash: true` the map writes its current
`#zoom/lat/lon` to the address bar, so a copied or bookmarked URL carries that
position; the default `false` leaves the hash empty. The **Share this view**
action (see `share_button`) builds a position link on demand regardless of
`url_hash`. Neither path involves the server: the position lives only in the URL
the visitor passes along.
