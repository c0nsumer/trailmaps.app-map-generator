# Pinned vendor builds

Libraries that `scripts/build.py` cannot fetch by URL yet. Each file is a
verbatim copy of an upstream build. The build copies them into a map's
`vendor/` directory next to the downloaded ones.

## maplibre-gl-lanes.js

Source: the maplibre-gl-lanes repository, `main`, commit `d47b15a`
(2026-09-18). Earlier on that branch, commit `2728420` added the MapLibre
5.x fallback for the projection data. MapLibre added `getProjectionData`
to the render arguments in 6.0, and this engine vendors 5.24, where the
layer threw on every frame without it.

Built with `corepack pnpm build`, which writes `dist/maplibre-gl-lanes.js`,
the classic-script build with the global `maplibreLanes` and the workers
inlined. License: MIT.

The file is 117 kB, 42 kB gzipped. It grew from 73 kB at `8596e19`, when
lane layout and tessellation moved off the render thread: the inlined
worker source now carries them as well as the lane orderer.

Shipped only when a map sets `lane_renderer: plugin`. To update, rebuild
upstream, copy `dist/maplibre-gl-lanes.js` over this file, and note the
commit here. Switch to a URL in `VENDOR_LIBS` once the plugin publishes a
release.
