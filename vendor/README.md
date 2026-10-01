# Pinned vendor builds

Libraries that `scripts/build.py` ships from a copy in this directory
instead of a URL in `VENDOR_LIBS`. Each file is a verbatim upstream
build. The copy a map ships loses one line, the trailing
`//# sourceMappingURL=` comment, because no build ships a `.map` and
the pointer is otherwise a 404 in any open browser inspector. To
profile a deployed map against source, put the `.map` built with the
exact script beside it on a test map by hand.

## maplibre-gl-lanes.js

An unreleased build of maplibre-gl-lanes, after 1.1.0, from the
plugin's working tree on the evening of 2026-09-30. It carries 1.1.0
plus everything under "Unreleased" in the plugin's CHANGELOG at that
time: `HighlightStyle.bright` (edges kept at full opacity under a
highlight's dim, for the trail a rider taps off a lifted route), a
translucent fill blended once per pixel (dimmed lanes showed bright
spots where their pieces joined), and a dim that follows the highlight
rather than the lifted route's presence in the build. Vendored by copy
so the maps can be tested on this code before it goes to npm as 1.2.0.

Built with `corepack pnpm build` in the plugin repository, which writes
`dist/maplibre-gl-lanes.js`, the classic-script build with the global
`maplibreLanes` and the workers inlined. License: MIT.

Shipped with every map: it is what draws the routes, and a page without
it says that it cannot start. To update, rebuild upstream, copy
`dist/maplibre-gl-lanes.js` over this file, and note the date here.
Once 1.2.0 is on npm, drop this directory and point `VENDOR_LIBS` back
at unpkg by version.
