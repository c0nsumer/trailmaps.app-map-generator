# Configuration

Every map is described by a single YAML file. This document is the canonical
reference for every supported key. It also holds deep dives on the features
that need more than a one-line description: custom routes, direction schedules,
route buckets, dash patterns, the About and Welcome modals, logo
and icon assets, and privacy posture.

Two starter YAML files live under `configs/reference/`:

- `reference-minimal.yaml`: the template for a new map. Section headers plus
  every supported key on a commented-out line at its default value. Copy it, set
  the required keys, and uncomment only the lines you want to change.
- `reference.yaml`: the same structure and key order with a one-line comment on
  each key, for quick in-editor lookup. This document holds the full prose.

Both files stay in identical key order, so you can diff them at any time. Use
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
  - [Marker and accent colors](#marker-and-accent-colors)
  - [Branding](#branding)
  - [User-supplied points](#user-supplied-points)
  - [About modal](#about-modal)
  - [Welcome modal](#welcome-modal)
- [Route buckets](#route-buckets)
- [Custom routes (full guide)](#custom-routes-full-guide)
- [Routes panel](#routes-panel)
- [Units](#units)
- [Trail finder](#trail-finder)
- [Trail difficulty](#trail-difficulty)
- [Direction arrows](#direction-arrows)
- [Dash patterns](#dash-patterns)
- [Trailhead and parking entries](#trailhead-and-parking-entries)
- [About this map block](#about-this-map-block)
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
one place. Copy `configs/example/` somewhere else, rename it, and you have the
scaffold for a new map.

Asset paths in the config are resolved relative to the config's directory. Bare
filenames like `logo: logo.webp` and `geometry: race-2025.geojson` pick up files
sitting next to the YAML. Absolute paths are passed through unchanged. This is
useful for shared assets kept outside the repo.

The two starter templates share one folder:

```
configs/reference/
  reference-minimal.yaml      canonical-order skeleton, most keys commented out
  reference.yaml              verbose annotated reference; same key order
```

Only source artifacts live in `configs/<slug>/`. Build-time-generated files (PWA
icons, manifests, favicons, vendor libraries) land in `build/<slug>/...` and are
never committed. The validator checks that every referenced asset file exists.
If one is missing, the build fails fast with a clear error naming the config
and the missing file.

## Quick start: minimal map config

The shortest valid config. Every key not shown takes the framework default:

```yaml
name: My Trails
slug: mytrails
relations: [12425503]
```

`name` and `slug` are always required. Beyond those, a config needs
**at least one geometry source**: `relations`, `custom_routes`, or
`event_mode.routes`. The example above uses `relations` (an OSM trail system).
A route-only map, such as a race course with no surrounding network, can
instead supply only `custom_routes` or `event_mode.routes` and omit `relations`
entirely. See [Custom routes](#custom-routes-full-guide) and
[Event mode](event-mode.md).

To start a new map, copy `configs/reference/reference-minimal.yaml` into
`configs/<your-slug>/<your-slug>.yaml`. Set the required identity keys plus a
geometry source. Drop your `logo.webp` / `icon.png` / any custom-route GeoJSONs
into the same folder, and reference them by bare filename in the config.

## Config reference

### Identity

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | Yes | : | Short name used in build logs and as the PWA icon label on mobile home screens. |
| `slug` | Yes | : | URL-safe identifier. Used for the map's config folder (`configs/<slug>/`), the build output directory (`build/<slug>/`), and the deploy destination subdirectory. Must match `[a-z0-9_-]+`. By convention it matches the folder holding the YAML; the `build_and_deploy.sh` wrapper discovers configs via the `configs/<name>/<name>.yaml` pattern. The validator does not require the slug and folder to match. |
| `title` | No | `"{name} Map"` | Full title used for the browser tab, share-card `og:title`, the in-app brand, and the PWA install dialog. When omitted, it is derived from `name`. Set it only where a curated string carries information the derivation can't (a race course, a route map). Because the derivation appends " Map", a `name` that already ends in " Map" would double it. |

### Data sources

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `relations` | Conditional | : | Non-empty list of OSM relation IDs to render as routes. **Required unless the map supplies geometry via `custom_routes` or `event_mode.routes`**; a route-only or event map can omit it. **Each entry may be a leaf route relation or a super-relation.** A super-relation is auto-expanded into its child routes one level deep at fetch time. The parent itself is dropped since it has no ways. Order doesn't matter. Multi-system maps list every entry-point relation. |
| `osm_file` | No | : | Path to local `.osm` XML file; when set, uses this instead of the Overpass API. See [Building](building.md#local-osm-file-support). |
| `clipped_relations` | No | `[]` | OSM relation IDs to include but clip to the core trail bounding box (e.g. rail trails). Super-relations are auto-expanded the same way as `relations`. A rider-facing distance for a clipped relation, or for a trail cut at the map edge, carries "shown" after the number. The tap popup instead relabels its row "Length shown:", because the shown distance is the map's window onto the trail, not the trail's full length. |
| `event_mode` | No | : | Optional event-mode block. Feature one or more routes prominently while every other trail renders as muted context. Also carries `gpx:`, downloadable course files offered via a download FAB. See [Event mode](event-mode.md) for the schema and worked examples ([GPX downloads](event-mode.md#gpx-downloads)). |

### Route buckets

See [Route buckets](#route-buckets) for how the Summer / Winter / Emergency
flags are computed from these lists plus OSM tags.

Each list below accepts either leaf route relation IDs or super-relation IDs. A
super-relation in any of these keys propagates the bucket assignment to every
child route. Listing one super-relation in `winter_relations` marks all its
children as winter without enumerating them.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `winter_relations` | No | `[]` | Relation IDs to flag `winter=true`. Use for winter-only routes not already tagged `seasonal=winter` in OSM (snowshoe relations, fatbike loops, etc.). Being in this list removes the route from Summer unless also in `summer_relations`. |
| `summer_relations` | No | `[]` | Relation IDs to flag `summer=true`. Use to re-add a route to Summer that would otherwise be pulled out of it. Examples: OSM `seasonal=winter` year-round routes like a Snow Bike Route, or emergency routes also used year-round. Overlap with `winter_relations` is how you express a route that lives in both buckets. |
| `emergency_access_relations` | No | `[]` | Relation IDs to flag `emergency=true`. Rendered only when the rider toggles the Emergency Access overlay on, regardless of season mode. |

### Custom routes

See [Custom routes (full guide)](#custom-routes-full-guide) for the complete
schema and rules.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `custom_routes` | No | `[]` | List of user-defined non-OSM routes with inline metadata (id, name, color, bucket flags) and a GeoJSON geometry file reference. |

### Map view geometry

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `bbox` | No | auto | Bounding box `[west, south, east, north]` used for the **initial view fit**. If omitted, it is auto-computed from trail geometry with a ~3% proportional buffer. |
| `pan_bbox` | No | computed | Explicit pan envelope `[west, south, east, north]`; overrides `pan_padding` when set. Usually unnecessary: use `pan_padding` unless the auto-symmetric expansion is wrong for your site (e.g. asymmetric pan room to cover a parking lot north of the trails but nothing south). |
| `pan_padding` | No | `0.5` | How much looser the pan wall is than `bbox`, as a fraction of the bbox's greater dimension added on each side. `0.5` is about 4x the pannable area; `0` pins the wall to `bbox`. Also widens basemap and terrain tile extraction to match. See the notes below. |

The zoom range is fixed. The camera runs from zoom 10 to 18. Basemap tiles are extracted up to zoom 15, and terrain tiles up to zoom 12.

#### Pan area: `bbox` vs. `pan_bbox`

`bbox` frames the trails on first paint. `pan_bbox` is the wall the rider hits
when panning; it comes from `bbox` widened by `pan_padding`, or you set it
directly. The pan wall is looser than the initial frame because the map clamps
on its center. Without the extra room, a rider zoomed in near a tight edge
would see little beyond it. Widening `pan_padding` also expands basemap and
terrain extraction to match. The edges still show real map, and the tile files
grow accordingly.

Tune `pan_padding` per map:

- `0.5` (default): room for the surrounding roads and landmarks at trail-detail
  zoom.
- `1.0`: about twice the pan room, for an access road or parking lot well
  outside the trail footprint. Roughly doubles tile size.
- `0.25` or lower: less surrounding basemap, smaller tiles.
- `0`: pins the pan wall to `bbox`.

For asymmetric room (more on one side than another), set `pan_bbox` directly
instead of `pan_padding`.

#### The basemap's path and service-road lines

The Protomaps basemap draws every path in the area as a pale line, every service road as a thin road, and every minor street (residential and unclassified) as a road. That includes the ones this map draws as routes, where the line would show beside the lanes and through dashed routes. The build replaces those lines with ones it generates from OpenStreetMap. A stretch that a route draws is hidden while that route is on. A hidden path or service road loses its name label with its line. A hidden street keeps its name label, so a route that follows a street still shows the street's name. Every path, service road, and street the map does not draw over keeps its label. A stretch drawn only by a winter or emergency route is still a plain line when that mode is off. Major roads (tertiary and up) and every other basemap feature stay exactly as Protomaps made them.

This step needs `tippecanoe` and `tile-join`, and it makes one Overpass query for the area. That query is cached like the trail data and refreshes with `--refresh` or `--refresh-trails`. For a map built from a local `.osm` file, a way that the file contains is taken from the file, so the basemap agrees with the routes where the file has been edited.

### Build-time data gates

These keys control **build-time data fetching and asset generation**. A map
that doesn't need a given data type can skip the Overpass query or sprite
generation entirely. The corresponding UI toggle is hidden automatically when
the underlying data or sprite is absent. First-visit toggle state is a separate
concern, handled by `default_visible` in the [Display](#display) section.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `show_markers` | No | `true` | When false, skips the Overpass query for trail markers (guideposts and emergency-access points, merged) and hides the Markers toggle. |
| `show_features` | No | `true` | When false, skips the Overpass query for `tourism=attraction` feature nodes. |
| `show_parking` | No | `true` | When false, parking markers from the config are not rendered. |
| `show_trailheads` | No | `true` | When false, trailhead markers from the config are not rendered. |
| `show_hubs` | No | `true` | When false, trail-hub markers from the config are not rendered. The Hubs toggle auto-hides when the map defines no hubs. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `show_toilets` | No | `true` | When false, skips the Overpass query for `amenity=toilets` nodes. The Toilets toggle auto-hides when none were found. |
| `show_drinking_water` | No | `true` | When false, skips the Overpass query for `amenity=drinking_water` nodes. The Drinking Water toggle auto-hides when none were found. |
| `show_bicycle_repair_stations` | No | `true` | When false, skips the Overpass query for `amenity=bicycle_repair_station` nodes. The Bicycle Repair toggle auto-hides when none were found. |
| `show_terrain` | No | `true` | When false, terrain tiles are not fetched, and the hillshade and contour-line layers are omitted. Contour lines are computed in the browser from the same terrain tiles as the hillshade. They are labeled in the rider's [units](#units). |
| `show_difficulty` | No | `true` | When false, no IMBA difficulty sprite is generated and no symbols appear. The toggle also auto-hides when no way carries an `mtb:scale:imba` value. First-visit state comes from `default_visible` (include `difficulty`, or use `all`); the rider's later choice persists. |
| `show_trails` | No | `true` | When false, hides the Finder's Trails section and the Trails label mode. Use where routes and trails overlap so heavily that listing both adds noise (e.g. DTE). Routes are always surfaced (a geometry source is required), so the Finder and the Labels control never disappear entirely. |
| `show_direction_arrows` | No | `true` | When false, no direction arrows are placed and the toggle is hidden. This gate wins even when `direction_arrows` is in `forced_visible`. The OSM oneway data stays on features for the finder; only the arrows are suppressed. Use for maps that should never show directional indicators. |
| `show_current_trail` | No | `true` | If on, the Options **Show Current Trail** row is shown, and it starts on. While that row and Locate are both on, a chip at the bottom of the map names the trail the rider is on, and that trail gets a blue glow. An unnamed way reads "Unnamed trail". The chip names a trail only when the fix is accurate to 25 m or better. If a coarser fix (up to 100 m) persists and a drawn trail lies inside its accuracy circle, the chip reads "Trail unknown" over "Low GPS accuracy". The rider's choice in the Options row persists per-map. Set `false` to remove the chip, the glow, and the Options row. |
| `suppress_basemap_pois` | No | `true` | Hide POI labels and `place=locality` labels (neighborhoods, clearings, hamlets) from the Protomaps basemap. Higher-tier place labels stay visible. Set `false` to show them. |
| `suppress_basemap_oneway_arrows` | No | `false` | Hide the one-way direction arrows the Protomaps basemap stamps on any `oneway=yes` road or path (its `roads_oneway` layer). Independent of `show_direction_arrows`, which governs the framework's own trail arrows. |
| `show_distance` | No | `true` | Computes distance at build time. For route-mode relations this shows per-route distance in the Finder rows and highlight chip. For difficulty-mode relations it also gates the per-rating totals in the key and the per-trail distance in the finder and the trail popup. It is shown in the rider's [units](#units). The finder's trail rows show each named trail's visible length in both color modes. The trail popup shows the same length after a finder pick. After a tap on the map, it shows the length of the tapped section of the trail. For an unnamed way, the popup shows the length of the tapped segment. Set `false` to hide every distance. |
| `poi_proximity_m` | No | `50` | Maximum distance (m) from a visible trail at which a feature or trail-marker POI renders. Tight (~10m) keeps only on-trail POIs; loose (~75m+) admits nearby attractions but risks bbox-incidental ones. The Features toggle auto-hides when no feature POI qualifies. |

### Per-route style overrides

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `color_by` | No | `"route"` | The default color mode for every relation. `"route"` colors a relation's lanes by its route color, with parallel lanes where routes share a trail. `"difficulty"` colors ways by their own IMBA `mtb:scale:imba` rating, one lane per way. `color_by_route` and `color_by_difficulty` list the exceptions. See [Color modes](#color-modes) for the keys that change meaning, are ignored, or are rejected. |
| `color_by_route` | No | `[]` | Relations drawn in route mode when `color_by` is `"difficulty"`. Takes OSM relation IDs, super-relation IDs, and custom-route IDs. A super-relation ID fans out to its child routes, as it does for `winter_relations`. A relation in both lists is an error. A relation in the list for the default mode gets a "redundant" warning. |
| `color_by_difficulty` | No | `[]` | Relations drawn in difficulty mode when `color_by` is `"route"`. Same kinds of IDs as `color_by_route`. `event_mode` is rejected while any relation is in difficulty mode. |
| `route_key` | No | `true` | Whether the bottom-right panel shows the key: one row per visible route-mode relation and one row per rating for difficulty-mode relations, each with its swatch, name and stats. Set `false` for a trail system dense enough that a full key is a wall. Then the panel is a Search button alone, with no key card and no collapsed chip. Search still finds every route (or trail) and place. |
| `default_trail_color` | No | `"#808080"` | Fallback trail color. For route-mode relations: used when a relation has no OSM `colour` tag. For difficulty-mode relations: used for ways with no `mtb:scale:imba` tag. Accepts a CSS color string or an object with `color`, `pattern` (dash array), and `cap` (`"round"`, `"square"`, or `"butt"`) for dashed uncolored trails. |
| `relation_colors` | No | `{}` | Map of relation ID to CSS color (hex, named, `rgb()`, `rgba()`, `hsl()`). On a route-mode relation it overrides the OSM `colour` tag and colors the whole route. On a difficulty-mode relation the entry is ignored, and the build warns. |
| `dashed_relations` | No | `{}` | Map of relation ID to dash config. See [Dash patterns](#dash-patterns). |
| `relation_names` | No | `{}` | Map of relation ID to display name. Overrides the OSM `name` tag everywhere the route name appears: routes panel, on-map route labels, popups, search, alphabetical panel ordering. Useful when the OSM name is formally correct but unwieldy on a map (e.g. renaming "Pontiac Lake Recreation Area Mountain Bike Trail" to "Mountain Bike Trail"). Keys must be *leaf* route relation IDs. When a super-relation is listed in `relations:`, rename its child routes rather than the parent. If you key the parent, the build warns and lists the child IDs. Applied at build time post-cache: adding, changing, or removing an override takes effect on the next plain rebuild, no `--refresh-trails` refetch needed. Custom routes are unaffected; they set `name` inline. |

### Direction schedules

See [Direction arrows](#direction-arrows) for the full model.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `direction_schedule` | No | `{}` | When and how arrows flip 180° (day-of-week / date-parity). One hierarchical key. Top-level `reverse_days:` is the system-wide schedule applied to every route. Nested `per_route:` is a dict of per-relation overrides keyed by OSM relation ID. **Required** (system-wide or per-route) for any way tagged `oneway=reversible` to render. See [Direction schedules](#direction-schedules-day-of-week--date-parity-reversal) for the full schema and examples. |

### Display

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `default_visible` | No | _(see description)_ | First-visit visibility for layer toggles. Four accepted forms. If unset (omitted or `null`), these layers default on: `trail_markers`, `trailheads`, `hubs`, `parking`, `toilets`, `drinking_water`, `bicycle_repair_stations`, `direction_arrows`. The rest (`features`, `difficulty`, `emergency`) default off. An empty list (`[]`) is the bare-map opt-out: everything off, riders opt in via Options. `"all"`: every supported layer on. A list of layer names: only those layers on. Valid layer names: `parking`, `trailheads`, `hubs`, `features`, `trail_markers`, `toilets`, `drinking_water`, `bicycle_repair_stations`, `difficulty`, `emergency`, `direction_arrows`. Once a rider toggles a layer in Options, their preference persists per-map in `localStorage`. That preference overrides the default on subsequent visits. **Safety note:** an unset `forced_visible` already forces `direction_arrows` on. If you set `forced_visible` without `direction_arrows`, keep `direction_arrows` in `default_visible` (or leave `default_visible` unset), so riders still see the arrows. The build prints a warning when one-way trails exist and neither list covers `direction_arrows`. |
| `forced_visible` | No | `[direction_arrows]` | Layers rendered on regardless of `localStorage` or `default_visible`. Their toggle is hidden, so the rider cannot turn them off. Same forms and layer names as `default_visible`. If unset, `direction_arrows` is forced. Set `[]` to force nothing. Use for safety-critical layers (`direction_arrows` on flow trails) or any layer that must always show. Subordinate to the `show_*` gates: a layer suppressed by `show_X: false`, or with no data, has nothing to force on. |
| `default_labels` | No | `"none"` | Initial label mode for first-visit riders: `"routes"` (route names), `"trails"` (trail names), or `"none"`. Defaults to `"none"`, so a fresh visit produces a clean map. If no relation is in route mode, the default is `"trails"` instead, since the trail name is the only name there is. On an event map, the default is `"routes"`. Event maps label only the featured routes' ways; see [Labels in event mode](event-mode.md#labels-in-event-mode). `"routes"` is rejected when no relation is in route mode. The rider opts into labels via the Labels segmented control. The in-UI select reflects `show_trails`; the Trails option is removed when trails are hidden. |
| `forced_labels` | No | _(unset)_ | Locks the label mode to `"routes"`, `"trails"`, or `"none"` and hides the Labels control, ignoring any persisted preference. Distinct from `default_labels`, which only seeds the initial value. Rejected at build time if it names a hidden category (`"trails"` with `show_trails: false`), or if it is `"routes"` when no relation is in route mode. |
| `default_color_scheme` | No | `"light"` | First-visit color scheme: `"light"`, `"dark"`, or `"auto"` (follows the rider's OS `prefers-color-scheme`). Riders override via the Options Appearance control; the choice persists per-map. The correct scheme is applied before first paint, so there is no light-to-dark flash. The Protomaps basemap, trail labels, direction arrows, and POI shadows have per-scheme variants; trail line colors are scheme-independent. |
| `invert_logo_dark` | No | `false` | Whether the brand logo auto-inverts in dark mode. If the logo is monochrome or limited-palette and needs inverting on the dark sheet, set `true`. Colorful and photographic logos look right without inversion. |

The bottom-right routes panel (the map's key) has no config knob; see [Routes panel](#routes-panel) below.

Routes that share a path are drawn as parallel lanes. This has no config key. Every map ships maplibre-gl-lanes. The browser orders and draws the lanes at load time, with crossing-minimized lane order and curved junctions. Route-name labels and the highlights ride the lanes. One-way chevrons run down the center of the bundle, because one-way is a fact about the trail and not about one route. The lanes need WebGL2, as MapLibre GL JS itself does. On a device without WebGL2, the page says that the map cannot start there. See [Troubleshooting](troubleshooting.md#the-page-says-the-map-has-not-started).

### Marker and accent colors

Each POI type follows the same three-knob pattern: fill color, glyph or dot
color, halo or ring color. Values flow to CSS custom properties on `:root`.
The Options swatch, the on-map marker, and any popup badge all read the same
hex: one source of truth per color. The accent color works differently: one
base color resolves into a per-mode light / dark palette (see the `accent_color`
row).

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `marker_color` | No | `"#795548"` | Trail-marker chip fill (merged guideposts + emergency-access points). |
| `marker_text_color` | No | `"white"` | Trail-marker glyph color. Applies to the `ref` or `name` text on the chip, and to the `#` on the Options key. A marker with neither value renders as an empty chip. |
| `marker_border_color` | No | `"white"` | Trail-marker outer halo border color. |
| `marker_shape` | No | `"box"` | Trail-marker chip shape: `box` (rounded rect), `pill`, `circle`, or `diamond`. `box` and `pill` size the chip to fit the ref text. A short ref (`"23"`) renders `pill` as a true circle; a longer ref (`"EAP-1"`) renders it as a rounded pill. `circle` and `diamond` are fixed-size and 1:1 for every marker, and truncate the ref text to the first two characters to fit (`"EAP-1"` shows `"EA"`). `diamond` is a square rotated 45 degrees with sharp points. All four shapes use the same `marker_color`, `marker_text_color`, and `marker_border_color`. |
| `parking_color` | No | `"#2980b9"` | Parking chip fill. |
| `parking_text_color` | No | `"white"` | Parking glyph (`P`) color. |
| `parking_border_color` | No | `"white"` | Parking outer halo border color. |
| `trailhead_color` | No | `"#27ae60"` | Trailhead chip fill. |
| `trailhead_text_color` | No | `"white"` | Trailhead glyph (`TH`) color. |
| `trailhead_border_color` | No | `"white"` | Trailhead outer halo border color. |
| `hub_color` | No | `"#f39c12"` | Trail-hub hexagonal chip fill. Default amber/orange chosen to read distinctly from trailhead green, parking blue, and feature purple. |
| `hub_text_color` | No | `"white"` | Trail-hub glyph (`H`) color. |
| `hub_border_color` | No | `"white"` | Trail-hub outer halo border color. Rendered as a CSS drop-shadow rather than a CSS border so the halo follows the hexagonal silhouette. |
| `feature_color` | No | `"#8e44ad"` | Feature marker inner dot color. |
| `feature_ring_color` | No | `"#ffffff"` | Feature marker outer ring color. |
| `accent_color` | No | `"auto"` | UI accent color: active toggle pill, search input focus ring, link color, FAB pressed state, segmented-control active fill, etc. From one base color the build derives a per-mode palette: a deep light-mode shade and a lightened dark-mode shade. Each shade is paired with its own text color (white or near-black, whichever contrasts more), so the accent stays legible in both schemes. `style.css` selects the active pair by `data-color-scheme`. Three accepted forms. Omitted: the same as `"auto"`. A 6-digit hex (e.g. `"#FF5733"`): used verbatim as the light shade, with the dark shade derived from it, so light mode is unchanged. The literal `"auto"`: derives the base from the logo via Pillow (most common saturated color, cached per source hash), then deepens and saturates it for a vivid light shade and lightens it to clear WCAG AA against the `#1c1c1e` dark sheet. SVG-only logos fall back to the `icon:` raster as the derive source. If neither is raster, `"auto"` falls back to `#1D6FA5`. A map with neither `logo:` nor `icon:` uses the bundled placeholder logo, so its accent derives from the placeholder's green. For a curator-chosen accent (explicit hex or successful `"auto"`), the build warns when the light shade fails AA against the white sheet or the dark shade fails AA against the dark sheet (the links / focus-rings role). The on-accent text color is chosen for contrast automatically and is not part of that check. |

### Branding

See [Logo and icon assets](#logo-and-icon-assets) for rendering specifics.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `logo` | No | : | Path (config-folder-relative) to logo image (any web format: PNG, WebP, JPEG). Resampled at build time to fit a 200x48 px box (map overlay) and a 140x56 px box (About modal). If omitted, the `icon:` source is used as the logo automatically. |
| `icon` | No | : | Path (config-folder-relative) to source image (PNG / WebP, at least 256 px on the longer side) for automatic icon + PWA-manifest generation. Any aspect ratio works: non-square sources are auto-padded to square (centered, transparent background). If omitted, the `logo:` source is used as the icon source automatically, provided the logo is a Pillow-readable raster (PNG/WebP/JPEG/…). An SVG logo can't be rasterized into icons; if the logo is an SVG, set `icon:` explicitly. Most maps only need to set one of the two. |
| `additional_logos` | No | `[]` | Secondary brand images (an event logo, one or more sponsor logos) stacked vertically **under** the primary logo in the top-left brand mark. They render top-to-bottom in the order listed. Each entry takes `path:` (required, config-folder-relative, same image pipeline as `logo:`) and `invert_dark:` (optional, default `false`; set `true` for a plain dark mark that would vanish in dark mode). Display-only: icon generation, `accent_color: auto`, the About modal image, and social-share previews all stay keyed to the primary `logo:` no matter how many logos are listed here. See [Additional logos](#additional-logos-additional_logos). |

If a map sets **neither** `logo:` nor `icon:`, the engine falls back to a bundled placeholder (a bicycle on the brand green). Every map still gets favicons, an installable PWA icon, and a brand mark. An explicit `logo:` or `icon:` always takes precedence.

### User-supplied points

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `trailheads` | No | `[]` | List of trailhead locations. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `parking` | No | `[]` | List of parking locations. See [Trailhead and parking entries](#trailhead-and-parking-entries). |
| `hubs` | No | `[]` | List of trail-hub locations: named on-trail intersections riders use as wayfinding landmarks ("meet me at Bottle Junction"). Distinct POI type from trailheads. See [Trailhead and parking entries](#trailhead-and-parking-entries). |

### About modal

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `about` | No | none | Object with optional `curator` and `links` keys, rendered in the About modal, the technical surface. (`about.description` was retired; the map's descriptive text is `welcome.body`.) See [About this map block](#about-this-map-block). |

### Welcome modal

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `welcome` | No | framework default | Welcome/Help modal. It auto-opens on first visit and reopens any time from the Options overlay's **How to use this map** row. Three forms: omit (default content), `false` (suppress the first-visit auto-open; the Help row still opens it), or a dict with optional `title` / `body` (plain-text, paragraphs separated by blank lines) / `show_controls_hint` (default `true`). `body` is the map's one descriptive text. It renders in this modal, and its first paragraph doubles as the `og:description` social-preview snippet. The `title` applies to the first-visit auto-open. Opened from the Help row, the modal is titled "How to use this map" to match the row. Dismissal persists per-map in `localStorage` and only affects the auto-open. |

## Route buckets

Every route in the map carries three independent boolean flags:

- `summer`: rideable without snow.
- `winter`: rideable / groomed only with snow (fatbike, snowshoe).
- `emergency`: a service, rescue, or access route.

**The flags are not mutually exclusive.** A single route can sit in one bucket,
two, or all three. The canonical example: a Snow Bike Route groomed for fatbikes
in winter and rideable on knobbies in summer carries
`summer=true, winter=true`. It stays visible when you switch from Summer to
Winter mode and back.

### UI behavior

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

The rule to remember: **being categorized Winter or Emergency removes a route
from Summer by default.** `summer_relations` is the opt-back-in list for
year-round routes that would otherwise sit only in Winter or Emergency.

Custom routes carry their three flags inline as YAML booleans and follow the
same rules.

## Custom routes (full guide)

Some routes can't live in OSM: race courses, event loops, demo routes, or any
transient route that doesn't match permanent signed infrastructure on the
ground. The framework supports these as first-class citizens via the
`custom_routes` config key.

`custom_routes` can supplement an OSM map (relations + custom routes together)
**or be the only geometry on the map**. A config with `custom_routes` (or
`event_mode.routes`) and no `relations` renders those routes alone; no
surrounding trail network is fetched. The map view is computed from the custom
geometry like any other map.

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
    # trail_name_field: name                          # optional: GeoJSON property to use as per-segment trail_name
    # oneway: "yes"                                   # optional: direction arrows ("yes" or "-1")
```

### Rules

- **`id`** is a string. It must be unique within the map and must not collide
  with any OSM relation ID in the config (`relations`, `clipped_relations`,
  `winter_relations`, etc.). Best practice: use a hyphenated slug like
  `race-2025` or `demo-loop` so it's visually distinct from the numeric OSM ids.
- **`color`** overrides any `relation_colors` / `default_trail_color` lookup for
  this route. It also becomes the swatch color in the finder and the glow
  color when the route is highlighted.
- **`summer`, `winter`, `emergency`** are three independent booleans: same
  semantics as OSM routes. Any combination is valid, but at least one must be
  true. A custom route that's invisible in all modes is rejected at validation
  time. Defaults if all three are omitted:
  `summer: true, winter: false, emergency: false`.
- **`geometry`** is a path to a GeoJSON file, resolved relative to the config's
  directory (`configs/<slug>/`). A bare filename like `race-2025.geojson` picks
  up the file sitting next to the YAML. Absolute paths are passed through
  unchanged. The file must be a FeatureCollection or a single Feature,
  containing LineString or MultiLineString geometry only. Points, Polygons, and
  other geometry types are rejected with a clear error.
- **`trail_name_field`** is optional. If the GeoJSON features carry a
  per-feature property whose value should become the trail name, name that
  property here. Individual named segments of the custom route then show up
  in the trail finder. If omitted, features pass through with no per-segment
  trail name.
- **`oneway`** is optional: `"yes"` renders direction arrows along the
  GeoJSON's digitized direction and `"-1"` renders them reversed. Omit it for
  a route with no directional arrows. Direction schedules attach to OSM
  relation IDs, so custom routes cannot use `"reversible"`; the build fails if
  one does.

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

Custom routes are indistinguishable from OSM routes in every runtime behavior:

- They follow the bucket model via their inline `summer` / `winter` /
  `emergency` flags.
- They appear in the **Routes** section of the finder (filtered to
  currently-visible routes just like OSM routes).
- Tapping a custom-route row in the finder highlights the whole route in its own
  color (same as any OSM route).
- If `trail_name_field` points at per-segment names, those trails also appear in
  the **Trails** section of the finder and can be picked individually.

## Routes panel

The bottom-right corner always shows the **routes panel**, the map's key.
There is no config knob: every map gets it. A geometry source is required, so
there is always at least one route. It lists every currently-visible route as
a color swatch + name + optional stats. A **Search** button pinned at the
bottom opens the [finder](#trail-finder), the panel's expanded search state.

- **The swatch keys the line.** It reuses the route's own on-map style: a
  solid bar for solid routes, a dashed, dotted, or two-color ribbon for
  dashed ones (see [Dash patterns](#dash-patterns)). Same-shaped colored
  loops can be told apart straight from the key. The finder's route rows use
  the identical swatch.
- **The rows mirror what's visible**, following the rider's season / emergency
  toggles exactly like the finder; event maps list featured routes only.
- **Tapping a row** highlights that route (tap again to clear), the same
  behavior as a finder route row.
- **Row stats** follow `show_distance` and
  are shown in the rider's [units](#units).
- **Boot state.** The panel opens either as the key card or as a compact round
  list-icon chip. The choice depends on how many rows there are and whether
  the card would swamp the viewport: roughly, it starts expanded when the card
  would fit within a third of the screen height, chip otherwise. The rider's
  expand / collapse choice then persists per-map (`mtb.routePanelExpanded`)
  and beats that default. When the panel boots collapsed, a first-visit
  **Route key** label points the chip out (the expanded card explains itself).

For [difficulty-mode relations](#color-modes), the panel adds a difficulty key
headed **Difficulty**. It lists one row per rating on the map, easiest first.
Each row shows the rating's symbol and name. An **Unrated** row follows when
unrated difficulty-mode ways are visible. On a map with both modes, the panel
is headed **Key**. The rows of the default mode come first, then the other
mode's rows, and **Unrated** comes last. If `show_distance` is on, each row shows that rating's
visible length. Tapping a row highlights every visible way with that rating.
The map fits to those ways. The first-visit chip label reads **Difficulty key**,
or **Key** on a map with both modes.

## Units

Units are the rider's choice, not a config setting. The Options **Units** row
switches between miles and kilometers. The choice covers everything the app
computes: route distance, the map scale, the off-screen distance
to the rider's location, and contour labels. Miles pairs with feet for short
distances and contours. Kilometers pairs with meters.

Names in the trail data are never converted. A guidepost named "Mile 5.0"
keeps that name under either setting.

Until a rider picks, the device's region decides. Regions that use miles day
to day (the US, Liberia, and Myanmar) get miles. Every other region gets
kilometers, including the UK. A web page can only see the browser's language
tag, such as `en-US`, not the phone's measurement-system setting. The Options
row covers riders whose tag doesn't match what they use.

The choice is stored once per origin (`mtb.units`), not per map. A rider who
picks kilometers on one map sees kilometers on every map served from the same
site. See [Privacy](#privacy).

The former `distance_units` config key is removed. The build rejects a config
that still sets it, with a message to delete the line.

## Trail finder

The Search overlay (opened via the routes panel's **Search** row at
bottom-right; the finder is that panel's expanded state) contains a combined
routes / trails / places finder with one search input, type-filter chips, and
sectioned results:

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

This example is a routes map. Difficulty-mode relations differ as described in
[Color modes](#color-modes).

- **One scrollable list, two section headers.** Routes on top, trails below.
- **Single search input** filters both sections (case-insensitive substring
  match against route names and trail names).
- **The list always mirrors what's currently visible on the map.** In Summer
  mode with Emergency off, you see summer routes and the trails that belong to
  them. Toggle Emergency on and emergency routes / trails appear in the list.
  Switch to Winter mode and the list live-refilters to winter content.
- **Route rows** show a swatch in the route's own on-map style (solid, or a
  dashed, dotted, or two-color ribbon for dashed routes; the same swatch the
  routes panel uses) plus the name. OSM and custom routes appear together and
  behave identically.
- **Trail rows** show the trail name and the parent route(s) underneath.
- **Tapping a route row** highlights the route on the map. A soft yellow halo
  lifts the route off the map, and the other routes dim. It also pans / zooms
  to its extent,
  collapses the sheet, and shows a floating chip at the top of the map. Tap
  the chip to clear.
- **Tapping a trail row** fits the map to the trail and collapses the sheet.
  The trail's popup then opens on the middle of the trail. A soft yellow glow
  lifts the trail off the map. The finder lists each name once, so a finder
  pick lifts and measures every stretch of that name. A tap on the map is
  narrower: it lifts and measures only the contiguous section of the named
  road or trail under the tap. The glow shows what the popup's length covers.
  The row reads "Length shown:" if the map edge cuts that section. The popup closes on the
  next tap elsewhere. A trail pick clears any highlighted route or place first.
- **One thing at a time.** Picking a new route, trail or place replaces the
  previous one. Everything else stays visible; the highlight only adds
  emphasis.

If no relation is in route mode, the finder lists trails and places only. It
has no Routes section and no Routes chip. On a map with difficulty-mode
relations, a trail row shows the symbol
of the rating that most of the trail's visible length carries. An unrated trail
shows the unrated line instead. The row names every rating on the trail, such
as "Easy, More Difficult", then the trail's visible route-mode parents, if
any. If `show_distance` is on, the row also shows the
trail's visible length. Tapping a trail row fits the map to the trail and
opens the trail's popup, as on a routes map.

## Trail difficulty

When `show_difficulty: true`, the map displays IMBA trail difficulty rating
symbols along trail segments. Ratings are read from the `mtb:scale:imba` OSM tag
on individual ways. Segments without this tag show no symbols.

| Rating | Symbol | Color |
|--------|--------|-------|
| 0 | Circle | White (Easiest) |
| 1 | Circle | Green (Easy) |
| 2 | Square | Blue (More Difficult) |
| 3 | Diamond | Black (Very Difficult) |
| 4 | Double diamond | Black (Extremely Difficult) |
| 5 | Double diamond | Orange (Pro-Only) |

The symbols use ski-trail-style iconography. They are always oriented
vertically (not rotated to follow the trail line) and include a white halo for
visibility against any background. The Difficulty toggle appears under "What
to show" in the Options overlay. First-visit visibility is controlled by
`default_visible` (include `difficulty` to default on; otherwise off). The
rider's choice persists in localStorage (`mtb.difficulty`). If no trail in the
map carries an `mtb:scale:imba` value, the toggle is hidden entirely, since
there is nothing to display.

Difficulty symbols only appear on segments of visible trails.
If you hide a trail (for example, by switching season), its difficulty symbols disappear too.

### Difficulty is a way-level tag

The `mtb:scale:imba` tag is read from individual **ways** only. Tags on the
parent **relation** are ignored, including for difficulty-mode coloring.

This matches OpenStreetMap's tagging convention (`mtb:scale:imba` is a per-way
tag) and reflects the reality that real trails often vary in difficulty along
their length:

| Way tag | Relation tag | What renders |
|---|---|---|
| `4` | `2` | Black diamond (4): way value wins |
| (none) | `2` | Nothing: segment is unrated; in difficulty mode it falls back to `default_trail_color` |
| `4` | (none) | Black diamond (4) |

### Color modes

Each relation, and each custom route, draws in one of two color modes.

- **Route mode** colors the relation's lanes by route. The color is the
  `relation_colors` entry, else the OSM `colour` tag, else
  `default_trail_color`. `dashed_relations` sets the dash.
- **Difficulty mode** colors ways by their own IMBA rating. An unrated way
  takes the `default_trail_color` look.

`color_by` sets the default mode for every relation. `color_by_route` and
`color_by_difficulty` list the exceptions. Both lists take relation IDs,
super-relation IDs, and custom-route IDs. A super-relation ID fans out to its
child routes. An ID listed directly beats a fan-out from a super-relation.

```yaml
color_by: difficulty
color_by_route: [6157604, 11140515]   # drawn as routes, beside the rating lanes
```

A way draws one lane for each distinct color key among its visible parent
relations. Two route-mode parents give two lanes. A route-mode parent and a
difficulty-mode parent give two lanes, the route's color beside the rating.
Two difficulty-mode parents give one rating lane, because the rating is a tag
on the way.

A map where every relation is in route mode is a routes map. Choose
difficulty mode for a trail system with named trails and `mtb:scale:imba`
tags but no route colors worth keying, such as Copper Harbor. Choose a mix for
a system such as NTN Marquette, where long routes cross a rated trail network.

The bottom-right key lists a row for each route-mode relation and a row for
each rating. An **Unrated** row follows when unrated difficulty-mode ways are
visible. The finder has a Routes section and a Routes filter chip when any
relation is in route mode. Labels show trail names, and the `"routes"` label
mode is offered when any relation is in route mode. The tap popup lists the
way's route-mode parents. The popup's yellow glow lifts the trail it describes and
changes nothing else. On a map tap, that is the tapped section of the trail,
as described under [Trail finder](#trail-finder). If the tapped way has no name, the popup is titled
"Unnamed". The title is muted, since the map does not know what kind of way it
is.

Several config keys depend on the color modes:

| Key | Effect |
|-----|--------|
| `event_mode` | Rejected while any relation is in difficulty mode. Event maps are routes maps. |
| `default_labels` / `forced_labels` | `"routes"` is rejected when no relation is in route mode. |
| `default_labels` | If unset, defaults to `"trails"` when no relation is in route mode. On an event map it defaults to `"routes"`. Otherwise it defaults to `"none"`. |
| `show_trails` | Rejected if `false` when no relation is in route mode. The map then lists trails only. |
| `relation_colors` / `dashed_relations` | Apply to route-mode relations. On a difficulty-mode relation the entry is ignored, and the build warns. |
| `clipped_relations` | Honored. Continuation arrows at the map edge take the lane's key color: the route's color, or the rating's color. The arrow of a single-color dashed route draws without an outline, as its lanes do. Distances for a clipped relation or a truncated trail carry "shown"; the tap popup says "Length shown:". |
| `route_key` | Same meaning: `false` hides the key, Search only. |
| `show_distance` | Gates per-route distances for route-mode relations, per-rating distances for difficulty-mode relations, and per-trail distances. |

The validator compares only the literal IDs in the lists. It cannot see
a super-relation's children before the fetch, so a child that reaches the wrong
mode through a fan-out is not reported.

## Direction arrows

The map renders direction-of-travel arrows along ways tagged with either
`oneway:bicycle=*` or `oneway=*` in OpenStreetMap. Two-way ways get no arrows.
Arrows render as a row of small arrowheads repeating along the line itself at
a constant on-screen spacing (roughly every 80 px at any zoom). Each arrowhead
is rotated to follow the line's local bearing. Arrows are always on where the
layer is visible. On shared corridors rendered as parallel route lines, one
arrowhead row marks the corridor rather than repeating per route. They're
sized to read as a subtle directional cue rather than compete with the trail
casing. Trail or route name text always draws over them.

Arrow rendering follows the same **per-way** model as IMBA difficulty. The tag
is read from individual ways; relations themselves don't have a direction, so
they never carry the tag.

**Tag resolution: `oneway:bicycle` wins over `oneway`.** This matters on trails
that ride one-way for bikes but allow foot traffic both ways. The
bicycle-specific tag describes the rule that matters; a bare `oneway=*`
(often inherited from non-bike use) is the fallback. Either tag takes the same
accepted values:

| Tag value | What renders |
|---|---|
| `yes` | Arrows along the way's digitized direction |
| `-1` | Arrows along the reverse direction (normalized at build time) |
| `reversible` | Arrows that **must** flip on a schedule (see below) |
| `no` or absent | No arrows |

So a way tagged `oneway:bicycle=yes` renders forward arrows even if its generic
`oneway` is unset. `oneway:bicycle=no` suppresses arrows even if `oneway=yes`
exists. And `oneway:bicycle=reversible` requires a schedule the same way bare
`oneway=reversible` does.

### Show / hide on a per-map basis

Three keys interact, from outermost to innermost:

1. `show_direction_arrows: false` suppresses arrows entirely: none are placed,
   the toggle is hidden, and the rider cannot surface them. The OSM oneway tags
   stay on features for the finder; only the arrows are suppressed. Use for maps
   that should never show directional indicators.
2. `forced_visible: [direction_arrows]` (the default) forces arrows always-on and
   hides the toggle. Set `forced_visible: []` to force nothing. Subordinate to
   `show_direction_arrows`: if arrows are suppressed there is nothing to force on. Use for safety-critical maps where wrong-way travel on
   flow trails would be dangerous.
3. `default_visible` controls the toggle's initial state when neither list above
   names `direction_arrows`. If `default_visible` is unset, arrows start on.
   An empty list (`[]`) or an explicit list that omits `direction_arrows`
   starts them off instead. Include `direction_arrows` in the list (or use
   `default_visible: all`) to start them on. The rider can flip the toggle,
   and that choice persists.

The default behavior (no keys set) is: arrows allowed and forced on, with
no toggle in Options. Set `forced_visible: []` to bring the toggle back, on at
first visit.

The toggle row also hides when the map has no oneway-tagged trail features,
since no arrows would render anyway. This runtime gate is driven by the trails
data itself; it applies regardless of `default_visible` or `forced_visible`,
and no config key controls it.

### Direction schedules (day-of-week / date-parity reversal)

Some trail systems are signed as one-way with the direction alternating by day
of week (e.g. clockwise Mon/Wed/Fri, counter-clockwise Tue/Thu/Sat) or by
calendar-date parity (one direction on even days, the other on odd). OSM has no
canonical schema for the schedule itself. The framework supplies it via the
single `direction_schedule:` key. The key has two optional parts: a top-level
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
fans out to every child route. This is useful for multi-system maps where a
whole second trail system should not reverse:

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
  relation or a super-relation; super-relations are auto-expanded to their child
  routes the same way as `relations`.
- The relation serves only as a grouping handle for "the ways under this
  relation share this schedule". Relations themselves don't have direction.
- `reverse_days` lists day tokens. Tokens are case-insensitive and any prefix
  of three or more characters parses (`monday`, `Monday`, and `mon` are all
  accepted). Two parity tokens are also accepted: `even_days` matches even
  calendar dates and `odd_days` matches odd dates. Weekday and parity tokens can
  coexist in one list, and any match triggers reversal.
- A `per_route` entry always wins over the top-level system-wide
  `reverse_days:`. An entry with `reverse_days: []` is the way to opt one route
  out of the default. **An explicit per-child entry always wins over a
  super-relation entry** that would otherwise fan out to that child.
- On a reverse day, a way's arrows rotate 180 degrees when two conditions
  hold. Its resolved oneway value (see
  [Tag resolution](#direction-arrows): `oneway:bicycle` first, `oneway` as
  fallback) is `yes`, `-1`, or `reversible`. And it belongs to a relation
  whose schedule lists today.
- Setting a schedule never makes an untagged way one-way; OSM tagging still
  controls which ways get arrows. The schedule only controls rotation.
- The trail popup follows the schedule, not the tag. If a one-way way belongs
  to a relation with a schedule, the popup reads "One-way (reverses by day)".
  Other one-way ways read "One-way".

### `reversible` is required to pair with a schedule

A `reversible` resolved oneway value (from either `oneway:bicycle=
reversible` or bare `oneway=reversible`) means "the direction is alternating per
ground signage". A way with this tag and no schedule cannot be rendered
correctly: the build would silently pick OSM digitization order, which would be
wrong half the time. The build therefore **fails** if any way resolves to
`reversible` and no schedule covers it. A schedule covers a way via the
top-level `direction_schedule.reverse_days` or a `direction_schedule.per_route`
entry on a parent relation. The error lists each offending way with a clickable
OSM URL and its parent relation IDs, so you can pick where to attach the
schedule.

By contrast, `yes` and `-1` resolved values have an inherent direction in OSM,
so a schedule for them is optional. Without one they render statically forward;
with one they flip on the configured days.

The day-of-week and calendar date are read from the visitor's local clock at
page load and rechecked every 5 minutes. A tab left open across midnight
stays correct.

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

**Object format**: for more control over cap style and colors:

```yaml
dashed_relations:
  13213211:
    pattern: [4, 2]                  # dash pattern (required)
    cap: square                      # "round" (default), "square", or "butt" line ends
    colors: ["#000000", "#FF0000"]   # two-color alternating dashes
```

| Key | Required | Default | Description |
|---|---|---|---|
| `pattern` | Yes | : | `[dash, gap]` in line-width multiples |
| `cap` | No | `"round"` | Line cap style: `"round"`, `"square"`, or `"butt"` |
| `colors` | No | : | One or two CSS colors. One color overrides the route's normal color; two colors produce alternating dash colors (see below). |

Both formats can be mixed in the same config. Dashed relations are rendered
without line offsets (centered on the geometry) to avoid oval distortion on
curves.

### Alternating-color dashes

Two colors in `colors` produce dashes of color A interleaved with color B
along the trail. This is useful for trails that share signage from two routes,
hazard stripes, emergency-access markings, or any case where one solid color
isn't enough.

Color A is the dash and color B is what shows in the gap, so pattern sizing
controls the visual proportion directly:

```yaml
dashed_relations:
  12345678:
    pattern: [4, 4]                  # equal dash and gap
    colors: ["#000000", "#ff0000"]   # color A = black, color B = red
    cap: square
```

| Pattern | colors `[black, red]` produces |
|---|---|
| `[4, 4]` | Equal-width black and red segments |
| `[6, 2]` | Mostly black, with narrow red showing through 2-wide gaps |
| `[2, 6]` | Mostly red, with narrow 2-wide black dashes on top |
| `[0, 2]` (dots) | Solid red line with **black dots** on top (because dash width is 0) |
| `[4, 0]` | Solid black line: no gap means color B never shows |

Pattern values are in line-width multiples, so the absolute size scales
naturally with zoom.

**Cap style interacts with the effect.** With `cap: round` (the default) the
dashes have semicircular ends that bulge slightly past the dash bounds and
visually soften the boundary between A and B. With `cap: square` (or `butt`) the
boundaries are crisp. That is generally what you want for an obvious alternating
look:

```yaml
dashed_relations:
  12345678:
    pattern: [4, 4]
    colors: ["#000000", "#ffffff"]   # crisp black/white hazard stripe
    cap: square
```

**One color vs. two.** A single-element `colors: ["#ff0000"]` overrides the
route's color entirely (equivalent to setting `relation_colors`) and applies
dashes from `pattern`. Two elements activates the alternating-color path. Three
or more colors are not supported; the build fails validation.

**Interaction with other features.** Alternating-color dashes work with
direction arrows, labels, and the route visibility rules exactly like any other
dashed route. They are *not* compatible with difficulty mode: on a difficulty-mode relation the
`dashed_relations` entry is ignored.

## Trailhead and parking entries

### Trailheads

Trailheads are shown as green "TH" markers by default (configurable via
`trailhead_color`, `trailhead_text_color`, `trailhead_border_color`). Each entry
supports:

| Key | Required | Description |
|---|---|---|
| `name` | No | Display name shown in popup; omitting it is allowed but leaves the popup unlabeled |
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
  when no hubs are configured for the map. Hubs default on when
  `default_visible` is unset. An empty list or a list that omits `hubs`
  starts them off instead. The rider's choice persists in localStorage
  (`mtb.poi.hubs`).
- **Search integration:** hubs appear in the Search overlay's POI scope
  alongside trailheads and parking; tap a result to pan and ring-pulse the
  marker (no popup, since there is none).

Each entry supports:

| Key | Required | Description |
|---|---|---|
| `name` | No | Display name shown inline under the on-map chip and in search results; omitting it is allowed but defeats the point of a hub |
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
| `name` | No | Display name shown in popup; omitting it is allowed but leaves the popup unlabeled |
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

The Options overlay includes an **About this map** action row (below Share,
Install, and **How to use this map**, when those are visible). Tapping it opens
the map's technical modal, headed "About this map". When `logo:` (or `icon:`
as fallback) is configured, the brand **logo** appears on the right. The body
is a compact details block with one label:value row per fact: **Title** (the
map's title), **Curator**, **App** (the engine version and date), **Map
config** (when this map's curation last changed), **Map data** (data snapshot
date), and an **Offline** diagnostic row. The **More info** links and a
**Credits** section follow. The `about` YAML block is optional; when omitted,
the modal still renders the framework-supplied rows and Credits.

The map's descriptive prose does not live here: it's `welcome.body`, rendered
in the Welcome/Help modal. That modal is the first-visit greeting, reopenable
from the **How to use this map** row. The map is described once, and the
descriptive and technical surfaces stay distinct. `about.description` was the
old home; the validator rejects it with a migration hint.

```yaml
about:
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
| `curator` | No | Single `{name, url}` object rendered as the "Curator" details row, as a hyperlink when `url` is set and plain text otherwise. |
| `links` | No | List of `{label, url}` entries rendered as a bulleted list under a "More info" header: trail-system pages, club pages, a source repo, and so on. YAML order is the render order. |

The framework-supplied rows and sections, always shown:

- **App** row: `v<N> (<date>)`: the engine's commit count as of the last
  commit that touched `templates/`, plus that commit's date. The version
  only advances when the shipped app code changes. The row is omitted when
  the build can't consult git (e.g. a tarball checkout).
- **Map config** row: `<date HH:MM>`: the newest mtime of the config YAML and
  the per-map assets it references (logo, icon, additional logos, custom-route
  geometry). Moves when the map's curation changes, independently of the App
  version and the data date. A styling/schedule/asset edit is therefore
  visible in About. A `buildDate` value (the same inputs plus the engine
  templates) is still injected into `CONFIG` for the landing page's "Updated"
  line, but is not displayed here.
- **Map data** row: `<date HH:MM>` (`_data_date`). This is the OSM snapshot
  timestamp recorded when the trail data was last actually fetched, or the
  `.osm` file's mtime for local-file maps. It is rendered in the build
  machine's local time.
- **Offline** row: a diagnostic line that always states something a
  troubleshooting user can relay. When the service worker is active, it reads
  "Saved for offline use." or "Saving for offline use, N% done." Otherwise it
  gives an explicit reason offline isn't working:
  "Requires a secure (HTTPS) connection.", "Not supported by
  this browser.", "Not active for this page load." (first visit before the
  worker activates, or a hard refresh), "Status unavailable." (worker didn't
  answer).
- **Credits** section: a "Generated by trailmaps.app Map Generator." line
  (the name links to the engine repo), then one credit line per data source
  and library. OSM, Protomaps, Material Design Icons, MapLibre GL JS,
  maplibre-gl-lanes, uqr, and SIL Open Font License always appear; Mapterhorn
  and maplibre-contour when terrain is enabled.
  See the framework-level credit list in [`README.md`](../README.md#credits).

Any curator-supplied row or section whose source data is absent is omitted
entirely.

## Logo and icon assets

The framework uses two separate image assets configured via `logo` and `icon`.
They serve different purposes and have different requirements.

### Logo (`logo`)

The logo is displayed as an overlay in the top-left corner of the map and at
the top-right of the **About this map** modal header. At build time the
framework opens the source with Pillow. It picks the binding axis from the
source's aspect ratio, resamples to ~2x the display size with LANCZOS for
retina sharpness, and writes a single normalized `logo.webp` into the output.
Source files can be PNG, WebP, JPEG, or any format Pillow can open; SVGs are
not currently rasterized and should be pre-converted.

| Property | Detail |
|---|---|
| **Purpose** | Map overlay branding; also shown in the About modal header |
| **Map overlay render** | Inside a 280x64 px bounding box on desktop (≥768px), 200x48 px on mobile |
| **About modal render** | Inside a 140x56 px box (all screen sizes) |
| **Binding axis** | Wide wordmarks (aspect wider than ~4.17:1, the 200:48 box) land at 200 px wide; square or tall logos land at 48 px tall |
| **Pre-resize target** | Source is resampled to ~2x the render size (max longer side ~400 px on desktop). Never upscaled; smaller sources are preserved. |
| **Recommended source** | At least 2x the expected render size on its long side (e.g. 400+ px wide for a wordmark); higher is fine, the framework resizes down |
| **Recommended format** | Any Pillow-readable raster format; output is always WebP |
| **Transparency** | Supported: the logo floats over the map with a subtle drop shadow |

The logo can be any shape: the bounding-box render handles wordmarks, square
badges, and tall marks cleanly. Wide horizontal wordmarks produce the most
brand-prominent result in the top-left overlay.

#### Color guidance

The logo is displayed on top of the map. For best legibility across varying
terrain colors, use dark artwork on a transparent background (e.g. a black or
near-black wordmark). Multi-color or photographic logos work too. If you need
to ensure contrast against busy map areas, design them with a built-in outline
or soft background.

If `logo` is omitted but `icon` is set, the icon source is used as the logo
automatically. Square icons render as ~80x80 badges in the overlay and ~56x56
in the About modal. If neither `logo` nor `icon` is set, the engine's bundled
placeholder becomes the icon source and therefore the logo too. The overlay
then shows the placeholder bicycle badge rather than being hidden.

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
| `icons/android-chrome-512x512.png` | 512x512 | Android home screen (Chrome WebAPK) |
| `icons/android-chrome-maskable-512x512.png` | 512x512 | Maskable PWA tile (Android). Content sits in the inner 80% safe zone; the margin bleeds the icon's own field color so it fills any OEM mask shape (circle, squircle, …) |
| `icons/favicon-32x32.png` | 32x32 | Standard browser tab icon |
| `icons/favicon-16x16.png` | 16x16 | Small browser tab icon |
| `favicon.ico` | 16, 32, 48 | Multi-resolution ICO for legacy browsers |
| `icons/safari-pinned-tab.svg` | : | Auto-traced silhouette (requires `potrace`) |
| `icons/site.webmanifest` | : | PWA manifest (`name`/`title` from config). Its `background_color` (the launch-splash field) is set to a full-bleed icon's detected color, defaulting to white for transparent/white-backplate sources |

If a map sets neither `icon` nor `logo`, the engine falls back to a bundled
placeholder icon (`assets/placeholder-logo.png`, a bicycle on the brand green).
Favicons and the PWA manifest are still generated, and the map stays
installable. An explicit `icon:` or `logo:` always takes precedence.

**Tip:** Use a simple, high-contrast design for the icon: it needs to be
recognizable at 16x16 pixels. Avoid fine text or thin lines.

### Additional logos (`additional_logos`)

Secondary brand images, typically an event logo plus one or more sponsor
logos, stacked vertically under the primary logo in the top-left brand mark:

```yaml
logo: club-logo.webp
additional_logos:
  - path: event-logo.svg
    invert_dark: true              # plain dark mark; invert in dark mode
  - path: sponsor-acme.webp
```

Each entry's `path:` goes through the same pipeline as the primary `logo:`
(raster sources → normalized WebP, SVG sources → dimensioned SVG). Output
files are written as `logo-2`, `logo-3`, … in listed order. Secondaries share
the primary logo's bounding box, rendered a touch smaller, so the stack reads
as one consistently-scaled brand column. Each image still keeps its own aspect
ratio inside that box. `invert_dark:` (default `false`) controls the per-logo
dark-mode auto-invert, the same default as `invert_logo_dark` on the primary.

These are display-only. Favicon / PWA icon generation, `accent_color: auto`
derivation, the About-modal image, and social-share previews are all keyed to
the primary `logo:` / `icon:` and ignore this list entirely. Like the primary
logo, the stack is click-through: taps pass to the map underneath.

## Privacy

A generated map is entirely client-side. It sets no cookies, runs no analytics,
loads no third-party scripts, and makes no network calls beyond fetching its own
static files (HTML, CSS, JS, tiles, fonts) from the server you deploy to. Nothing
a visitor does is reported anywhere.

The app stores a small set of UI preferences in the browser's `localStorage`.
Each key is prefixed with the map's `slug` so several maps on one origin stay
independent (for example, `<slug>.mtb.colorScheme`). The one exception is
`mtb.units`. It has no prefix, so every map on the origin shares it:

| Key | Value |
|---|---|
| `mtb.seasonMode` | `"summer"` or `"winter"` |
| `mtb.emergencyOn` | Boolean: Emergency overlay on or off |
| `mtb.poi.<kind>` | Boolean per POI category (`parking`, `trailheads`, `hubs`, `features`, `markers`, `toilets`, `drinking_water`, `bicycle_repair_stations`) |
| `mtb.labels` | `"routes"`, `"trails"`, or `"none"` |
| `mtb.difficulty` | Boolean: IMBA difficulty symbols on or off |
| `mtb.directionArrows` | Boolean: direction arrows on or off |
| `mtb.colorScheme` | `"light"`, `"dark"`, or `"auto"` |
| `mtb.units` | `"mi"` or `"km"`. No slug prefix; shared by every map on the origin |
| `mtb.fabsLabeled` | Boolean: whether the on-map buttons show text labels |
| `mtb.welcomed` | Boolean: welcome modal already dismissed |
| `mtb.routePanelExpanded` | Boolean: the key panel's docked state (`true` = expanded key card, `false` = minimized chip). Only set on an explicit rider toggle; the open search overlay is never persisted |

These persist a returning visitor's own choices and are never transmitted. A
visitor can clear them at any time through their browser.

While Locate is on, the chip that names the trail under the rider is resolved on
the device from the position fix the map already has for the Locate dot.

The map never writes its position to the address bar. The **Share this map**
action builds a position link on demand, and only when the visitor chooses to
share. That path does not involve the server: the position lives only in the
URL the visitor passes along.
