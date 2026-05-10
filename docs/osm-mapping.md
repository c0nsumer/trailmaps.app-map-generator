# Mapping and tagging trails in OpenStreetMap

This framework reads its trail data straight from
[OpenStreetMap](https://www.openstreetmap.org/) via Overpass — the
better the OSM data is, the better the rendered map is. This document
describes the tags this renderer reads and offers general advice on
mapping trail systems well.

## Don't tag for the renderer

Everything below is "tag what the trail actually is, in standard OSM
conventions, and a sensible renderer will surface it." Nothing here is
specific to this map framework. Adding tags purely to manipulate how
this (or any) renderer draws something is an anti-pattern that
degrades the OSM dataset for every other consumer:

- [OSM Wiki — Tagging for the renderer](https://wiki.openstreetmap.org/wiki/Tagging_for_the_renderer)

If something doesn't show up the way you'd like, the right fix is
almost always one of:

1. The OSM data is wrong or incomplete — fix it correctly.
2. The OSM data is fine but the renderer doesn't read that tag — file
   an issue (or a PR) on this framework, not a workaround in OSM.

The tags listed here are the ones this renderer happens to consume.
They are all standard, widely-used OSM tags — there are no custom
keys, no `trailmaps:*` namespace, nothing specific to this project.
Mapping a trail system using these conventions also makes the data
useful in OsmAnd, OpenAndroMaps, Trailforks, Komoot, and every other
OSM consumer.

## Trail system structure: relations

The framework expects each trail system (or each named route within a
system) to be a **relation** of [`type=route`](https://wiki.openstreetmap.org/wiki/Relation:route).
A super-relation grouping multiple route relations is also supported —
the renderer expands it one level deep at fetch time.

### Required on the relation

| Tag | Value | Notes |
|---|---|---|
| [`type`](https://wiki.openstreetmap.org/wiki/Key:type) | [`route`](https://wiki.openstreetmap.org/wiki/Relation:route) | Identifies the relation as a route relation. |
| [`route`](https://wiki.openstreetmap.org/wiki/Key:route) | [`mtb`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dmtb) / [`bicycle`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dbicycle) / [`hiking`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dhiking) / [`foot`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dfoot) / [`ski`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dski) / [`snowmobile`](https://wiki.openstreetmap.org/wiki/Tag:route%3Dsnowmobile) / etc. | The activity type. The framework doesn't filter on this — any `type=route` relation works — but it's standard OSM and helps other consumers. |

### Recommended on the relation

| Tag | Used by | What this renderer does with it |
|---|---|---|
| [`name`](https://wiki.openstreetmap.org/wiki/Key:name) | name | Route's display name — appears in the search/finder, in tap popups, and as a label on the map when labels are set to "routes." Falls back to `Route <id>` if missing. |
| [`ref`](https://wiki.openstreetmap.org/wiki/Key:ref) | short code | Short reference / number for the route. Surfaced in the finder secondarily; doesn't drive any geometry. |
| [`colour`](https://wiki.openstreetmap.org/wiki/Key:colour) | line colour | A CSS-compatible colour string (`#RRGGBB`, named colours like `red`, `rgb(...)`, etc.). Falls back to the per-map `default_trail_color` (typically `#808080`) when unset. The hex form is preferred; named colours render but vary slightly between consumers. |
| [`network`](https://wiki.openstreetmap.org/wiki/Key:network) | (not consumed) | Standard OSM network tag (e.g. `mtb` / `lcn` / `rcn`). Not used by this renderer but useful elsewhere — set it. |
| [`seasonal`](https://wiki.openstreetmap.org/wiki/Key:seasonal) | passthrough | Free-text seasonality info; passed through into route metadata. |

### Relation members

Each route relation's members should be the **ways** that make up the
trail. The framework follows the standard OSM convention: member ways,
in any order, with no role. The renderer reconstructs trail order via
shared endpoints when it needs to (for label placement, IMBA-tier
sequencing, etc.).

If a single physical trail belongs to multiple routes (a connector
segment shared between two loops, for example), tag it as a member of
**every** route relation it belongs to. The renderer collapses
duplicates by way ID and tracks the "shared routes" set on each
geometry feature so taps surface the right thing.

### Super-relations (multi-loop trail systems)

A trail system that's organised as several named loops or
sub-routes under one umbrella is best mapped as a **super-relation**:
a `type=route` relation whose members are the individual sub-route
relations (each itself `type=route` with its own ways). This is
standard OSM — the same convention used by long-distance hiking
networks, regional bike networks, etc.

A super-relation lets the curator point this framework's `relations:`
config key at a single ID and have every constituent loop come along
automatically. When the framework fetches a super-relation, it
**expands one level deep**:

- The super-relation itself is dropped from the result (no umbrella
  "route" appears in the finder; it has no geometry of its own).
- Each child sub-route becomes a route the rider can highlight,
  search, and toggle individually.
- Tags on the super-relation (`name`, `colour`, etc.) are **not**
  inherited by children — each child needs its own tags. This is
  important: a child without a `name` falls back to `Route <id>`,
  and a child without a `colour` falls back to the per-map
  `default_trail_color`.

Only one level of nesting is supported. A super-relation containing
super-relations would need to be flattened in OSM (or every leaf
listed individually in the per-map `relations:` config).

#### Example: DTE Energy Foundation Trail

The [DTE map](https://trailmaps.app/dte/) is a real-world example.
The per-map config lists a single OSM relation:

```yaml
relations: [6364861]
```

[Relation `6364861`](https://www.openstreetmap.org/relation/6364861)
is the super-relation `DTE Energy Foundation Trail`. At fetch time it
expands into seven child route relations, each with its own
`name=`, `colour=`, member ways, and (where appropriate) IMBA / oneway
tagging on those ways:

| Child relation | Name |
|---|---|
| `12255871` | Big Kame Loop |
| `12255870` | Sugar Loop |
| `12255872` | Winn Loop |
| `12255873` | Green Lake Loop |
| `12255869` | Connectors |
| `13291234` | Winter Grooming |
| `13291235` | Emergency Access |

Each of those is a `type=route` relation in OSM, with the super-relation
as its parent. Tagging-wise the super-relation carries the umbrella
identity (`name=DTE Energy Foundation Trail`, plus the area's tagging),
and the children carry the per-loop colours and names that the rider
sees when they highlight a route in the finder.

When you set up a new multi-loop trail system, follow the same shape:
one super-relation per system, each loop as its own child relation
with its own colour and name, and ways tagged at the way level.

## Trail-segment (way) tags

These go on the individual ways that the route relation references —
not on the relation itself.

### Mountain-bike difficulty

| Tag | Value | What this renderer does with it |
|---|---|---|
| [`mtb:scale:imba`](https://wiki.openstreetmap.org/wiki/Key:mtb:scale:imba) | `0` / `1` / `2` / `3` / `4` / `5` | Drives the IMBA difficulty diamond glyphs and (under `color_by: trail`) the trail's line colour. |

The IMBA-rating scale, condensed:

| Rating | Glyph | Difficulty |
|---|---|---|
| `0` | white circle | Easiest (rolling, smooth, gentle grades) |
| `1` | green circle | Easy |
| `2` | blue square | More difficult |
| `3` | black diamond | Very difficult |
| `4` | double black diamond | Extremely difficult |
| `5` | double black with orange highlight | Pro-only / extreme features |

Tag the actual difficulty of the segment, not what you wish were on
the map. A segment without a rating renders as the default trail
colour (so it's still visible — just unflagged).

### Direction (one-way trails)

| Tag | Value | What this renderer does with it |
|---|---|---|
| [`oneway`](https://wiki.openstreetmap.org/wiki/Key:oneway) | `yes` / `no` / `reversible` | When `yes`, the renderer places direction arrows along the trail and drives the share/finder direction-aware behaviour. `reversible` is supported via `direction_schedule:` in the per-map config (alternating direction by day-of-week or parity). |
| [`oneway:bicycle`](https://wiki.openstreetmap.org/wiki/Key:oneway:bicycle) | `yes` / `no` / `reversible` | Wins over `oneway` when both are present. Use this when a trail is one-way for bikes but two-way for hikers (or vice versa) — same standard OSM convention used everywhere. |

### Names on individual ways (optional)

| Tag | Value | What this renderer does with it |
|---|---|---|
| [`name`](https://wiki.openstreetmap.org/wiki/Key:name) | string | Way-level trail name ("Pipe Dreams," "Old Camp Ridge"). Surfaced in the search/finder under "Trails" and in tap popups. When a trail name is the same as the parent route's name, the renderer dedupes; when they differ, both are shown. |

A trail system where each named singletrack is a separate way (or set
of contiguous ways) with a `name=` tag gives the richest experience —
riders can search for individual trails by name. If only the parent
route has a name and the member ways are unnamed, the search still
works at route granularity.

### Highway type, surface, and access

The renderer doesn't gate on these but they're standard OSM tags
worth setting correctly so the data is useful elsewhere:

- [`highway=path`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dpath)
  is the typical value for singletrack;
  [`highway=track`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dtrack)
  for fire-road / two-track surfaces.
- [`bicycle=designated`](https://wiki.openstreetmap.org/wiki/Tag:bicycle%3Ddesignated)
  / [`foot=designated`](https://wiki.openstreetmap.org/wiki/Tag:foot%3Ddesignated)
  / etc. as appropriate (see also [`Key:bicycle`](https://wiki.openstreetmap.org/wiki/Key:bicycle)
  and [`Key:foot`](https://wiki.openstreetmap.org/wiki/Key:foot)).
- [`surface`](https://wiki.openstreetmap.org/wiki/Key:surface) =
  `ground` / `dirt` / `gravel` / `compacted` / etc.
- [`access`](https://wiki.openstreetmap.org/wiki/Key:access) =
  `permissive` / `private` / `customers` etc. when there's
  permission tagging to convey.

## POIs (points of interest)

The framework fetches a small set of POI categories from the
**bounding-box of the route relations** (Overpass query, not from the
relation members themselves). They render as markers on the map when
the corresponding `show_*` config gate is on.

| OSM tagging | Category | What this renderer does with it |
|---|---|---|
| [`tourism=information`](https://wiki.openstreetmap.org/wiki/Tag:tourism%3Dinformation) + [`information=guidepost`](https://wiki.openstreetmap.org/wiki/Tag:information%3Dguidepost) | trail markers / guideposts | Renders as small numbered markers along trails. The [`ref`](https://wiki.openstreetmap.org/wiki/Key:ref) value is shown on the marker; [`name`](https://wiki.openstreetmap.org/wiki/Key:name) and [`ele`](https://wiki.openstreetmap.org/wiki/Key:ele) (elevation) are surfaced on tap. |
| `tourism=information` + `information=guidepost` + [`highway=emergency_access_point`](https://wiki.openstreetmap.org/wiki/Tag:highway%3Demergency_access_point) | emergency access points | Same as guideposts but rendered with a distinct emergency-marker style. Used for "the point you're closest to if you need rescue." |
| [`tourism=attraction`](https://wiki.openstreetmap.org/wiki/Tag:tourism%3Dattraction) | features | Scenic viewpoints, named rocks, monuments, etc. Surfaced on tap with `name` + [`description`](https://wiki.openstreetmap.org/wiki/Key:description). |
| [`amenity=toilets`](https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dtoilets) | toilets | Renders the toilet marker. `name`, [`access`](https://wiki.openstreetmap.org/wiki/Key:access), [`fee`](https://wiki.openstreetmap.org/wiki/Key:fee), and [`opening_hours`](https://wiki.openstreetmap.org/wiki/Key:opening_hours) show in the popup when present. |
| [`amenity=drinking_water`](https://wiki.openstreetmap.org/wiki/Tag:amenity%3Ddrinking_water) | drinking water | Renders the water marker. `name` and `seasonal` show in the popup when present. |

Parking and trailheads aren't fetched from OSM by default — the
framework expects them to be supplied per-map in the YAML
(`trailheads:` / `parking:` blocks) because curators usually have
specific points they want to surface. If they exist as
[`amenity=parking`](https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dparking)
in OSM, you can still reference them by their lat/lon in the YAML.

## Practical advice

- **Start with the relation, then add ways.** Every trail starts as
  a way; assemble those ways into a route relation once the system's
  shape is settled. Mapping the relation first makes it easy to track
  which ways still need to be attached.
- **One way per name.** If a singletrack changes character (rating,
  surface, direction) along its length, split it at the transition.
  IMBA difficulty, `oneway`, and `surface` all live on ways, so
  splitting is the only way to express variation.
- **Don't merge across junctions.** Even if two segments share a name,
  if they meet at a junction with a third trail, they should be
  separate ways meeting at the junction node. Routing tools and
  trail-network analysers depend on this.
- **Verify on the ground when you can.** Trail systems evolve fast —
  rerouted sections, new builds, decommissioned trails. Local mapping
  beats armchair tracing for accuracy.
- **Get the colour right at the relation level.** When the renderer
  uses `color_by: relation` (the default), the relation's `colour=`
  tag is what riders see for that route. A relation without `colour=`
  renders in the per-map default (usually grey) — fine as a fallback
  but lifeless on a complex map.

## Example: a minimal MTB route

Way `123` (a singletrack):
```
highway=path
bicycle=designated
mtb:scale:imba=2
oneway:bicycle=yes
surface=ground
name=Old Camp Ridge
```

Relation `456` (the named route this way belongs to):
```
type=route
route=mtb
name=NTN Marquette South Trails
ref=NTN-S
colour=#0074D9
network=mtb
```

The relation lists way 123 (and any others) as members with empty
roles. The framework picks the relation up via Overpass, walks its
member ways, applies the IMBA / oneway tagging to each segment, and
renders.

## Where to learn more about OSM tagging

- [OSM Wiki: Mountain biking](https://wiki.openstreetmap.org/wiki/Mountain_biking)
- [OSM Wiki: Tag:mtb:scale:imba=*](https://wiki.openstreetmap.org/wiki/Key:mtb:scale:imba)
- [OSM Wiki: Relation:route](https://wiki.openstreetmap.org/wiki/Relation:route)
- [OSM Wiki: Key:oneway](https://wiki.openstreetmap.org/wiki/Key:oneway)
- [OSM Wiki: Tagging for the renderer](https://wiki.openstreetmap.org/wiki/Tagging_for_the_renderer)
