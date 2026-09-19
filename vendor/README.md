# Pinned vendor builds

Libraries that `scripts/build.py` cannot fetch by URL yet. Each file is a
verbatim copy of an upstream build. The build copies them into a map's
`vendor/` directory next to the downloaded ones.

## maplibre-gl-lanes.js

Source: the maplibre-gl-lanes repository, `main`, commit `4f4f604`
(2026-09-19). Earlier on that branch, commit `0ce9db9` added the
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

The file is 123 kB, 44 kB gzipped. It grew from 73 kB at `28f3cd3`, when
lane layout and tessellation moved off the render thread: the inlined
worker source now carries them as well as the lane orderer.

Shipped only when a map sets `lane_renderer: plugin`. To update, rebuild
upstream, copy `dist/maplibre-gl-lanes.js` over this file, and note the
commit here. Switch to a URL in `VENDOR_LIBS` once the plugin publishes a
release.
