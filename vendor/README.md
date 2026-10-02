# Pinned vendor builds

Libraries that `scripts/build.py` ships from a copy in this directory
instead of a URL in `VENDOR_LIBS`. Each file is a verbatim upstream
build. The copy a map ships loses one line, the trailing
`//# sourceMappingURL=` comment, because no build ships a `.map` and
the pointer is otherwise a 404 in any open browser inspector.

## maplibre-gl-lanes.js

An unreleased build of maplibre-gl-lanes, after 1.2.0, from plugin
commit e8d6835 (built 2026-10-02 in a clean worktree at that commit,
sha256 dff92fba0d1a1984390ee7000b4d5e8846594fa64504fa2e94e54ef0f1ec19ed;
byte-identical to the plugin checkout's own dist). The package version
inside still reads 1.2.0. It carries every fix in the plugin's CHANGELOG
"Unreleased" section, among them: an inside-corner intersection farther
than eight lane offsets from its vertex takes the bevel (7a1d9a7: a
sub-pixel hairpin at low zoom put a lane vertex kilometers off its edge,
the spike seen on mfo's Massive MTB at z10.5); a loop-removal cut
point's anchor is interpolated along its segment (fac3278); lanes keep
sub-pixel precision across lon 0 and the equator (8f7b84d); an opaque
fill layer above the lanes covers them, translucent lanes included
(941f67b, e8d6835); every route draws at every overview zoom (7ac641c);
the dash phase no longer moves with the viewport (9886929); the worker
keeps up to 16 lane layers' graphs. Vendored by copy so the maps can be
tested on this code before it goes to npm.

Built with `corepack pnpm build` in the plugin repository, which writes
`dist/maplibre-gl-lanes.js`, the classic-script build with the global
`maplibreLanes` and the workers inlined. License: MIT.

Shipped with every map: it is what draws the routes, and a page without
it says that it cannot start. To update, rebuild upstream, copy
`dist/maplibre-gl-lanes.js` over this file, and note the commit here.
Once the release is on npm, drop this directory and point `VENDOR_LIBS`
back at unpkg by version.
