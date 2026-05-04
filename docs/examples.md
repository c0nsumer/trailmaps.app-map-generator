# Examples

Real-world configurations drawn from production trail systems
deployed at [trailmaps.app](https://trailmaps.app). Each section
covers one map, what makes it interesting from a config standpoint,
and the YAML knobs the curator turned. The annotated reference at
[`configs/example/reference.yaml`](../configs/example/reference.yaml)
explains every key in isolation; this document shows how the keys
combine in practice.

> **Note.** The detail under each map is currently a placeholder.
> Steve Vigneau (the curator) will fill these in over time as each
> map's particulars are documented. Cross-link from here to the live
> deployment at `https://trailmaps.app/<slug>` for a quick look at
> any map.

## Contents

- [RAMBA Trails (Marquette, MI)](#ramba-trails-marquette-mi)
- [Glacial Hills (Bellaire, MI)](#glacial-hills-bellaire-mi)
- [Potawatomi Trail (Pinckney, MI)](#potawatomi-trail-pinckney-mi)
- [DTE Energy Foundation Trail (Brighton, MI)](#dte-energy-foundation-trail-brighton-mi)
- [Potoloo (DTE + Pinckney route)](#potoloo-dte--pinckney-route)
- [Shelden Trails at Stony Creek (Shelby Twp, MI)](#shelden-trails-at-stony-creek-shelby-twp-mi)
- [Bloomer + Clinton River Oaks (Rochester Hills, MI)](#bloomer--clinton-river-oaks-rochester-hills-mi)
- [Addison Oaks County Park (Leonard, MI)](#addison-oaks-county-park-leonard-mi)
- [Oakdale Trails (Wixom, MI)](#oakdale-trails-wixom-mi)
- [Tenhave Woods (Royal Oak, MI: test data)](#tenhave-woods-royal-oak-mi-test-data)

## RAMBA Trails (Marquette, MI)

- **Live map**: <https://trailmaps.app/ramba>
- **Config**: [`configs/ramba/ramba.yaml`](../configs/ramba/ramba.yaml)
- **Anchor relation**: [12425503](https://www.openstreetmap.org/relation/12425503)

What makes this map worth studying:

- TODO (curator to fill in).

Notable config decisions:

- TODO

## Glacial Hills (Bellaire, MI)

- **Live map**: <https://trailmaps.app/glacialhills>
- **Config**: [`configs/glacialhills/glacialhills.yaml`](../configs/glacialhills/glacialhills.yaml)
- **Anchor relation**: [12562243](https://www.openstreetmap.org/relation/12562243)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## Potawatomi Trail (Pinckney, MI)

- **Live map**: <https://trailmaps.app/poto>
- **Config**: [`configs/poto/poto.yaml`](../configs/poto/poto.yaml)
- **Anchor relation**: [12381246](https://www.openstreetmap.org/relation/12381246)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## DTE Energy Foundation Trail (Brighton, MI)

- **Live map**: <https://trailmaps.app/dte>
- **Config**: [`configs/dte/dte.yaml`](../configs/dte/dte.yaml)
- **Anchor relation**: [6364861](https://www.openstreetmap.org/relation/6364861)

What makes this map worth studying:

- Uses `show_route_distance` and `show_route_elevation` to expose
  per-route stats in the Finder.
- TODO (curator to add the rest).

Notable config decisions:

- TODO

## Potoloo (DTE + Pinckney route)

- **Live map**: <https://trailmaps.app/potoloo>
- **Config**: [`configs/potoloo/potoloo.yaml`](../configs/potoloo/potoloo.yaml)
- **Source relations**: [6364861](https://www.openstreetmap.org/relation/6364861) (DTE), [12381246](https://www.openstreetmap.org/relation/12381246) (Potawatomi), [14293352](https://www.openstreetmap.org/relation/14293352) (Poto-DTE Connector)

What makes this map worth studying:

- Combines the DTE Energy super-relation with two additional
  relations to cover a multi-system loop. A good case study in
  multi-entry `relations:` lists with super-relation expansion.
- TODO (curator to add the rest).

Notable config decisions:

- TODO

## Shelden Trails at Stony Creek (Shelby Twp, MI)

- **Live map**: <https://trailmaps.app/shelden>
- **Config**: [`configs/shelden/shelden.yaml`](../configs/shelden/shelden.yaml)
- **Anchor relation**: [11298864](https://www.openstreetmap.org/relation/11298864)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## Bloomer + Clinton River Oaks (Rochester Hills, MI)

- **Live map**: <https://trailmaps.app/bloomer>
- **Config**: [`configs/bloomer/bloomer.yaml`](../configs/bloomer/bloomer.yaml)
- **Anchor relation**: [20512969](https://www.openstreetmap.org/relation/20512969)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## Addison Oaks County Park (Leonard, MI)

- **Live map**: <https://trailmaps.app/addison>
- **Config**: [`configs/addison/addison.yaml`](../configs/addison/addison.yaml)
- **Source relations**: [12666899](https://www.openstreetmap.org/relation/12666899) (Addison Oaks), [12562195](https://www.openstreetmap.org/relation/12562195)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## Oakdale Trails (Wixom, MI)

- **Live map**: <https://trailmaps.app/oakdale>
- **Config**: [`configs/oakdale/oakdale.yaml`](../configs/oakdale/oakdale.yaml)
- **Anchor relation**: [11085831](https://www.openstreetmap.org/relation/11085831)

What makes this map worth studying:

- TODO

Notable config decisions:

- TODO

## Tenhave Woods (Royal Oak, MI: test data)

- **Live map**: not deployed publicly (development / test bed).
- **Config**: [`configs/tenhave/tenhave.yaml`](../configs/tenhave/tenhave.yaml)
- **Anchor relation**: synthetic (`-102`), loaded from `osm.osm`.

What makes this map worth studying:

- The framework's primary fixture for the `osm_file:` code path. The
  trail data lives in a JOSM-edited `osm.osm` file next to the YAML
  rather than being fetched from Overpass.
- The negative relation ID (`-102`) is a JOSM convention for unsaved
  relations: useful when iterating on a trail system that hasn't
  been pushed to OSM yet.
- TODO (curator to add the rest).

Notable config decisions:

- TODO
