#!/usr/bin/env python3
"""Build orchestrator for the trailmaps.app Map Generator.

Runs all pipeline steps, assembles templates with injected config,
and copies assets to produce a deployable static site.

Usage:
    python scripts/build.py configs/example/example.yaml
    python scripts/build.py configs/example/example.yaml --force
    python scripts/build.py configs/example/example.yaml --trails
    python scripts/build.py configs/example/example.yaml --no-terrain
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

if sys.version_info < (3, 11):
    sys.exit(
        f"map-generator requires Python 3.11+ (running {sys.version.split()[0]}). "
        "See README.md / docs/building.md."
    )

import requests
import yaml

# Add scripts directory to path for imports
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

import console
from cache_signatures import (
    _bbox_signature,
    _load_signature,
    _pmtiles_needs_regen,
    _save_signature,
    _trails_content_hash,
    _trails_fetch_fingerprint,
    _trails_needs_refetch,
)
from colors import resolve_accent_palette
from enrichment import _enrich_trails_geojson
from event_mode import (
    _apply_event_mode_to_custom_routes,
    _apply_event_mode_to_feature_oneway,
    _event_mode_background_style,
)
from fetch_basemap import fetch_basemap
from fetch_pois import fetch_pois
from fetch_terrain import fetch_terrain
from fetch_trails import fetch_trails
from template_inject import copy_assets, copy_templates
from validate_config import validate_config

# CDN libraries to bundle locally for offline/PWA support.
# Update versions here when upgrading dependencies.
VENDOR_LIBS = {
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css",
    "maplibre-gl.js": "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js",
    "pmtiles.js": "https://unpkg.com/pmtiles@4.4.1/dist/pmtiles.js",
    "basemaps.js": "https://unpkg.com/@protomaps/basemaps@5.7.2/dist/basemaps.js",
}


# ---------------------------------------------------------------------------
# Coordinate precision for the rendered trails.geojson
# ---------------------------------------------------------------------------
# 6 decimal places is ~11 cm at these latitudes — far finer than the
# underlying OSM geometry's real accuracy or anything visible on screen,
# yet it strips the trailing float noise (the subway-style parallel-offset
# math emits up to 15 dp) that both bloats the file and, being high-entropy,
# resists gzip. Rounding the render output roughly halves the GZIPPED size
# of trails.geojson — the largest text asset every visitor fetches and
# JSON-parses on load. Only the expanded render output is rounded;
# trails.src.geojson keeps full precision so the next build re-expands from
# clean geometry.
COORD_PRECISION = 6


def _round_coords(node, ndigits):
    """Recursively round every float in a nested coordinate array, in place.

    Geometry-type agnostic: rounds floats wherever they appear, so it
    handles Point through MultiPolygon (and GeometryCollection members)
    without special-casing each type.
    """
    for i, v in enumerate(node):
        if isinstance(v, float):
            node[i] = round(v, ndigits)
        elif isinstance(v, list):
            _round_coords(v, ndigits)


def _round_geojson_precision(geojson, ndigits=COORD_PRECISION):
    """Round all feature-geometry coordinates to ndigits, in place."""
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("coordinates") is not None:
            _round_coords(geom["coordinates"], ndigits)
        for sub in geom.get("geometries") or []:  # GeometryCollection
            if sub.get("coordinates") is not None:
                _round_coords(sub["coordinates"], ndigits)
    return geojson


def _minify_assets(output_dir):
    """Minify app.js + style.css in-place, logging progress via console.

    Used by main() unless --no-minify is passed (minification is on by
    default). Conservative pure-python
    minifiers (rjsmin / rcssmin): they preserve string literals
    verbatim (so the embedded CONFIG JSON in app.js stays intact),
    don't rewrite identifiers (no breaking changes for code that
    hooks into named DOM ids / event handlers), and only strip
    whitespace + comments + safe redundancies. ~30-50% file-size
    reduction on each.

    Vendor libs (vendor/*.js) are NOT minified — upstream ships them
    in production form already, and re-minifying risks breaking the
    upstream's intended behaviour.

    Errors are logged but don't abort the build — the unminified file
    stays in place, so the deploy still ships a working (just larger)
    artifact.
    """
    targets = [
        ("app.js", "rjsmin"),
        ("style.css", "rcssmin"),
    ]
    for fname, lib in targets:
        path = os.path.join(output_dir, fname)
        if not os.path.exists(path):
            console.info(f"{fname}: not present, skipping")
            continue
        try:
            before = os.path.getsize(path)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if lib == "rjsmin":
                import rjsmin

                minified = rjsmin.jsmin(src)
            else:
                import rcssmin

                minified = rcssmin.cssmin(src)
            with open(path, "w", encoding="utf-8") as f:
                f.write(minified)
            after = os.path.getsize(path)
            pct = (1 - after / before) * 100 if before else 0
            console.info(f"{fname}: {before:,} → {after:,} bytes (-{pct:.0f}%)")
        except ImportError:
            console.warn(
                f"{lib} not installed — {fname} left unminified. Run: .venv/bin/pip install {lib}"
            )
        except (OSError, UnicodeDecodeError) as e:
            console.warn(f"failed to minify {fname} ({e}) — left unminified")


def download_vendor_libs(output_dir, cache_dir):
    """Download CDN dependencies to vendor/ for offline use.

    Downloads are cached in cache/vendor/ so subsequent builds skip the fetch.
    """
    vendor_cache = os.path.join(cache_dir, "vendor")
    vendor_dst = os.path.join(output_dir, "vendor")
    os.makedirs(vendor_cache, exist_ok=True)
    os.makedirs(vendor_dst, exist_ok=True)

    downloaded = 0
    for filename, url in VENDOR_LIBS.items():
        cached = os.path.join(vendor_cache, filename)
        dst = os.path.join(vendor_dst, filename)

        if not os.path.exists(cached):
            console.info(f"Downloading {filename}...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                f.write(resp.content)
            downloaded += 1

        shutil.copy2(cached, dst)

    if downloaded:
        console.info(f"Downloaded {downloaded} vendor libraries")
    console.info(f"Bundled {len(VENDOR_LIBS)} vendor libraries")


def generate_service_worker(config, output_dir):
    """Generate service worker with precache list from build output.

    Must run LAST — after all other files are in place so the precache
    list is complete.
    """
    project_root = os.path.dirname(SCRIPTS_DIR)
    sw_template = os.path.join(project_root, "templates", "sw.js")

    if not os.path.exists(sw_template):
        console.warn(f"Service worker template not found: {sw_template}")
        return

    with open(sw_template, encoding="utf-8") as f:
        sw_content = f.read()

    # Walk the build tree once to collect every DEPLOYED file (for the
    # CACHE_VERSION hash — see comment below on why), then filter that
    # down to PRECACHE_URLS by dropping all but the essential glyph
    # PBFs. The trim keeps PRECACHE_URLS at ~30 entries for a typical
    # map instead of ~537 (full glyph parade), which removed the
    # parallel-glyph storm that competed with MapLibre's foreground
    # rendering on first visit. Glyph ranges outside 0-255 flow through
    # the SW's cache-on-fetch handler — whatever the rider's view
    # actually needs gets pulled from the network on first use and
    # cached for offline as a side effect of normal use. See sw.js
    # install/fetch handlers for the runtime half of this design.
    #
    # Build-only cache artifacts (trails.src.geojson and every .sig
    # sidecar) are dropped up front, before they reach either the hash
    # or the precache list. The runtime never fetches them — app.js
    # reads trails.geojson and the .pmtiles directly — and
    # build_and_deploy.sh excludes them from the server tree. Leaving
    # them in PRECACHE_URLS made the SW background-fetch each one on
    # every install, logging a 404 per file against the (correctly)
    # absent artifact; leaving them in the hash would needlessly bust
    # every rider's cache when a base-cache fingerprint changed without
    # any rider-visible output changing. (_is_build_only_artifact is a
    # module-level helper so the precompress pass skips the same files.)

    def _is_precachable_glyph(rel_url):
        # rel_url uses forward slashes (normalized below). Non-glyph
        # files always precache. For glyphs (fonts/*/N-M.pbf) only
        # the Basic Latin baseline range is precached.
        if not rel_url.startswith("fonts/") or not rel_url.endswith(".pbf"):
            return True
        return rel_url.endswith("/0-255.pbf")

    all_files = []  # every deployed file in output_dir, for the hash
    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if fname == "sw.js":
                continue
            # Skip precompression sidecars: the runtime always requests the
            # original URL and the server negotiates the encoded variant, so
            # sidecars must never enter the precache list or the cache hash
            # (the original's bytes are already hashed). precompress_assets
            # runs last, but a rebuild over a prior build's output would
            # otherwise see stale sidecars here.
            if fname.endswith((".gz", ".zst", ".br")):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, output_dir)
            # Normalize Windows separators for URL use + the glyph
            # filter check (which expects forward slashes).
            rel_url = rel.replace(os.sep, "/")
            # Drop build-only cache artifacts before they reach either
            # the hash or the precache list (see comment above).
            if _is_build_only_artifact(rel_url):
                continue
            all_files.append(rel_url)

    precache_urls = ["./"]
    pmtiles_files = []
    for rel_url in all_files:
        if not _is_precachable_glyph(rel_url):
            continue
        precache_urls.append(rel_url)
        if rel_url.endswith(".pmtiles"):
            pmtiles_files.append(rel_url)

    # Compute cache version from actual file CONTENTS of every
    # deployed file in the build (not just the precache subset). The earlier
    # "filenames + data_date" approach missed the most common case:
    # editing app.js / style.css / index.html without touching trails
    # or POIs left the cache version unchanged, so the service worker
    # happily served the stale cached JS/CSS to every previously-
    # installed visitor — fixes "appeared to do nothing" until they
    # manually cleared site data.
    #
    # CRITICAL: hash over all_files, not precache_urls. PRECACHE_URLS
    # excludes most glyph PBFs (they cache-on-fetch instead) but those
    # files are still part of the deploy. A change to a non-precached
    # glyph must still bump CACHE_VERSION so the SW evicts the stale
    # cache entry the next time a rider hits it.
    #
    # Hashing every file's bytes adds ~1-2 s for a typical 24 MB build
    # and guarantees correctness: any change anywhere in the output
    # tree (code, data, assets) produces a fresh CACHE_VERSION, which
    # the SW activate handler uses to evict the old cache and reload.
    hasher = hashlib.sha256()
    for url in sorted(all_files):
        path = os.path.join(output_dir, url)
        if not os.path.isfile(path):
            continue
        hasher.update(url.encode())
        hasher.update(b"\0")
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        hasher.update(b"\n")
    hasher.update((config.get("_data_date", "") or "").encode())
    cache_version = hasher.hexdigest()[:12]

    sw_config = {
        "CACHE_VERSION": cache_version,
        "PRECACHE_URLS": precache_urls,
        "PMTILES_FILES": pmtiles_files,
    }

    sw_config_json = json.dumps(sw_config, indent=2)
    sw_content = sw_content.replace("/*__SW_CONFIG__*/", f"const SW_CONFIG = {sw_config_json};")

    sw_path = os.path.join(output_dir, "sw.js")
    with open(sw_path, "w", encoding="utf-8") as f:
        f.write(sw_content)

    console.info(f"Generated service worker ({len(precache_urls)} files, cache {cache_version})")


