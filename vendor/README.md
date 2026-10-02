# Pinned vendor builds

Libraries that `scripts/build.py` ships from a copy in this directory
instead of a URL in `VENDOR_LIBS`. Each file is a verbatim upstream
build. The copy a map ships loses one line, the trailing
`//# sourceMappingURL=` comment, because no build ships a `.map` and
the pointer is otherwise a 404 in any open browser inspector.

## maplibre-gl-lanes.js

An unreleased build of maplibre-gl-lanes, after 1.2.0, from plugin
commit fac3278 (built 2026-10-01 in a clean worktree at that commit,
sha256 d2ac64c0e6e29120006e393b9d7d5a3d86fce555cace5ce19516cef681c3efcd).
The package version inside still reads 1.2.0. It carries two fixes over
the release: an inside-corner intersection farther than eight lane
offsets from its vertex now takes the bevel (7a1d9a7: a sub-pixel
hairpin at low zoom put a lane vertex kilometers off its edge, the spike
seen on mfo's Massive MTB at z10.5), and a loop-removal cut point's
anchor is interpolated along its segment (fac3278: the vertex no longer
drifts off the lane between rebuilds while zooming). Vendored by copy so
the maps can be tested on this code before it goes to npm.

Built with `corepack pnpm build` in the plugin repository, which writes
`dist/maplibre-gl-lanes.js`, the classic-script build with the global
`maplibreLanes` and the workers inlined. License: MIT.

Shipped with every map: it is what draws the routes, and a page without
it says that it cannot start. To update, rebuild upstream, copy
`dist/maplibre-gl-lanes.js` over this file, and note the commit here.
Once the release is on npm, drop this directory and point `VENDOR_LIBS`
back at unpkg by version.
