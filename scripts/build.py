#!/usr/bin/env python3
"""Build orchestrator for the trailmaps.app Map Generator.

Runs all pipeline steps, assembles templates with injected config,
and copies assets to produce a deployable static site.

Usage:
    python scripts/build.py configs/example/example.yaml
    python scripts/build.py configs/example/example.yaml --refresh
    python scripts/build.py configs/example/example.yaml --refresh-trails
    python scripts/build.py configs/example/example.yaml --no-terrain
"""

import argparse
import concurrent.futures
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import NamedTuple

if sys.version_info < (3, 11):  # noqa: UP036 - runtime gate FOR older Pythons
    sys.exit(
        f"map-generator requires Python 3.11+ (running {sys.version.split()[0]}). "
        "See README.md / docs/building.md."
    )

import requests

# Add scripts directory to path for imports
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

import basemap_paths
import cache_manifest
import console
from cache_signatures import (
    _bbox_signature,
    _clear_signature,
    _load_signature,
    _pmtiles_needs_regen,
    _save_signature,
    _trails_content_hash,
    _trails_fetch_fingerprint,
    _trails_needs_refetch,
)
from colors import resolve_accent_palette
from config_io import read_config_yaml
from enrichment import _enrich_trails_geojson
from event_mode import (
    _apply_event_mode_to_custom_routes,
    _apply_event_mode_to_feature_oneway,
    _event_mode_background_style,
)
from fetch_basemap import EXTRACT_PAD_DEG, fetch_basemap
from fetch_pois import POI_SHOW_FLAGS, fetch_pois
from fetch_terrain import fetch_terrain
from fetch_trails import fetch_trails
from osm_diff import report_refresh_diff, stash_previous_snapshot
from pmtiles_util import BASEMAP_MAXZOOM, EXTRACT_MINZOOM, TERRAIN_MAXZOOM
from tagging_report import report_tagging_quality
from template_inject import copy_assets, copy_templates
from validate_config import validate_config

# CDN libraries to bundle locally for offline/PWA support.
# Update versions here when upgrading dependencies.
VENDOR_LIBS = {
    # MapLibre GL JS 6 is ESM only: an entry module, a chunk it shares
    # with its worker, and the worker, which the entry starts as a module
    # worker from a URL beside itself. All three must sit in vendor/
    # under their upstream names, because they import each other by
    # those names. index.html loads the entry with a module script that
    # sets window.maplibregl for the classic scripts after it. A file
    # missing here still works online and fails offline, since the
    # service worker precache is a walk of the build directory.
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl.css",
    "maplibre-gl.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl.mjs",
    "maplibre-gl-shared.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl-shared.mjs",
    "maplibre-gl-worker.mjs": "https://unpkg.com/maplibre-gl@6.10.0/dist/maplibre-gl-worker.mjs",
    "pmtiles.js": "https://unpkg.com/pmtiles@4.4.1/dist/pmtiles.js",
    "basemaps.js": "https://unpkg.com/@protomaps/basemaps@5.7.2/dist/basemaps.js",
    # Client-side contour isolines from the terrain raster-dem
    # (templates/app.js addContourLayers). Upstream ships index.min.js;
    # renamed here to match the vendor/ one-lib-one-name convention.
    # 0.1.1 is the floor: 0.1.0 returned its cached ArrayBuffer itself,
    # MapLibre detached it on transfer to its worker, and the next cache
    # hit lost that tile's contours on the worker:false path this app
    # uses (fixed in onthegomap/maplibre-contour#437).
    "maplibre-contour.js": "https://unpkg.com/maplibre-contour@0.1.1/dist/index.min.js",
    # QR code encoder for the Options Share sheet (renderQrSheet in
    # app.js): ES module, about 27 KB, MIT, Project Nayuki's generator
    # as packaged by Anthony Fu. index.html imports it and sets
    # window.uqr; the QR row stays hidden when it is missing.
    "uqr.mjs": "https://unpkg.com/uqr@0.1.3/dist/index.mjs",
    # Draws every route, so a page without it cannot start. The
    # classic-script build (global maplibreLanes) with its workers
    # inlined, so it needs no sibling files. 1.3.0 is the floor: the
    # inside-corner bevel that ended the low-zoom lane spike, the
    # loop-removal cut anchor, every route at every overview zoom, and
    # the dash phase fixed to the map.
    "maplibre-gl-lanes.js": "https://unpkg.com/maplibre-gl-lanes@1.3.0/dist/maplibre-gl-lanes.js",
}


# ---------------------------------------------------------------------------
# Coordinate precision for the rendered trails.geojson
# ---------------------------------------------------------------------------
# 6 decimal places is ~11 cm at these latitudes - far finer than the
# underlying OSM geometry's real accuracy or anything visible on screen,
# yet it strips trailing float noise (custom-route and clipping math emits
# up to 15 dp) that both bloats the file and, being high-entropy, resists
# gzip. trails.geojson is the largest text asset every visitor fetches and
# JSON-parses on load. Only the render output is rounded;
# trails.src.geojson keeps full precision so the next build enriches from
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


MINIFY_TARGETS = [
    ("app.js", "rjsmin"),
    ("style.css", "rcssmin"),
    ("index.html", "html"),
]

# sw.js is minified on its own pass because it does not exist yet when
# MINIFY_TARGETS runs: the service worker is generated later (it needs
# the complete file list to build its precache manifest). Minifying the
# template up front would also break the build - generate_service_worker
# injects its config by substituting the `/*__SW_CONFIG__*/` block
# comment, and rjsmin strips comments, leaving no token to substitute
# into and a service worker with no config.
MINIFY_TARGETS_SW = [
    ("sw.js", "rjsmin"),
]


# Inline <script>/<style> bodies, captured with their opening and closing
# tags so the surrounding markup survives untouched. Skips external
# references (src=) - those files are handled as their own targets, or
# left alone entirely in vendor/'s case.
_INLINE_BLOCK_RE = re.compile(
    r"(<(script|style)(?![^>]*\bsrc=)[^>]*>)(.*?)(</\2>)", re.S | re.I
)

# A downlevel-hidden conditional comment (`<!--[if lt IE 9]>…`) is markup,
# not commentary: stripping it would silently drop whatever it guards.
# Nothing in the template uses one today, but the cost of respecting them
# is one predicate.
_CONDITIONAL_COMMENT_RE = re.compile(r"^<!--\s*\[if\b", re.I)

# The `<!-- BEGIN OG -->` / `<!-- END OG -->` pair in templates/index.html
# is a replacement contract, not commentary: the trailmaps.app orchestrator's
# inject-og-meta.py locates the OG image block by these exact markers in the
# BUILT page and swaps it for per-map summary_large_image tags. Stripping
# them breaks that downstream step, so they survive minification.
_OG_MARKER_COMMENT_RE = re.compile(r"^<!--\s*(?:BEGIN|END) OG\s*-->")


def _minify_html(src):
    """Strip comments from an HTML document without touching its markup.

    Deliberately NOT a full HTML minifier: attribute-quote removal and
    inline-whitespace collapsing are where those tools change rendering,
    and they would buy little here on top of the comment stripping. This
    only removes what a reader never sees anyway.

    Three passes: HTML comments outside inline <script>/<style> (the
    template carries ~23 KB of design rationale in these), then rjsmin /
    rcssmin over inline block bodies (app.js minification never reaches
    the theme-bootstrap script embedded in the page), then a collapse of
    the blank lines the first pass leaves behind.

    Safe to run only on a BUILT copy, never on templates/index.html:
    template_inject deletes optional blocks by matching paired
    `<!-- GPX start -->` / `<!-- GPX end -->` markers, so a stripped
    template would silently ship every optional block on every map. The
    build reaches this in _stage_templates, after copy_templates has already
    consumed those markers.
    """
    import rcssmin
    import rjsmin

    out, pos = [], 0
    for m in _INLINE_BLOCK_RE.finditer(src):
        out.append(_strip_html_comments(src[pos : m.start()]))
        body = m.group(3)
        minifier = rcssmin.cssmin if m.group(2).lower() == "style" else rjsmin.jsmin
        out.append(m.group(1) + minifier(body) + m.group(4))
        pos = m.end()
    out.append(_strip_html_comments(src[pos:]))

    return re.sub(r"\n[ \t]*\n+", "\n", "".join(out))


