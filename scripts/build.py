#!/usr/bin/env python3
"""Build orchestrator for MTB Trail Map Framework.

Runs all pipeline steps, assembles templates with injected config,
and copies assets to produce a deployable static site.

Usage:
    python scripts/build.py configs/ramba/ramba.yaml
    python scripts/build.py configs/ramba/ramba.yaml --force
    python scripts/build.py configs/ramba/ramba.yaml --trails
    python scripts/build.py configs/ramba/ramba.yaml --skip-terrain
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

# Add scripts directory to path for imports
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from fetch_trails import fetch_trails
from fetch_pois import fetch_pois
from fetch_basemap import fetch_basemap
from fetch_terrain import fetch_terrain
from font_trimmer import copy_trimmed_fonts
from inject_clip_arrow import inject_clip_arrow
from generate_icons import generate_icons, generate_manifest
from validate_config import validate_config

# CDN libraries to bundle locally for offline/PWA support.
# Update versions here when upgrading dependencies.
VENDOR_LIBS = {
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@5.5.0/dist/maplibre-gl.css",
    "maplibre-gl.js": "https://unpkg.com/maplibre-gl@5.5.0/dist/maplibre-gl.js",
    "pmtiles.js": "https://unpkg.com/pmtiles@4.2.1/dist/pmtiles.js",
    "basemaps.js": "https://unpkg.com/@protomaps/basemaps@5.7.2/dist/basemaps.js",
}


# Logo render bounding box on desktop. The map overlay and the About modal
# both shrink-to-fit inside this box via CSS max-width/max-height; we resample
# the source to ~2× this on its longer side so retina displays render cleanly
# without us shipping the original (often much larger) source.
LOGO_DESKTOP_W = 200
LOGO_DESKTOP_H = 80


def logo_output_filename(source_path):
    """Return the filename the logo asset should be written as in the build dir.

    SVG sources are left as SVG (the browser scales them cleanly via CSS and
    Pillow can't rasterize them without extra deps). Everything else is
    normalized to WebP so the output is predictable for templating.
    """
    ext = os.path.splitext(source_path)[1].lower()
    return "logo.svg" if ext == ".svg" else "logo.webp"


def _copy_svg_with_intrinsic_size(source_path, output_path):
    """Copy an SVG, ensuring the root <svg> tag has pixel width/height.

    HTML <img> needs intrinsic pixel dimensions (or a definite aspect ratio)
    for `width:auto; height:auto; max-width/max-height` CSS to compute a
    non-zero render box. Many designer-exported SVGs use width="100%"
    height="100%" with only a viewBox; loaded via <img> with auto sizing
    they collapse to 0x0. We patch the root tag so width/height match the
    viewBox in pixels. The viewBox is preserved so nothing about the
    rendered geometry changes -- this purely gives the browser an
    intrinsic size to anchor layout.

    If the SVG already has non-percentage width/height, or has no viewBox
    to derive from, the file is copied verbatim.
    """
    import re

    try:
        with open(source_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"  WARNING: Could not read SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return

    svg_open = re.search(r"<svg\b[^>]*>", text)
    if not svg_open:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return

    tag = svg_open.group(0)
    width_m = re.search(r'\bwidth\s*=\s*"([^"]*)"', tag)
    height_m = re.search(r'\bheight\s*=\s*"([^"]*)"', tag)
    viewbox_m = re.search(r'\bviewBox\s*=\s*"([^"]*)"', tag)

    def _is_definite_pixel(v):
        if not v:
            return False
        v = v.strip()
        if v.endswith("%"):
            return False
        # Bare number or px / pt / em etc. -- the browser treats these as
        # definite intrinsic sizes.
        return bool(re.match(r"^-?\d+(\.\d+)?", v))

    has_definite = (
        width_m and _is_definite_pixel(width_m.group(1))
        and height_m and _is_definite_pixel(height_m.group(1))
    )

    if has_definite or not viewbox_m:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return

    parts = viewbox_m.group(1).split()
    if len(parts) != 4:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return
    try:
        vb_w = float(parts[2])
        vb_h = float(parts[3])
    except ValueError:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return

    # Format pixel values without a trailing ".0" when integer-valued.
    def _fmt(v):
        return str(int(v)) if v.is_integer() else str(v)

    new_w = _fmt(vb_w)
    new_h = _fmt(vb_h)

    new_tag = tag
    if width_m:
        new_tag = re.sub(
            r'\bwidth\s*=\s*"[^"]*"', f'width="{new_w}"', new_tag, count=1
        )
    else:
        new_tag = new_tag[:-1] + f' width="{new_w}">'
    if height_m:
        new_tag = re.sub(
            r'\bheight\s*=\s*"[^"]*"', f'height="{new_h}"', new_tag, count=1
        )
    else:
        new_tag = new_tag[:-1] + f' height="{new_h}">'

    new_text = text[:svg_open.start()] + new_tag + text[svg_open.end():]
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError as e:
        print(f"  WARNING: Could not write SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return

    print(
        f"  Wrote logo.svg ({new_w}×{new_h} from viewBox, "
        f"{os.path.getsize(output_path)} bytes, vector)"
    )


def process_logo(source_path, output_path):
    """Resample a logo source down to ~2× its on-screen render size and save.

    For raster sources the output is always WebP (even if the input was PNG/
    JPEG/etc.) with render dimensions inside the LOGO_DESKTOP_W × LOGO_DESKTOP_H
    box: wide wordmarks (aspect ≥ 2.5:1) are width-bound at 200 px, anything
    else is height-bound at 80 px. We never upscale; small sources are saved
    at their original dimensions.

    SVG sources skip Pillow entirely; they are copied with one defensive
    rewrite -- if the root <svg> uses percentage width/height (or omits them)
    we substitute pixel values derived from the viewBox so the browser has an
    intrinsic size for our `width: auto; height: auto; max-*` CSS to anchor.
    Without this, e.g. `<svg width="100%" height="100%" viewBox="0 0 600 600">`
    renders at 0x0 inside an <img>.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".svg":
        _copy_svg_with_intrinsic_size(source_path, output_path)
        return

    try:
        from PIL import Image
    except ImportError:
        print(f"  WARNING: Pillow not installed — copying logo verbatim to {output_path}")
        shutil.copy2(source_path, output_path)
        return

    try:
        img = Image.open(source_path)
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            raise ValueError(f"invalid logo dimensions {src_w}x{src_h}")

        aspect = src_w / src_h
        # Bounding-box decision: wider than the target box -> width-bound,
        # else height-bound. (target box aspect = 200/80 = 2.5)
        if aspect >= LOGO_DESKTOP_W / LOGO_DESKTOP_H:
            render_w = LOGO_DESKTOP_W
            render_h = LOGO_DESKTOP_W / aspect
        else:
            render_h = LOGO_DESKTOP_H
            render_w = LOGO_DESKTOP_H * aspect

        # Resample to 2× the render's longer side for retina sharpness;
        # never upscale.
        target_long = 2 * max(render_w, render_h)
        long_side = max(src_w, src_h)
        if long_side > target_long:
            scale = target_long / long_side
            new_size = (
                max(1, int(round(src_w * scale))),
                max(1, int(round(src_h * scale))),
            )
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # Preserve alpha when present; otherwise convert to RGB so WEBP
        # encoding doesn't choke on palette-based modes.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")

        img.save(output_path, "WEBP", quality=90, method=6)
        out_w, out_h = img.size
        print(f"  Wrote logo.webp ({out_w}×{out_h}, {os.path.getsize(output_path)} bytes)")
    except Exception as e:
        print(f"  WARNING: Failed to process logo ({e}) — copying source verbatim")
        try:
            shutil.copy2(source_path, output_path)
        except Exception as e2:
            print(f"  ERROR: Could not write logo: {e2}")


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
            print(f"  Downloading {filename}...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                f.write(resp.content)
            downloaded += 1

        shutil.copy2(cached, dst)

    if downloaded:
        print(f"  Downloaded {downloaded} vendor libraries")
    print(f"  Bundled {len(VENDOR_LIBS)} vendor libraries")


def generate_service_worker(config, output_dir):
    """Generate service worker with precache list from build output.

    Must run LAST — after all other files are in place so the precache
    list is complete.
    """
    project_root = os.path.dirname(SCRIPTS_DIR)
    sw_template = os.path.join(project_root, "templates", "sw.js")

    if not os.path.exists(sw_template):
        print(f"  WARNING: Service worker template not found: {sw_template}")
        return

    with open(sw_template) as f:
        sw_content = f.read()

    # Collect all files in output for precaching
    precache_urls = ["./"]
    pmtiles_files = []

    for root, dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if fname == "sw.js":
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, output_dir)
            precache_urls.append(rel)
            if fname.endswith(".pmtiles"):
                pmtiles_files.append(rel)

    # Compute cache version from actual file CONTENTS, not just the
    # filename list. The earlier "filenames + data_date" approach
    # missed the most common case: editing app.js / style.css /
    # index.html without touching trails or POIs left the cache version
    # unchanged, so the service worker happily served the stale cached
    # JS/CSS to every previously-installed visitor — fixes "appeared
    # to do nothing" until they manually cleared site data.
    #
    # Hashing every file's bytes adds ~1-2 s for a typical 24 MB build
    # and guarantees correctness: any change anywhere in the output
    # tree (code, data, assets) produces a fresh CACHE_VERSION, which
    # the SW activate handler uses to evict the old cache and reload.
    hasher = hashlib.sha256()
    for url in sorted(precache_urls):
        if url == "./":
            continue
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
    with open(sw_path, "w") as f:
        f.write(sw_content)

    print(f"  Generated service worker ({len(precache_urls)} files, cache {cache_version})")


def load_config(config_path):
    """Load and return a map config, resolving user-supplied asset paths
    to absolute on-disk paths relative to the config file's directory.

    Every per-map asset lives alongside its config (``configs/<slug>/``),
    so paths like ``logo: logo.webp`` resolve to
    ``<repo>/configs/<slug>/logo.webp`` — the user doesn't have to repeat
    the slug in every path. Absolute paths in the YAML are passed through
    unchanged (useful for shared assets outside the repo).

    Resolved keys: ``logo``, ``icon``, ``osm_file``, ``icons_dir`` (legacy),
    and every ``custom_routes[].geometry``. All other paths (``output_dir``,
    ``base_layers[].url``, etc.) stay in their original form — they're
    either repo-relative or external URLs.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    config_dir = os.path.dirname(os.path.abspath(config_path))

    def _resolve(path):
        if not path or not isinstance(path, str):
            return path
        return path if os.path.isabs(path) else os.path.join(config_dir, path)

    for key in ("logo", "icon", "osm_file", "icons_dir"):
        if key in config:
            config[key] = _resolve(config[key])

    for entry in (config.get("custom_routes") or []):
        if isinstance(entry, dict) and "geometry" in entry:
            entry["geometry"] = _resolve(entry["geometry"])

    # Stash the config's directory in case downstream code wants it
    # (error messages, future relative-path fields). Name-spaced with an
    # underscore so it doesn't collide with user-supplied keys.
    config["_config_dir"] = config_dir

    return config


def compute_bbox_from_trails(trails_geojson, buffer_frac=0.03,
                             buffer_min=0.001, buffer_max=0.01):
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

def _bbox_signature(bbox, maxzoom):
    """Stable text signature for an (bbox, maxzoom) extraction request."""
    return f"bbox={','.join(f'{v:.4f}' for v in bbox)};maxzoom={maxzoom}"


def _signature_path(output_path):
    return output_path + ".sig"


def _load_signature(output_path):
    sig_path = _signature_path(output_path)
    if not os.path.exists(sig_path):
        return None
    try:
        with open(sig_path) as f:
            return f.read().strip()
    except OSError:
        return None


def _save_signature(output_path, signature):
    try:
        with open(_signature_path(output_path), "w") as f:
            f.write(signature + "\n")
    except OSError as e:
        print(f"  WARNING: could not write {_signature_path(output_path)}: {e}")


def _pmtiles_needs_regen(output_path, bbox, maxzoom):
    """True if the cached PMTiles is missing OR its (bbox, maxzoom)
    signature doesn't match what's being requested. Returns a tuple
    (needs_regen: bool, reason: str | None) so the caller can log why."""
    if not os.path.exists(output_path):
        return True, "file missing"
    expected = _bbox_signature(bbox, maxzoom)
    actual = _load_signature(output_path)
    if actual is None:
        return True, "signature sidecar missing (legacy build)"
    if actual != expected:
        return True, f"signature changed (was {actual!r}, want {expected!r})"
    return False, None


def _enrich_trails_geojson(config, trails_geojson, project_root):
    """Enrich trails.geojson in-place with bucket flags + custom routes.

    Runs after trails have been fetched (or loaded from cache). It:

    - Strips any previously-appended custom-route features and metadata
      entries so re-runs are idempotent.
    - Computes three non-exclusive bucket booleans (``summer``, ``winter``,
      ``emergency``) for every OSM-sourced route in metadata.routes. Rules:
        winter    = (seasonal=winter in OSM)  OR  id in winter_relations
        emergency = id in emergency_access_relations
        summer    = id in summer_relations
                    OR  (not winter AND not emergency)
      "Summer is the default" — a plain OSM route with no seasonal tag and
      no inclusion in any of the three lists is summer-only.
      ``summer_relations`` is the opt-back-in list for year-round routes
      (the RAMBA SBR pattern: ridden in summer AND groomed in winter).
    - Loads each ``custom_routes`` entry's GeoJSON file, validates geometry
      type (LineString / MultiLineString only), normalises features into
      the shape fetch_trails.py emits (one LineString per feature), and
      appends them to ``features``. Custom-route metadata entries are added
      to metadata.routes with bucket flags declared inline in the config.

    Returns True if anything was changed (caller writes back to disk).
    """
    features = trails_geojson.get("features") or []
    # Strip previously-appended custom features for idempotent re-runs.
    cleaned = [
        f for f in features
        if not f.get("properties", {}).get("isCustom")
    ]
    stripped_count = len(features) - len(cleaned)
    trails_geojson["features"] = cleaned

    metadata = trails_geojson.setdefault("metadata", {})
    routes = metadata.setdefault("routes", {})
    for rid in list(routes.keys()):
        if routes[rid].get("isCustom"):
            del routes[rid]

    changed = stripped_count > 0

    # ----- Bucket flags on OSM routes -----
    # Config lists are ints (OSM relation ids); metadata.routes is keyed
    # by string. Stringify config values for lookup.
    summer_ids = {str(x) for x in (config.get("summer_relations") or [])}
    winter_ids = {str(x) for x in (config.get("winter_relations") or [])}
    emergency_ids = {
        str(x) for x in (config.get("emergency_access_relations") or [])
    }

    for rid_str, info in routes.items():
        is_winter = (info.get("seasonal") == "winter") \
            or (rid_str in winter_ids)
        is_emergency = rid_str in emergency_ids
        is_summer = (rid_str in summer_ids) \
            or (not is_winter and not is_emergency)

        # Compare before overwriting so we can return a tight "changed?" bit.
        prior = (info.get("summer"), info.get("winter"),
                 info.get("emergency"), info.get("isCustom", False))
        info["summer"] = is_summer
        info["winter"] = is_winter
        info["emergency"] = is_emergency
        info["isCustom"] = False
        # Keep the OSM-source `seasonal` field as-is. It's the upstream
        # input that `is_winter` reads above, so deleting it would make
        # this function non-idempotent: a rebuild that reuses an existing
        # trails.geojson (no --trails / --force) would see `seasonal`
        # already gone from the previous enrichment pass and miscompute
        # `is_winter` for every OSM-tagged winter relation.

        if prior != (is_summer, is_winter, is_emergency, False):
            changed = True

    # ----- Stringify route_id / shared_routes on every feature -----
    # The runtime treats route ids as opaque strings everywhere (so
    # OSM relation ids and custom string ids coexist in the same
    # filter expressions). fetch_trails.py emits OSM ids as ints;
    # custom routes already emit strings. Normalise both here so the
    # downstream JSON has a single consistent type.
    for feat in trails_geojson["features"]:
        props = feat.setdefault("properties", {})
        rid = props.get("route_id")
        if isinstance(rid, int):
            props["route_id"] = str(rid)
            changed = True
        sr = props.get("shared_routes")
        if isinstance(sr, list) and any(isinstance(x, int) for x in sr):
            props["shared_routes"] = [str(x) for x in sr]
            changed = True
        # Endpoint-cluster features carry route_ids (plural) + a
        # pipe-delimited string for MapLibre `in` filters; stringify
        # the array too.
        rids = props.get("route_ids")
        if isinstance(rids, list) and any(isinstance(x, int) for x in rids):
            props["route_ids"] = [str(x) for x in rids]
            changed = True

    # ----- Append custom-route features and metadata -----
    custom_routes = config.get("custom_routes") or []
    for entry in custom_routes:
        cid = entry["id"]
        cname = entry["name"]
        ccolor = entry["color"]
        cgeom_rel = entry["geometry"]
        cgeom_abs = (cgeom_rel if os.path.isabs(cgeom_rel)
                     else os.path.join(project_root, cgeom_rel))

        # Bucket flags: if none of the three are set, default to summer-only
        # to match the OSM-default rule. If any is set explicitly, use the
        # given values (False for the unset ones).
        flags_given = any(k in entry
                          for k in ("summer", "winter", "emergency"))
        if flags_given:
            c_summer = bool(entry.get("summer", False))
            c_winter = bool(entry.get("winter", False))
            c_emergency = bool(entry.get("emergency", False))
        else:
            c_summer, c_winter, c_emergency = True, False, False

        c_dashed = bool(entry.get("dashed", False))
        trail_name_field = entry.get("trail_name_field")

        # Load and validate geometry.
        try:
            with open(cgeom_abs) as f:
                gj = json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(
                f"ERROR: custom_routes[{cid!r}].geometry: "
                f"cannot read {cgeom_abs!r}: {e}"
            )

        if gj.get("type") == "FeatureCollection":
            gj_features = gj.get("features") or []
        elif gj.get("type") == "Feature":
            gj_features = [gj]
        else:
            sys.exit(
                f"ERROR: custom_routes[{cid!r}].geometry: top-level type "
                f"must be Feature or FeatureCollection "
                f"(got {gj.get('type')!r})"
            )

        appended = 0
        for i, feat in enumerate(gj_features):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            if gtype not in ("LineString", "MultiLineString"):
                sys.exit(
                    f"ERROR: custom_routes[{cid!r}].geometry feature {i}: "
                    f"geometry type must be LineString or MultiLineString "
                    f"(got {gtype!r})"
                )
            coords = geom.get("coordinates") or []
            if not coords:
                sys.exit(
                    f"ERROR: custom_routes[{cid!r}].geometry feature {i}: "
                    f"empty coordinates"
                )

            # Normalise MultiLineString into separate LineString features
            # (matches fetch_trails.py's per-segment shape).
            linestrings = coords if gtype == "MultiLineString" else [coords]

            src_props = feat.get("properties") or {}
            trail_name = ""
            if trail_name_field and trail_name_field in src_props:
                val = src_props[trail_name_field]
                if isinstance(val, str):
                    trail_name = val

            for line in linestrings:
                if not isinstance(line, list) or len(line) < 2:
                    continue
                new_feat = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": line,
                    },
                    "properties": {
                        "route_id": cid,          # string id; Phase 4 will
                                                   # stringify OSM ids too.
                        "route_name": cname,
                        "route_colour": ccolor,
                        "route_ref": "",
                        "trail_name": trail_name,
                        "shared_routes": [cid],
                        "imba_difficulty": "",
                        "oneway": "",
                        "segment_index": appended,
                        "way_ids": [],
                        "isCustom": True,
                    },
                }
                trails_geojson["features"].append(new_feat)
                appended += 1

        # Metadata.routes entry — shape mirrors OSM-sourced routes plus
        # the three bucket flags and isCustom.
        info = {
            "name": cname,
            "colour": ccolor,
            "ref": "",
            "seasonal": "winter" if (c_winter and not c_summer) else "",
            "summer": c_summer,
            "winter": c_winter,
            "emergency": c_emergency,
            "isCustom": True,
        }
        if c_dashed:
            # Default dash pattern for custom dashed routes; users who
            # need a specific pattern can adjust by adding a matching
            # dashed_relations entry keyed by the custom id (the runtime
            # filter treats the route id opaquely).
            info["dashed"] = [4, 4]
        routes[cid] = info
        changed = True

    return changed


# Declarative config spec: (yaml_key, js_key, default).
# Hoisted to module scope so validate_config.py's `--check-spec` drift
# lint can import it. Simple entries flow through `inject_config_into
# _template` automatically; the keys with custom logic (routes,
# directionSchedules, baseLayers, customRoutes, defaultTrailColor,
# about, logoUrl) are handled in a separate block right after the
# loop and intentionally do NOT appear in CONFIG_SPEC. The set
# `validate_config.HANDLED_SPECIALLY` lists those YAML keys so the
# drift lint accepts the omission.
#
# The runtime persists user-facing toggle states (POI visibility,
# season mode, Emergency, labels, Difficulty) in localStorage under
# `mtb.*` keys — there are no `*_default_on` config knobs. House
# defaults live in app.js. The `show_*` fields below gate *data
# fetching* and *build-time asset generation* (e.g. show_markers:
# false skips the Overpass query entirely; show_difficulty: false
# skips IMBA sprite generation), not UI visibility.
CONFIG_SPEC = [
    # Identity
    ("name",                    "name",                 None),
    ("slug",                    "slug",                 None),
    ("title",                   "title",                None),

    # View geometry.
    # `bbox` still frames the trails for the initial view fit.
    # `pan_bbox` is the looser envelope used for maxBounds (the pan
    # wall) and gets precomputed from `bbox` + `pan_padding` above.
    ("bbox",                    "bbox",                 None),
    ("pan_bbox",                "panBbox",              None),
    ("center",                  "center",               None),
    ("zoom",                    "zoom",                 14),
    ("min_zoom",                "minZoom",              10),
    ("max_zoom",                "maxZoom",              18),

    # Build-time data gates (skip fetching / sprite-gen when False).
    # `show_markers` merges guideposts + emergency access points into
    # one "trail marker" category — they render identically now.
    ("show_markers",            "showMarkers",          True),
    ("show_features",           "showFeatures",         True),
    ("show_parking",            "showParking",          True),
    ("show_trailheads",         "showTrailheads",       True),
    ("show_terrain",            "showTerrain",          True),
    ("show_difficulty",         "showDifficulty",       False),

    # Distance (meters) from the nearest visible trail within which a
    # trail-marker or feature POI is allowed to render. Tight values
    # (~10 m) hide POIs that aren't directly on the trail; loose
    # values (~75 m+) include nearby attractions but risk surfacing
    # bbox-incidental POIs. Default 50 surfaces typical
    # `tourism=attraction` features (often 10-50 m off-trail) while
    # keeping the filter useful. The Features peek toggle auto-hides
    # if no feature POI passes this check.
    ("poi_proximity_m",         "poiProximityMeters",   50),

    # UI gates for the Finder + Labels dropdown. Some systems have no
    # curated routes (trails only) or treat routes and trails as the
    # same set; turning one off hides the matching Finder section and
    # Labels option.
    ("show_routes",             "showRoutes",           True),
    ("show_trails",             "showTrails",           True),

    # Display
    ("default_labels",          "defaultLabels",        "routes"),
    ("color_by",                "colorBy",              "relation"),
    ("suppress_path_labels",    "suppressPathLabels",   False),
    ("suppress_basemap_pois",   "suppressBasemapPois",  False),
    # When true (the default), highlighting a route or trail dims
    # everything else on the map (basemap tint + non-highlighted
    # labels/arrows/difficulty hidden + POI markers faded) so the
    # highlighted feature reads as a spotlight. Set false per-map to
    # keep every route visible at full brightness behind the
    # highlight ribbon.
    ("map_dim_on_highlight",    "mapDimOnHighlight",    True),

    # When true (the default), MapLibre writes `#zoom/lat/lon` to
    # the URL hash as the user pans/zooms, and honours any hash on
    # page load. Makes views shareable and survives reload, at the
    # cost of leaking last-viewed location in the address bar /
    # screenshots / screen-shares. Set false to drop the hash
    # entirely (URL stays clean, no persistence across reload, no
    # shareable deep-links).
    ("url_hash",                "urlHash",              False),

    # Marker colours (kept per user request; some systems have
    # branded marker palettes aligned with their trail colours).
    # parking/trailhead/feature colours flow to CSS custom
    # properties on :root so the peek-bar swatch, the on-map
    # marker, and the popup badge all stay in lockstep.
    ("marker_color",            "markerColor",          "#795548"),
    ("marker_text_color",       "markerTextColor",      "white"),
    ("marker_border_color",     "markerBorderColor",    "white"),
    ("parking_color",           "parkingColor",         "#2980b9"),
    ("parking_text_color",      "parkingTextColor",     "white"),
    ("parking_border_color",    "parkingBorderColor",   "white"),
    ("trailhead_color",         "trailheadColor",       "#27ae60"),
    ("trailhead_text_color",    "trailheadTextColor",   "white"),
    ("trailhead_border_color",  "trailheadBorderColor", "white"),
    ("feature_color",           "featureColor",         "#8e44ad"),
    ("feature_ring_color",      "featureRingColor",     "#ffffff"),

    # PWA
    ("pwa",                     "pwa",                  True),

    # User-supplied
    ("parking",                 "parking",              []),
]


def inject_config_into_template(template_content, config, trails_geojson):
    """Replace the CONFIG placeholder in templates with actual config data."""
    # Extract route metadata from the trails GeoJSON
    routes = {}
    if trails_geojson and "metadata" in trails_geojson:
        routes = trails_geojson["metadata"].get("routes", {})

    # Apply route overrides (winter, colour, dash) in a single pass.
    # YAML keys keep their original "relation" names since they take OSM
    # relation IDs as input; the values populate route info on the JS side.
    winter_ids = set(config.get("winter_relations") or [])
    relation_colors = config.get("relation_colors") or {}
    dashed_relations = config.get("dashed_relations") or {}

    # Direction schedules control when ways tagged oneway=yes/-1/reversible
    # have their arrows rotated 180° (day-of-week alternation). Two layers:
    #
    #   default_direction_schedule  - applies to every route in the map.
    #                                 Use this for the common case where the
    #                                 whole trail system shares one schedule
    #                                 (same posted signage, same alternation).
    #   direction_schedules         - per-relation overrides. Use only when
    #                                 a specific route differs from the
    #                                 system-wide default.
    #
    # An explicit per-relation entry always wins, even if its reverse_days is
    # empty — that's the way to opt one route out of the default. Relations
    # NOT mentioned in direction_schedules fall back to the default.
    #
    # Relations are just the grouping handle here — relations themselves don't
    # have direction; their member ways do.
    VALID_WEEKDAYS = {"sunday", "monday", "tuesday", "wednesday",
                      "thursday", "friday", "saturday"}
    # Parity tokens extend the vocabulary: a route with `reverse_days:
    # [even_days]` flips 180° on any even calendar date (2, 4, 6 …); `odd_days`
    # is the mirror. These are evaluated at runtime the same way weekday tokens
    # are — any match in the list triggers reversal — so mixing them is fine
    # ("today is Monday OR today is even"), though most users will use one form
    # or the other. The canonical snake_case forms are what gets emitted into
    # CONFIG.directionSchedules; short forms like "even" or "odd" are accepted
    # for author convenience.
    VALID_PARITY = {"even_days", "odd_days"}
    VALID_DAYS = VALID_WEEKDAYS | VALID_PARITY

    def _normalise_days(days_in, error_label):
        days_norm = []
        for d in days_in or []:
            dl = str(d).strip().lower()
            match = next((full for full in VALID_DAYS
                          if full == dl or (len(dl) >= 3 and full.startswith(dl))),
                         None)
            if match is None:
                sys.exit(
                    f"ERROR: {error_label} contains unknown day token {d!r}; "
                    f"valid: {sorted(VALID_DAYS)}"
                )
            days_norm.append(match)
        return sorted(set(days_norm))

    # Default schedule (singular). Stored as None when unset OR when explicitly
    # set with empty reverse_days (degenerate; treated the same as unset).
    def_sched_raw = config.get("default_direction_schedule") or {}
    def_sched_days = _normalise_days(
        (def_sched_raw or {}).get("reverse_days"),
        "default_direction_schedule.reverse_days",
    )
    def_sched_norm = {"reverse_days": def_sched_days} if def_sched_days else None

    # Per-relation overrides (plural).
    sched_raw = config.get("direction_schedules") or {}
    sched_processed = {}
    for rel_id, spec in sched_raw.items():
        days = _normalise_days(
            (spec or {}).get("reverse_days"),
            f"direction_schedules[{rel_id}].reverse_days",
        )
        # Even an empty list is recorded — that's how a user opts a single
        # route out of the system-wide default.
        sched_processed[int(rel_id)] = {"reverse_days": days}

    # Resolve the effective schedule for each route: per-relation override if
    # present (whether empty or not), else the default if any.
    def effective_schedule(rel_id):
        if rel_id in sched_processed:
            return sched_processed[rel_id]
        return def_sched_norm  # None if no default set

    effective_schedules = {}
    for route_id_str, route_info in routes.items():
        # Custom routes have non-numeric string ids and no direction
        # schedules. Skip them so int() doesn't blow up.
        if route_info.get("isCustom"):
            continue
        rid = int(route_id_str)
        eff = effective_schedule(rid)
        if eff and eff.get("reverse_days"):
            effective_schedules[rid] = eff

    # Validate oneway=reversible features against the resolved schedules.
    # fetch_trails.py performs the same check against raw OSM data, but it
    # only runs when the GeoJSON is being (re)built. A "config-only" rebuild
    # — `build.py <config>` without --trails/--force — reuses the cached
    # GeoJSON and would otherwise skip the check. We re-validate here so the
    # build always fails when a reversible way has no schedule covering it,
    # regardless of whether trails were refetched this run.
    if trails_geojson and trails_geojson.get("features"):
        # scheduled_ids is compared against feature `shared_routes`, which are
        # stringified in the enriched GeoJSON. Stringify the keys to match.
        scheduled_ids = set(str(k) for k in effective_schedules.keys())
        unscheduled = []  # list of (way_id_or_None, parent_route_ids)
        unscheduled_no_wayids = 0  # features missing way_ids (legacy cache)
        seen = set()
        for feat in trails_geojson["features"]:
            props = feat.get("properties", {})
            if props.get("oneway") != "reversible":
                continue
            parents = set(props.get("shared_routes") or [])
            if parents & scheduled_ids:
                continue
            way_ids = props.get("way_ids") or []
            if not way_ids:
                # Cached GeoJSON predates the way_ids field. We can still flag
                # the problem at the route level, just not point at specific
                # OSM ways. Recommend rebuilding trails to get URLs.
                unscheduled_no_wayids += 1
                key = ("__route__", tuple(sorted(parents)))
                if key not in seen:
                    seen.add(key)
                    unscheduled.append((None, sorted(parents)))
                continue
            for wid in way_ids:
                if wid in seen:
                    continue
                seen.add(wid)
                unscheduled.append((wid, sorted(parents)))
        if unscheduled:
            lines = [
                "ERROR: Found oneway=reversible way(s) without a direction",
                "       schedule covering them. Reversible trails change",
                "       direction by schedule and cannot render correctly",
                "       without one.",
                "",
                "       Either set a system-wide default that covers every route:",
                "",
                "         default_direction_schedule:",
                "           reverse_days: [tuesday, thursday, saturday]",
                "",
                "       …or schedule the specific parent relation:",
                "",
                "         direction_schedules:",
                "           <relation_id>:",
                "             reverse_days: [tuesday, thursday, saturday]",
                "",
                "       Offending ways (way_id → parent relation IDs):",
            ]
            for way_id, parents in unscheduled[:20]:
                if way_id is None:
                    lines.append(f"         (way_ids unavailable in cached GeoJSON)"
                                 f"  →  parent relations: {parents}")
                else:
                    lines.append(f"         https://www.openstreetmap.org/way/{way_id}"
                                 f"  →  {parents}")
            if len(unscheduled) > 20:
                lines.append(f"         ... and {len(unscheduled) - 20} more")
            if unscheduled_no_wayids:
                lines.append("")
                lines.append("       Tip: rerun with --trails to refresh the cached")
                lines.append("       GeoJSON; new builds include OSM way IDs so this")
                lines.append("       message will list specific ways.")
            sys.exit("\n".join(lines))

    for route_id_str, route_info in routes.items():
        # Custom routes carry their own style + flags (set at enrichment
        # time) and don't participate in the int-keyed OSM overrides.
        if route_info.get("isCustom"):
            # Still normalise dashed to a concrete value so the runtime
            # can assume the field is present.
            if "dashed" not in route_info:
                route_info["dashed"] = False
            continue

        route_id = int(route_id_str)

        # Winter override — kept for the legacy "seasonal" field on route
        # info (backward compat with the current template). The three
        # bucket flags (summer/winter/emergency) are set in
        # _enrich_trails_geojson before this function runs.
        if route_id in winter_ids:
            route_info["seasonal"] = "winter"

        # Colour override
        color_override = relation_colors.get(route_id)
        if color_override:
            route_info["colour"] = color_override

        # Dash pattern override
        raw = dashed_relations.get(route_id)
        if raw:
            if isinstance(raw, list):
                route_info["dashed"] = raw
            elif isinstance(raw, dict):
                route_info["dashed"] = raw.get("pattern", [2, 2])
                if raw.get("cap"):
                    route_info["dashCap"] = raw["cap"]
                if raw.get("colors"):
                    route_info["dashColors"] = raw["colors"]
            else:
                route_info["dashed"] = False
        else:
            route_info["dashed"] = False

        # Mark routes that have an effective direction schedule (per-relation
        # override or system-wide default) so the runtime can find them quickly
        # without scanning the schedule map.
        if route_id in effective_schedules:
            route_info["hasDirectionSchedule"] = True

    config_obj = {}
    for yaml_key, js_key, default in CONFIG_SPEC:
        if default is None:
            config_obj[js_key] = config[yaml_key]       # required keys
        else:
            config_obj[js_key] = config.get(yaml_key, default)

    # Keys with custom logic
    config_obj["routes"] = routes
    config_obj["directionSchedules"] = {
        # JSON object keys must be strings; the runtime parses them to Number.
        # We emit the *resolved* per-route schedules (default expanded out for
        # every route it applies to, with per-relation overrides honored) so
        # the runtime doesn't need to know about the default/override layering.
        str(rel_id): spec for rel_id, spec in effective_schedules.items()
    }
    config_obj["baseLayers"] = config.get("base_layers") or []
    config_obj["customRoutes"] = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "color": entry["color"],
            "description": entry.get("description", ""),
        }
        for entry in (config.get("custom_routes") or [])
    ]
    # default_trail_color: string or object with color/pattern/cap
    dtc = config.get("default_trail_color", "#808080")
    if isinstance(dtc, dict):
        config_obj["defaultTrailColor"] = dtc.get("color", "#808080")
        config_obj["defaultTrailDash"] = dtc.get("pattern", False)
        config_obj["defaultTrailCap"] = dtc.get("cap", "round")
    else:
        config_obj["defaultTrailColor"] = dtc
        config_obj["defaultTrailDash"] = False
        config_obj["defaultTrailCap"] = "round"

    config_obj["buildDate"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    config_obj["dataDate"] = config.get("_data_date", "")
    config_obj["hasClipEndpoints"] = bool(config.get("_has_clip_endpoints"))
    config_obj["about"] = config.get("about") or None

    # Logo: derived from `logo:` if set, else falls back to `icon:`. Processed
    # in copy_assets() into a normalized `logo.webp` (raster) or `logo.svg`
    # (vector). Only emit logoUrl when the chosen source actually exists.
    project_root_for_logo = os.path.dirname(SCRIPTS_DIR)
    logo_p = config.get("logo") or ""
    icon_p = config.get("icon") or ""
    logo_source_rel = ""
    if logo_p and os.path.isfile(os.path.join(project_root_for_logo, logo_p)):
        logo_source_rel = logo_p
    elif not logo_p and icon_p and os.path.isfile(os.path.join(project_root_for_logo, icon_p)):
        logo_source_rel = icon_p
    config_obj["logoUrl"] = logo_output_filename(logo_source_rel) if logo_source_rel else None

    config_json = json.dumps(config_obj, indent=2)
    return template_content.replace("/*__CONFIG__*/", f"const CONFIG = {config_json};")


def copy_templates(config, output_dir, trails_geojson):
    """Copy and process HTML/JS/CSS templates."""
    project_root = os.path.dirname(SCRIPTS_DIR)
    templates_dir = os.path.join(project_root, "templates")

    for filename in ["index.html", "app.js", "style.css"]:
        src = os.path.join(templates_dir, filename)
        if not os.path.exists(src):
            print(f"  WARNING: Template not found: {src}")
            continue

        with open(src) as f:
            content = f.read()

        # Inject config into JS files
        if filename.endswith(".js"):
            content = inject_config_into_template(content, config, trails_geojson)

        # Process HTML template
        if filename == "index.html":
            # Dynamic page title
            content = re.sub(
                r'<title>.*?</title>',
                f'<title>{config["title"]}</title>',
                content,
            )

            # Inject or remove logo. Logo source falls back to icon: when
            # logo: is omitted; raster sources are normalized to `logo.webp`
            # in copy_assets() while SVG sources are copied as `logo.svg`.
            # The template ships with src="logo.webp"; rename to .svg if the
            # source is vector. Strip the overlay only when neither source
            # is set.
            logo_path = config.get("logo", "")
            icon_path_for_logo = config.get("icon", "")
            logo_chosen = logo_path or icon_path_for_logo
            if logo_chosen:
                out_name = logo_output_filename(logo_chosen)
                if out_name != "logo.webp":
                    content = content.replace("logo.webp", out_name)
            else:
                content = re.sub(
                    r'\s*<!-- Logo overlay -->.*?</div>\n',
                    '', content, flags=re.DOTALL,
                )

            # Strip PWA install UI and SW registration when PWA is disabled
            if not config.get("pwa", True):
                content = re.sub(
                    r'\s*<!-- PWA start -->.*?<!-- PWA end -->\n',
                    '', content, flags=re.DOTALL,
                )
                content = re.sub(
                    r'\s*<!-- SW start -->.*?<!-- SW end -->\n',
                    '', content, flags=re.DOTALL,
                )

            # Strip icon links when no icon is configured
            icon_path = config.get("icon", "")
            icons_dir_legacy = config.get("icons_dir", "")
            if not icon_path and not icons_dir_legacy:
                content = re.sub(
                    r'\s*<!-- Icons start -->.*?<!-- Icons end -->\n',
                    '', content, flags=re.DOTALL,
                )

        dst = os.path.join(output_dir, filename)
        with open(dst, "w") as f:
            f.write(content)
        print(f"  Copied {filename}")


def copy_assets(config, output_dir):
    """Copy logo, icons, fonts, and sprites."""
    project_root = os.path.dirname(SCRIPTS_DIR)

    # Logo: resampled to a bounding-box render size and written as logo.webp.
    # If `logo:` is omitted but `icon:` is set, the icon source is used as the
    # logo automatically (square icons render as ~80x80 badges).
    logo_path = config.get("logo", "")
    icon_path = config.get("icon", "")
    logo_src = None
    if logo_path:
        candidate = os.path.join(project_root, logo_path)
        if os.path.isfile(candidate):
            logo_src = candidate
        else:
            print(f"  WARNING: Logo not found: {candidate}")
    elif icon_path:
        candidate = os.path.join(project_root, icon_path)
        if os.path.isfile(candidate):
            logo_src = candidate
            print(f"  No logo configured — using icon as logo")
    if logo_src:
        out_name = logo_output_filename(logo_src)
        out_path = os.path.join(output_dir, out_name)
        process_logo(logo_src, out_path)

    # Icons — generate from source image or copy from legacy icons_dir
    icon_path = config.get("icon", "")
    icons_dir_legacy = config.get("icons_dir", "")
    if icon_path:
        icon_src = os.path.join(project_root, icon_path)
        generate_icons(icon_src, output_dir, config)
        # Manifest is generated inside generate_icons
    elif icons_dir_legacy:
        print(f"  DEPRECATION: 'icons_dir' is deprecated, use 'icon' instead")
        icons_src = os.path.join(project_root, icons_dir_legacy)
        icons_dst = os.path.join(output_dir, "icons")
        if os.path.exists(icons_src):
            os.makedirs(icons_dst, exist_ok=True)
            icon_files = [
                "apple-touch-icon.png",
                "favicon-32x32.png",
                "favicon-16x16.png",
                "safari-pinned-tab.svg",
                "android-chrome-192x192.png",
                "android-chrome-256x256.png",
                "mstile-150x150.png",
            ]
            copied = 0
            for fname in icon_files:
                src = os.path.join(icons_src, fname)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(icons_dst, fname))
                    copied += 1
            print(f"  Copied {copied} icon files")
            # Always generate manifest dynamically (replaces static copy)
            generate_manifest(config, output_dir)
        else:
            print(f"  WARNING: Icons directory not found: {icons_src}")
    else:
        print(f"  No icon configured — skipping icon generation")

    # Fonts (trimmed based on map data)
    fonts_src = os.path.join(project_root, "assets", "fonts")
    copy_trimmed_fonts(output_dir, fonts_src)

    # Sprites — only copy the version referenced by app.js
    sprites_src = os.path.join(project_root, "assets", "sprites")
    sprites_dst = os.path.join(output_dir, "sprites")
    app_js_path = os.path.join(output_dir, "app.js")
    sprite_version = None
    if os.path.exists(app_js_path):
        with open(app_js_path) as f:
            m = re.search(r'sprites/(v\d+)/', f.read())
            if m:
                sprite_version = m.group(1)
    sprites_injected_dirs = []  # collect for clip-arrow injection below
    if sprite_version:
        ver_src = os.path.join(sprites_src, sprite_version)
        ver_dst = os.path.join(sprites_dst, sprite_version)
        if os.path.exists(ver_src):
            if os.path.exists(sprites_dst):
                shutil.rmtree(sprites_dst)
            os.makedirs(sprites_dst, exist_ok=True)
            shutil.copytree(ver_src, ver_dst)
            print(f"  Copied sprites ({sprite_version})")
            sprites_injected_dirs.append(ver_dst)
        else:
            print(f"  WARNING: Sprites {sprite_version} not found at {ver_src}")
            print(f"  Download from: https://github.com/protomaps/basemaps-assets")
    elif os.path.exists(sprites_src) and os.listdir(sprites_src):
        if os.path.exists(sprites_dst):
            shutil.rmtree(sprites_dst)
        shutil.copytree(sprites_src, sprites_dst)
        print(f"  Copied sprites (all versions)")
        # Inject into every version directory found.
        for entry in sorted(os.listdir(sprites_dst)):
            sub = os.path.join(sprites_dst, entry)
            if os.path.isdir(sub):
                sprites_injected_dirs.append(sub)
    else:
        print(f"  WARNING: Sprites not found at {sprites_src}")
        print(f"  Download from: https://github.com/protomaps/basemaps-assets")

    # Inject the SDF clip-continuation arrowhead into each copied atlas so
    # the renderer can tint it per-route via icon-color. Idempotent — a no-op
    # on rebuilds where the icon is already present.
    sdf_1x = os.path.join(project_root, "assets", "extras", "clip-arrow.sdf.png")
    sdf_2x = os.path.join(project_root, "assets", "extras", "clip-arrow.sdf@2x.png")
    if os.path.exists(sdf_1x) and os.path.exists(sdf_2x):
        for sprite_dir in sprites_injected_dirs:
            inject_clip_arrow(sprite_dir, sdf_1x, sdf_2x)
    else:
        print(f"  WARNING: clip-arrow SDF assets missing — continuation "
              f"arrowheads will not render. Run "
              f"`python assets/extras/generate_clip_arrow.py` to regenerate.")


def print_summary(output_dir):
    """Print a summary of the build output."""
    print("\n" + "=" * 60)
    print("BUILD SUMMARY")
    print("=" * 60)
    total = 0
    fonts_size = 0
    fonts_count = 0
    fonts_dir = os.path.join(output_dir, "fonts")
    for root, dirs, files in os.walk(output_dir):
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
                print(f"  {rel:40s} {size / (1024*1024):8.1f} MB")
            else:
                print(f"  {rel:40s} {size / 1024:8.1f} KB")

    if fonts_count > 0:
        label = f"fonts/ ({fonts_count} PBF files)"
        if fonts_size > 1024 * 1024:
            print(f"  {label:40s} {fonts_size / (1024*1024):8.1f} MB")
        else:
            print(f"  {label:40s} {fonts_size / 1024:8.1f} KB")

    print(f"  {'TOTAL':40s} {total / (1024*1024):8.1f} MB")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Build MTB trail map")
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument("--force", action="store_true", help="Force re-fetch all data (including Overpass cache)")
    parser.add_argument("--trails", action="store_true", help="Re-fetch only trail data (uses Overpass cache)")
    parser.add_argument("--skip-terrain", action="store_true", help="Skip terrain tile generation")
    parser.add_argument("--skip-basemap", action="store_true", help="Skip basemap extraction")
    args = parser.parse_args()

    config = load_config(args.config)
    project_root = os.path.dirname(SCRIPTS_DIR)

    # Validate the config before doing anything expensive (Overpass fetches,
    # tile generation). Errors abort the build; warnings (e.g. asset files
    # not present yet) print but allow it to continue.
    errors, warnings = validate_config(
        config, config_path=args.config, project_root=project_root)
    for line in warnings:
        print(line)
    if errors:
        print(f"\nConfig validation failed for {args.config}:")
        for line in errors:
            print(line)
        sys.exit(1)

    output_dir = os.path.join(project_root, config.get("output_dir", os.path.join("build", config["slug"])))
    cache_dir = os.path.join(project_root, "cache")

    os.makedirs(output_dir, exist_ok=True)

    # --force clears the Overpass response cache so data is re-fetched from OSM
    if args.force and os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
        print(f"Cleared Overpass cache: {cache_dir}")

    print(f"Building map: {config['title']}")
    print(f"Output: {output_dir}")
    print()

    # Step 1: Fetch trails
    trails_path = os.path.join(output_dir, "trails.geojson")
    if args.force or args.trails or not os.path.exists(trails_path):
        trails_geojson = fetch_trails(config, trails_path, cache_dir)
    else:
        print(f"Trails: Using existing {trails_path}")
        with open(trails_path) as f:
            trails_geojson = json.load(f)

    # Record the data date from the trails file modification time, including
    # local-time HH:MM so the About modal can show fetch granularity finer
    # than a single day. Also feeds the service-worker cache key (line ~109),
    # so finer precision means stale clients update more reliably.
    # Captured BEFORE enrichment write-back so the displayed "data date"
    # tracks when OSM was fetched, not when this build re-enriched.
    config["_data_date"] = datetime.fromtimestamp(
        os.path.getmtime(trails_path)
    ).strftime("%Y-%m-%d %H:%M")

    # Tell the runtime whether clip_endpoints.geojson exists in this
    # build. fetch_trails only writes the file when there are clipped
    # relations whose endpoints fall inside the bbox (most maps don't
    # have any), so a runtime probe-fetch produced a noisy 404 on
    # those maps. Reading the file's existence here lets the runtime
    # skip the fetch entirely.
    config["_has_clip_endpoints"] = os.path.exists(
        os.path.join(output_dir, "clip_endpoints.geojson"))

    # Enrich trails.geojson with the three non-exclusive bucket flags
    # (summer/winter/emergency) on every route, and append any
    # user-defined custom_routes. Idempotent — safe to re-run against
    # a cached trails.geojson that's already been enriched.
    if _enrich_trails_geojson(config, trails_geojson, project_root):
        with open(trails_path, "w") as f:
            json.dump(trails_geojson, f, separators=(",", ":"))
        custom_count = len(config.get("custom_routes") or [])
        suffix = (f" + {custom_count} custom route{'s' if custom_count != 1 else ''}"
                  if custom_count else "")
        print(f"  Enriched {os.path.basename(trails_path)} "
              f"with bucket flags{suffix}")

    # Compute bbox from trail geometry if not specified in config
    if "bbox" not in config:
        config["bbox"] = compute_bbox_from_trails(trails_geojson)
        print(f"  Computed bbox from trails: {config['bbox']}")

    # Compute center from bbox if not specified in config
    if "center" not in config:
        bbox = config["bbox"]
        config["center"] = [
            round((bbox[0] + bbox[2]) / 2, 4),
            round((bbox[1] + bbox[3]) / 2, 4),
        ]
        print(f"  Computed center from bbox: {config['center']}")

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
            print(f"  Pan envelope (pad {pan_padding}): {config['pan_bbox']}")
    else:
        print(f"  Pan envelope (explicit): {config['pan_bbox']}")
    print()

    # Step 2: Fetch POIs (skip when every POI category is disabled)
    pois_path = os.path.join(output_dir, "pois.geojson")
    if (not config.get("show_markers", True)
            and not config.get("show_features", True)
            and not config.get("show_parking", True)
            and not config.get("show_trailheads", True)):
        print("POIs: Skipped (all POI layers disabled)")
        # Write empty GeoJSON so the viewer doesn't 404
        with open(pois_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    elif args.force or args.trails or not os.path.exists(pois_path):
        fetch_pois(config, pois_path, cache_dir)
    else:
        print(f"POIs: Using existing {pois_path}")
    print()

    # Step 3: Fetch basemap
    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    basemap_bbox = config.get("pan_bbox") or config["bbox"]
    basemap_maxzoom = config.get("basemap_maxzoom", 15)
    basemap_sig = _bbox_signature(basemap_bbox, basemap_maxzoom)
    if args.skip_basemap:
        print("Basemap: Skipped (--skip-basemap)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(
            basemap_path, basemap_bbox, basemap_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                print(f"Basemap: regenerating ({reason})")
            fetch_basemap(config, basemap_path)
            _save_signature(basemap_path, basemap_sig)
        else:
            size_mb = os.path.getsize(basemap_path) / (1024 * 1024)
            print(f"Basemap: Using existing {basemap_path} ({size_mb:.1f} MB)")
    print()

    # Step 4: Fetch terrain
    terrain_path = os.path.join(output_dir, "terrain.pmtiles")
    terrain_bbox = config.get("pan_bbox") or config["bbox"]
    terrain_maxzoom = config.get("terrain_maxzoom", 12)
    terrain_sig = _bbox_signature(terrain_bbox, terrain_maxzoom)
    if not config.get("show_terrain", True):
        print("Terrain: Disabled in config (show_terrain: false)")
    elif args.skip_terrain:
        print("Terrain: Skipped (--skip-terrain)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(
            terrain_path, terrain_bbox, terrain_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                print(f"Terrain: regenerating ({reason})")
            if fetch_terrain(config, terrain_path):
                _save_signature(terrain_path, terrain_sig)
        else:
            size_mb = os.path.getsize(terrain_path) / (1024 * 1024)
            print(f"Terrain: Using existing {terrain_path} ({size_mb:.1f} MB)")
    print()

    # Step 5: Copy templates and assets
    print("Assembling output...")
    copy_templates(config, output_dir, trails_geojson)
    copy_assets(config, output_dir)
    print()

    # Step 6: Bundle vendor libraries (CDN deps served locally for offline)
    print("Bundling vendor libraries...")
    download_vendor_libs(output_dir, cache_dir)
    print()

    # Step 7: Generate service worker (MUST be last — needs complete file list)
    if config.get("pwa", True):
        print("Generating PWA assets...")
        generate_service_worker(config, output_dir)

        # Check for missing pieces that will prevent the PWA from being installable
        pwa_warnings = []
        manifest_path = os.path.join(output_dir, "icons", "site.webmanifest")
        if not os.path.exists(manifest_path):
            pwa_warnings.append(
                "No web manifest (site.webmanifest) — set 'icon:' in your config "
                "to generate icons and a manifest from a source image"
            )
        else:
            icons_dir = os.path.join(output_dir, "icons")
            has_icon = any(
                f.endswith(".png") for f in os.listdir(icons_dir)
            ) if os.path.isdir(icons_dir) else False
            if not has_icon:
                pwa_warnings.append(
                    "Web manifest exists but no icon PNGs found — "
                    "the browser needs at least one icon to show an install prompt"
                )
        if pwa_warnings:
            print()
            print("  PWA WARNINGS — the app will not be installable until fixed:")
            for w in pwa_warnings:
                print(f"    • {w}")
            print()
    else:
        print("PWA disabled — skipping service worker generation")

    print_summary(output_dir)
    print(f"\nServe locally: python scripts/serve.py {output_dir} --port 8080")
    print(f"Then open: http://localhost:8080\n")


if __name__ == "__main__":
    main()
