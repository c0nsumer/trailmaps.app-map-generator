# Elevation data

When `show_route_elevation: true` is set on a map, per-route climb (`gain`) and
descent (`loss`) totals appear in the Finder rows and highlight chip. This
document explains where those numbers come from, how they're computed, and why
they may not match what your GPS or another app says.

## Contents

- [Source: USGS 3DEP](#source-usgs-3dep)
- [How it's computed](#how-its-computed)
- [Why both gain and loss](#why-both-gain-and-loss)
- [Caveats: when the numbers may be wrong](#caveats-when-the-numbers-may-be-wrong)
- [Diagnostic tool](#diagnostic-tool)

## Source: USGS 3DEP

Elevation samples are fetched at build time from the **U.S. Geological Survey 3D
Elevation Program (3DEP)**, specifically the public
`3DEPElevation/ImageServer/getSamples` ArcGIS endpoint at
`elevation.nationalmap.gov`. 3DEP is a multi-resolution DEM mosaic that serves
whichever underlying raster is highest-resolution at each query point:

- **1m lidar-derived bare-earth**: covers most of the US. Bare-earth means
  vegetation has been removed by lidar processing, so the elevations are ground
  level, not canopy top.
- **10m (1/3 arc-second)**: fallback in areas without lidar.
- **30m (1 arc-second)**: final fallback. Rare in the contiguous US.

The endpoint is free, requires no API key, has no documented daily quota, and
supports up to 2000 sample points per request. The only practical failure mode
is occasional HTTP 502 under service load, handled by automatic retry.

**The framework is US-only for elevation.** A point outside US 3DEP coverage
returns `NoData`, and that route's `elevation_*_m` fields are omitted from
`trails.geojson`. The runtime renders such routes without elevation stats.

## How it's computed

For each route, the framework samples 3DEP elevation about every 25 m along the
route, smooths the profile to remove lidar noise, discards sub-metre changes as
noise, then sums the rises as `gain` and the drops as `loss` (rounded to whole
metres). The sampling spacing and the noise threshold are tuned together so that
anything a rider would call "climbing" is counted while sensor noise and
rolling-terrain jitter are not.

The result is stored per route and shown in the rider's chosen units (feet for
`distance_units: mi`, metres for `distance_units: km`).

## Why both gain and loss

OSM doesn't tell us which direction a route is intended to be ridden. Our
segment walk gives us an arbitrary feature-order direction that's rarely the
same as the rider's actual path. Showing both `gain` and `loss` lets:

- Loops read as **balanced** (numbers should match within ~5%).
- Out-and-back routes also read as balanced (you climb what you descend going
  home).
- One-way / asymmetric routes communicate their nature through the difference.

We don't claim to know direction; we just expose both numbers and let the rider
interpret.

## Caveats: when the numbers may be wrong

The framework's elevation numbers reflect the **DEM's interpretation of the
natural terrain along the centerline of the OSM way**. There are several reasons
the values may not match what you'd get from a phone or GPS device:

1. **Trail features built BEFORE the lidar acquisition are mostly captured.**
   USGS 3DEP lidar in Michigan was acquired 2015 to 2020 (the MiSAIL statewide
   acquisition). Berms, rollers, tabletops, jump piles, and similar earth-moving
   that existed at acquisition time are typically retained in the bare-earth
   DEM: the classification algorithm treats compacted-dirt features as "ground"
   and only filters above-ground returns (trees, buildings, vehicles). So Roller
   Coaster style trails really do read with extra vertical from their rollers,
   and the difference vs. the natural-terrain-only number can be substantial.

2. **Trail features built AFTER the lidar acquisition are NOT in the data.** A
   new flow trail cut into the woods in 2024 won't show in 2017 lidar; the DEM
   still reflects the pre-trail forest floor. The framework can't know which
   bits of trail postdate the lidar.

3. **Sub-meter MTB micro-features are below DEM resolution.** Roots, rock
   features, log overs, drainage swales smaller than a metre: none of these are
   resolved by 1m lidar and certainly not by 10m or 30m fallback. The DEM tells
   you about the landform at metre scale; it doesn't tell you about the trail
   surface at finger and tire scale.

4. **Aliasing on dense small features.** Glacial Hills' kettle moraines are
   mostly 5 to 15m features. At 25m sampling we capture them well on average,
   but in heavily-corrugated terrain, small differences in where samples land
   can produce visible gain / loss asymmetry on routes that are actually loops.
   This isn't a bug; it's an unavoidable consequence of any finite-rate sampling
   on small features. Loop-asymmetry alone isn't a reliable signal of
   one-way-vs-loop nature.

5. **OSM way geometry.** The DEM gets sampled at the OSM way's centerline. If
   the way is digitised loosely (typical: 5 to 15m horizontal accuracy for OSM
   trail mapping), the sampled elevations are slightly off-trail. On steep
   cross-slope sections this can introduce errors of several metres per sample.
   Better OSM geometry produces more accurate elevation.

6. **Routes that aren't true loops.** If an OSM relation isn't actually a closed
   ring (lollipop sticks, multiple in/out points, intentional one-way segments),
   gain not equal to loss is correct and not a measurement error.

7. **Why your phone or Strava shows different numbers.** Phone-based and
   bike-computer elevation typically combines GPS-derived horizontal position
   with a barometric altimeter. Barometric data is high-resolution but
   absolute-pressure-drift-prone; GPS vertical is noisy at the metre scale.
   Strava and similar apps then post-process aggressively (smoothing, "elevation
   correction" against their own DEM, etc.). Our values are pre-computed from a
   fixed DEM source: they don't depend on any individual rider's device.
   Different methodologies, different results. Neither is "ground truth"; ground
   truth would require a survey-grade traverse.

## Diagnostic tool

`scripts/compare_elevation_sources.py` is kept in the repo as a permanent
diagnostic. It can fetch elevations from multiple sources for the same routes
and print a side-by-side comparison. Use it if a future alternative source
becomes interesting, or if a specific map's numbers look suspect:

```bash
.venv/bin/python scripts/compare_elevation_sources.py --config configs/<slug>/<slug>.yaml --source 3dep
```

It has its own cache directory (`cache/comparison/`) so it never interferes with
production builds.