# ---------------------------------------------------------------------------
# Build-time precompression (.gz / .zst sidecars)
# ---------------------------------------------------------------------------
# Compressible static assets are gzip- and zstd-compressed once at build
# time. A precompressed-aware static server (Caddy `precompressed`, nginx
# `gzip_static`/`brotli_static`, …) then ships the compressed bytes with
# zero request-time CPU — and at higher levels than on-the-fly encoding
# would risk for latency. The sidecars are a portable convention: a server
# without precompressed support simply ignores them and serves the
# original, so the build output stays host-agnostic. The runtime never
# requests a sidecar by name; the server negotiates it via Accept-Encoding.
#
# Skipped: already-compressed media (png/webp/ico) where gzip only adds
# bytes, and .pmtiles, which MUST stay uncompressed so HTTP Range slicing
# (PMTiles' whole point) keeps working.
PRECOMPRESS_EXTENSIONS = (
    ".pbf",
    ".geojson",
    ".json",
    ".js",
    ".css",
    ".svg",
    ".webmanifest",
    ".html",
    ".txt",
)
# Below ~1 KB the sidecar + extra Accept-Encoding negotiation isn't worth it.
PRECOMPRESS_MIN_BYTES = 1024


def _is_build_only_artifact(rel_path):
    """True for files generated only for the build's own bookkeeping that must
    never reach the server: signature sidecars (.sig) and the pre-enrichment
    geometry base (.src.geojson). Dropped from the SW hash/precache AND skipped
    by precompression — otherwise a `.src.geojson.gz` would slip past the rsync
    `*.src.geojson` exclude. Keep in sync with the --exclude list in
    tools/build_and_deploy.sh.
    """
    return rel_path.endswith(".sig") or rel_path.endswith(".src.geojson")


