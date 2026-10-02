# Pinned vendor builds

Libraries that `scripts/build.py` ships from a copy in this directory
instead of a URL in `VENDOR_LIBS`. Each file is a verbatim upstream
build. The copy a map ships loses one line, the trailing
`//# sourceMappingURL=` comment, because no build ships a `.map` and
the pointer is otherwise a 404 in any open browser inspector.

## maplibre-gl-lanes.js

An unreleased build of maplibre-gl-lanes, after 1.2.0, from plugin
commit 76e6556 (pushed to GitHub main; built 2026-10-02 in a clean
worktree at that commit, sha256
dec0bda8f42c68ccf1ac00a15a78d45e9bb410cdffde871061a0800a71d0d610;
byte-identical to the plugin session's lab build). The package version
inside still reads 1.2.0; 1.3.0 release prep follows. It carries every
fix in the plugin's CHANGELOG "Unreleased" section, among them: the
inside-corner bevel past eight lane offsets (7a1d9a7, the mfo spike),
the loop-removal cut anchor (fac3278), sub-pixel precision across lon 0
and the equator (8f7b84d), opaque fills above the lanes covering them,
translucent lanes included (941f67b, e8d6835), every route at every
overview zoom (7ac641c), the dash phase fixed to the map (9886929), the
16-layer worker graph cache, and after e8d6835 four performance-only
commits with identical output: faster overview layouts (6d59c94), faster
dashed rebuilds (a012a72), no per-frame GPU round trip for depth or
context loss (39dc8f1), a benchmark fix (76e6556). Vendored by copy so
the maps can be tested on this code before it goes to npm.

Built with `corepack pnpm build` in the plugin repository, which writes
`dist/maplibre-gl-lanes.js`, the classic-script build with the global
`maplibreLanes` and the workers inlined. License: MIT.

Shipped with every map: it is what draws the routes, and a page without
it says that it cannot start. To update, rebuild upstream, copy
`dist/maplibre-gl-lanes.js` over this file, and note the commit here.
Once the release is on npm, drop this directory and point `VENDOR_LIBS`
back at unpkg by version.
