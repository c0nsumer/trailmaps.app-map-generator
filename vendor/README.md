# Pinned vendor builds

Libraries that `scripts/build.py` cannot fetch by URL yet. Each file is a
verbatim copy of an upstream build. The build copies them into a map's
`vendor/` directory next to the downloaded ones.

## maplibre-gl-lanes.js

Source: the maplibre-gl-lanes repository, `main`, commit `3135599`
(2026-09-20), built from a clean checkout of that commit. Six changes
since the pin before these, `20c3c17`, reach what a map draws.

`3135599` draws the connector that was missing where two edges join the
same two junctions and a route turns from one onto the other at both.
Found on River Bends at 42.646821, -83.062909: a 56 m link's lane
stopped a junction's length short of the through path, with a round
cap, at every zoom. Connectors were cached without saying which
junction, so the second turn reused the first one's. Layout code only.
It adds connectors and changes nothing else: one more on River Bends,
two on RAMBA's Other Trails, one on Burchfield; none on MFO or Glacial
Hills.

`7e9365c` keeps a route one lane wide where it forks inside a merged
junction. Found on MFO at z13.06 near 42.7683, -83.2207: a route that
reaches a cluster of junctions both on the main bundle and on a spur,
and leaves as one, drew a lane and a half wide while the short edge
between the two arrivals was merged (about z13; fine at z12.6 and from
z13.5). Each arrival had crossed the merged junction on its own
connector. Layout code only, no renderer change, no option. A handful
of paths move at low zoom on MFO, RAMBA, BDB and DTE; none on Glacial
Hills.

`84db91f` adds the layer option `openFolds`, default true, which this
engine leaves at its default. Where a bundled path folds back on itself
more tightly than the bundle is wide, the two legs are pushed apart and
the apex becomes a half circle through the original tip, so every lane
nests round the hairpin. Before it, the inner lanes cut across such a
hairpin and on some a whole lane vanished through the turn (Glacial
Hills at z15 to z16, for example near 44.990502, -85.236463). No point
moves more than half the bundle's width, in pixels, so the effect fades
as the zoom opens the fold. Stacks of switchbacks, hairpins whose apex
is a junction, and anything within a lane of a junction are left alone
by design. `openFolds: false` restores the previous lines exactly.

`174bc3c` draws dots (`dash: [0, gap]`, this engine's "Other Trails"
look) as one quad per dot. Worked out in the shader along the ribbon
they came out as wedges and half discs at sharp bends. Positions along
the route are unchanged. The mesh and the worker response gained a
field for it, so the script and its inlined worker must come from one
build, as they always do here.

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

Verified step by step on full Glacial Hills, RAMBA and MFO builds, with the
basemap, hillshade and contours on, in light and dark and in winter, in
headless Chromium. `03a40a6` against `20c3c17`, and `84db91f` against
`03a40a6`, and `7e9365c` against `84db91f` on MFO and RAMBA: outside the lanes' own footprint the frames are identical
apart from route labels and chevrons following the moved lanes, and on
every view without moved lanes they are identical with the lane layer
hidden, so nothing leaks into later layers or across tile edges. The
casing acceptance frame (Glacial Hills z18 at 44.98889, -85.24598) is
pixel-identical between `03a40a6` and `84db91f`. The packaging commits
between the pins do not change this file at runtime.

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

The file is 137 kB, 49 kB gzipped. It grew from 73 kB at `28f3cd3`, when
lane layout and tessellation moved off the render thread: the inlined
worker source now carries them as well as the lane orderer.

Shipped with every map except one that sets `lane_renderer: native`
(the plugin is the default renderer). To update, rebuild
upstream, copy `dist/maplibre-gl-lanes.js` over this file, and note the
commit here. Switch to a URL in `VENDOR_LIBS` once the plugin publishes a
release.