def precompress_assets(output_dir):
    """Write .gz + .zst sidecars for compressible assets in output_dir.

    MUST run after generate_service_worker: the SW hashes and precaches the
    ORIGINAL files, and the sidecars must not exist when that file list is
    built (the runtime requests e.g. ``0-255.pbf``, never ``0-255.pbf.gz``).
    Stale sidecars from a previous build are cleared first so a file that is
    no longer emitted (e.g. a glyph range dropped by font trimming) can't
    leave an orphan behind.
    """
    import gzip as _gzip

    try:
        from compression import zstd as _zstd  # Python 3.14+ stdlib
    except ImportError:
        _zstd = None

    # Clear prior sidecars for deterministic output.
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.endswith((".gz", ".zst", ".br")):
                os.remove(os.path.join(root, fname))

    count = orig_total = comp_total = 0
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            # Don't compress build-only artifacts — they aren't deployed,
            # and their .gz/.zst wouldn't match the rsync excludes.
            if _is_build_only_artifact(fname):
                continue
            if not fname.lower().endswith(PRECOMPRESS_EXTENSIONS):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if len(raw) < PRECOMPRESS_MIN_BYTES:
                continue
            # Only keep a sidecar if it actually saves bytes (guards the rare
            # incompressible case so we never ship a larger "compressed" file).
            gz = _gzip.compress(raw, 9)
            if len(gz) < len(raw):
                with open(path + ".gz", "wb") as f:
                    f.write(gz)
            if _zstd is not None:
                zz = _zstd.compress(raw, level=19)
                if len(zz) < len(raw):
                    with open(path + ".zst", "wb") as f:
                        f.write(zz)
            count += 1
            orig_total += len(raw)
            comp_total += len(gz)

    if count:
        console.info(
            f"Precompressed {count} assets "
            f"({'gzip + zstd' if _zstd is not None else 'gzip'}): "
            f"{orig_total / 1024:.0f} KB -> {comp_total / 1024:.0f} KB gzip "
            f"(-{100 * (1 - comp_total / orig_total):.0f}%)"
        )
    if _zstd is None:
        console.warn("compression.zstd unavailable — wrote gzip sidecars only")


