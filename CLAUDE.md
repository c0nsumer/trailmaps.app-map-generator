# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static MTB trail-map generator: each map is described by one YAML config, and the Python build pipeline fetches OSM trail data (Overpass), extracts basemap/terrain PMTiles, and emits a self-contained static site (HTML/CSS/JS + PMTiles) under `build/<slug>/`. No runtime services, no CDNs, optional PWA. This engine powers most maps at trailmaps.app; the trailmaps.app-specific configs live in a separate private orchestrator repo that drives this engine as a CLI.

## Commands

Run from the repo root with the venv active (`source .venv/bin/activate`).

```bash
# Build a map (example config is the smoke test)
python scripts/build.py configs/example/example.yaml

# Fast iteration on templates/config: skip tile extraction, skip minification
python scripts/build.py configs/example/example.yaml --no-basemap --no-terrain --no-minify

# Validate config only / print the plan without building
python scripts/validate_config.py configs/example/example.yaml
python scripts/build.py configs/example/example.yaml --dry-run

# Preview output (Range-request-capable dev server)
python scripts/serve.py build/example   # http://localhost:8090

# Tests (offline, run in seconds); both must pass before committing
python -m pytest scripts/tests/ -q
ruff check scripts/ tools/ map_generator/

# Template lint (ESLint no-undef over templates/app.js + sw.js). The
# templates have no build step, so an undefined identifier parses fine
# and only fails at runtime, killing the app at boot; this catches it
# statically. One-time setup: `corepack pnpm install` (or npm install).
# Also runs inside pytest (test_eslint.py; skips if Node/ESLint absent).
corepack pnpm lint

# Single test file / single test
python -m pytest scripts/tests/test_validate_config.py -q
python -m pytest scripts/tests/test_build_smoke.py -k <pattern> -q

# Build + rsync deploy wrapper (dest from $TRAILMAPS_DEPLOY_DEST)
./tools/build_and_deploy.sh --build-only <slug>
```

Cached remote data never expires: rebuilds are offline and reproducible until you explicitly pass `--refresh` (everything), `--refresh-trails`, or `--refresh-pois`. Config edits that change relation IDs or the bbox trigger the relevant re-fetch automatically; YAML-only styling changes never need a refresh.

## Architecture

- **`scripts/` is the engine, despite the name.** `build.py` is the orchestrator: load + validate config → fetch trails (`fetch_trails.py` via Overpass, or a local `.osm` file through `osm_parser.py`) → enrichment pass (`enrichment.py` applies per-route config styling to the GeoJSON on *every* build, no refetch needed) → POIs (`fetch_pois.py`) → basemap/terrain PMTiles extraction (`fetch_basemap.py`, `fetch_terrain.py`, using the external `pmtiles` CLI) → optional per-route stats (`compute_route_stats.py`, USGS 3DEP) → template injection → icons/logo (`generate_icons.py`, `logo.py`) → font trimming (`font_trimmer.py`) → vendor bundling, minify, service worker, precompress.
- **`templates/`** is the entire runtime web app, shipped with every map: `app.js` (~10k lines, MapLibre GL JS application), `index.html`, `style.css` (light + dark via `[data-color-scheme]`), `sw.js`. `template_inject.py` substitutes config-derived values at build time. There is no JS build step; `app.js` is plain JS, minified only at build time. Because nothing compiles it, a runtime `ReferenceError` ships silently - always run the template lint (`corepack pnpm lint`) after editing template JS, and verify non-trivial changes in a real browser.
- **`map_generator/`** is a thin facade so `python -m map_generator build …` forwards to `scripts/build.py`. Both entry points are interchangeable.
- **`configs/`** is gitignored except `example/` and `reference/`. `configs/reference/reference.yaml` is the annotated schema; `reference-minimal.yaml` is the skeleton to copy. `validate_config.py` (`KNOWN_KEYS`) is the schema's source of truth.
- **`tools/`** are maintainer helpers: `build_and_deploy.sh` (validate → build → rsync), `clean_config.py` (re-align a production YAML against the reference template, with a parse-equality gate), `list_relations.py` (cache-only diagnostic of which OSM relations a map uses).
- Caching: Overpass responses in `cache/` keyed by query hash; `cache_signatures.py` decides when basemap/terrain need re-extraction. `--output-dir` / `--cache-dir` redirect both away from the repo.

## Hard constraints

- **Orchestrator CLI contract must not break:** external tooling drives `scripts/build.py <config> --dry-run --output-dir [--refresh]`. Verify with a `--dry-run` before changing CLI behavior.
- Tests must stay offline; they run against `configs/example/`.
- American English in docs, comments, and UI text, but literal OSM tags keep British spelling (`colour=` stays `colour=`).
- Comments carry design rationale (why), not narration (what).
- Ruff config is in `ruff.toml` (py311 target, line length 100); `pyproject.toml` exists only to declare the Python floor. The project is run from a checkout, never `pip install .`.