def _strip_html_comments(chunk):
    """Remove HTML comments, keeping conditionals and the OG markers."""
    return re.sub(
        r"<!--.*?-->[ \t]*\n?",
        lambda m: m.group(0)
        if _CONDITIONAL_COMMENT_RE.match(m.group(0))
        or _OG_MARKER_COMMENT_RE.match(m.group(0))
        else "",
        chunk,
        flags=re.S,
    )


def _minify_assets(output_dir, targets=None):
    """Minify the given targets in-place, logging progress via console.

    Defaults to MINIFY_TARGETS. The minifiers (rjsmin, rcssmin, and
    _minify_html for the page) are conservative: they keep string literals
    verbatim, so the embedded CONFIG JSON survives, and never rewrite
    identifiers. Vendor libs are not minified, since upstream ships them
    in production form.

    A failure costs only size: the unminified file stays in place. With
    Node on PATH, minified JavaScript must also pass ``node --check``; a
    minifier bug that breaks the syntax would otherwise kill the app at
    boot on every device.
    """
    node = shutil.which("node")
    for fname, lib in targets if targets is not None else MINIFY_TARGETS:
        path = os.path.join(output_dir, fname)
        if not os.path.exists(path):
            console.detail(f"{fname}: not present, skipping")
            continue
        try:
            before = os.path.getsize(path)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if lib == "rjsmin":
                import rjsmin

                minified = rjsmin.jsmin(src)
            elif lib == "html":
                minified = _minify_html(src)
            else:
                import rcssmin

                minified = rcssmin.cssmin(src)
            with open(path, "w", encoding="utf-8") as f:
                f.write(minified)
            if node and lib == "rjsmin" and not _node_check(node, path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(src)
                console.warn(f"minified {fname} failed node --check - shipped unminified")
                continue
            after = os.path.getsize(path)
            pct = (1 - after / before) * 100 if before else 0
            console.detail(f"{fname}: {before:,} → {after:,} bytes (-{pct:.0f}%)")
        except ImportError:
            # The html target has no minifier of its own; it leans on both.
            missing = "rjsmin rcssmin" if lib == "html" else lib
            console.warn(
                f"{missing} not installed - {fname} left unminified. "
                f"Run: .venv/bin/pip install {missing}"
            )
        except Exception as e:
            # Any minifier failure, including a bug inside rjsmin, costs
            # only size. The file is rewritten in one call after the
            # minifier returns, so a failure leaves the original bytes.
            console.warn(f"failed to minify {fname} ({e}) - left unminified")


def _node_check(node, path):
    """True when ``node --check`` parses the file, or Node cannot run."""
    try:
        proc = subprocess.run([node, "--check", path], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return True
    return proc.returncode == 0


_SOURCE_MAP_COMMENT = re.compile(rb"\s*//[#@] ?sourceMappingURL=\S+\s*\Z")


def _copy_vendor_script(src, dst):
    """Copy a vendored file into a build, minus its source map pointer.

    Upstream builds end in `//# sourceMappingURL=<name>.js.map`. A build
    ships no maps (several MB, and the service worker sweep would precache
    them onto every phone), so an open inspector would log a 404 per
    library. For a profiling session, drop the matching .map beside the
    script on a test map by hand. Only the build's copy loses the
    comment; the cached and repo copies stay verbatim.
    """
    with open(src, "rb") as f:
        data = f.read()
    # dst, not src: a cached download is named <file>.js.<url hash>
    stripped = _SOURCE_MAP_COMMENT.sub(b"\n", data) if dst.endswith((".js", ".mjs")) else data
    if stripped == data:
        shutil.copy2(src, dst)
        return
    with open(dst, "wb") as f:
        f.write(stripped)
    shutil.copystat(src, dst)


def download_vendor_libs(output_dir, cache_dir):
    """Download CDN dependencies to vendor/ for offline use.

    Downloads are cached in cache/vendor/ so subsequent builds skip the
    fetch. The cache filename embeds a hash of the source URL, because
    the version lives only in the URL: bumping a version misses the
    cache naturally. Any cache entry that is not a current VENDOR_LIBS
    entry is removed, so a bumped or dropped library does not linger.
    """
    vendor_cache = os.path.join(cache_dir, "vendor")
    vendor_dst = os.path.join(output_dir, "vendor")
    os.makedirs(vendor_cache, exist_ok=True)
    os.makedirs(vendor_dst, exist_ok=True)

    downloaded = 0
    cache_names = set()
    for filename, url in VENDOR_LIBS.items():
        url_tag = hashlib.sha256(url.encode()).hexdigest()[:8]
        cache_names.add(f"{filename}.{url_tag}")
        cached = os.path.join(vendor_cache, f"{filename}.{url_tag}")
        dst = os.path.join(vendor_dst, filename)

        if not os.path.exists(cached):
            console.info(f"Downloading {filename}...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                f.write(resp.content)
            downloaded += 1

        _copy_vendor_script(cached, dst)

    for name in os.listdir(vendor_cache):
        path = os.path.join(vendor_cache, name)
        if name not in cache_names and os.path.isfile(path):
            os.remove(path)
            console.detail(f"Removed stale vendor cache entry {name}")

    bundled = len(VENDOR_LIBS)

    # The same walk ships anything else left in vendor/ by an earlier
    # build. When a library is renamed or dropped here (maplibre-gl.js,
    # 1 MB, became three .mjs files with MapLibre 6) the old file would
    # otherwise ride along to every phone for as long as the build
    # directory lives. Precompressed siblings go with their file.
    expected = set(VENDOR_LIBS)
    for name in os.listdir(vendor_dst):
        base = name
        for ext in (".br", ".gz"):
            if base.endswith(ext):
                base = base[: -len(ext)]
        path = os.path.join(vendor_dst, name)
        if base not in expected and os.path.isfile(path):
            os.remove(path)
            console.detail(f"Removed stale vendor/{name}")

    if downloaded:
        console.info(f"Downloaded {downloaded} vendor libraries")
    console.detail(f"Bundled {bundled} vendor libraries")


def generate_service_worker(config, output_dir):
    """Generate service worker with precache list from build output.

    Must run LAST - after all other files are in place so the precache
    list is complete.
    """
    project_root = os.path.dirname(SCRIPTS_DIR)
    sw_template = os.path.join(project_root, "templates", "sw.js")

    if not os.path.exists(sw_template):
        console.warn(f"Service worker template not found: {sw_template}")
        return

    with open(sw_template, encoding="utf-8") as f:
        sw_content = f.read()

    # Walk the build tree once to collect every deployed file (the
    # CACHE_VERSION hash covers all of them), then filter that down to
    # PRECACHE_URLS by keeping only the 0-255 glyph PBFs: about 30 entries
    # instead of ~537, which avoids a parallel-glyph storm competing with
    # MapLibre's first render. Other glyph ranges flow through the SW's
    # cache-on-fetch handler.
    #
    # Build-only artifacts (see _is_build_only_artifact) are dropped before
    # the hash and the precache list. The SW would otherwise fetch each one
    # on install and log a 404, and a base-cache fingerprint change would
    # bust every rider's cache with no rider-visible difference.

    def _is_precachable_glyph(rel_url):
        # Glyphs (fonts/*/N-M.pbf) precache only the Basic Latin range.
        if not rel_url.startswith("fonts/") or not rel_url.endswith(".pbf"):
            return True
        return rel_url.endswith("/0-255.pbf")

    all_files = []  # every deployed file in output_dir, for the hash
    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if fname == "sw.js":
                continue
            # Sidecars never enter the precache list or the hash: the
            # runtime requests the original URL and the server negotiates
            # the encoding. A rebuild over a prior output would otherwise
            # see stale ones here.
            if fname.endswith((".gz", ".br")):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, output_dir)
            # URLs and the glyph filter expect forward slashes.
            rel_url = rel.replace(os.sep, "/")
            if _is_build_only_artifact(rel_url):
                continue
            all_files.append(rel_url)

    precache_urls = ["./"]
    # Large archives go last in the precache list; the same list feeds
    # PMTILES_FILES in the SW config.
    pmtiles_files = []
    for rel_url in all_files:
        if not _is_precachable_glyph(rel_url):
            continue
        # og-image.png is a ~580 KB social-preview card that only link
        # scrapers fetch. It stays in all_files, so the hash covers it.
        if rel_url == "og-image.png":
            continue
        # The "./" seed already precaches the document in the form every
        # entry point navigates to. Adding "index.html" would store it
        # twice and double-count it in the readiness total. It stays in
        # all_files so the hash tracks its bytes.
        if rel_url == "index.html":
            continue
        if rel_url.endswith(".pmtiles"):
            pmtiles_files.append(rel_url)
        else:
            precache_urls.append(rel_url)

    # The multi-MB .pmtiles archives (basemap ~2 MB, terrain up to ~30 MB)
    # go at the END of the list. backgroundPrecache() fetches sequentially,
    # so an early archive would monopolize the link for tens of seconds
    # and contend with the foreground map. The viewport is served meanwhile
    # via cache-on-fetch and Range passthrough.
    #
    # The order is also load-bearing for the silent update swap: app.js
    # reloads onto a new version once the non-.pmtiles "core" subset (per
    # sw.js CORE_STATUS) is cached, so the small files must finish within
    # the swap's grace window. Reordering does not bust any cache, since
    # CACHE_VERSION hashes all_files.
    precache_urls.extend(pmtiles_files)

    # CACHE_VERSION hashes the CONTENTS of every deployed file, so any
    # change to code, data or assets makes the SW activate handler evict
    # the old cache. Names plus data_date missed edits to app.js or
    # style.css alone, and riders kept the stale JS/CSS.
    #
    # CRITICAL: hash all_files, not precache_urls. A change to a
    # non-precached glyph must still bump the version so the stale
    # cache-on-fetch entry is evicted. Cost is ~1-2 s per 24 MB build.
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

    # Byte size of every precached URL, so the page reports offline
    # readiness by weight, not file count: the .pmtiles tail is most of the
    # bytes (a ~2 MB basemap plus an 8-30 MB terrain against ~3 MB of
    # everything else), so a count would sit near 90% while the files
    # that matter at the trailhead were still missing. Keyed by URL so
    # reordering cannot mis-attribute sizes. Minification has already run,
    # so the sizes are final.
    precache_bytes = {}
    for rel_url in precache_urls:
        # "./" is the page itself, which the server answers from index.html.
        rel_path = "index.html" if rel_url == "./" else rel_url
        try:
            precache_bytes[rel_url] = os.path.getsize(
                os.path.join(output_dir, rel_path))
        except OSError:
            # Omit rather than fail the build over a stat; the runtime
            # treats a missing entry as weightless.
            pass

    sw_config = {
        # Cache Storage is per-origin and production serves every map
        # as a path on one origin, so cache names must carry the slug
        # (see the CACHE_PREFIX comment in sw.js).
        "CACHE_SCOPE": config["slug"],
        "CACHE_VERSION": cache_version,
        "PRECACHE_URLS": precache_urls,
        "PRECACHE_BYTES": precache_bytes,
        "PMTILES_FILES": pmtiles_files,
    }

    sw_config_json = json.dumps(sw_config, indent=2)
    sw_content = sw_content.replace("/*__SW_CONFIG__*/", f"const SW_CONFIG = {sw_config_json};")

    sw_path = os.path.join(output_dir, "sw.js")
    with open(sw_path, "w", encoding="utf-8") as f:
        f.write(sw_content)

    console.detail(f"Generated service worker ({len(precache_urls)} files, cache {cache_version})")


# ---------------------------------------------------------------------------
# Build-time precompression (.gz / .br sidecars)
# ---------------------------------------------------------------------------
# Compressible static assets are gzip- and brotli-compressed once at build
# time. A precompressed-aware static server (Caddy `precompressed`, nginx
# `gzip_static`/`brotli_static`, …) then ships the compressed bytes with
# zero request-time CPU - and at higher levels than on-the-fly encoding
# would risk for latency. The sidecars are a portable convention: a server
# without precompressed support simply ignores them and serves the
# original, so the build output stays host-agnostic. The runtime never
# requests a sidecar by name; the server negotiates it via Accept-Encoding.
#
# Brotli, not zstd: Safari never sends `zstd` in Accept-Encoding, br-11
# measured ~2.5x smaller than gzip-9 on trails.geojson and 5-10% under
# zstd-19 elsewhere, and every zstd-capable browser also accepts br.
#
# Skipped: already-compressed media (png/webp/ico) where gzip only adds
# bytes, and .pmtiles, which MUST stay uncompressed so HTTP Range slicing
# (PMTiles' whole point) keeps working.
PRECOMPRESS_EXTENSIONS = (
    ".pbf",
    ".geojson",
    ".json",
    ".js",
    ".mjs",
    ".css",
    ".svg",
    ".webmanifest",
    ".html",
    ".txt",
    ".gpx",  # XML; event-map course downloads compress ~5-10x
)
# Below ~1 KB the sidecar + extra Accept-Encoding negotiation isn't worth it.
PRECOMPRESS_MIN_BYTES = 1024


def _is_build_only_artifact(rel_path):
    """True for files generated only for the build's own bookkeeping that must
    never reach the server: signature sidecars (.sig), the pre-enrichment
    geometry base (.src.geojson), and in-progress atomic-write temp files
    (.tmp - a hard-killed pmtiles extract can leave one behind). Dropped from
    the SW hash/precache AND skipped by precompression - otherwise a
    `.src.geojson.gz` would slip past the rsync `*.src.geojson` exclude. Keep
    in sync with the --exclude list in tools/build_and_deploy.sh.
    """
    return rel_path.endswith((".sig", ".src.geojson", ".tmp"))


def _load_json_or_none(path):
    """Parse a JSON file, or None if it's missing or unreadable.

    For consumers that only enrich the build's reporting (the OSM data notes)
    and must degrade quietly rather than abort a build over a bad read.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def precompress_assets(output_dir):
    """Write .gz + .br sidecars for compressible assets in output_dir.

    MUST run after generate_service_worker: the SW hashes and precaches the
    ORIGINAL files, and the sidecars must not exist when that file list is
    built (the runtime requests e.g. ``0-255.pbf``, never ``0-255.pbf.gz``).
    Stale sidecars from a previous build are cleared first so a file that is
    no longer emitted (e.g. a glyph range dropped by font trimming) can't
    leave an orphan behind.
    """
    import gzip as _gzip

    try:
        import brotli as _brotli
    except ImportError:
        _brotli = None

    # Clear prior sidecars for deterministic output.
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.endswith((".gz", ".br")):
                os.remove(os.path.join(root, fname))

    count = orig_total = comp_total = 0
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            # Don't compress build-only artifacts - they aren't deployed,
            # and their .gz/.br wouldn't match the rsync excludes.
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
            if _brotli is not None:
                br = _brotli.compress(raw, quality=11)
                if len(br) < len(raw):
                    with open(path + ".br", "wb") as f:
                        f.write(br)
            count += 1
            orig_total += len(raw)
            comp_total += len(gz)

    if count:
        console.detail(
            f"Precompressed {count} assets "
            f"({'gzip + brotli' if _brotli is not None else 'gzip'}): "
            f"{orig_total / 1024:.0f} KB -> {comp_total / 1024:.0f} KB gzip "
            f"(-{100 * (1 - comp_total / orig_total):.0f}%)"
        )
    if _brotli is None:
        console.warn("brotli unavailable - wrote gzip sidecars only")


def load_config(config_path):
    """Load and return a map config, resolving user-supplied asset paths
    to absolute on-disk paths relative to the config file's directory.

    Every per-map asset lives alongside its config (``configs/<slug>/``),
    so paths like ``logo: logo.webp`` resolve to
    ``<repo>/configs/<slug>/logo.webp`` - the user doesn't have to repeat
    the slug in every path. Absolute paths in the YAML are passed through
    unchanged (useful for shared assets outside the repo).

    Resolved keys: ``logo``, ``icon``, ``osm_file``, every
    ``custom_routes[].geometry``, ``additional_logos[].path``,
    ``event_mode.routes[].geometry`` and ``event_mode.gpx.routes[].file``.
    All other paths (``--output-dir``, vendor library URLs, etc.) stay in
    their original form, since they are repo-relative or external URLs.
    """
    config = read_config_yaml(config_path)

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

    # Secondary brand images (event + sponsor logos) live alongside the
    # config like `logo:`/`icon:`; resolve each path so copy_assets sees
    # an absolute source.
    for entry in config.get("additional_logos") or []:
        if isinstance(entry, dict) and "path" in entry:
            entry["path"] = _resolve(entry["path"])

    # Inline event_mode.routes resolve like top-level custom_routes, so the
    # later fold into config["custom_routes"] sees absolute paths.
    em = config.get("event_mode")
    if isinstance(em, dict):
        for entry in em.get("routes") or []:
            if isinstance(entry, dict) and "geometry" in entry:
                entry["geometry"] = _resolve(entry["geometry"])
        # event_mode.gpx.routes[].file: curator-supplied .gpx assets.
        em_gpx = em.get("gpx")
        if isinstance(em_gpx, dict):
            for entry in em_gpx.get("routes") or []:
                if isinstance(entry, dict) and "file" in entry:
                    entry["file"] = _resolve(entry["file"])

    # Per-relation override dicts accept quoted YAML keys ("1234567"), but
    # the injector looks routes up by INT key. A string key would drop the
    # override silently, and event mode's synthesized background entries
    # could clobber a string-keyed explicit color. Coerce once, here.
    # Non-coercible keys are left for validate_config to reject.
    for key in ("relation_colors", "dashed_relations", "relation_names"):
        d = config.get(key)
        if isinstance(d, dict):
            config[key] = {
                (int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): v
                for k, v in d.items()
            }

    # `name` is the one authored identity string; the title is
    # "{name} Map" unless the curator overrides it (worth it only where
    # the string carries information the derivation can't, e.g.
    # "Custer's Last Stand Route Map"). Resolved once so every reader
    # sees the same string.
    #
    # A name already ending in " Map" derives "... Map Map". The
    # orchestrator's pre-validate forbids such names; the engine does not
    # second-guess a curator who wants that string.
    if not config.get("title"):
        name = config.get("name")
        if isinstance(name, str) and name.strip():
            config["title"] = f"{name.strip()} Map"

    # The YAML's own path lets template_inject fold its mtime into
    # buildDate (a YAML edit is an app change from the rider's
    # perspective). The underscore keeps both keys clear of user keys.
    config["_config_dir"] = config_dir
    config["_config_path"] = os.path.abspath(config_path)

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
        # Index rather than unpack: the GeoJSON spec allows a third
        # (elevation) element per position, and GPX→GeoJSON converters
        # commonly emit it - custom-route geometry is baked in verbatim,
        # so `for lon, lat in coords` crashed on any curator route file
        # carrying elevations.
        for pos in coords:
            lon, lat = pos[0], pos[1]
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)

    if min_lon == float("inf"):
        raise ValueError(
            "No trail geometry found to compute bounding box: no relation produced "
            "any ways. Check the relation ids in the config against the data source, "
            "or set an explicit 'bbox' in the config."
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
    terrain PMTiles extraction footprint - so when the user pans to the
    edge they still see real map tiles rather than the empty fallback.

    `pan_padding=0.5` adds 50% of the greater dimension's extent to each
    side, roughly quadrupling the pannable area. `pan_padding=0` disables
    the expansion entirely (maxBounds == bbox).

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


def print_summary(output_dir):
    """Print the per-file build summary and return the bytes a deploy ships."""
    console.step("\n" + "=" * 60, detail=True)
    console.step("BUILD SUMMARY", detail=True)
    console.step("=" * 60, detail=True)
    total = 0
    deploy_total = 0
    fonts_size = 0
    fonts_count = 0
    fonts_dir = os.path.join(output_dir, "fonts")
    for root, _dirs, files in os.walk(output_dir):
        for f in files:
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            total += size
            # What a deploy ships: no precompressed sidecars, no bookkeeping files.
            if not f.endswith((".gz", ".br")) and not _is_build_only_artifact(f):
                deploy_total += size
            # Aggregate font PBFs into a single summary line
            if root.startswith(fonts_dir + os.sep) and f.endswith(".pbf"):
                fonts_size += size
                fonts_count += 1
                continue
            rel = os.path.relpath(path, output_dir)
            if size > 1024 * 1024:
                console.detail(f"{rel:40s} {size / (1024 * 1024):8.1f} MB")
            else:
                console.detail(f"{rel:40s} {size / 1024:8.1f} KB")

    if fonts_count > 0:
        label = f"fonts/ ({fonts_count} PBF files)"
        if fonts_size > 1024 * 1024:
            console.detail(f"{label:40s} {fonts_size / (1024 * 1024):8.1f} MB")
        else:
            console.detail(f"{label:40s} {fonts_size / 1024:8.1f} KB")

    console.detail(f"{'TOTAL':40s} {total / (1024 * 1024):8.1f} MB")
    console.step("=" * 60, detail=True)
    return deploy_total


def _fmt_elapsed(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _print_dry_run_summary(config, args, output_dir, cache_dir):
    """Print what the build WOULD do.

    Runs after validate_config has accepted the YAML but before any
    Overpass query, tile fetch or file write.
    """
    console.step(f"Dry run for: {config['title']}")
    console.info(f"slug:        {config['slug']}")
    console.info(f"output_dir:  {output_dir}")
    console.info(f"cache_dir:   {cache_dir}")
    console.blank()

    # load_config made these paths absolute. Show the bare filename the
    # user wrote, and the full path only when the file is missing.
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
    for key in POI_SHOW_FLAGS:
        on = bool(config.get(key, True))
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
        console.info(
            f"basemap: pan_bbox extracted, zoom {EXTRACT_MINZOOM}-{BASEMAP_MAXZOOM}, "
            "path and service-road lines generated")
    if args.no_terrain or not config.get("show_terrain", True):
        reason = "--no-terrain" if args.no_terrain else "show_terrain: false"
        console.info(f"terrain: SKIPPED ({reason})")
    else:
        console.info(
            f"terrain: pan_bbox extracted, zoom {EXTRACT_MINZOOM}-{TERRAIN_MAXZOOM}")
    console.blank()

    # ---- Route stats ----
    if config.get("show_distance"):
        console.step("Per-route stats:")
        console.info("distance: computed (haversine, no API)")
        console.blank()

    # ---- Branding assets ----
    console.step("Branding assets:")
    for key in ("logo", "icon"):
        path = config.get(key) or ""
        if not path:
            console.info(f"{key}: (none)")
        else:
            console.info(f"{key}: {_display_path(path)}")
    extra_logos = config.get("additional_logos") or []
    for i, entry in enumerate(extra_logos):
        if not isinstance(entry, dict):
            continue
        path = entry.get("path") or ""
        invert = entry.get("invert_dark", False)
        console.info(
            f"additional_logos[{i}]: {_display_path(path)}"
            f"{' (invert_dark: true)' if invert else ''}"
        )
    console.blank()

    console.step("Dry run complete - no files written, no network calls made.")


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


def _build_parser():
    parser = argparse.ArgumentParser(description="Build MTB trail map")
    parser.add_argument("config", help="Path to YAML config file")
    # Remote data never updates on its own - cached responses are served
    # regardless of age, so an unflagged rebuild is offline and
    # reproducible. The --refresh family is the only way to pull fresh
    # data for an unchanged config (config edits still trigger the
    # relevant re-fetch automatically via fingerprints/signatures).
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch all of this map's remote data: trails and POIs "
        "from Overpass (bypasses its cached responses; other maps' "
        "shared-cache entries are untouched), plus basemap and terrain "
        "tiles.",
    )
    parser.add_argument(
        "--refresh-trails",
        action="store_true",
        help="Re-fetch trail data from Overpass (bypasses cached responses).",
    )
    parser.add_argument(
        "--refresh-pois",
        action="store_true",
        help="Re-fetch POI data from Overpass (bypasses cached responses). "
        "Config-defined POIs (parking, trailheads, hubs) are rebuilt on "
        "every build regardless.",
    )
    parser.add_argument("--no-terrain", action="store_true", help="Skip terrain tile generation")
    parser.add_argument("--no-basemap", action="store_true", help="Skip basemap extraction")
    parser.add_argument(
        "--output-dir",
        help="Write build output to this directory instead of the "
        "default 'build/<slug>/' layout. Resolved against the "
        "current working directory if relative.",
    )
    parser.add_argument(
        "--cache-dir",
        help="Use this directory for the Overpass / derive-accent "
        "cache. Defaults to 'cache/' at the "
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
    # The .gz/.br sidecars are inert on a server that doesn't serve them
    # (and on serve.py), so precompress default-on is safe.
    parser.add_argument(
        "--no-precompress",
        dest="precompress",
        action="store_false",
        help="Disable .gz/.br sidecars for compressible assets (default: "
        "enabled). The sidecars let a precompressed-aware server (Caddy "
        "`precompressed`, nginx `gzip_static`) serve them with no "
        "request-time CPU. Skip for fast local-iteration builds.",
    )
    # Default output is one line per pipeline stage plus every note, warning
    # and error. The orchestrator streams it for dozens of maps, so the
    # per-file and per-relation detail is opt-in.
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output; show only warnings and errors.",
    )
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full build log: per-relation listings, every file "
        "written, cache paths, and the final file table. The default prints "
        "one line per stage.",
    )
    parser.set_defaults(minify=True, precompress=True)
    return parser


def _resolve_dirs(config, args, project_root):
    """Return (output_dir, cache_dir) for this build."""
    # Path resolution precedence: CLI flag > default.
    # CLI-flag paths resolve against the current working directory so the
    # caller (orchestrator or shell) controls layout entirely; the default
    # path resolves against project_root so the legacy
    # `python scripts/build.py configs/<slug>/<slug>.yaml` invocation keeps
    # writing to `build/<slug>/` under the repo regardless of cwd.
    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    else:
        output_dir = os.path.join(project_root, "build", config["slug"])

    if args.cache_dir:
        cache_dir = os.path.abspath(args.cache_dir)
    else:
        cache_dir = os.path.join(project_root, "cache")
    return output_dir, cache_dir


def _fetch_and_snapshot(config, trails_path, trails_src_path, cache_dir, refresh_trails):
    # Snapshot the canonical base BEFORE enrichment edits it in place,
    # so the next build enriches clean geometry again instead of
    # re-enriching its own output. Copied after
    # fetch_trails succeeds so a partial/aborted fetch leaves no base.
    #
    # Stash the outgoing snapshot first so the refresh can be diffed
    # against it (vetted-deploys-only means the curator has to know what
    # changed upstream). Read before fetch_trails so a fetch that
    # rewrites trails_path can't race it; returns None on a first build.
    prev_snapshot = stash_previous_snapshot(
        trails_src_path, cache_dir, config["slug"])
    fetched = fetch_trails(config, trails_path, cache_dir, refresh=refresh_trails)
    shutil.copyfile(trails_path, trails_src_path)
    _save_signature(
        trails_src_path,
        _trails_fetch_fingerprint(config)
        + "\ntrails-content="
        + (_trails_content_hash(trails_src_path) or ""),
    )
    report_refresh_diff(prev_snapshot, fetched, cache_dir, config["slug"])
    return fetched


def _stage_trails(config, args, output_dir, cache_dir):
    """Fetch the trail base, or reuse it when its inputs are unchanged.

    Returns (trails_geojson, fetch_ran, trails_path, trails_src_path).
    fetch_ran feeds the cache-manifest step: a reuse build records no
    Overpass trail paths, but neither does an osm_file map whose fetch
    DID run, so drained-path counts can't tell them apart.
    """
    trails_path = os.path.join(output_dir, "trails.geojson")
    # trails.geojson is the render output: the fetched geometry plus what
    # enrichment adds (bucket flags, custom routes, stats) and what event
    # mode strips, rounded for size. It is regenerated on every build from
    # trails.src.geojson, the fetched geometry exactly as it arrived, so
    # that YAML-only changes never need a refetch and never compound. All
    # reuse / fingerprint / content-guard logic keys off the base file,
    # never the render output.
    trails_src_path = os.path.join(output_dir, "trails.src.geojson")

    auto_refetch_reason = None
    refresh_trails = args.refresh or args.refresh_trails
    needs_fetch = refresh_trails or not os.path.exists(trails_src_path)
    if not needs_fetch:
        # Base cache exists; refetch only if the config inputs changed or the
        # base file was modified out from under us (content-guard).
        needs, reason = _trails_needs_refetch(trails_src_path, config)
        if needs:
            auto_refetch_reason = reason

    fetch_ran = False
    if needs_fetch or auto_refetch_reason:
        if auto_refetch_reason:
            console.step(f"Trails: refetching ({auto_refetch_reason})")
        fetch_ran = True
        trails_geojson = _fetch_and_snapshot(
            config, trails_path, trails_src_path, cache_dir, refresh_trails)
    else:
        console.step(f"Trails: reusing base {trails_src_path}", detail=True)
        try:
            with open(trails_src_path, encoding="utf-8") as f:
                trails_geojson = json.load(f)
            console.summary(
                f"Trails: {len(trails_geojson.get('features') or [])} features "
                "(reused from the previous build)"
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            # A truncated base with no sidecar (a first build killed
            # between the snapshot copy and the signature save) escapes
            # the content guard, which only fires when a sidecar exists.
            # A bad base is never reused: refetch.
            console.warn(f"{trails_src_path} is unreadable (truncated?); refetching")
            fetch_ran = True
            trails_geojson = _fetch_and_snapshot(
                config, trails_path, trails_src_path, cache_dir, refresh_trails)
    return trails_geojson, fetch_ran, trails_path, trails_src_path


def _data_date(trails_geojson, trails_src_path):
    """Return the data date ("when is this OSM data from") as local
    "YYYY-MM-DD HH:MM", for the About modal and the service-worker cache
    key."""
    # Local time with HH:MM so stale clients update reliably on sub-day
    # refetches. Read from the base's embedded metadata.data_timestamp
    # (the Overpass osm3s snapshot captured at fetch time, or the .osm
    # file's mtime), NOT from the base file's mtime: the base gets
    # rewritten by any build that rebuilds it from cached Overpass
    # responses (fresh checkout, machine move, config-triggered refetch),
    # so its mtime reports the rebuild moment even when no fetch happened
    # and the data is weeks old.
    data_ts = (trails_geojson.get("metadata") or {}).get("data_timestamp") or ""
    if data_ts:
        try:
            return (
                datetime.strptime(data_ts, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=UTC)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
        except ValueError:
            pass  # unrecognized stamp - fall through to the mtime path
    # A route-only map has no OSM data, so its base carries an empty
    # stamp (fetch_trails._write_empty_trails), as does a response
    # without an osm3s block: date the data by the base's mtime.
    return datetime.fromtimestamp(os.path.getmtime(trails_src_path)).strftime(
        "%Y-%m-%d %H:%M"
    )


def _event_mode_prepass(config):
    # Event-mode pre-pass (no-op when event_mode is absent). Folds
    # event_mode.routes into config["custom_routes"] so they
    # participate in the standard custom-route bake-in, and
    # mutates non-featured custom routes' color / dashed fields to
    # the background style. Also adds `direction_arrows` to
    # `forced_visible` when event_mode.direction_arrows is true,
    # which is why this has to run BEFORE _warn_arrows_hidden
    # (so the check sees the resolved value). The companion
    # relations-side pass runs in inject_config_into_template later.
    if config.get("event_mode"):
        em = config["event_mode"] or {}
        em_routes_count = len(em.get("routes") or [])
        em_featured_count = len(em.get("featured") or [])
        bg_summary = _event_mode_background_style(config)
        msg = (
            f"Event mode: featuring {em_routes_count} inline route(s) "
            f"+ {em_featured_count} reference(s); background "
            f"{bg_summary['color']} dashed {bg_summary['pattern']}."
        )
        console.step(msg, detail=True)
        console.summary(msg)
        _apply_event_mode_to_custom_routes(config)


def _warn_arrows_hidden(config, trails_geojson):
    # Safety warning: a map with one-way trails should normally
    # surface the direction-arrow layer by default, otherwise a
    # first-visit rider on a flow trail won't see which way they're
    # supposed to ride. An unset default_visible already turns arrows
    # on (DEFAULT_FIRST_VISIT_LAYERS includes direction_arrows), so
    # this only needs to catch the case where the curator wrote an
    # explicit list (or []) that leaves arrows out. Skip the warning
    # when forced_visible includes direction_arrows (the layer is
    # forced on at every visit, so default_visible is irrelevant).
    # The event-mode pre-pass adds direction_arrows to forced_visible
    # when event_mode.direction_arrows: true, so the warning is naturally
    # suppressed for event maps.
    raw_dv = config.get("default_visible")
    raw_fv = config.get("forced_visible")
    arrows_suppressed = config.get("show_direction_arrows", True) is False
    # Unset forced_visible defaults to [direction_arrows].
    arrows_forced_on = (
        raw_fv is None
        or raw_fv == "all"
        or (isinstance(raw_fv, list) and "direction_arrows" in raw_fv)
    )
    # Only an explicit default_visible list (including []) can leave the
    # arrows off; unset means the first-visit set, which has them.
    explicit_dv_omits_arrows = isinstance(raw_dv, list) and "direction_arrows" not in raw_dv
    if explicit_dv_omits_arrows and not arrows_forced_on and not arrows_suppressed:
        oneway_count = 0
        for f in trails_geojson.get("features") or []:
            ow = (f.get("properties") or {}).get("oneway")
            if ow in ("yes", True, "-1", "reversible"):
                oneway_count += 1
        if oneway_count > 0:
            console.warn(
                f"Map has {oneway_count} one-way trail segment(s) "
                "but direction_arrows is not in default_visible. Riders "
                "won't see directional indicators on first visit. "
                "Leave forced_visible or default_visible unset, or add "
                "'direction_arrows' to one of the lists."
            )


def _stage_enrich(config, trails_geojson, trails_path, cache_dir):
    """Enrich trails_geojson in place and write it to trails_path."""
    # Enrich trails.geojson with the three non-exclusive bucket flags
    # (summer/winter/emergency) on every route, append any user-defined
    # custom_routes, and compute per-route distance stats.
    # Idempotent - safe to re-run against a trails.geojson that's
    # already been enriched.
    enriched = _enrich_trails_geojson(config, trails_geojson, cache_dir)

    # Event-mode arrow restriction: when event_mode.direction_arrows is
    # true, the runtime would render arrows on every OSM-tagged oneway
    # way (clutter on an event map). Strip the `oneway` property from
    # any feature whose routes are all non-featured so the existing
    # arrow emitter naturally only renders on the event route(s).
    # Runs AFTER enrichment so featured custom routes' features (which
    # carry the curator's intended `oneway: "yes"`) are present.
    arrows_restricted = _apply_event_mode_to_feature_oneway(config, trails_geojson)

    # Always (re)write trails.geojson. It is the render output,
    # regenerated from the base on every build, so it must reflect this
    # build's enrichment regardless of which passes reported a change. (The
    # reuse fingerprint + content-guard live on trails.src.geojson, written
    # at fetch time; the render output is never reused as a cache.)
    #
    # Trim coordinate precision on the render output only (see
    # COORD_PRECISION) - roughly halves the gzipped transfer size and speeds
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
    if arrows_restricted:
        bits.append("event-mode arrow restriction")
    if bits:
        console.detail(f"Enriched {os.path.basename(trails_path)} with {' and '.join(bits)}")


def _stage_bbox(config, trails_geojson):
    # Compute bbox from trail geometry if not specified in config
    if "bbox" not in config:
        config["bbox"] = compute_bbox_from_trails(trails_geojson)
        console.detail(f"Computed bbox from trails: {config['bbox']}")

    # pan_bbox drives maxBounds and the basemap/terrain extraction
    # footprint; the tight `bbox` still frames the initial view. An
    # explicit pan_bbox wins; otherwise derive it from pan_padding
    # (default 0.5 = 50% of extent per side, about 4x the pannable area).
    if "pan_bbox" not in config:
        pan_padding = config.get("pan_padding", 0.5)
        config["pan_bbox"] = expand_bbox_for_pan(config["bbox"], pan_padding)
        if pan_padding > 0:
            console.detail(f"Pan envelope (pad {pan_padding}): {config['pan_bbox']}")
    else:
        console.detail(f"Pan envelope (explicit): {config['pan_bbox']}")
    console.blank(detail=True)


def _stage_pois(config, args, output_dir, cache_dir):
    """Write pois.geojson and return its path."""
    # Skip when every POI category is disabled. Otherwise fetch_pois runs
    # on every build, so config-defined POIs (parking, trailheads,
    # event_mode.pois) take effect without a refresh. The OSM portion
    # hits the Overpass cache, so a cached map pays under a second.
    pois_path = os.path.join(output_dir, "pois.geojson")
    if not any(config.get(k, True) for k in POI_SHOW_FLAGS):
        console.step("POIs: Skipped (all POI layers disabled)", detail=True)
        console.summary("POIs: skipped (all POI layers disabled)")
        # Write empty GeoJSON so the viewer doesn't 404
        with open(pois_path, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    else:
        fetch_pois(config, pois_path, cache_dir, refresh=args.refresh or args.refresh_pois)
    return pois_path


def _count_pois(config, pois_path):
    """Return (poi_counts, pois_data): features per poi_type, plus the
    parsed pois.geojson (None when unreadable)."""
    # The counts let the Welcome modal's Search line name only the POI
    # types that exist. Sources are pois.geojson (OSM-fetched) plus the
    # curator-supplied parking / trailheads YAML lists, which the runtime
    # renders as separate markers.
    poi_counts = {}
    pois_data = None
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
    return poi_counts, pois_data


def _do_basemap(config, refresh, extract_stale, refresh_paths, trails_geojson,
                extract_path, basemap_path, cache_dir, paths_bounds, tiles_minzoom,
                basemap_maxzoom, basemap_sig, ways_cache):
    # Old sidecar first: it vouched for the previous file and
    # must not survive to vouch for an interrupted regen.
    _clear_signature(basemap_path)
    if refresh or extract_stale:
        _clear_signature(extract_path)
        fetch_basemap(config, extract_path)
        _save_signature(extract_path, basemap_sig)
    try:
        # Whatever refreshes the trails refreshes the path
        # data with them: a way split in OSM between two
        # fetches would otherwise show through under its
        # new id.
        basemap_paths.generate(
            trails_geojson, extract_path, basemap_path, cache_dir,
            paths_bounds, tiles_minzoom, basemap_maxzoom,
            refresh=refresh_paths, osm_file_path=config.get("osm_file"))
    except basemap_paths.BasemapPathsError as e:
        console.error(str(e))
        sys.exit(1)
    # After generating: a refresh rewrites the path data,
    # and the signature must name what was actually used.
    _save_signature(basemap_path, basemap_paths.input_signature(
        basemap_sig, trails_geojson, ways_cache, config.get("osm_file")))


def _do_terrain(config, terrain_path, terrain_sig):
    # Old sidecar first: it vouched for the previous file and
    # must not survive to vouch for an interrupted regen.
    _clear_signature(terrain_path)
    if fetch_terrain(config, terrain_path):
        _save_signature(terrain_path, terrain_sig)
    elif os.path.exists(terrain_path):
        # Terrain failure is soft (the build continues and the
        # runtime's HEAD-probe disables hillshade when the file
        # is absent) - but regen was triggered because the
        # PREVIOUS file no longer matches this build's
        # bbox/zoom. Fetches are atomic, so what's on disk is
        # that stale wrong-extent file; leaving it would ship
        # it with exit code 0 and precache it on every phone.
        os.remove(terrain_path)
        console.warn(
            "terrain regen failed; removed the previous "
            "terrain.pmtiles (wrong extent) - hillshade is "
            "disabled until a build fetches terrain successfully"
        )


class _TileNote(NamedTuple):
    """What a basemap/terrain step reports once the fetches finish.

    verbose is the line --verbose prints, or None when the step already
    printed its own. summary is the default-level line; path, when set,
    adds the file's size to it.
    """

    verbose: str | None
    summary: str
    path: str | None = None


def _fmt_size(n_bytes):
    if n_bytes >= 1024 * 1024:
        return f"{n_bytes / (1024 * 1024):.1f} MB"
    return f"{n_bytes / 1024:.0f} KB"


def _plan_basemap(config, args, output_dir, cache_dir, trails_geojson, tiles_minzoom):
    """Return (task, messages): the basemap fetch to run (or None) and the
    notes to print after the fetches finish."""
    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    basemap_bbox = config.get("pan_bbox") or config["bbox"]
    basemap_maxzoom = BASEMAP_MAXZOOM
    basemap_sig = _bbox_signature(basemap_bbox, basemap_maxzoom, tiles_minzoom)

    if args.no_basemap:
        return None, [_TileNote(
            "Basemap: Skipped (--no-basemap)", "Basemap: skipped (--no-basemap)")]
    # basemap.pmtiles is the Protomaps extract with its path and
    # service-road lines replaced by generated ones
    # (basemap_paths.py). The plain extract is kept in the cache
    # dir, outside output_dir where the service worker sweep would
    # ship it, so a trail change re-runs the join without
    # extracting again.
    try:
        basemap_paths.require_tools()
    except basemap_paths.BasemapPathsError as e:
        console.error(str(e))
        sys.exit(1)
    extract_path = os.path.join(cache_dir, "basemap", f"{config['slug']}-protomaps.pmtiles")
    os.makedirs(os.path.dirname(extract_path), exist_ok=True)
    # The extract's bounds, padded the way fetch_basemap pads them.
    pad = EXTRACT_PAD_DEG
    paths_bounds = (basemap_bbox[0] - pad, basemap_bbox[1] - pad,
                    basemap_bbox[2] + pad, basemap_bbox[3] + pad)
    ways_cache = basemap_paths.overpass_cache_path(
        basemap_paths.tile_cover_bounds(paths_bounds), cache_dir)
    # Claimed on every build, not only when the query runs, or the
    # cache prune would drop it after the first build that reuses it.
    cache_manifest.record(ways_cache)

    existing_sig = _load_signature(basemap_path)
    extract_stale, extract_reason = _pmtiles_needs_regen(
        extract_path, basemap_bbox, basemap_maxzoom, tiles_minzoom)
    refresh_paths = args.refresh or args.refresh_trails
    expected_sig = basemap_paths.input_signature(
        basemap_sig, trails_geojson, ways_cache, config.get("osm_file"))
    paths_stale = (not os.path.exists(basemap_path) or existing_sig != expected_sig
                   or not os.path.exists(ways_cache)
                   # a signature vouches for the inputs, not for the
                   # bytes: an archive cut short by a full disk was
                   # signed like any other (2026-09-21)
                   or not basemap_paths.archive_ok(basemap_path))
    if args.refresh or extract_stale or refresh_paths or paths_stale:
        if not args.refresh and extract_stale and extract_reason:
            console.step(f"Basemap: re-extracting ({extract_reason})", detail=True)
        elif not refresh_paths and paths_stale:
            console.step(
                "Basemap: regenerating paths (trails, area or path data changed)", detail=True)
        task = functools.partial(
            _do_basemap, config, args.refresh, extract_stale, refresh_paths, trails_geojson,
            extract_path, basemap_path, cache_dir, paths_bounds, tiles_minzoom,
            basemap_maxzoom, basemap_sig, ways_cache)
        what = ("extracted from Protomaps, paths regenerated" if args.refresh or extract_stale
                else "paths regenerated")
        return task, [_TileNote(None, f"Basemap: {what}", basemap_path)]
    size_mb = os.path.getsize(basemap_path) / (1024 * 1024)
    return None, [_TileNote(
        f"Basemap: Using existing {basemap_path} ({size_mb:.1f} MB)",
        "Basemap: reused", basemap_path)]


def _plan_terrain(config, args, terrain_path, tiles_minzoom):
    """Return (task, messages): the terrain fetch to run (or None) and the
    notes to print after the fetches finish."""
    terrain_bbox = config.get("pan_bbox") or config["bbox"]
    terrain_maxzoom = TERRAIN_MAXZOOM
    terrain_sig = _bbox_signature(terrain_bbox, terrain_maxzoom, tiles_minzoom)

    if not config.get("show_terrain", True):
        messages = [_TileNote("Terrain: Disabled in config (show_terrain: false)",
                              "Terrain: disabled (show_terrain: false)")]
        # A previous build's archive must not survive the flip: the SW
        # sweep hashes and precaches everything in output_dir, so a
        # stale terrain.pmtiles (up to ~30 MB) would keep shipping to
        # every rider's phone for a layer the runtime never enables
        # (showTerrain gates the HEAD probe in app.js).
        if os.path.exists(terrain_path):
            _clear_signature(terrain_path)
            os.remove(terrain_path)
            messages.append(_TileNote(
                "Terrain: Removed stale terrain.pmtiles left by a previous build",
                "Terrain: removed stale terrain.pmtiles left by a previous build"))
        return None, messages
    if args.no_terrain:
        return None, [_TileNote(
            "Terrain: Skipped (--no-terrain)", "Terrain: skipped (--no-terrain)")]
    needs_regen, reason = _pmtiles_needs_regen(
        terrain_path, terrain_bbox, terrain_maxzoom, tiles_minzoom)
    if args.refresh or needs_regen:
        if not args.refresh and reason:
            console.step(f"Terrain: regenerating ({reason})", detail=True)
        task = functools.partial(_do_terrain, config, terrain_path, terrain_sig)
        return task, [_TileNote(None, "Terrain: extracted from Mapterhorn", terrain_path)]
    size_mb = os.path.getsize(terrain_path) / (1024 * 1024)
    return None, [_TileNote(
        f"Terrain: Using existing {terrain_path} ({size_mb:.1f} MB)",
        "Terrain: reused", terrain_path)]


def _plan_tiles(config, args, output_dir, cache_dir, trails_geojson):
    """Decide what the basemap and terrain steps must do.

    Returns (fetch_tasks, post_messages, terrain_path): fetch_tasks is a
    list of (label, callable) for _run_tiles; post_messages (_TileNote)
    print after every task completes.
    """
    # Plan-then-execute split: decision logic (skip / use cached /
    # regenerate) runs synchronously up front so the pre-fetch console
    # messages stay tidy. Only the actual fetch + signature-save runs
    # concurrently; subprocess output from the two fetches will
    # interleave on stdout, which is acceptable for build logs.
    tiles_minzoom = EXTRACT_MINZOOM
    terrain_path = os.path.join(output_dir, "terrain.pmtiles")

    fetch_tasks = []
    basemap_task, basemap_messages = _plan_basemap(
        config, args, output_dir, cache_dir, trails_geojson, tiles_minzoom)
    if basemap_task:
        fetch_tasks.append(("basemap", basemap_task))
    terrain_task, terrain_messages = _plan_terrain(config, args, terrain_path, tiles_minzoom)
    if terrain_task:
        fetch_tasks.append(("terrain", terrain_task))
    return fetch_tasks, basemap_messages + terrain_messages, terrain_path


def _run_tiles(fetch_tasks, post_messages):
    # Basemap and terrain are independent (no shared state, no order
    # dependency) and both are I/O-bound (network + subprocess for
    # pmtiles extract or mapterhorn fetch). Running them in a 2-worker
    # thread pool roughly halves the wall time on builds where both
    # fetch (~30-60s each → ~30-60s total instead of 60-120s).
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

    for note in post_messages:
        if note.verbose:
            console.step(note.verbose, detail=True)
        # The size is read now, not at planning time: a task that just ran
        # wrote the file. A missing file means a soft failure (terrain),
        # whose warning is already on screen.
        if note.path and os.path.exists(note.path):
            console.summary(f"{note.summary} ({_fmt_size(os.path.getsize(note.path))})")
        elif note.path:
            console.summary(note.summary.split(":")[0] + ": not generated (see the warning above)")
        else:
            console.summary(note.summary)
    console.blank(detail=True)


def _stage_templates(config, args, output_dir, cache_dir, trails_geojson):
    # Order matters: copy_assets runs first because it stashes
    # processed-logo dimensions on config["_brand_img_dims"] which
    # copy_templates reads when substituting the brand-img <img>
    # width/height/fetchpriority attributes. Swapping the order leaves
    # brand_dims as None and the brand-img tag emits without dimension
    # hints (CLS regression).
    console.step("Assembling output...", detail=True)
    copy_assets(config, output_dir)
    copy_templates(config, output_dir, trails_geojson)
    console.blank(detail=True)

    # Minify after copy_templates (which writes the targets) and before
    # generate_service_worker, so the SW hash covers the minified bytes
    # the rider downloads.
    if args.minify:
        console.step("Minifying assets...", detail=True)
        _minify_assets(output_dir)
        console.blank(detail=True)

    # CDN deps served locally for offline use.
    console.step("Bundling vendor libraries...", detail=True)
    download_vendor_libs(output_dir, cache_dir)
    console.blank(detail=True)


def _pwa_warnings(output_dir):
    """Return the reasons the PWA will not be installable (empty when it is)."""
    pwa_warnings = []
    manifest_path = os.path.join(output_dir, "icons", "site.webmanifest")
    if not os.path.exists(manifest_path):
        pwa_warnings.append(
            "No web manifest (site.webmanifest) - set 'icon:' (or "
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
                "Web manifest exists but no icon PNGs found - "
                "the browser needs at least one icon to show an install prompt"
            )
    return pwa_warnings


def _stage_pwa(config, args, output_dir):
    """Service worker, then precompression.

    MUST run after every other output file is written: the service worker
    needs the complete file list.
    """
    console.step("Generating PWA assets...", detail=True)
    generate_service_worker(config, output_dir)

    # Minify the service worker we just wrote (see MINIFY_TARGETS_SW
    # for why it cannot ride along with the other minify targets).
    # Safe here: the SW's own bytes are deliberately excluded from
    # CACHE_VERSION, so rewriting them does not invalidate the hash,
    # and the precompress step then compresses the minified bytes.
    if args.minify:
        _minify_assets(output_dir, MINIFY_TARGETS_SW)

    pwa_warnings = _pwa_warnings(output_dir)
    if pwa_warnings:
        console.blank()
        console.info("PWA WARNINGS - the app will not be installable until fixed:")
        for w in pwa_warnings:
            console.info(f"  • {w}")
        console.blank()

    # MUST be after the service worker - see precompress_assets.
    if args.precompress:
        console.step("Precompressing static assets...", detail=True)
        precompress_assets(output_dir)
        console.blank(detail=True)


def _stage_cache_manifest(config, args, cache_dir, cats, trails_fetch_ran):
    """Save this build's cache claims and prune what the last build
    claimed and this one did not."""
    # Last step of a successful build: every earlier failure raises or
    # exits before this, so an aborted build never prunes. Write the new
    # manifest FIRST, then delete - the on-disk manifest always
    # understates what is deletable, so no crash window between the two
    # can widen a later prune.
    old_cats = cache_manifest.load(cache_dir, config["slug"])
    if not trails_fetch_ran and old_cats:
        # Reuse build: fetch_trails never ran, so nothing was recorded
        # for trails, but those cached responses are what makes an
        # offline rebuild-from-scratch possible if build/<slug>/ is
        # deleted. Carry the previous claims forward verbatim.
        cats["overpass_trails"] = [
            os.path.join(cache_dir, p) for p in old_cats.get("overpass_trails", [])
        ]
    if args.no_basemap and old_cats:
        # Same reasoning for the basemap's path data: a --no-basemap
        # build planned nothing, and must not orphan what the last full
        # build fetched.
        cats["overpass_basemap"] = [
            os.path.join(cache_dir, p) for p in old_cats.get("overpass_basemap", [])
        ]
    new_cats = cache_manifest.save(cache_dir, config["slug"], cats)
    if old_cats is not None and new_cats is not None:
        removed, freed = cache_manifest.prune(cache_dir, config["slug"], old_cats, new_cats)
        if removed:
            noun = "entry" if removed == 1 else "entries"
            console.info(f"Cache: pruned {removed} stale {noun} ({freed / 1024:.0f} KB)")


def main(argv=None):
    args = _build_parser().parse_args(argv)

    console.set_verbosity(quiet=args.quiet, verbose=args.verbose)
    started = time.monotonic()

    config = load_config(args.config)
    project_root = os.path.dirname(SCRIPTS_DIR)

    # Validate the config before doing anything expensive (Overpass fetches,
    # tile generation). Errors abort the build; warnings (e.g. asset files
    # not present yet) print but allow it to continue.
    errors, warnings = validate_config(config, config_path=args.config)
    for line in warnings:
        console.raw(line)
    if errors:
        console.step(f"\nConfig validation failed for {args.config}:")
        for line in errors:
            console.raw(line)
        sys.exit(1)

    # A map that configures neither logo: nor icon: still gets favicons,
    # a maskable PWA icon + manifest (installable), and an on-page brand
    # mark by falling back to the engine's bundled placeholder. Applied
    # after validation (which judges the curator's real config) so it
    # also shows up in --dry-run's branding summary below.
    if apply_default_brand(config, project_root):
        # Shown by default: it fires only on brandless maps, and the
        # accent then derives from the placeholder's green.
        console.info("No logo/icon configured - using the bundled placeholder bike icon")

    output_dir, cache_dir = _resolve_dirs(config, args, project_root)

    # --dry-run: print what would happen, exit before any work.
    # Runs AFTER validate_config so any schema/value errors still abort
    # with a non-zero exit; runs BEFORE os.makedirs so dry-run leaves
    # zero filesystem footprint (no empty output_dir created).
    if args.dry_run:
        _print_dry_run_summary(config, args, output_dir, cache_dir)
        return

    os.makedirs(output_dir, exist_ok=True)

    # Discard collector state a prior in-process main() call may have
    # left behind (tests invoke main() repeatedly); the stage drains
    # below must only ever see this build's recordings. After the
    # --dry-run return so dry-run keeps its zero-footprint guarantee.
    cache_manifest.drain()

    # --refresh re-fetches this map's data by bypassing cached Overpass
    # responses (refresh flag on the fetch calls below). The cache
    # directory itself is left alone: it's SHARED across every map
    # (plus the vendor-lib and accent-derivation caches), so the old
    # rmtree here threw away all the other maps' responses too.

    if console.is_verbose():
        console.step(f"Building map: {config['title']}")
        console.step(f"Output: {output_dir}")
        console.blank()
    else:
        console.step(f"Building {config['title']} → {console.rel_path(output_dir)}")

    trails_geojson, trails_fetch_ran, trails_path, trails_src_path = _stage_trails(
        config, args, output_dir, cache_dir)
    overpass_trails_paths = cache_manifest.drain()

    config["_data_date"] = _data_date(trails_geojson, trails_src_path)

    # Tell the runtime whether clip_endpoints.geojson exists in this
    # build. fetch_trails only writes the file when there are clipped
    # relations whose endpoints fall inside the bbox (most maps don't
    # have any), so a runtime probe-fetch produced a noisy 404 on
    # those maps. Reading the file's existence here lets the runtime
    # skip the fetch entirely.
    config["_has_clip_endpoints"] = os.path.exists(
        os.path.join(output_dir, "clip_endpoints.geojson")
    )

    # Accent palette: stash the resolved 4-value palette (light + dark
    # shades, each with its on-accent text color) so
    # inject_config_into_template can emit them as the CONFIG.accent*
    # vars. resolve_accent_palette handles "auto" (Pillow-based logo
    # derivation, cached per-source-hash as the raw pick), explicit hex,
    # and unset (the same as "auto") uniformly, and emits per-shade
    # WCAG contrast warnings. Always returns a palette (never None).
    config["_accent_palette"] = resolve_accent_palette(config, project_root, cache_dir)
    derive_accent_paths = cache_manifest.drain()

    _event_mode_prepass(config)
    _warn_arrows_hidden(config, trails_geojson)
    _stage_enrich(config, trails_geojson, trails_path, cache_dir)
    route_stats_paths = cache_manifest.drain()
    _stage_bbox(config, trails_geojson)

    pois_path = _stage_pois(config, args, output_dir, cache_dir)
    overpass_pois_paths = cache_manifest.drain()
    console.blank(detail=True)
    # MUST run below _stage_pois: pois.geojson is read from disk.
    config["_poi_counts"], pois_data = _count_pois(config, pois_path)

    # OSM data-quality notes. Audits the PRE-enrichment snapshot re-read from
    # disk, not the in-memory trails_geojson: by this point enrichment has
    # baked in custom routes (not OSM data, so not OSM's to fix) and applied
    # rounded the coordinates, either of which would confuse the
    # unconnected-way check. One extra JSON parse buys an audit of exactly what OSM said.
    report_tagging_quality(_load_json_or_none(trails_src_path), pois_data,
                           config, cache_dir)

    fetch_tasks, post_messages, terrain_path = _plan_tiles(
        config, args, output_dir, cache_dir, trails_geojson)
    # Drained here, before the fetches run: the path data's cache path
    # is recorded during planning, and overpass.query records the same
    # path again when generation runs, which the next drain would then
    # hand to whatever category comes after.
    overpass_basemap_paths = cache_manifest.drain()
    _run_tiles(fetch_tasks, post_messages)

    # Tell the runtime whether terrain.pmtiles exists in this build, so
    # it can skip the HEAD probe (one serialized RTT before any trail
    # layer is created on every cold load). Checked AFTER the fetch step
    # above: extraction may have just written the file, a show_terrain
    # flip may have just removed it, and a soft terrain-fetch failure
    # removes a stale wrong-extent file. A --no-terrain build serving an
    # archive left by a previous build still reads True, matching what
    # the runtime's probe would have concluded.
    config["_has_terrain"] = os.path.exists(terrain_path)

    _stage_templates(config, args, output_dir, cache_dir, trails_geojson)
    _stage_pwa(config, args, output_dir)
    _stage_cache_manifest(config, args, cache_dir, {
        "overpass_trails": overpass_trails_paths,
        "overpass_pois": overpass_pois_paths,
        "overpass_basemap": overpass_basemap_paths,
        "route_stats": route_stats_paths,
        "derive_accent": derive_accent_paths,
    }, trails_fetch_ran)

    total = print_summary(output_dir)
    if not console.is_verbose():
        console.step(f"Built in {_fmt_elapsed(time.monotonic() - started)}: {_fmt_size(total)}")


if __name__ == "__main__":
    main()