def load_config(config_path):
    """Load and return a map config, resolving user-supplied asset paths
    to absolute on-disk paths relative to the config file's directory.

    Every per-map asset lives alongside its config (``configs/<slug>/``),
    so paths like ``logo: logo.webp`` resolve to
    ``<repo>/configs/<slug>/logo.webp`` — the user doesn't have to repeat
    the slug in every path. Absolute paths in the YAML are passed through
    unchanged (useful for shared assets outside the repo).

    Resolved keys: ``logo``, ``icon``, ``osm_file``, and every
    ``custom_routes[].geometry``. All other paths (``output_dir``,
    ``base_layers[].url``, etc.) stay in their original form — they're
    either repo-relative or external URLs.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    config_dir = os.path.dirname(os.path.abspath(config_path))

    def _resolve(path):
        if not path or not isinstance(path, str):
            return path
        return path if os.path.isabs(path) else os.path.join(config_dir, path)

    for key in ("logo", "icon", "osm_file"):
        if key in config:
            config[key] = _resolve(config[key])

    for entry in config.get("custom_routes") or []:
        if isinstance(entry, dict) and "geometry" in entry:
            entry["geometry"] = _resolve(entry["geometry"])

    # Inline event_mode.routes share the same path-resolution semantics
    # as top-level custom_routes (relative to the config YAML). Resolve
    # here so the build-time fold into config["custom_routes"]
    # downstream sees absolute paths.
    em = config.get("event_mode")
    if isinstance(em, dict):
        for entry in em.get("routes") or []:
            if isinstance(entry, dict) and "geometry" in entry:
                entry["geometry"] = _resolve(entry["geometry"])

    # Stash the config's directory in case downstream code wants it
    # (error messages, future relative-path fields). Name-spaced with an
    # underscore so it doesn't collide with user-supplied keys.
    config["_config_dir"] = config_dir

    return config


def compute_bbox_from_trails(trails_geojson, buffer_frac=0.03, buffer_min=0.001, buffer_max=0.01):
    """Compute bounding box from trail geometry with a proportional buffer.

    Returns [west, south, east, north] with a buffer added on all sides.

    The buffer is sized as a fraction of the system's maximum extent
    (buffer_frac, default 3%) and clamped to [buffer_min, buffer_max].
    Proportional sizing keeps small compact systems (e.g. a ~3 km trail
    network) from looking lost in empty margin while also preventing very
    large systems (e.g. a 30+ km rail trail) from getting a buffer so big
    it wastes viewport. The clamp floor ensures degenerate tiny bboxes
    still get a visible margin; the ceiling caps the absolute margin at
    ~1000m so a huge bbox doesn't pull in distant irrelevant terrain.
    """
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")

    for feature in trails_geojson.get("features", []):
        coords = feature.get("geometry", {}).get("coordinates", [])
        for lon, lat in coords:
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)

    if min_lon == float("inf"):
        raise ValueError(
            "No trail geometry found to compute bounding box. "
            "This usually means the Overpass API returned empty data — "
            "try running the build again, or set an explicit 'bbox' in the config."
        )

    extent = max(max_lon - min_lon, max_lat - min_lat)
    buffer = min(buffer_max, max(buffer_min, extent * buffer_frac))

    # Round to 4 decimal places (~11m precision) for clean output
    bbox = [
        round(min_lon - buffer, 4),
        round(min_lat - buffer, 4),
        round(max_lon + buffer, 4),
        round(max_lat + buffer, 4),
    ]
    return bbox


def expand_bbox_for_pan(bbox, pan_padding):
    """Expand bbox by pan_padding fraction of the larger extent on each side.

    The tight `bbox` frames the trails for the initial view; the expanded
    bbox returned here drives `maxBounds` (the pan wall) and the basemap/
    terrain PMTiles extraction footprint — so when the user pans to the
    edge they still see real map tiles rather than the empty fallback.

    `pan_padding=0.5` adds 50% of the greater dimension's extent to each
    side, roughly quadrupling the pannable area. `pan_padding=0` disables
    the expansion entirely (maxBounds == bbox, the pre-knob behaviour).

    Applies symmetrically in lon/lat so the pan envelope keeps the same
    shape as the source bbox (consistent with `compute_bbox_from_trails`,
    which also uses a single scalar padding derived from max extent).
    """
    extent = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    pad = extent * pan_padding
    return [
        round(bbox[0] - pad, 4),
        round(bbox[1] - pad, 4),
        round(bbox[2] + pad, 4),
        round(bbox[3] + pad, 4),
    ]


# ---------------------------------------------------------------------
# PMTiles cache invalidation
# ---------------------------------------------------------------------
# basemap.pmtiles and terrain.pmtiles are large (~5-30 MB each) and slow
# to extract (Mapterhorn / Protomaps planet pulls). We previously cached
# them by output-path existence only — change `pan_bbox`, `pan_padding`,
# or `*_maxzoom` and the *old* PMTiles silently stayed because the file
# was still there.
#
# Fix: write a small `<output_path>.sig` sidecar whenever a PMTiles is
# generated, containing the (bbox, maxzoom) tuple that produced it. On
# subsequent builds, regenerate when the sidecar is missing or doesn't
# match the requested signature. `--force` still wipes everything; this
# just turns "different bbox now" from a silent staleness bug into an
# automatic rebuild.


def print_summary(output_dir):
    """Print a summary of the build output."""
    console.step("\n" + "=" * 60)
    console.step("BUILD SUMMARY")
    console.step("=" * 60)
    total = 0
    fonts_size = 0
    fonts_count = 0
    fonts_dir = os.path.join(output_dir, "fonts")
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            total += size
            # Aggregate font PBFs into a single summary line
            if root.startswith(fonts_dir + os.sep) and f.endswith(".pbf"):
                fonts_size += size
                fonts_count += 1
                continue
            rel = os.path.relpath(path, output_dir)
            if size > 1024 * 1024:
                console.info(f"{rel:40s} {size / (1024 * 1024):8.1f} MB")
            else:
                console.info(f"{rel:40s} {size / 1024:8.1f} KB")

    if fonts_count > 0:
        label = f"fonts/ ({fonts_count} PBF files)"
        if fonts_size > 1024 * 1024:
            console.info(f"{label:40s} {fonts_size / (1024 * 1024):8.1f} MB")
        else:
            console.info(f"{label:40s} {fonts_size / 1024:8.1f} KB")

    console.info(f"{'TOTAL':40s} {total / (1024 * 1024):8.1f} MB")
    console.step("=" * 60)


def _print_dry_run_summary(config, args, output_dir, cache_dir):
    """Print what the build WOULD do, then exit 0.

    Runs after validate_config has accepted the YAML but before any
    Overpass query / tile fetch / file write. Useful for catching
    config errors and previewing the build's external footprint
    without committing to a long run.
    """
    console.step(f"Dry run for: {config['title']}")
    console.info(f"slug:        {config['slug']}")
    console.info(f"output_dir:  {output_dir}")
    console.info(f"cache_dir:   {cache_dir}")
    console.blank()

    # load_config resolves logo/icon/osm_file/custom_routes[].geometry
    # to absolute paths. For display we want the bare filename (matches
    # what the user wrote in the YAML) and only fall back to the full
    # path if the file's missing.
    def _display_path(abs_path):
        return (
            os.path.basename(abs_path)
            if os.path.isfile(abs_path)
            else f"{os.path.basename(abs_path)}  → MISSING (looked for {abs_path})"
        )

    # ---- OSM data source ----
    console.step("OSM data source:")
    if config.get("osm_file"):
        console.info(f"Local OSM file: {_display_path(config['osm_file'])}")
    else:
        console.info("Overpass API")
        console.info(f"  relations: {config.get('relations') or []}")
        for key in (
            "clipped_relations",
            "winter_relations",
            "summer_relations",
            "emergency_access_relations",
        ):
            ids = config.get(key) or []
            if ids:
                console.info(f"  {key}: {ids}")
        custom = config.get("custom_routes") or []
        if custom:
            console.info(f"  custom_routes ({len(custom)}):")
            for entry in custom:
                geom = entry.get("geometry") or ""
                if not geom:
                    console.info(f"    - id={entry.get('id')}: NO GEOMETRY PATH")
                    continue
                console.info(f"    - id={entry.get('id')} geometry={_display_path(geom)}")
    console.blank()

    # ---- POI fetching ----
    console.step("POI fetching (gated by show_* keys):")
    for key, default in (
        ("show_markers", True),
        ("show_features", True),
        ("show_parking", True),
        ("show_trailheads", True),
        ("show_hubs", True),
        ("show_toilets", True),
        ("show_drinking_water", True),
    ):
        on = bool(config.get(key, default))
        console.info(f"{key}: {'YES' if on else 'no'}")
    if config.get("show_trailheads", True):
        th = config.get("trailheads") or []
        if th:
            console.info(f"  trailheads from config: {len(th)} point(s)")
    if config.get("show_parking", True):
        pk = config.get("parking") or []
        if pk:
            console.info(f"  parking from config: {len(pk)} point(s)")
    if config.get("show_hubs", True):
        hb = config.get("hubs") or []
        if hb:
            console.info(f"  hubs from config: {len(hb)} point(s)")
    console.blank()

    # ---- Tile generation ----
    console.step("Tile generation:")
    if args.no_basemap:
        console.info("basemap: SKIPPED (--no-basemap)")
    else:
        bm_zoom = config.get("basemap_maxzoom", 15)
        console.info(f"basemap: pan_bbox extracted to maxzoom {bm_zoom}")
    if args.no_terrain or not config.get("show_terrain", True):
        reason = "--no-terrain" if args.no_terrain else "show_terrain: false"
        console.info(f"terrain: SKIPPED ({reason})")
    else:
        tr_zoom = config.get("terrain_maxzoom", 12)
        console.info(f"terrain: pan_bbox extracted to maxzoom {tr_zoom}")
    console.blank()

    # ---- Route stats ----
    want_dist = bool(config.get("show_route_distance"))
    want_elev = bool(config.get("show_route_elevation"))
    if want_dist or want_elev:
        console.step("Per-route stats:")
        if want_dist:
            console.info("distance: computed (haversine, no API)")
        if want_elev:
            console.info(
                "elevation gain + loss: USGS 3DEP getSamples "
                "(network calls, ~1 per route at 5m sampling)"
            )
        console.blank()

    # ---- Branding assets ----
    console.step("Branding assets:")
    for key in ("logo", "icon"):
        path = config.get(key) or ""
        if not path:
            console.info(f"{key}: (none)")
        else:
            console.info(f"{key}: {_display_path(path)}")
    console.blank()

    # ---- PWA / sharing ----
    console.step("Runtime features:")
    console.info(f"pwa: {bool(config.get('pwa', True))}")
    console.info(f"pwa_install_prompt: {bool(config.get('pwa_install_prompt', True))}")
    console.info(f"share_button: {bool(config.get('share_button', True))}")
    console.info(f"url_hash: {bool(config.get('url_hash', False))}")
    console.info(f"distance_units: {config.get('distance_units', 'mi')}")
    console.blank()

    console.step("Dry run complete — no files written, no network calls made.")


def apply_default_brand(config, project_root):
    """Fall back to the engine's bundled placeholder when a map sets no
    branding source of its own (``logo:`` or ``icon:``). Returns True if
    the default applied.

    Set as ``icon`` (not ``logo``) so every existing consumer treats it
    exactly like a curator-set icon: favicon + maskable-PWA generation
    (``resolve_icon_source``), the on-page brand image (``logo:`` ->
    ``icon:`` fallback), and ``accent_color: auto`` (logo -> icon
    fallback). The result: a brandless map is still installable and
    shows the bicycle as its mark. An explicit ``logo:`` or ``icon:``
    always wins.
    """
    if config.get("logo") or config.get("icon"):
        return False
    default_icon = os.path.join(project_root, "assets", "placeholder-logo.png")
    if not os.path.isfile(default_icon):
        return False
    config["icon"] = default_icon
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build MTB trail map")
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument(
        "--force", action="store_true", help="Force re-fetch all data (including Overpass cache)"
    )
    parser.add_argument(
        "--trails",
        action="store_true",
        help="Re-fetch trail data from OSM (uses Overpass cache). POIs are rebuilt on every build regardless of this flag.",
    )
    parser.add_argument("--no-terrain", action="store_true", help="Skip terrain tile generation")
    parser.add_argument("--no-basemap", action="store_true", help="Skip basemap extraction")
    parser.add_argument(
        "--output-dir",
        help="Write build output to this directory. Overrides "
        "the config 'output_dir' field and the default "
        "'build/<slug>/' layout. Resolved against the "
        "current working directory if relative.",
    )
    parser.add_argument(
        "--cache-dir",
        help="Use this directory for the Overpass / derive-accent "
        "/ route-stats cache. Defaults to 'cache/' at the "
        "repo root. Resolved against the current working "
        "directory if relative.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and print what would be fetched / generated, then exit. "
        "No Overpass calls, no tile downloads, no file writes.",
    )
    # Minification and precompression both default to ON: the canonical
    # use of build.py is "produce ready-to-deploy artifacts," regardless
    # of which deploy mechanism (tools/build_and_deploy.sh, or any other
    # static-host workflow) ships them. Each has a single --no-* opt-out
    # for local-iteration debug; the defaults are set explicitly via
    # parser.set_defaults() below.
    parser.add_argument(
        "--no-minify",
        dest="minify",
        action="store_false",
        help="Disable minification of app.js and style.css (default: "
        "enabled). Use for local-iteration debug where readable output "
        "is more useful than smaller output; for deploy, leave it on.",
    )
    # The .gz/.zst sidecars are inert on a server that doesn't serve them
    # (and on serve.py), so precompress default-on is safe.
    parser.add_argument(
        "--no-precompress",
        dest="precompress",
        action="store_false",
        help="Disable .gz/.zst sidecars for compressible assets (default: "
        "enabled). The sidecars let a precompressed-aware server (Caddy "
        "`precompressed`, nginx `gzip_static`) serve them with no "
        "request-time CPU. Skip for fast local-iteration builds.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output; show only notes, warnings, and errors.",
    )
    parser.set_defaults(minify=True, precompress=True)
    args = parser.parse_args(argv)

    console.set_verbosity(quiet=args.quiet)

    config = load_config(args.config)
    project_root = os.path.dirname(SCRIPTS_DIR)

    # Validate the config before doing anything expensive (Overpass fetches,
    # tile generation). Errors abort the build; warnings (e.g. asset files
    # not present yet) print but allow it to continue.
    errors, warnings = validate_config(config, config_path=args.config, project_root=project_root)
    for line in warnings:
        print(line)
    if errors:
        console.step(f"\nConfig validation failed for {args.config}:")
        for line in errors:
            print(line)
        sys.exit(1)

    # A map that configures neither logo: nor icon: still gets favicons,
    # a maskable PWA icon + manifest (installable), and an on-page brand
    # mark by falling back to the engine's bundled placeholder. Applied
    # after validation (which judges the curator's real config) so it
    # also shows up in --dry-run's branding summary below.
    if apply_default_brand(config, project_root):
        console.info("No logo/icon configured — using the bundled placeholder bike icon")

    # Path resolution precedence: CLI flag > config field > legacy default.
    # CLI-flag paths resolve against the current working directory so the
    # caller (orchestrator or shell) controls layout entirely; config-field
    # and default paths resolve against project_root so the legacy
    # `python scripts/build.py configs/<slug>/<slug>.yaml` invocation keeps
    # writing to `build/<slug>/` under the repo regardless of cwd.
    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    elif config.get("output_dir"):
        output_dir = os.path.join(project_root, config["output_dir"])
    else:
        output_dir = os.path.join(project_root, "build", config["slug"])

    if args.cache_dir:
        cache_dir = os.path.abspath(args.cache_dir)
    else:
        cache_dir = os.path.join(project_root, "cache")

    # --dry-run: print what would happen, exit before any work.
    # Runs AFTER validate_config so any schema/value errors still abort
    # with a non-zero exit; runs BEFORE os.makedirs so dry-run leaves
    # zero filesystem footprint (no empty output_dir created).
    if args.dry_run:
        _print_dry_run_summary(config, args, output_dir, cache_dir)
        return

    os.makedirs(output_dir, exist_ok=True)

    # --force clears the Overpass response cache so data is re-fetched from OSM
    if args.force and os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
        console.step(f"Cleared Overpass cache: {cache_dir}")

    console.step(f"Building map: {config['title']}")
    console.step(f"Output: {output_dir}")
    console.blank()

    # Step 1: Fetch trails
    trails_path = os.path.join(output_dir, "trails.geojson")
    # trails.geojson is the EXPANDED render output (subway-style parallel
    # routes, per-mode stubs, etc.), regenerated from scratch on every build.
    # That expansion is NOT reversible, so re-running enrichment on an
    # already-expanded trails.geojson silently destroys geometry. We therefore
    # cache the canonical, pre-enrichment fetched geometry separately in
    # trails.src.geojson and always re-expand FROM it. All reuse / fingerprint
    # / content-guard logic keys off the base file, never the expanded output.
    trails_src_path = os.path.join(output_dir, "trails.src.geojson")
    auto_refetch_reason = None
    if not (args.force or args.trails or not os.path.exists(trails_src_path)):
        # Base cache exists; refetch only if the config inputs changed or the
        # base file was modified out from under us (content-guard). A missing
        # sidecar is a legacy backfill, not a refetch.
        needs, reason = _trails_needs_refetch(trails_src_path, config)
        if needs:
            auto_refetch_reason = reason
    if args.force or args.trails or not os.path.exists(trails_src_path) or auto_refetch_reason:
        if auto_refetch_reason:
            console.step(f"Trails: refetching ({auto_refetch_reason})")
        trails_geojson = fetch_trails(config, trails_path, cache_dir)
        # Snapshot the canonical base BEFORE enrichment expands it in place,
        # so the next build re-expands from clean geometry instead of
        # re-enriching (and destroying) the expanded output. Copied after
        # fetch_trails succeeds so a partial/aborted fetch leaves no base.
        shutil.copyfile(trails_path, trails_src_path)
        _save_signature(
            trails_src_path,
            _trails_fetch_fingerprint(config)
            + "\ntrails-content="
            + (_trails_content_hash(trails_src_path) or ""),
        )
    else:
        console.step(f"Trails: reusing base {trails_src_path}")
        with open(trails_src_path, encoding="utf-8") as f:
            trails_geojson = json.load(f)
        # Backfill the sidecar for a base written before content-guarding.
        if _load_signature(trails_src_path) is None:
            _save_signature(
                trails_src_path,
                _trails_fetch_fingerprint(config)
                + "\ntrails-content="
                + (_trails_content_hash(trails_src_path) or ""),
            )

    # Record the data date from the BASE file's modification time (the moment
    # OSM was last fetched), including local-time HH:MM so the About modal can
    # show fetch granularity finer than a single day. Also feeds the
    # service-worker cache key, so finer precision means stale clients update
    # more reliably. Uses trails.src.geojson, not the expanded output: the
    # latter is rewritten on every build and would report the re-enrich time
    # rather than the fetch time.
    config["_data_date"] = datetime.fromtimestamp(os.path.getmtime(trails_src_path)).strftime(
        "%Y-%m-%d %H:%M"
    )

    # Tell the runtime whether clip_endpoints.geojson exists in this
    # build. fetch_trails only writes the file when there are clipped
    # relations whose endpoints fall inside the bbox (most maps don't
    # have any), so a runtime probe-fetch produced a noisy 404 on
    # those maps. Reading the file's existence here lets the runtime
    # skip the fetch entirely.
    config["_has_clip_endpoints"] = os.path.exists(
        os.path.join(output_dir, "clip_endpoints.geojson")
    )

    # Count POI features by type so the runtime can render an
    # accurate Welcome modal Search line (e.g. "Find ... places
    # (parking, toilets)" instead of always claiming every POI
    # type exists). Two sources: pois.geojson for OSM-fetched POIs
    # (toilets, drinking water, trail markers, OSM-tagged features
    # and trailheads), AND the curator-supplied parking /
    # trailheads YAML lists which the runtime renders as separate
    # markers. Both are user-visible POIs from the rider's
    # perspective, so both should count toward the Search line.
    pois_path = os.path.join(output_dir, "pois.geojson")
    poi_counts = {}
    if os.path.exists(pois_path):
        try:
            with open(pois_path, encoding="utf-8") as f:
                pois_data = json.load(f)
            for feat in pois_data.get("features", []):
                ptype = (feat.get("properties") or {}).get("poi_type")
                if not ptype:
                    continue
                poi_counts[ptype] = poi_counts.get(ptype, 0) + 1
        except (OSError, json.JSONDecodeError) as exc:
            console.warn(f"could not count pois.geojson: {exc}")
    # Curator-supplied YAML lists (gated by their show_* flags so
    # we don't credit hidden ones).
    if config.get("show_parking", True):
        yaml_pk = config.get("parking") or []
        if yaml_pk:
            poi_counts["parking"] = poi_counts.get("parking", 0) + len(yaml_pk)
    if config.get("show_trailheads", True):
        yaml_th = config.get("trailheads") or []
        if yaml_th:
            poi_counts["trailhead"] = poi_counts.get("trailhead", 0) + len(yaml_th)
    if config.get("show_hubs", True):
        yaml_hb = config.get("hubs") or []
        if yaml_hb:
            poi_counts["hub"] = poi_counts.get("hub", 0) + len(yaml_hb)
    config["_poi_counts"] = poi_counts

    # Accent palette: stash the resolved 4-value palette (light + dark
    # shades, each with its on-accent text colour) so
    # inject_config_into_template can emit them as the CONFIG.accent*
    # vars. resolve_accent_palette handles "auto" (Pillow-based logo
    # derivation, cached per-source-hash as the raw pick), explicit hex,
    # and the unset framework default uniformly, and emits per-shade
    # WCAG contrast warnings. Always returns a palette (never None).
    config["_accent_palette"] = resolve_accent_palette(config, project_root, cache_dir)

    # Event-mode pre-pass (no-op when event_mode is absent). Folds
    # event_mode.routes into config["custom_routes"] so they
    # participate in the standard custom-route bake-in below, and
    # mutates non-featured custom routes' colour / dashed fields to
    # the background style. Also adds `direction_arrows` to
    # `forced_visible` when event_mode.direction_arrows is true,
    # which is why this has to run BEFORE the safety-warning check
    # below (so the check sees the resolved value). The companion
    # relations-side pass runs in inject_config_into_template later.
    if config.get("event_mode"):
        em = config["event_mode"] or {}
        em_routes_count = len(em.get("routes") or [])
        em_featured_count = len(em.get("featured") or [])
        bg_summary = _event_mode_background_style(config)
        console.step(
            f"Event mode: featuring {em_routes_count} inline route(s) "
            f"+ {em_featured_count} reference(s); background "
            f"{bg_summary['color']} dashed {bg_summary['pattern']}."
        )
        _apply_event_mode_to_custom_routes(config)

    # Safety warning: a map with one-way trails should normally
    # surface the direction-arrow layer by default, otherwise a
    # first-visit rider on a flow trail won't see which way they're
    # supposed to ride. Detect oneway segments in trails_geojson and
    # warn if direction_arrows isn't included in default_visible.
    # Skip the warning when forced_visible includes direction_arrows
    # (the layer is forced on at every visit, so default_visible is
    # irrelevant). Event mode adds direction_arrows to forced_visible
    # above when event_mode.direction_arrows: true, so the warning is
    # naturally suppressed for event maps.
    raw_dv = config.get("default_visible")
    raw_fv = config.get("forced_visible")
    arrows_suppressed = config.get("show_direction_arrows", True) is False
    arrows_default_on = (
        raw_dv == "all"
        or (isinstance(raw_dv, list) and "direction_arrows" in raw_dv)
        or raw_fv == "all"
        or (isinstance(raw_fv, list) and "direction_arrows" in raw_fv)
    )
    if not arrows_default_on and not arrows_suppressed:
        oneway_count = 0
        for f in trails_geojson.get("features") or []:
            ow = (f.get("properties") or {}).get("oneway")
            if ow in ("yes", True, "-1", "reversible"):
                oneway_count += 1
        if oneway_count > 0:
            console.warn(
                f"Map has {oneway_count} one-way trail segment(s) "
                "but direction_arrows is not in default_visible. Riders "
                "won't see directional indicators on first visit — "
                "consider adding 'direction_arrows' to default_visible "
                "(or use default_visible: all)."
            )

    # Enrich trails.geojson with the three non-exclusive bucket flags
    # (summer/winter/emergency) on every route, and append any
    # user-defined custom_routes. Idempotent — safe to re-run against
    # a cached trails.geojson that's already been enriched.
    enriched = _enrich_trails_geojson(config, trails_geojson, project_root)

    # Event-mode arrow restriction: when event_mode.direction_arrows is
    # true, the runtime would render arrows on every OSM-tagged oneway
    # way (clutter on an event map). Strip the `oneway` property from
    # any feature whose routes are all non-featured so the existing
    # arrow emitter naturally only renders on the event route(s).
    # Runs AFTER enrichment so featured custom routes' features (which
    # carry the curator's intended `oneway: "yes"`) are present.
    arrows_restricted = _apply_event_mode_to_feature_oneway(config, trails_geojson)

    # Compute per-route distance/elevation stats if either gate is on.
    # Runs after enrichment so custom_routes are included in the totals.
    # compute_route_stats also handles cleanup when a previously-enabled
    # gate has been turned off (strips stale fields). Idempotent.
    from compute_route_stats import compute_and_attach as compute_route_stats

    stats_changed = compute_route_stats(trails_geojson, config, cache_dir)

    # Always (re)write the expanded trails.geojson. It is the render output,
    # regenerated from the base on every build, so it must reflect this
    # build's enrichment regardless of which passes reported a change. (The
    # reuse fingerprint + content-guard live on trails.src.geojson, written
    # at fetch time above; the expanded output is never reused as a cache.)
    #
    # Trim coordinate precision on the render output only (see
    # COORD_PRECISION) — roughly halves the gzipped transfer size and speeds
    # up the client-side JSON.parse. Done in place right before serialization;
    # the only later reader (compute_bbox_from_trails) is unaffected by ~cm
    # rounding since it re-rounds the derived bbox to 4 dp anyway.
    _round_geojson_precision(trails_geojson)
    with open(trails_path, "w", encoding="utf-8") as f:
        json.dump(trails_geojson, f, separators=(",", ":"))
    custom_count = len(config.get("custom_routes") or [])
    bits = []
    if enriched:
        bits.append(
            "bucket flags"
            + (
                f" + {custom_count} custom route{'s' if custom_count != 1 else ''}"
                if custom_count
                else ""
            )
        )
    if stats_changed:
        bits.append("route stats")
    if arrows_restricted:
        bits.append("event-mode arrow restriction")
    if bits:
        console.info(f"Enriched {os.path.basename(trails_path)} with {' and '.join(bits)}")

    # Compute bbox from trail geometry if not specified in config
    if "bbox" not in config:
        config["bbox"] = compute_bbox_from_trails(trails_geojson)
        console.info(f"Computed bbox from trails: {config['bbox']}")

    # Compute center from bbox if not specified in config
    if "center" not in config:
        bbox = config["bbox"]
        config["center"] = [
            round((bbox[0] + bbox[2]) / 2, 4),
            round((bbox[1] + bbox[3]) / 2, 4),
        ]
        console.info(f"Computed center from bbox: {config['center']}")

    # Compute pan_bbox: the looser envelope that drives maxBounds at
    # runtime and the basemap/terrain PMTiles extraction footprint. The
    # tight `bbox` still frames the initial view; pan_bbox gives the user
    # room to wander without falling off the edge of the covered map.
    # If the YAML specifies pan_bbox explicitly it wins; otherwise we
    # derive it from pan_padding (default 0.5 = add 50% of extent per
    # side ≈ 4x the pannable area).
    if "pan_bbox" not in config:
        pan_padding = config.get("pan_padding", 0.5)
        config["pan_bbox"] = expand_bbox_for_pan(config["bbox"], pan_padding)
        if pan_padding > 0:
            console.info(f"Pan envelope (pad {pan_padding}): {config['pan_bbox']}")
    else:
        console.info(f"Pan envelope (explicit): {config['pan_bbox']}")
    console.blank()

    # Step 2: Fetch POIs (skip when every POI category is disabled).
    # Otherwise ALWAYS re-run fetch_pois — even on a no-flag rebuild —
    # so config-defined POIs (parking, trailheads, event_mode.pois)
    # take effect on the next build automatically. The OSM portion
    # still hits the Overpass cache internally, so the cost of the
    # always-on rebuild is sub-second on cached maps.
    #
    # The previous behaviour gated this on `--force` / `--trails` /
    # missing pois.geojson, which created an asymmetric trap: editing
    # parking lots or event_mode.pois in YAML wouldn't show up until
    # the curator remembered to pass `--trails`. Trails are
    # re-enriched every build (the unconditional
    # `_enrich_trails_geojson` pass at the top of inject does that
    # for custom routes); POIs now follow the same convention.
    pois_path = os.path.join(output_dir, "pois.geojson")
    if (
        not config.get("show_markers", True)
        and not config.get("show_features", True)
        and not config.get("show_parking", True)
        and not config.get("show_trailheads", True)
        and not config.get("show_hubs", True)
    ):
        console.step("POIs: Skipped (all POI layers disabled)")
        # Write empty GeoJSON so the viewer doesn't 404
        with open(pois_path, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    else:
        fetch_pois(config, pois_path, cache_dir)
    console.blank()

    # Steps 3+4: Fetch basemap + terrain in parallel.
    #
    # Both are independent (no shared state, no order dependency) and
    # both are I/O-bound (network + subprocess for pmtiles extract or
    # mapterhorn fetch). Running them in a 2-worker thread pool
    # roughly halves the wall time on builds where both fetch (~30-60s
    # each → ~30-60s total instead of 60-120s).
    #
    # Plan-then-execute split: decision logic (skip / use cached /
    # regenerate) runs synchronously up front so the pre-fetch console
    # messages stay tidy. Only the actual fetch + signature-save runs
    # concurrently; subprocess output from the two fetches will
    # interleave on stdout, which is acceptable for build logs.
    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    basemap_bbox = config.get("pan_bbox") or config["bbox"]
    basemap_maxzoom = config.get("basemap_maxzoom", 15)
    basemap_sig = _bbox_signature(basemap_bbox, basemap_maxzoom)

    terrain_path = os.path.join(output_dir, "terrain.pmtiles")
    terrain_bbox = config.get("pan_bbox") or config["bbox"]
    terrain_maxzoom = config.get("terrain_maxzoom", 12)
    terrain_sig = _bbox_signature(terrain_bbox, terrain_maxzoom)

    fetch_tasks = []  # list of (label, callable) for parallel work
    post_messages = []  # printed AFTER all parallel tasks complete

    # ---- Basemap planning ----
    if args.no_basemap:
        post_messages.append("Basemap: Skipped (--no-basemap)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(basemap_path, basemap_bbox, basemap_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                console.step(f"Basemap: regenerating ({reason})")

            def _do_basemap():
                fetch_basemap(config, basemap_path)
                _save_signature(basemap_path, basemap_sig)

            fetch_tasks.append(("basemap", _do_basemap))
        else:
            size_mb = os.path.getsize(basemap_path) / (1024 * 1024)
            post_messages.append(f"Basemap: Using existing {basemap_path} ({size_mb:.1f} MB)")

    # ---- Terrain planning ----
    if not config.get("show_terrain", True):
        post_messages.append("Terrain: Disabled in config (show_terrain: false)")
    elif args.no_terrain:
        post_messages.append("Terrain: Skipped (--no-terrain)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(terrain_path, terrain_bbox, terrain_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                console.step(f"Terrain: regenerating ({reason})")

            def _do_terrain():
                if fetch_terrain(config, terrain_path):
                    _save_signature(terrain_path, terrain_sig)

            fetch_tasks.append(("terrain", _do_terrain))
        else:
            size_mb = os.path.getsize(terrain_path) / (1024 * 1024)
            post_messages.append(f"Terrain: Using existing {terrain_path} ({size_mb:.1f} MB)")

    # ---- Parallel execution ----
    if len(fetch_tasks) >= 2:
        # Two real fetches → run concurrently (the win case).
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = {ex.submit(fn): label for label, fn in fetch_tasks}
            # Re-raise any exception in the main thread so the build
            # aborts with a useful traceback rather than silently
            # half-completing.
            for f in concurrent.futures.as_completed(futures):
                f.result()
    else:
        # Zero or one fetch → run inline (no thread overhead, simpler
        # exception path).
        for _label, fn in fetch_tasks:
            fn()

    for line in post_messages:
        console.step(line)
    console.blank()

    # Step 5: Copy templates and assets. Order matters: copy_assets
    # runs first because it stashes processed-logo dimensions on
    # config["_brand_img_dims"] which copy_templates reads when
    # substituting the brand-img <img> width/height/fetchpriority
    # attributes. Swapping the order leaves brand_dims as None and
    # the brand-img tag emits without dimension hints (CLS regression).
    console.step("Assembling output...")
    copy_assets(config, output_dir)
    copy_templates(config, output_dir, trails_geojson)
    console.blank()

    # Step 5.5: Minify app.js + style.css unless --no-minify was passed. Runs
    # AFTER copy_templates (which writes the files we minify) and
    # BEFORE generate_service_worker (which hashes file contents into
    # CACHE_VERSION — so the SW's hash refers to the final minified
    # bytes the rider downloads). Vendor libs (download_vendor_libs
    # below) are NOT touched — we serve whatever upstream ships.
    if args.minify:
        console.step("Minifying assets...")
        _minify_assets(output_dir)
        console.blank()

    # Step 6: Bundle vendor libraries (CDN deps served locally for offline)
    console.step("Bundling vendor libraries...")
    download_vendor_libs(output_dir, cache_dir)
    console.blank()

    # Step 7: Generate service worker (MUST be last — needs complete file list)
    if config.get("pwa", True):
        console.step("Generating PWA assets...")
        generate_service_worker(config, output_dir)

        # Check for missing pieces that will prevent the PWA from being installable
        pwa_warnings = []
        manifest_path = os.path.join(output_dir, "icons", "site.webmanifest")
        if not os.path.exists(manifest_path):
            pwa_warnings.append(
                "No web manifest (site.webmanifest) — set 'icon:' (or "
                "'logo:') in your config so icons + manifest are generated "
                "from a source image"
            )
        else:
            icons_dir = os.path.join(output_dir, "icons")
            has_icon = (
                any(f.endswith(".png") for f in os.listdir(icons_dir))
                if os.path.isdir(icons_dir)
                else False
            )
            if not has_icon:
                pwa_warnings.append(
                    "Web manifest exists but no icon PNGs found — "
                    "the browser needs at least one icon to show an install prompt"
                )
        if pwa_warnings:
            console.blank()
            console.info("PWA WARNINGS — the app will not be installable until fixed:")
            for w in pwa_warnings:
                console.info(f"  • {w}")
            console.blank()
    else:
        console.step("PWA disabled — skipping service worker generation")

    # Step 8: Precompress static assets (MUST be after the service worker —
    # see precompress_assets). Skipped with --no-precompress.
    if args.precompress:
        console.step("Precompressing static assets...")
        precompress_assets(output_dir)
        console.blank()

    print_summary(output_dir)
    console.step(f"\nServe locally: python scripts/serve.py {output_dir} --port 8080")
    console.step("Then open: http://localhost:8080\n")


if __name__ == "__main__":
    main()
