# Event mode

Event mode is a presentation mode that flips the map's information hierarchy:
one or more "featured" routes display prominently in their declared colours, and
every other trail on the map renders as muted background context (typically a
grey dashed line). POIs (parking, trailheads, water, toilets, features, trail
markers) all render normally so riders can still find facilities.

Use event mode for:

- Race courses (single class or multi-class).
- Group rides crossing one or more parks.
- Demo loops, training rides, or any event-specific publication where one route
  is the focus.

Event mode is purely about visual differentiation. It doesn't change data
fetching, POI rendering, search, or any other framework behaviour. The featured
route(s) and background style are translated into per-route style overrides at
build time; the runtime sees a normal map where some routes happen to share the
background treatment.

## Contents

- [Quick start](#quick-start)
- [Schema](#schema)
- [How featuring works](#how-featuring-works)
- [Background style application](#background-style-application)
- [Direction arrows](#direction-arrows)
- [Event POIs](#event-pois)
- [GPX downloads](#gpx-downloads)
- [Worked examples](#worked-examples)
- [Branding](#branding)
- [Welcome modal](#welcome-modal)
- [Labels in event mode](#labels-in-event-mode)
- [Routes panel in event mode](#routes-panel-in-event-mode)
- [What event mode does NOT change](#what-event-mode-does-not-change)
- [Cross-references](#cross-references)

## Quick start

The shortest possible event-mode config:

```yaml
name: My Race
slug: my-race
title: "2026 My Race Map"
relations: [12345678]              # the OSM trail system the race runs on

event_mode:
  routes:
    - id: race-2026
      name: "2026 My Race Course"
      color: "#FF0000"
      geometry: race-2026.geojson  # path relative to this YAML
```

Drop the GeoJSON file (typically a single `LineString` or `MultiLineString`
`FeatureCollection`) next to the YAML, build, and deploy. The race course
renders bright red; every OSM trail in relation 12345678 renders muted
grey-dashed; POIs and the rest of the chrome behave normally.

### Route-only event maps (no OSM relations)

To show the event route *alone* — no surrounding trail network — omit
`relations` entirely. As long as `event_mode.routes` (or `custom_routes`)
supplies geometry, the map builds with just those routes:

```yaml
name: My Race
slug: my-race
title: "2026 My Race Map"

event_mode:
  routes:
    - id: race-2026
      name: "2026 My Race Course"
      color: "#FF0000"
      geometry: race-2026.geojson
```

With no `relations` there's no background network to mute, so only the course
renders. The map view (center / zoom / bounds) is computed from the route
geometry. Event mode's featuring transform still runs, but with nothing to push
to the background it's effectively a no-op — for a single route alone,
`custom_routes` on its own is equivalent; reach for `event_mode` when you also
want featured-only [direction arrows](#direction-arrows) or always-on
[event POIs](#event-pois). To suppress the OSM POIs/amenities that would
otherwise be fetched within the route's bounding box, set the relevant `show_*`
gates to `false` (see
[Build-time data gates](configuration.md#build-time-data-gates)).

## Schema

```yaml
event_mode:
  routes:                          # optional, list
    - id: <string>                 # required if entry present
      name: <string>               # required
      color: <css colour>          # required
      geometry: <path>             # required; relative to this YAML
      # dashed: false              # optional; defaults to false
      # description: ""            # optional
      # trail_name_field: ""       # optional; per-feature trail name property
      # summer: true               # optional bucket flags (default: summer-only)
      # winter: false
      # emergency: false

  featured: [<id>, ...]            # optional, list of references
                                   # string id  -> matches a top-level
                                   #               custom_routes entry
                                   # int id     -> matches an OSM relation
                                   #               id present in this map

  background_style:                # optional, default: dotted gray
    color: gray                    # CSS colour or hex
    pattern: [0, 2]                # dash pattern (line-width multiples)
    cap: round                     # "round" (default), "square", or "butt"

  direction_arrows: false          # optional; when true, arrows along
                                   # inline event routes; rider can't
                                   # disable.

  pois:                            # optional list of always-on POIs
    - name: <string>               # required; shown in popup
      coordinates: [<lon>, <lat>]  # required
      # description: <string>      # optional; shown below name in popup

  poi_color: <css colour>          # optional; chip colour for event
                                   # POIs (default: deep red #D32F2F).

  gpx:                             # optional; downloadable GPX files
    routes:                        # required, non-empty list
      - name: <string>             # required; label in the download sheet
        file: <path>               # required; .gpx relative to this YAML
```

**Required**: at least one of `routes` or `featured` must be non-empty.

**`routes`**: inline-defined featured routes. Each entry is the same shape as a
top-level [`custom_routes`](configuration.md#custom-routes-full-guide) entry.
Entries are featured by definition; their declared `color` is what displays on
the map. The routes flow through the same `custom_routes` pipeline downstream,
so they appear in the Search overlay's Routes section, can be highlighted via
tap, and respect the bucket model.

**`featured`**: references to existing routes. Each entry resolves either by
string ID (matching a top-level
[`custom_routes`](configuration.md#custom-routes-full-guide) entry) or by
integer OSM relation ID (matching an entry in `relations`, `clipped_relations`,
`winter_relations`, `summer_relations`, or `emergency_access_relations`). Use
this when:

- The route you want to feature already exists at top-level `custom_routes`.
- You're featuring a route that's already in OSM (no inline definition needed;
  just point at the relation ID).
- You want to feature an entire OSM super-relation: list its parent ID and every
  child route fans out as featured.

**`background_style`**: visual treatment applied to every non-featured route.
Default if omitted:

```yaml
background_style:
  color: gray
  pattern: [0, 2]    # dots
  cap: round
```

Pattern values are in line-width multiples (same convention as
`dashed_relations`). `[0, 2]` is dots; `[2, 2]` short dashes; `[4, 2]` long
dashes. Cap controls dash end-shape.

**Featured rendering**: featured routes render on top of background routes
(their MapLibre layers are added last, after every background route's layer) AND
with a `1.5x` line-width multiplier vs. the standard trail line. The combined
effect: the spotlighted route reads clearly as foreground, with background
trails dotted softly beneath it.

**ID uniqueness**: every `event_mode.routes[].id` must NOT collide with any
top-level `custom_routes[].id` or any OSM relation ID anywhere in the config.
The validator enforces this and points at the collision.

## How featuring works

The framework computes a featured set at build time:

1. Every `event_mode.routes[].id` joins the set (inline routes are featured by
   definition).
2. Every entry in `event_mode.featured` joins the set:
   - String references add their literal ID.
   - Int references add the literal ID OR fan out to every child route ID if the
     int is a super-relation in this map.

For each route that flows through the build:

- If its ID is in the featured set: **render with declared colour and dash
  style**. OSM-sourced featured routes use their OSM `colour=` tag (or
  `relation_colors` override). Inline featured routes use the `color` declared
  on the entry.
- Otherwise: **apply background_style**. The route's metadata is rewritten so
  the runtime renders it muted.

A featured OSM super-relation expands to its children: each child gets its
normal colour. The parent itself is dropped (super-relations have no ways of
their own; the children carry the visual).

## Background style application

The background style is applied to every non-featured route on the map,
regardless of source:

- OSM-sourced routes (from `relations:`).
- Clipped routes (from `clipped_relations:`).
- Bucket-flagged routes (winter / summer / emergency).
- Top-level `custom_routes` not listed in `event_mode.featured`.

If a curator wants to keep one specific route visible at full prominence even
though it's not the event route (e.g. an emergency-access loop kept distinct
for safety marshals, or a demo loop next to the race course), they have two
options:

- Add the route's ID to `event_mode.featured` (treat it as a second featured
  route, render in its own colour).
- Add an explicit `relation_colors` and `dashed_relations` entry for the route.
  **Curator's explicit overrides win**: any route covered by the curator's
  `relation_colors` or `dashed_relations` is left alone by event mode.

## Direction arrows

For races and group rides where direction matters (one-way race courses, signed
loops, etc.), set `event_mode.direction_arrows: true`:

```yaml
event_mode:
  routes:
    - id: race-2026
      name: "2026 Race"
      color: "#C44035"
      geometry: race-2026.geojson
  direction_arrows: true
```

Effect:

- Every inline `event_mode.routes[i]` has its features stamped with
  `oneway: "yes"` so the existing direction-arrow renderer draws arrows along
  the route in its digitised direction.
- `direction_arrows` is added to `forced_visible` on the runtime, which hides
  the rider's arrow-toggle row and forces the arrow layer always-visible. Riders
  can't disable event-route arrows.
- **Arrows render on the event route ONLY.** Any OSM-tagged oneway ways on the
  underlying trail system have their `oneway` property stripped at build time so
  the arrow renderer skips them. Without this, an event map sitting on top of a
  trail system with OSM oneway flow trails would clutter with arrows on every
  flow segment, drowning out the event-route arrows that actually matter. Build
  log will show e.g.
  `Event mode: stripped oneway from 32 non-featured feature(s) ...` to make the
  behaviour visible.

The arrows follow the order of coordinates in your GeoJSON LineString. Reverse
the coord array (or use a tool like geojson.io's "reverse direction" action) if
the arrows point the wrong way.

For per-route control (e.g. one inline route is one-way, another is two-way),
set `oneway` directly on each route entry:

```yaml
event_mode:
  routes:
    - id: race-class-a
      name: "Class A"
      color: "#FF0000"
      geometry: class-a.geojson
      oneway: "yes"             # arrows along digitised direction
    - id: open-loop
      name: "Open Loop"
      color: "#FF8800"
      geometry: open-loop.geojson
      # no oneway, no arrows on this one
  direction_arrows: true        # still required to hide the toggle
```

Featured OSM relations (referenced via `event_mode.featured: [int]`) keep their
normal OSM-tag-driven arrow behaviour: arrows render on ways tagged `oneway=yes`
/ `oneway=-1` / `oneway=reversible` per the standard rules. The
`direction_arrows: true` flag affects the toggle-suppression but doesn't add
arrows where the OSM tags don't already.

## Event POIs

Race-day fixtures: start / finish, aid stations, support areas, mechanical aid,
registration, etc. Defined inline in `event_mode.pois`, ALWAYS visible (no rider
toggle), distinctive saturated red chip with a flag glyph.

```yaml
event_mode:
  routes: [...]
  pois:
    - name: "Start / Finish"
      coordinates: [-83.123, 42.456]
    - name: "Aid Station 1"
      coordinates: [-83.124, 42.457]
      description: "Water + bananas. Mechanic on site."
    - name: "Support Vehicle"
      coordinates: [-83.125, 42.458]
      description: "First aid + sag wagon."
  poi_color: "#D32F2F"            # optional; defaults to this red
```

Each entry needs:

- **`name`** (required): shown as the popup title and used by the search
  overlay.
- **`coordinates`** (required): `[longitude, latitude]`.
- **`description`** (optional): one-line context shown below the name in the
  popup. Trusted as plain text (no HTML escaping applied; same convention as
  parking / trailhead popups).

Event POIs differ from regular POIs in four ways:

1. **Always visible.** No toggle row, no rider control. Race-day fixtures are
   essential to the map's purpose; hiding them under any condition would defeat
   that.
2. **No proximity gate.** Trail-marker / feature / toilet / water markers get
   hidden when they're far from any visible trail (`poi_proximity_m` etc.).
   Event POIs ignore that rule entirely; a finish-line bivouac 200m off-trail
   still renders.
3. **Distinct colour + glyph.** Saturated red chip (`event_mode.poi_color`,
   default `#D32F2F`) with a white flag glyph (MDI `flag`). Visually
   unmistakable as "race fixture, not OSM POI."
4. **Always-visible name label.** A small white pill below each chip shows the
   POI's `name`. Other POI categories rely on the tap-to-popup interaction; for
   race-day fixtures, the label-on-sight reading is more important than visual
   quietness.

Event POIs are indexed in the search overlay alongside other places, so a rider
can type "start" or "aid" to find them.

## GPX downloads

Offer riders the event's official course file(s) for their bike computer,
straight from the map:

```yaml
event_mode:
  routes: [...]
  gpx:
    routes:
      - name: "Long Course"
        file: long_course_2026_prepared.gpx
      - name: "Short Course"
        file: short_course_2026_prepared.gpx
```

Each entry needs:

- **`name`** (required): the label shown in the download sheet. Display only —
  it does not affect the downloaded file.
- **`file`** (required): a curator-supplied `.gpx` file, relative to this YAML
  (same convention as `geometry:`). Copied into the build **verbatim, filename
  preserved** — a rider who downloads it from the map gets a file identical,
  name included, to one distributed by the event's official source. Entries
  must not share a filename (the validator rejects duplicates).

What the rider sees: a download FAB (down-arrow glyph) in the top-right stack
below Options, present only when `gpx:` is configured. Tapping it opens a
compact bottom sheet with one row per entry; tapping a row downloads that
file. The sheet always opens — even with a single file — so an exploratory
tap shows a dismissible sheet naming the route rather than instantly dropping
a file into the rider's Downloads. The sheet stays open across row taps, so
grabbing several routes is just several taps. There is deliberately no
"download all" bundle: bike computers import one course per file anyway.

The files land in `build/<slug>/gpx/` and are picked up by the service-worker
precache automatically, so they remain downloadable offline once the map has
been visited.

Prepare the files for head-unit compatibility before dropping them in (the
framework does not transform them): GPX 1.1, one course per file, a single
continuous track segment, points ordered in the direction of travel.
Generating GPX files from the map's own route data (OSM relations / GeoJSON)
is planned but not yet implemented — the validator reserves `relation:` and
`route:` source keys for that.

## Worked examples

### Single race on a single trail system

```yaml
name: Big Bang 2026
slug: big-bang-2026
title: "Shelden's Big Bang 2026"
relations: [11298864]              # Shelden Trails

event_mode:
  routes:
    - id: bb-2026
      name: "2026 Big Bang Course"
      color: "#C44035"
      geometry: 2026_big_bang.json

logo: BigBang-2026-Logo.png        # event-specific branding
accent_color: auto                 # derive UI accent from the logo

# Optional: keep parking + trailheads visible (they're on by default).
# Toilets / water also default-on if OSM has them.
default_visible: all
```

Result: the race course displays bright red over a Shelden Trails backdrop where
every trail is grey dashed. Trailheads, parking, and any toilets / water in the
bbox render with their normal swatches.

### Multi-class race (two classes, different colours)

```yaml
name: Trail Cup 2026
slug: trail-cup-2026
title: "2026 Trail Cup"
relations: [12345678]

event_mode:
  routes:
    - id: tc-class-a
      name: "Class A: Long Course (40 mi)"
      color: "#FF0000"
      geometry: tc-class-a.geojson
    - id: tc-class-b
      name: "Class B: Short Course (20 mi)"
      color: "#FF8800"
      geometry: tc-class-b.geojson

logo: TrailCup-2026-Logo.png
```

Both class courses render in their own colours; the underlying OSM trails are
muted. Riders can tap either course in the Search overlay to highlight it.

### Featuring an existing OSM loop

For a "ride the entire Wolf Den loop" group ride where Wolf Den is already in
OSM as a relation:

```yaml
name: Wolf Den Group Ride 2026
slug: wolf-den-2026
title: "2026 Wolf Den Group Ride"
relations: [12425503]              # RAMBA super-relation

event_mode:
  featured: [8467566]              # Wolf Den (Al Quaal Loop), a child of RAMBA
```

Wolf Den renders in its OSM colour; every other RAMBA child route goes muted. No
GeoJSON needed; we're spotlighting an existing OSM loop.

### Mixed inline + referenced

A race that follows a custom course, with one OSM emergency-access loop kept
visible at full prominence for safety marshals:

```yaml
relations: [12345678]
emergency_access_relations: [99999999]

event_mode:
  routes:
    - id: race-2026
      name: "2026 Race"
      color: "#FF0000"
      geometry: race-2026.geojson
  featured: [99999999]             # also feature the emergency loop
```

The race course (red) and the emergency loop (its OSM colour) both display
prominently; everything else is muted.

### Adjusting the background style

If the default dotted gray feels too subtle and you want darker context:

```yaml
event_mode:
  routes:
    - id: race-2026
      name: "2026 Race"
      color: "#FF0000"
      geometry: race-2026.geojson
  background_style:
    color: "#444444"               # darker grey
    pattern: [4, 2]                # long dashes
    cap: square                    # crisp dash ends
```

Or if you want crisp short dashes instead of dots:

```yaml
event_mode:
  routes: [...]
  background_style:
    pattern: [2, 2]                # short dashes (override only the pattern)
```

## Branding

Event maps usually want event-specific logo and icon:

```yaml
logo: 2026-Race-Logo.png
icon: 2026-Race-Logo.png           # optional: same source for both
accent_color: auto                 # derive UI accent from the logo
```

The `icon:` key is also used as the logo source when `logo:` is omitted (so most
event maps only need to set one of the two). See
[Logo and icon assets](configuration.md#logo-and-icon-assets) in the
configuration guide.

The optional `accent_color: auto` runs a build-time Pillow analysis of the logo
to derive the UI accent palette (a vivid light-mode shade plus a lightened
dark-mode shade, each with its own text colour) from the logo's dominant
saturated colour. This lets the whole UI feel branded for the event without
manually picking a hex value.

Event maps often carry co-branding — the organising club's mark plus the
event's own logo, or a title sponsor. Use
[`additional_logos:`](configuration.md#additional-logos-additional_logos) to
stack secondary images under the primary logo in the brand mark:

```yaml
icon: sponsor_logo.webp            # primary: drives icons + accent
additional_logos:
  - path: club_logo.webp
    invert_dark: false             # colourful mark; don't invert in dark mode
```

These are display-only — icons, accent derivation, and share previews stay
keyed to the primary `logo:` / `icon:`.

## Welcome modal

Use the existing [`welcome:`](configuration.md#welcome-modal) block to write
event-specific intro copy:

```yaml
welcome:
  title: "Welcome to the 2026 Race"
  body: |
    The race course is shown in red. Everything else is muted
    context for orientation.

    Aid stations: marked as drinking water and toilets.
    Parking: marked as P.

    Race day: April 15. Briefing 8am at the start area.
  show_controls_hint: false        # we already explained the visuals above
```

## Labels in event mode

Event mode locks the Labels segmented control to the "routes" mode internally
and hides the control from the Options overlay (the rider can't flip it). On top
of that lock, only featured routes get a label layer at all, so the only on-map
trail label that ever appears is the event route's name.

Background routes get no labels. Trail-name labels (the "decor-trail-name"
decoration layer that normally renders one label per named trail when labelMode
is "trails") is also suppressed in event mode for the same reason: the event
route is the focus, not the underlying trail network.

This pairs well with `default_labels: routes` in your YAML (which seeds the
rider's first-visit experience), but event mode forces the runtime state
regardless of what's persisted in localStorage.

## Routes panel in event mode

The routes panel (the bottom-right key card + Search entry; see
[`route_panel`](configuration.md#display) in the configuration guide) keys
**featured routes only** on event maps — the muted background network isn't a
course a rider chooses between, so it doesn't earn a key row. This mirrors
the label restriction above: featured routes are the map's subject,
everything else is context. The panel's Search row still opens the full
finder, where featured AND background routes both appear.

For the common two-course event (a full and a short route), the key is the
first-glance answer to "which colour am I riding?": with `route_panel: auto`
(the default) a map with 2–5 featured routes boots with the card expanded,
showing each course's colour, name, and (when
[`show_route_distance`](configuration.md#build-time-data-gates) is on)
distance. Tapping a row highlights that course and fits it in view.

## What event mode does NOT change

Event mode is intentionally narrow. It changes how trails are styled. It does
NOT change:

- **POI rendering**: parking, trailheads, water, toilets, features, trail
  markers all render with their normal colours and toggles.
- **Search / finder**: featured AND background routes both appear in the Routes
  section. Tap either to highlight.
- **Bucket toggles**: Season switching (Summer / Winter), Emergency overlay,
  Difficulty toggle, etc. all work normally if the curator enables them.
- **Map basemap, terrain, fonts, etc.**: every other framework feature works
  exactly as it does on a normal map.

If you want to suppress UI affordances that don't fit your event map (e.g. the
Difficulty toggle, the Season toggle), use the existing per-key knobs:

- `show_difficulty: false` to skip the IMBA sprite + toggle.
- Omit `winter_relations` / `summer_relations` / `emergency_access_relations` to
  skip the Season + Emergency toggles.
- `default_labels: routes` (or `trails` or `none`) to set the initial label
  mode.
- `default_visible: all` (or a specific list) to set first-visit layer
  visibility.

These are independent of event mode; they apply to any map.

## Cross-references

- [`configuration.md`](configuration.md) for the general config reference.
- [`configuration.md#custom-routes-full-guide`](configuration.md#custom-routes-full-guide)
  for the per-route schema (inline `event_mode.routes` shares this
  shape).
- [`configuration.md#dash-patterns`](configuration.md#dash-patterns)
  for the dash-pattern primitives that `background_style` uses.
- [`configuration.md#logo-and-icon-assets`](configuration.md#logo-and-icon-assets)
  for branding asset specs.
- [`configs/reference/reference.yaml`](../configs/reference/reference.yaml)
  for the canonical annotated YAML reference.
