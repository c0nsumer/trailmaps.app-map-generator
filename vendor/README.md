# Pinned vendor builds

Libraries that `scripts/build.py` cannot fetch by URL yet. Each file is a
verbatim copy of an upstream build. The build copies them into a map's
`vendor/` directory next to the downloaded ones.

## maplibre-gl-lanes.js

Source: the maplibre-gl-lanes repository, `main`, commit `20c3c17`
(2026-09-19), built from a clean checkout of that commit. The commit
before it, `d252af1`, settles the plugin's public names before 1.0, with
no aliases kept. Two of the renames reach this engine, both in
`templates/app.js`: the layer option `style` is now `sizes`, and the
object that callback returns carries `casingWidth` where it carried
`casing`. `20c3c17` makes a wrong option fail by name: the constructor
throws when `sizes` is not a function, and the returned object is
checked for finite numbers. In this engine the constructor runs inside
the ordering promise, so such an error surfaces as "lanes: ordering
failed" followed by the plugin's own message. Neither commit changes
lane geometry: a RAMBA build against `34a0aa3` and one against `20c3c17`
render pixel-identical. Earlier on that branch, commit `0ce9db9` added the
MapLibre 5.x fallback for the projection data. MapLibre added
`getProjectionData` to the render arguments in 6.0, and this engine
vendors 5.24, where the layer threw on every frame without it.

The plugin rewrote its history on 2026-09-18, before going public, to
purge a file from every commit. The content is unchanged, but every
commit has a new hash. The hashes this file named before that date no
longer resolve.

Built with `corepack pnpm build`, which writes `dist/maplibre-gl-lanes.js`,
the classic-script build with the global `maplibreLanes` and the workers
inlined. License: MIT.

The file is 120 kB, 39 kB gzipped. It grew from 73 kB at `28f3cd3`, when
lane layout and tessellation moved off the render thread: the inlined
worker source now carries them as well as the lane orderer.

Shipped only when a map sets `lane_renderer: plugin`. To update, rebuild
upstream, copy `dist/maplibre-gl-lanes.js` over this file, and note the
commit here. Switch to a URL in `VENDOR_LIBS` once the plugin publishes a
release.
