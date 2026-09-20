# Pinned vendor builds

Libraries that `scripts/build.py` cannot fetch by URL yet. Each file is a
verbatim copy of an upstream build. The build copies them into a map's
`vendor/` directory next to the downloaded ones.

## maplibre-gl-lanes.js

Source: the maplibre-gl-lanes repository, `main`, commit `03a40a6`
(2026-09-20), built from a clean checkout of that commit. Two changes
since the previous pin, `20c3c17`, reach what a map draws.

`03a40a6` blends a translucent casing once per pixel. This engine passes
a translucent `casingColor` (white at 0.30 in dark, black at 0.50 in
light), and before this commit the casing read stronger wherever two
casings overlapped: along the seam between adjacent lanes, and in a
lens at every join between a lane and a connector. The plugin keeps its
per-pixel mark in the depth buffer, at values beyond the farthest depth
MapLibre gives any layer, and makes no stencil call, because MapLibre
reuses its tile clipping masks from the stencil buffer across layers. It
needs a depth buffer of 24 bits or more and keeps the old path without
one. One limit is documented upstream: an opaque fill layer ABOVE the
lane layer, with a translucent layer between the two, shows that layer
over the fill within a lane's width. This engine's only fill or
background layer above the lanes is its own translucent `dim-tint`, so
the case does not arise here. Keep it that way, or re-check.

`e5d38ff` fixes lanes that ended in a sub-pixel backward hook where a
node front cut an edge just past a bend. Connectors took their direction
from the hook and swung the wrong way. Lanes move by up to about 10 px
at the affected junctions (45 of 520 paths on Glacial Hills at z15, 34
of 1587 on RAMBA at z15.9, none on RAMBA at z18), and route labels and
chevrons that ride those lanes move with them.

Verified against `20c3c17` on full Glacial Hills and RAMBA builds, with
the basemap, hillshade and contours on, in light and dark, in headless
Chromium: outside the lanes' own footprint the frames are identical
apart from label placement following the moved lanes, and with the lane
layer hidden they are identical, so nothing leaks into later layers or
across tile edges. Lane layout time is unchanged. The packaging commits
between the two pins do not change this file at runtime.

Earlier pins, for the record: `d252af1` settled the plugin's public
names before 1.0, with no aliases kept, and two of the renames reach
`templates/app.js` (the layer option `sizes`, and `casingWidth` in the
object it returns). `20c3c17` makes a wrong option fail by name; in this
engine the constructor runs inside the ordering promise, so such an
error surfaces as "lanes: ordering failed" followed by the plugin's own
message. `0ce9db9` added the MapLibre 5.x fallback for the projection
data: MapLibre added `getProjectionData` to the render arguments in 6.0,
and this engine vendors 5.24, where the layer threw on every frame
without it.

The plugin rewrote its history on 2026-09-18, before going public, to
purge a file from every commit. The content is unchanged, but every
commit has a new hash. The hashes this file named before that date no
longer resolve.

Built with `corepack pnpm build`, which writes `dist/maplibre-gl-lanes.js`,
the classic-script build with the global `maplibreLanes` and the workers
inlined. License: MIT.

The file is 123 kB, 42 kB gzipped. It grew from 73 kB at `28f3cd3`, when
lane layout and tessellation moved off the render thread: the inlined
worker source now carries them as well as the lane orderer.

Shipped only when a map sets `lane_renderer: plugin`. To update, rebuild
upstream, copy `dist/maplibre-gl-lanes.js` over this file, and note the
commit here. Switch to a URL in `VENDOR_LIBS` once the plugin publishes a
release.
