#!/usr/bin/env python3
"""Build orchestrator for the trailmaps.app Map Generator.

Runs all pipeline steps, assembles templates with injected config,
and copies assets to produce a deployable static site.

Usage:
    python scripts/build.py configs/ramba/ramba.yaml
    python scripts/build.py configs/ramba/ramba.yaml --force
    python scripts/build.py configs/ramba/ramba.yaml --trails
    python scripts/build.py configs/ramba/ramba.yaml --skip-terrain
"""

import argparse
import concurrent.futures
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
from validate_config import validate_config, DEFAULT_VISIBLE_LAYERS

# CDN libraries to bundle locally for offline/PWA support.
# Update versions here when upgrading dependencies.
VENDOR_LIBS = {
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@5.5.0/dist/maplibre-gl.css",
    "maplibre-gl.js": "https://unpkg.com/maplibre-gl@5.5.0/dist/maplibre-gl.js",
    "pmtiles.js": "https://unpkg.com/pmtiles@4.2.1/dist/pmtiles.js",
    "basemaps.js": "https://unpkg.com/@protomaps/basemaps@5.7.2/dist/basemaps.js",
}


# Logo render bounding box. The brand-img CSS at templates/style.css:1982
# bounds it to max-width: 200px, max-height: 48px on the map overlay.
# About modal uses the same image, similar bounds. We resample the source
# to ~2× this on its longer side so retina displays render cleanly without
# us shipping the original (often much larger) source.
#
# LOGO_DESKTOP_H was previously 80 — that left square icons (e.g. DTE's
# icon-as-logo fallback) processed to 160×160 = ~12KB, but the actual
# rendered size is 48×48. Lowered to 48 to match the CSS max-height,
# which shrinks square-icon outputs to ~96×96 = ~1-2KB, a ~10× saving
# (Lighthouse image-delivery-insight ~11KB savings on DTE). Wide
# wordmarks unchanged — they remain width-bound at 200px.
LOGO_DESKTOP_W = 200
LOGO_DESKTOP_H = 48


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

    Returns (width, height) as integers when the SVG has known pixel
    dimensions (either definite width/height attributes or a viewBox we
    could parse), otherwise (None, None). Callers use this to set the
    brand-img element's HTML width/height attributes for CLS prevention.
    """
    import re

    try:
        with open(source_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"  warn: Could not read SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return (None, None)

    svg_open = re.search(r"<svg\b[^>]*>", text)
    if not svg_open:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)

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

    def _parse_pixel(v):
        """Extract a numeric pixel value from "100", "100px", "100.5pt", etc.
        Returns int or None. Used to recover dimensions from existing
        width/height attributes for the brand-img HTML attribute.
        """
        if not v:
            return None
        m = re.match(r"^-?\d+(\.\d+)?", v.strip())
        if not m:
            return None
        try:
            return int(round(float(m.group(0))))
        except ValueError:
            return None

    has_definite = (
        width_m and _is_definite_pixel(width_m.group(1))
        and height_m and _is_definite_pixel(height_m.group(1))
    )

    if has_definite or not viewbox_m:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        if has_definite:
            return (_parse_pixel(width_m.group(1)),
                    _parse_pixel(height_m.group(1)))
        return (None, None)

    parts = viewbox_m.group(1).split()
    if len(parts) != 4:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)
    try:
        vb_w = float(parts[2])
        vb_h = float(parts[3])
    except ValueError:
        shutil.copy2(source_path, output_path)
        print(f"  Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)

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
        print(f"  warn: Could not write SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return (None, None)

    print(
        f"  Wrote logo.svg ({new_w}×{new_h} from viewBox, "
        f"{os.path.getsize(output_path)} bytes, vector)"
    )
    return (int(round(vb_w)), int(round(vb_h)))


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

    Returns (width, height) integers — the dimensions actually written to
    output_path — for the caller to substitute into the brand-img HTML
    width/height attributes (CLS prevention). Returns (None, None) when
    dimensions can't be determined (e.g., Pillow missing, SVG without
    pixel size + viewBox, IO failure); callers should fall back to
    omitting the attributes.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".svg":
        return _copy_svg_with_intrinsic_size(source_path, output_path)

    try:
        from PIL import Image
    except ImportError:
        print(f"  warn: Pillow not installed — copying logo verbatim to {output_path}")
        shutil.copy2(source_path, output_path)
        return (None, None)

    try:
        img = Image.open(source_path)
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            raise ValueError(f"invalid logo dimensions {src_w}x{src_h}")

        aspect = src_w / src_h
        # Bounding-box decision: wider than the target box -> width-bound,
        # else height-bound. (target box aspect = LOGO_DESKTOP_W /
        # LOGO_DESKTOP_H — see constants at top of file)
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
        return (out_w, out_h)
    except Exception as e:
        print(f"  warn: Failed to process logo ({e}) — copying source verbatim")
        try:
            shutil.copy2(source_path, output_path)
        except Exception as e2:
            print(f"  ERROR: Could not write logo: {e2}")
        return (None, None)


def _minify_assets(output_dir):
    """Minify app.js + style.css in-place. Returns a list of status lines.

    Used by main() when --minify is set. Conservative pure-python
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
    results = []
    targets = [
        ("app.js", "rjsmin"),
        ("style.css", "rcssmin"),
    ]
    for fname, lib in targets:
        path = os.path.join(output_dir, fname)
        if not os.path.exists(path):
            results.append(f"  {fname}: not present, skipping")
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
            results.append(
                f"  {fname}: {before:,} → {after:,} bytes (-{pct:.0f}%)"
            )
        except ImportError:
            results.append(
                f"  warn: {lib} not installed — {fname} left unminified. "
                f"Run: .venv/bin/pip install {lib}"
            )
        except (OSError, UnicodeDecodeError) as e:
            results.append(
                f"  warn: failed to minify {fname} ({e}) — left unminified"
            )
    return results


def _relative_luminance(rgb):
    """WCAG relative luminance for an (r, g, b) triple in 0–255.

    Used by the accent-color contrast warning + the auto-darken loop
    in derive_accent. Formula from WCAG 2.x; sRGB linearisation +
    Rec. 709 luma weights.
    """
    def _channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(rgb_a, rgb_b):
    """WCAG contrast ratio between two colours."""
    la = _relative_luminance(rgb_a)
    lb = _relative_luminance(rgb_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(hex_str):
    """Parse '#RRGGBB' → (r, g, b)."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _darken_for_contrast(rgb, target_contrast=4.5, against=(255, 255, 255)):
    """Darken an RGB triple in HSL-space until it hits target_contrast
    against `against` (white by default — the assumption is white text
    on accent background). Returns the (possibly modified) triple.

    Walks lightness down in small steps; bails after a fixed number of
    iterations to avoid pathological inputs spinning forever.
    """
    import colorsys
    r, g, b = (c / 255.0 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    for _ in range(40):
        rgb = tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))
        if _contrast_ratio(rgb, against) >= target_contrast:
            return rgb
        l -= 0.025
        if l < 0:
            l = 0
            break
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))


def derive_accent(image_path):
    """Pick a single accent colour from a raster logo.

    Algorithm: thumbnail to 100×100 for speed, walk every pixel,
    discard near-white / near-black / desaturated / transparent
    contributions, pick the most common surviving colour. Returns
    a (r, g, b) triple or None if no candidate qualifies (caller
    falls back to the framework default).

    SVG inputs aren't supported — Pillow can't open them. The
    caller is expected to fall back to the icon (typically PNG)
    when the logo is vector.
    """
    try:
        from PIL import Image
    except ImportError:
        print("  warn: Pillow not installed — cannot derive accent_color")
        return None
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as exc:
        print(f"  warn: could not open {image_path} for accent derivation: {exc}")
        return None
    img.thumbnail((100, 100))
    # Iterate raw RGBA bytes — Image.getdata() is deprecated in
    # Pillow 11+ and slated for removal in Pillow 14. tobytes() returns
    # a flat byte buffer in (R, G, B, A) order which we walk in groups
    # of four. Faster too, no per-pixel tuple allocation.
    pixels = img.tobytes()
    counts = {}
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
        if a < 128:
            continue
        # Skip near-white (likely background)
        if max(r, g, b) > 240 and min(r, g, b) > 200:
            continue
        # Skip near-black (likely outline / shadow)
        if max(r, g, b) < 30:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == 0:
            continue
        # Skip desaturated (looks grey, not branding)
        if (mx - mn) / mx < 0.20:
            continue
        # Quantise to nearest 8 to consolidate near-identical pixels
        key = (r & 0xF8, g & 0xF8, b & 0xF8)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def resolve_accent_color(config, project_root, cache_dir):
    """Resolve `accent_color` config to a final hex string for runtime.

    Returns one of:
      - None: use framework default (#2980b9)
      - "#RRGGBB" string: explicit accent colour to inject into CONFIG

    Handles three input forms:
      - omitted: returns None
      - explicit hex: returned verbatim (uppercased)
      - "auto": derive from logo or icon, cache, auto-darken if low
        contrast against white text. Falls back to None with a
        warning if neither raster source exists or no candidate
        colour qualifies.

    Also prints a build-time warning if the resolved colour has
    poor contrast against either light (#ffffff) or dark (#1c1c1e)
    backgrounds — both schemes need to read cleanly since dark mode
    is on the roadmap.
    """
    raw = config.get("accent_color")
    if raw is None:
        return None
    if raw != "auto":
        # Explicit hex: validator already checked the format.
        result = raw.upper()
        _warn_low_contrast_accent(result)
        return result

    # "auto" path — derive from logo, falling back to icon.
    logo_p = config.get("logo") or ""
    icon_p = config.get("icon") or ""
    candidates = []
    for rel in (logo_p, icon_p):
        if not rel:
            continue
        abs_path = os.path.join(project_root, rel)
        if not os.path.isfile(abs_path):
            continue
        if abs_path.lower().endswith(".svg"):
            # Pillow can't read SVG — try the next candidate.
            continue
        candidates.append(abs_path)
    if not candidates:
        print("  warn: accent_color: 'auto' requires a raster logo or icon "
              "(PNG/WebP/JPG); none found. Falling back to framework default.")
        return None

    source = candidates[0]

    # Cache by content hash so re-builds skip the Pillow walk.
    accent_cache_dir = os.path.join(cache_dir, "derive_accent")
    os.makedirs(accent_cache_dir, exist_ok=True)
    with open(source, "rb") as f:
        src_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    cache_path = os.path.join(accent_cache_dir, f"{src_hash}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cached = json.load(f)
            result = cached.get("hex")
            if result:
                _warn_low_contrast_accent(result)
                return result
        except (OSError, json.JSONDecodeError):
            pass

    rgb = derive_accent(source)
    if rgb is None:
        print(f"  warn: accent_color: 'auto' could not pick a colour from "
              f"{os.path.basename(source)} (logo may be greyscale or fully "
              "neutral). Falling back to framework default.")
        return None

    # Auto-darken so white text on the accent stays readable (WCAG AA).
    rgb = _darken_for_contrast(rgb, target_contrast=4.5, against=(255, 255, 255))
    result = _rgb_to_hex(rgb)
    try:
        with open(cache_path, "w") as f:
            json.dump({"hex": result, "source": os.path.basename(source)}, f)
    except OSError:
        pass
    print(f"  accent_color: derived {result} from {os.path.basename(source)}")
    _warn_low_contrast_accent(result)
    return result


def _warn_low_contrast_accent(hex_color):
    """Print a build-time warning if the accent has poor contrast
    against either the light-mode or dark-mode sheet background.

    Both schemes need to read cleanly because the accent is used
    for active toggle pills, focus rings, link colour, etc. — all
    surfaces that overlay the sheet background and need to stand
    out in either mode.
    """
    try:
        rgb = _hex_to_rgb(hex_color)
    except (ValueError, IndexError):
        return
    light_bg = (255, 255, 255)
    dark_bg = (28, 28, 30)  # matches --sheet-bg in dark mode
    light_ratio = _contrast_ratio(rgb, light_bg)
    dark_ratio = _contrast_ratio(rgb, dark_bg)
    THRESHOLD = 4.5  # WCAG AA for normal text
    if light_ratio < THRESHOLD or dark_ratio < THRESHOLD:
        print(
            f"  warn: accent_color {hex_color} contrast vs light bg = "
            f"{light_ratio:.2f}, vs dark bg = {dark_ratio:.2f} (target "
            f">= {THRESHOLD:.1f}). Active pills / focus rings / links may "
            "be hard to read on one or both schemes."
        )


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
        print(f"  warn: Service worker template not found: {sw_template}")
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

    # Inline event_mode.routes share the same path-resolution semantics
    # as top-level custom_routes (relative to the config YAML). Resolve
    # here so the build-time fold into config["custom_routes"]
    # downstream sees absolute paths.
    em = config.get("event_mode")
    if isinstance(em, dict):
        for entry in (em.get("routes") or []):
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
        print(f"  warn: could not write {_signature_path(output_path)}: {e}")


def _trails_fetch_fingerprint(config):
    """Stable hash of every config key fetch_trails() consumes. When
    this changes between builds, the cached trails.geojson is stale
    even though it exists on disk — adding a relation to
    clipped_relations, swapping osm_file, or editing direction_schedule
    all flip the hash and force a refetch.

    custom_routes is intentionally NOT in the fingerprint:
    _enrich_trails_geojson runs idempotently on every build and folds
    custom routes into the (cached or fresh) trails.geojson, so adding
    a custom route doesn't require an Overpass refetch.

    File-path-based inputs (osm_file) hash the path string only, not
    file content; the curator's --trails flag remains the explicit
    tool when a file's contents change without the path changing.
    """
    inputs = {
        "relations": sorted(config.get("relations") or []),
        "clipped_relations": sorted(config.get("clipped_relations") or []),
        "winter_relations": sorted(config.get("winter_relations") or []),
        "summer_relations": sorted(config.get("summer_relations") or []),
        "emergency_access_relations": sorted(
            config.get("emergency_access_relations") or []),
        "osm_file": config.get("osm_file") or "",
        "direction_schedule": config.get("direction_schedule") or {},
    }
    blob = json.dumps(inputs, sort_keys=True, default=str)
    return "trails-fp=" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _trails_needs_refetch(trails_path, config):
    """True iff the cached trails.geojson exists but its sidecar
    fingerprint doesn't match the current config. Returns
    (needs_refetch, reason). Missing sidecar is treated as a
    silent backwards-compat backfill (assume match, write sidecar
    on next save), not a refetch — avoids surprise refetches on every
    map the first time after upgrading to fingerprinted caches.
    """
    if not os.path.exists(trails_path):
        return True, "file missing"
    expected = _trails_fetch_fingerprint(config)
    actual = _load_signature(trails_path)
    if actual is None:
        return False, "fingerprint sidecar missing (legacy build, backfilling)"
    if actual != expected:
        return True, f"config inputs changed since last fetch ({actual!r} → {expected!r})"
    return False, None


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


# ----------------------------------------------------------------------
# Event mode: feature one or more routes prominently while every other
# trail on the map renders as muted background context.
#
# Implementation strategy: pure build-time pre-processing translates the
# `event_mode` directive into the existing per-route override
# mechanisms (relation_colors, dashed_relations, custom_routes mutation).
# No runtime code needs to know about event_mode. Two helpers, called at
# different points in the build sequence:
#
#   _apply_event_mode_to_custom_routes(config)
#       Pre-enrichment. Folds event_mode.routes into config["custom_
#       routes"] (so they participate in the geometry bake-in inside
#       _enrich_trails_geojson) and overrides non-featured custom
#       routes' colour and dashed fields with the background style.
#
#   _apply_event_mode_to_relations(config, trails_geojson)
#       Pre-injection. After enrichment has populated the trails.geojson
#       routes metadata, this pass synthesises relation_colors and
#       dashed_relations entries for every non-featured OSM route.
#       Curator's explicit per-route entries always win.
#
# Background style default (when omitted): grey dashed.
# ----------------------------------------------------------------------

_EVENT_MODE_DEFAULT_BG = {
    "color": "gray",
    "pattern": [0, 2],
    "cap": "round",
}


def _event_mode_background_style(config):
    """Resolve the background_style for event mode (override + default)."""
    em = config.get("event_mode") or {}
    bg = dict(_EVENT_MODE_DEFAULT_BG)
    bg.update(em.get("background_style") or {})
    # Defensive: ensure pattern is a list.
    if not isinstance(bg.get("pattern"), list) or not bg["pattern"]:
        bg["pattern"] = list(_EVENT_MODE_DEFAULT_BG["pattern"])
    return bg


def _event_mode_inline_route_ids(config):
    """Set of stringified IDs declared inline under event_mode.routes."""
    em = config.get("event_mode") or {}
    routes = em.get("routes") or []
    out = set()
    for entry in routes:
        if isinstance(entry, dict):
            cid = entry.get("id")
            if isinstance(cid, str) and cid:
                out.add(cid)
    return out


def _event_mode_featured_set(config, super_expansions):
    """Resolve the complete set of featured route IDs (stringified).

    Combines:
      - Every event_mode.routes[i].id (inline; featured by definition).
      - Every entry in event_mode.featured (string ids pass through;
        int ids resolve to themselves OR fan out via super_expansions).
    """
    em = config.get("event_mode") or {}
    out = _event_mode_inline_route_ids(config)
    for ref in em.get("featured") or []:
        if isinstance(ref, bool):
            continue
        if isinstance(ref, str):
            out.add(ref)
        elif isinstance(ref, int):
            sref = str(ref)
            if sref in super_expansions:
                out.update(str(c) for c in super_expansions[sref])
            else:
                out.add(sref)
    return out


def _apply_event_mode_to_custom_routes(config):
    """Pre-enrichment event-mode pass.

    Folds event_mode.routes into config["custom_routes"] so they
    participate in the standard geometry bake-in inside
    _enrich_trails_geojson. Overrides non-featured custom routes'
    `color` and `dashed` fields so the muted background style flows
    through that bake-in into trails.geojson metadata.

    Also handles `event_mode.direction_arrows`: when true, every
    inline event route gets `oneway: "yes"` (so the bake-in
    propagates it to features as the existing arrow renderer
    expects), and `direction_arrows` is added to `forced_visible`
    so the rider toggle disappears + arrows always render.

    Mutates `config` in place (and returns it). No-op when event_mode
    is absent.
    """
    em = config.get("event_mode")
    if not em:
        return config

    inline_routes = list(em.get("routes") or [])
    inline_ids = _event_mode_inline_route_ids(config)

    # event_mode.direction_arrows: stamp `oneway: "yes"` on each inline
    # route entry (so the custom-route bake-in carries it onto every
    # emitted feature) and add `direction_arrows` to `forced_visible`
    # so the runtime renders arrows always (no rider toggle to
    # disable).
    if em.get("direction_arrows"):
        for entry in inline_routes:
            if isinstance(entry, dict) and not entry.get("oneway"):
                entry["oneway"] = "yes"
        existing_forced = list(config.get("forced_visible") or [])
        if existing_forced != "all" and "direction_arrows" not in existing_forced:
            existing_forced.append("direction_arrows")
            config["forced_visible"] = existing_forced

    # Fold inline event_mode.routes into top-level custom_routes.
    # Validator already checked id-uniqueness across both lists, so
    # we don't need to dedupe here.
    if inline_routes:
        existing = list(config.get("custom_routes") or [])
        config["custom_routes"] = existing + inline_routes

    # Featured set, less the OSM-int side (which we resolve later when
    # super_expansions is available). For the custom-route mutation
    # pass we only care about which custom-route string ids are
    # featured, which is: every inline route id PLUS any string entry
    # in event_mode.featured.
    featured_strings = set(inline_ids)
    for ref in em.get("featured") or []:
        if isinstance(ref, str):
            featured_strings.add(ref)

    bg = _event_mode_background_style(config)
    bg_color = bg["color"]
    bg_pattern = bg["pattern"]
    bg_cap = bg.get("cap", "round")

    for entry in config.get("custom_routes") or []:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        if not isinstance(cid, str):
            continue
        if cid in featured_strings:
            # Featured: leave declared color + dashed alone.
            continue
        # Non-featured custom route: overwrite to background style.
        # `dashed` accepts a list-form pattern (see _enrich_trails_geojson),
        # so we push the background pattern + cap directly onto the entry
        # and the bake-in picks them up.
        entry["color"] = bg_color
        entry["dashed"] = list(bg_pattern)
        entry["dashCap"] = bg_cap

    return config


def _apply_event_mode_to_feature_oneway(config, trails_geojson):
    """Restrict direction arrows to featured routes only.

    Without this pass, `event_mode.direction_arrows: true` would make
    every OSM-tagged oneway way render arrows alongside the featured
    route's arrows (because the runtime arrow emitter checks
    `way.oneway === "yes"` on every way regardless of route). That
    clutters an event map with OSM oneway clutter.

    Solution: strip the `oneway` property from any feature whose
    routes are ALL non-featured. A way shared between a featured
    route and a non-featured route keeps its `oneway` (the featured
    route still wants the arrows on its shared geometry).

    Mutates trails_geojson features in place. Returns True if any
    feature was changed (caller writes back to disk). No-op (returns
    False) when event_mode is absent or `direction_arrows: false`.
    """
    em = config.get("event_mode") or {}
    if not em.get("direction_arrows"):
        return False

    super_expansions = trails_geojson.get("metadata", {}).get(
        "super_relation_expansions", {}) or {}
    featured = _event_mode_featured_set(config, super_expansions)

    stripped = 0
    for feat in (trails_geojson.get("features") or []):
        props = feat.get("properties") or {}
        if not props.get("oneway"):
            continue
        # Collect every route ID this feature contributes to: its
        # primary route_id plus any shared_routes (a way borrowed
        # by multiple routes shows up in all of them).
        rids = set()
        rid = props.get("route_id")
        if rid is not None:
            rids.add(str(rid))
        for sr in (props.get("shared_routes") or []):
            rids.add(str(sr))
        # If any of the feature's routes is featured, keep oneway.
        if rids & featured:
            continue
        # No featured route uses this way: drop arrows on it.
        props["oneway"] = ""
        stripped += 1

    if stripped:
        print(f"  Event mode: stripped `oneway` from {stripped} non-featured "
              f"feature(s) so arrows render on the featured route(s) only.")
    return stripped > 0


def _apply_event_mode_to_relations(config, trails_geojson):
    """Pre-injection event-mode pass.

    Synthesises relation_colors and dashed_relations entries for every
    non-featured route ID in trails_geojson.metadata.routes. Curator's
    explicit entries WIN: a route already covered by config[
    "relation_colors"] or config["dashed_relations"] is left alone.

    Mutates `config` in place (and returns it). No-op when event_mode
    is absent.
    """
    em = config.get("event_mode")
    if not em:
        return config

    routes = trails_geojson.get("metadata", {}).get("routes", {}) or {}
    super_expansions = trails_geojson.get("metadata", {}).get(
        "super_relation_expansions", {}) or {}

    featured = _event_mode_featured_set(config, super_expansions)

    bg = _event_mode_background_style(config)
    bg_color = bg["color"]
    bg_pattern = list(bg["pattern"])
    bg_cap = bg.get("cap", "round")

    explicit_relation_colors = config.get("relation_colors") or {}
    explicit_dashed = config.get("dashed_relations") or {}

    new_relation_colors = dict(explicit_relation_colors)
    new_dashed = dict(explicit_dashed)

    for rid_str, info in routes.items():
        if rid_str in featured:
            # Mark featured so the runtime can sort it on top of
            # background routes and render it slightly wider. The
            # flag flows through to CONFIG.routes via the metadata
            # passthrough in inject_config_into_template.
            info["featured"] = True
            continue
        # Custom routes (string ids) were already handled in the
        # pre-enrichment pass via direct mutation of custom_routes
        # entries; their per-route metadata was set during bake-in.
        if info.get("isCustom"):
            continue
        # OSM route: use int key for relation_colors / dashed_relations
        # (matches the existing config conventions; lookups in
        # inject_config_into_template use int keys).
        try:
            rid_int = int(rid_str)
        except ValueError:
            continue
        if rid_int not in explicit_relation_colors:
            new_relation_colors[rid_int] = bg_color
        if rid_int not in explicit_dashed:
            new_dashed[rid_int] = {
                "pattern": bg_pattern,
                "cap": bg_cap,
                "colors": [bg_color],
            }

    config["relation_colors"] = new_relation_colors
    config["dashed_relations"] = new_dashed
    return config


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
    #
    # Super-relation expansion: if the curator listed a super-relation
    # ID in any of these config keys, fetch_trails.py expanded it into
    # the child route IDs and persisted the parent→children map to
    # trails.geojson metadata. We replay that expansion here so each
    # child route inherits the parent's bucket assignment (winter /
    # summer / emergency) without the curator having to enumerate the
    # children individually in YAML.
    super_expansions = trails_geojson.get("metadata", {}).get(
        "super_relation_expansions", {}) or {}

    def _expand(config_ids):
        out = set()
        for x in config_ids or []:
            sx = str(x)
            if sx in super_expansions:
                out.update(super_expansions[sx])
            else:
                out.add(sx)
        return out

    summer_ids = _expand(config.get("summer_relations"))
    winter_ids = _expand(config.get("winter_relations"))
    emergency_ids = _expand(config.get("emergency_access_relations"))

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

        # `dashed` accepts three shapes for flexibility:
        #   False / absent: solid line (default).
        #   True:           default dashed pattern [4, 4].
        #   list of nums:   explicit dash pattern (e.g. [2, 2]).
        # The list form lets the event-mode pre-pass push a specific
        # background pattern into a non-featured custom route without
        # going through the relation-id-keyed dashed_relations override
        # (custom routes have string ids; that path is OSM-only).
        c_dashed_raw = entry.get("dashed", False)
        if isinstance(c_dashed_raw, list):
            c_dashed = True
            c_dashed_pattern = list(c_dashed_raw)
        else:
            c_dashed = bool(c_dashed_raw)
            c_dashed_pattern = [4, 4]  # framework default for `dashed: true`
        c_dash_cap = entry.get("dashCap")
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
                        # Custom routes don't have OSM `oneway=` tags on
                        # individual segments (the GeoJSON has no per-
                        # segment OSM metadata). Curators opt in via
                        # the entry-level `oneway:` field — typically
                        # set automatically when event_mode.direction_arrows
                        # is true (see _apply_event_mode_to_custom_routes).
                        # Empty string means no arrows.
                        "oneway": entry.get("oneway", ""),
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
            # Pattern: explicit list-form on the entry takes precedence;
            # otherwise [4, 4] (framework default for `dashed: true`).
            # Users who need a specific pattern can either use the
            # list form directly OR add a matching dashed_relations
            # entry keyed by the custom id (the runtime filter treats
            # the route id opaquely).
            info["dashed"] = c_dashed_pattern
            if c_dash_cap:
                info["dashCap"] = c_dash_cap
        routes[cid] = info
        changed = True

    # ----- Subway-style parallel-route smoothing (always on) -----
    # Runs LAST so custom routes are included in the junction analysis.
    # Idempotent: strips prior stub features (isStub: true) AND
    # restores any host corridors whose first vertex was truncated by
    # a previous subway-style pass. Without the restore, re-runs would
    # leave the truncation baked in; with it, every build starts from
    # the canonical fetched geometry.
    pre_stub_count = len(trails_geojson["features"])
    trails_geojson["features"] = [
        f for f in trails_geojson["features"]
        if not f.get("properties", {}).get("isStub")
    ]
    stripped_stubs = pre_stub_count - len(trails_geojson["features"])
    if stripped_stubs:
        changed = True
    # Restore any prior subway-style truncations so re-runs don't
    # compound. apply_subway_style stashes the original first vertex
    # in _subwayOriginalCoord0 when it truncates.
    for feat in trails_geojson["features"]:
        geom = feat.get("geometry", {}) or {}
        if geom.get("type") != "LineString":
            continue
        props = feat.get("properties", {}) or {}
        orig = props.get("_subwayOriginalCoord0")
        if orig is not None:
            geom["coordinates"][0] = list(orig)
            del props["_subwayOriginalCoord0"]
            changed = True
    from parallel_routes import apply_subway_style
    added = apply_subway_style(trails_geojson)
    if added:
        print(f"  Subway style: emitted {added} junction transition micro-feature(s)")
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
    ("show_toilets",            "showToilets",          True),
    ("show_drinking_water",     "showDrinkingWater",    True),
    ("show_terrain",            "showTerrain",          True),
    ("show_difficulty",         "showDifficulty",       True),

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

    # Build-time data gate for the direction-arrow layer. When False,
    # no arrows are placed on any oneway trail and the Options toggle
    # row is hidden — even if `direction_arrows` is in `forced_visible`
    # (the show gate wins). Use for aesthetic maps that should never
    # display directional indicators regardless of the underlying OSM
    # tagging. Default True mirrors every other show_* gate's
    # "show by default, opt out per-map" pattern.
    ("show_direction_arrows",    "showDirectionArrows",  True),

    # Display
    # Labels mode default. Was "routes" historically; now defaults to
    # "none" so a fresh-LS visit produces a clean map with the rider
    # opting into labels via the Labels segmented control.
    ("default_labels",          "defaultLabels",        "none"),
    # Labels mode lock. When set ("routes" / "trails" / "none"), the
    # Labels segmented control is hidden in Options and the rider's
    # persisted choice is ignored. Validated at build time against
    # show_routes / show_trails so the lock can't contradict the
    # surfaced sections.
    # Default "" (empty string) rather than None — None is the
    # "required key" sentinel for inject_config_into_template's loop;
    # the runtime check is `CONFIG.forcedLabels ? lock : free` which
    # treats "" as the unset/free state.
    ("forced_labels",           "forcedLabels",         ""),
    # Initial colour scheme for first-visit riders. "light" / "dark"
    # / "auto" (auto resolves prefers-color-scheme). Default "light"
    # preserves existing behaviour for maps that don't opt in. The
    # rider can override via the Options Appearance toggle; LS wins
    # over this default on subsequent visits. The build also injects
    # this value into the inline <head> bootstrap script so first
    # paint already has the right scheme set on <html>.
    ("default_color_scheme",    "defaultColorScheme",   "light"),
    # Whether the brand-img logo should auto-invert in dark mode.
    # Default true matches historical behaviour; curators with
    # colored logos that look bad inverted set false per-map.
    ("invert_logo_dark",        "invertLogoDark",       True),
    ("color_by",                "colorBy",              "relation"),
    ("suppress_basemap_path_labels", "suppressBasemapPathLabels", False),
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

    # Distance units for every distance/elevation display in the
    # app: off-screen indicator pill, route stats in the Finder,
    # highlight chip, any future distance display. The underlying
    # data is always meters in trails.geojson; this setting only
    # affects render-time formatting. "mi" → ft + decimal mi (and
    # ft for elevation gain); "km" → m + decimal km (and m for
    # elevation gain). Validator (validate_config.py) restricts the
    # value to "mi" or "km".
    ("distance_units",          "distanceUnits",        "mi"),

    # Share button in the expanded sheet (above Install). When true
    # (default), generates a shareable URL of the current view +
    # highlighted route/trail and surfaces it via the Web Share API
    # (or clipboard fallback). When false, the entire share section
    # is stripped from index.html at build time. Set false for maps
    # where the curator wants no share affordance (e.g. private/
    # family maps); leave true for community/public maps.
    ("share_button",            "shareButton",          True),

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

    # When true, surface PWA install affordances on platforms that
    # support them (Chrome's mini-infobar + our custom Install button
    # via beforeinstallprompt; iOS Safari Add-to-Home-Screen
    # instructions). Default false because not every map wants install
    # promotion (e.g. a personal/family map). When false, the
    # beforeinstallprompt handler is not registered at all — silencing
    # Chrome's "page must call prompt()" warning — and the custom
    # Install button is hidden everywhere.
    ("pwa_install_prompt",      "pwaInstallPrompt",     True),

    # User-supplied
    ("parking",                 "parking",              []),
]


def inject_config_into_template(template_content, config, trails_geojson):
    """Replace the CONFIG placeholder in templates with actual config data."""
    # Extract route metadata from the trails GeoJSON
    routes = {}
    if trails_geojson and "metadata" in trails_geojson:
        routes = trails_geojson["metadata"].get("routes", {})

    # Event-mode pre-pass on the relations side. Synthesises
    # relation_colors and dashed_relations entries for every
    # non-featured OSM route so the override loop below applies the
    # background style. No-op when event_mode is absent. Curator's
    # explicit per-relation entries WIN.
    _apply_event_mode_to_relations(config, trails_geojson)

    # Apply route overrides (winter, colour, dash) in a single pass.
    # YAML keys keep their original "relation" names since they take OSM
    # relation IDs as input; the values populate route info on the JS side.
    winter_ids = set(config.get("winter_relations") or [])
    relation_colors = config.get("relation_colors") or {}
    dashed_relations = config.get("dashed_relations") or {}

    # Direction schedule controls when ways tagged oneway=yes/-1/reversible
    # have their arrows rotated 180° (day-of-week alternation). Single
    # hierarchical key with two parts:
    #
    #   direction_schedule:
    #     reverse_days: [...]    # system-wide default for every route
    #     per_route:             # per-relation overrides (optional)
    #       <rel_id>:
    #         reverse_days: [...]
    #
    # An explicit per-route entry always wins, even if its reverse_days is
    # empty — that's the way to opt one route out of the system-wide
    # default. Relations NOT mentioned under per_route fall back to the
    # default.
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

    # Pull the schedule block. Both halves are optional; an absent
    # block means no rotation anywhere on the map.
    sched_block = config.get("direction_schedule") or {}

    # System-wide default. Stored as None when unset OR when explicitly
    # set with empty reverse_days (degenerate; treated the same as unset).
    def_sched_days = _normalise_days(
        sched_block.get("reverse_days"),
        "direction_schedule.reverse_days",
    )
    def_sched_norm = {"reverse_days": def_sched_days} if def_sched_days else None

    # Per-relation overrides. Two-pass processing so super-relation
    # entries (auto-expanded via metadata.super_relation_expansions)
    # propagate to children, but explicit per-child entries always win:
    #   Pass 1 — process every entry whose key is a LEAF (or absent
    #            from the expansion table). These take precedence.
    #   Pass 2 — process super-relation entries, fanning out to
    #            children only if the child wasn't already set in
    #            Pass 1.
    # This lets a curator write `per_route: { 99999999: { reverse_days:
    # [] } }` for a whole second trail system that doesn't reverse,
    # while still individually overriding one of its child routes if
    # needed.
    super_expansions = trails_geojson.get("metadata", {}).get(
        "super_relation_expansions", {}) or {}
    per_route_raw = sched_block.get("per_route") or {}
    sched_processed = {}

    # Pass 1: explicit leaves and any keys not in the expansion table.
    deferred_supers = []
    for rel_id, spec in per_route_raw.items():
        rel_id_str = str(rel_id)
        if rel_id_str in super_expansions:
            deferred_supers.append((rel_id, spec))
            continue
        days = _normalise_days(
            (spec or {}).get("reverse_days"),
            f"direction_schedule.per_route[{rel_id}].reverse_days",
        )
        # Even an empty list is recorded — that's how a user opts a single
        # route out of the system-wide default.
        sched_processed[int(rel_id)] = {"reverse_days": days}

    # Pass 2: super-relations fan out to children (without clobbering
    # an explicit per-child entry from Pass 1).
    for rel_id, spec in deferred_supers:
        days = _normalise_days(
            (spec or {}).get("reverse_days"),
            f"direction_schedule.per_route[{rel_id}].reverse_days",
        )
        for child_id in super_expansions[str(rel_id)]:
            child_int = int(child_id)
            if child_int not in sched_processed:
                sched_processed[child_int] = {"reverse_days": days}

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
                "       Either set a system-wide schedule that covers every route:",
                "",
                "         direction_schedule:",
                "           reverse_days: [tuesday, thursday, saturday]",
                "",
                "       …or schedule the specific parent relation:",
                "",
                "         direction_schedule:",
                "           per_route:",
                "             <relation_id>:",
                "               reverse_days: [tuesday, thursday, saturday]",
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

    # Event-mode runtime hints. The runtime uses these to:
    #   - eventModeActive: gate the always-on event-mode UX changes
    #     (force Labels mode to "routes" + restrict labels to featured
    #     routes only + hide the Labels segmented control).
    #   - eventPoiColor: chip background for the always-on event POIs.
    #   - hasEventPois: presence flag so addEventPoiMarkers runs at boot.
    em = config.get("event_mode") or {}
    config_obj["eventModeActive"] = bool(em)
    config_obj["eventPoiColor"] = em.get("poi_color", "")
    config_obj["hasEventPois"] = bool(em.get("pois"))
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
    # Build-time scan for trail-property gates that surface Options
    # toggles. Done at build time (rather than counting placed
    # decorations at runtime) because the first computeDecorations()
    # pass is deferred to map.once('idle', …) for first-paint perf,
    # so a runtime count would race the deferral and read 0 at gate
    # time. Currently scans for:
    #   - any oneway-tagged feature → CONFIG.hasOnewayTrails →
    #     direction-arrow toggle
    #   - any mtb:scale:imba-tagged feature → CONFIG.hasDifficultyTrails
    #     → difficulty toggle
    # See setupFloatingChrome() in app.js where these are read.
    has_oneway = False
    has_difficulty = False
    for f in (trails_geojson.get("features") or []) if trails_geojson else []:
        props = f.get("properties") or {}
        if not has_oneway:
            ow = props.get("oneway")
            if ow in ("yes", True, "-1", "reversible"):
                has_oneway = True
        if not has_difficulty:
            imba = props.get("imba_difficulty")
            if imba and str(imba).strip():
                has_difficulty = True
        if has_oneway and has_difficulty:
            break
    config_obj["hasOnewayTrails"] = has_oneway
    config_obj["hasDifficultyTrails"] = has_difficulty
    config_obj["about"] = config.get("about") or None
    # Welcome modal config: pass through unchanged. Three forms
    # accepted: omitted (None → framework default), false (modal
    # suppressed), or a dict with title/body/show_controls_hint.
    # Distinguish "explicitly false" from "omitted" — `or` would
    # collapse both to None, which the runtime would interpret as
    # "use defaults" and still show the modal.
    config_obj["welcome"] = config.get("welcome") if "welcome" in config else None
    # default_visible: list of layer names that default to ON for
    # first-visit riders. Three accepted YAML forms:
    #   - omitted: empty list (everything off)
    #   - "all":   expand to the full layer list
    #   - list:   pass through (validator already checked names)
    # Runtime always sees a list, so isDefaultVisible() can do a
    # plain .includes() check.
    raw_default_visible = config.get("default_visible")
    if raw_default_visible == "all":
        config_obj["defaultVisible"] = sorted(DEFAULT_VISIBLE_LAYERS)
    elif isinstance(raw_default_visible, list):
        config_obj["defaultVisible"] = list(raw_default_visible)
    else:
        config_obj["defaultVisible"] = []
    # forced_visible: list of layer names whose toggle row is hidden
    # AND whose layer is force-rendered ON regardless of LS state /
    # default_visible. Same shape + accepted-name set as
    # default_visible. Runtime checks isForcedVisible(name) before
    # any toggle wiring; matched layers skip the toggle and are
    # rendered visible at boot.
    raw_forced_visible = config.get("forced_visible")
    if raw_forced_visible == "all":
        config_obj["forcedVisible"] = sorted(DEFAULT_VISIBLE_LAYERS)
    elif isinstance(raw_forced_visible, list):
        config_obj["forcedVisible"] = list(raw_forced_visible)
    else:
        config_obj["forcedVisible"] = []
    # Accent colour: resolved at build time (see _accent_color
    # stash). None means "use framework default" (the runtime CSS
    # var falls back to #2980b9). A hex string is set on :root by
    # the runtime so the rest of the chrome picks it up via
    # var(--accent).
    config_obj["accentColor"] = config.get("_accent_color")

    # Per-type POI counts (computed from pois.geojson at build
    # time — see _poi_counts stash). Drives the dynamic Welcome
    # Search line so it only mentions place types actually present
    # on this map.
    config_obj["poiCounts"] = config.get("_poi_counts") or {}

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


# Pillow-readable raster extensions used by the icon-source resolver
# below. SVG and PDF aren't readable by Pillow, so a logo with one of
# those extensions can't fall back to icon source — generate_icons
# would crash. Defined at module scope so resolve_icon_source() and
# the icon-generation call site (copy_assets) share one definition.
_PIL_READABLE_EXTS = {".png", ".jpg", ".jpeg", ".webp",
                      ".gif", ".bmp", ".tiff", ".tif"}


def resolve_icon_source(config, project_root):
    """Return the resolved icon source path, or "" if none exists.

    Three input shapes, in priority order:
      1. config["icon"]: explicit. Returned as-is when set (any value,
         even non-raster, is the curator's call — generate_icons will
         flag the failure if Pillow can't read it).
      2. config["logo"]: automatic fallback when icon: is unset, but
         only when the logo file exists AND has a Pillow-readable
         extension. SVG logos can't fall back (Pillow won't read).
      3. Empty string: no usable source. Caller should skip icon
         generation AND strip the icon links from the HTML.

    Used by both copy_templates (to decide whether to keep the icons
    HTML block) AND copy_assets (to decide whether to call
    generate_icons). One function so they can't drift — the previous
    bug was copy_templates checking only `icon:` while copy_assets
    also accepted `logo:`, producing builds with manifest+icons on
    disk but no <link rel="manifest"> in the HTML.
    """
    icon_path = config.get("icon", "")
    if icon_path:
        return icon_path
    logo_path = config.get("logo", "")
    if logo_path:
        ext = os.path.splitext(logo_path)[1].lower()
        if ext in _PIL_READABLE_EXTS:
            candidate = os.path.join(project_root, logo_path)
            if os.path.isfile(candidate):
                return logo_path
    return ""


def copy_templates(config, output_dir, trails_geojson):
    """Copy and process HTML/JS/CSS templates."""
    project_root = os.path.dirname(SCRIPTS_DIR)
    templates_dir = os.path.join(project_root, "templates")

    for filename in ["index.html", "app.js", "style.css"]:
        src = os.path.join(templates_dir, filename)
        if not os.path.exists(src):
            print(f"  warn: Template not found: {src}")
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

            # Open Graph + Twitter Card metadata. Always-on (no gate) —
            # benefits search engines and the Share-button preview cards
            # equally. Values are HTML-attribute-escaped to survive
            # quotes / ampersands in trail-system names + descriptions.
            og_title = (config.get("title") or config.get("name") or "Trail Map")
            about = config.get("about") or {}
            og_description_raw = (about.get("description") or "").strip()
            # First paragraph only (split on the first double-newline);
            # cap at ~200 chars to avoid runaway snippet length in
            # share previews.
            og_description = og_description_raw.split("\n\n", 1)[0].strip()
            if len(og_description) > 200:
                og_description = og_description[:197].rstrip() + "..."
            # If no description configured, fall back to the title so
            # OG previews still have something readable instead of an
            # empty `content=""` attribute.
            if not og_description:
                og_description = og_title
            # html.escape with quote=True turns " into &quot; so the
            # value is safe inside the `content="..."` attribute.
            from html import escape as _html_escape
            content = content.replace("__OG_TITLE__",
                                      _html_escape(og_title, quote=True))
            content = content.replace("__OG_DESCRIPTION__",
                                      _html_escape(og_description, quote=True))

            # Strip the Share button section when share_button: false.
            # Default true — the section's `hidden` class is only used
            # to keep the section invisible until app.js reveals it.
            if not config.get("share_button", True):
                content = re.sub(
                    r'\s*<!-- Share start -->.*?<!-- Share end -->\n',
                    '', content, flags=re.DOTALL,
                )

            # Brand title — substitute the map's title text into both
            # the alt= on the brand-img (used by screen readers + as a
            # fallback when the image is missing) AND the brand-title
            # span text (shown when no logo is configured at all). Same
            # value as the OG title.
            brand_title = (config.get("title") or config.get("name")
                          or "Trail Map")
            content = content.replace("__BRAND_TITLE__",
                                      _html_escape(brand_title, quote=True))

            # Brand-img CLS-prevention dimensions. process_logo() stashes
            # the actual written pixel dimensions on config["_brand_img_dims"]
            # (or (None, None) when it couldn't determine them — Pillow
            # missing, SVG without viewBox, etc.). Substitute width/height
            # into the <img> tag when known; otherwise emit the empty
            # string so the tag stays valid HTML and we accept a small
            # CLS hit rather than emit wrong dimensions. The CSS
            # (style.css #brand-img: max-width 200px, max-height 48px,
            # width/height auto) still controls actual render size; the
            # HTML attrs only set the aspect ratio used by the browser
            # to reserve layout box before image bytes arrive.
            #
            # fetchpriority="high" is unconditional in the template — it
            # makes the brand-img the LCP image regardless of whether we
            # could determine dims. Browsers without fetchpriority support
            # ignore the attribute (no regression).
            brand_dims = config.get("_brand_img_dims") or (None, None)
            bw, bh = brand_dims
            if bw and bh:
                content = content.replace(
                    "__BRAND_IMG_DIMS__",
                    f' width="{bw}" height="{bh}"')
            else:
                content = content.replace("__BRAND_IMG_DIMS__", "")

            # Inline colour-scheme bootstrap script. Runs synchronously
            # in <head> BEFORE the stylesheet, so first paint already
            # has the right data-color-scheme attribute on <html> and
            # CSS variables resolve to the correct values without FOUC.
            # Slug + default-scheme are baked in at build time; the
            # snippet reads LS / falls back to the default / resolves
            # "auto" against prefers-color-scheme.
            slug = config.get("slug", "")
            default_scheme = config.get("default_color_scheme", "light")
            bootstrap_slug = json.dumps(slug)        # JS string-safe
            bootstrap_default = json.dumps(default_scheme)
            # The runtime stores LS values JSON-stringified (see
            # LS.set in app.js: setItem(..., JSON.stringify(value))),
            # so a stored "dark" preference is on disk as the
            # 6-char string `"dark"` (with literal quote marks).
            # The bootstrap parses it back; on parse failure (older
            # raw values, or future schema drift) it falls through
            # to the curator default rather than blocking on a
            # broken LS entry.
            bootstrap_script = (
                "<script>"
                "(function(){"
                "try{"
                f"var raw=localStorage.getItem({bootstrap_slug}+\".mtb.colorScheme\");"
                "var stored=null;"
                "if(raw){try{stored=JSON.parse(raw);}catch(e){stored=raw;}}"
                f"var s=stored||{bootstrap_default};"
                "if(s===\"auto\"){"
                "s=matchMedia(\"(prefers-color-scheme: dark)\").matches?\"dark\":\"light\";"
                "}"
                "document.documentElement.setAttribute(\"data-color-scheme\",s);"
                # Sync the meta theme-color tag in the same pass so
                # the Android Chrome PWA status bar paints the right
                # colour on first frame (the static value in the
                # template is just a fallback; without this update,
                # a dark-mode PWA would show a light status bar
                # until applyColorScheme runs much later in app.js).
                "var m=document.querySelector('meta[name=\"theme-color\"]');"
                "if(!m){m=document.createElement('meta');m.setAttribute('name','theme-color');document.head.appendChild(m);}"
                "m.setAttribute('content',s===\"dark\"?\"#1c1c1e\":\"#ffffff\");"
                "}catch(e){}"
                "})();"
                "</script>"
            )
            content = content.replace("__COLOR_SCHEME_BOOTSTRAP__",
                                      bootstrap_script)

            # Inject or remove brand image. Logo source falls back to
            # icon: when logo: is omitted; raster sources are normalized
            # to `logo.webp` in copy_assets() while SVG sources are
            # copied as `logo.svg`. The template ships the brand-img
            # with src="logo.webp"; rename to .svg if the source is
            # vector. Strip the brand-img tag entirely when neither
            # source is set, so the brand element falls back to the
            # title span only.
            logo_path = config.get("logo", "")
            icon_path_for_logo = config.get("icon", "")
            logo_chosen = logo_path or icon_path_for_logo
            if logo_chosen:
                out_name = logo_output_filename(logo_chosen)
                if out_name != "logo.webp":
                    content = content.replace("logo.webp", out_name)
            else:
                content = re.sub(
                    r'\s*<!-- Brand img start -->.*?<!-- Brand img end -->\n',
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

            # Strip icon links when no icon source is resolvable.
            # Uses the same fallback logic as copy_assets so a config
            # with `logo:` set but no `icon:` keeps the manifest /
            # apple-touch / theme-color links — the icons get
            # generated from the logo and the HTML correctly points
            # at them. (Without this consistency, the HTML strip
            # would remove the manifest link even though copy_assets
            # would happily generate a manifest from the logo,
            # leaving a build with icons on disk but no PWA install.)
            icons_dir_legacy = config.get("icons_dir", "")
            if not resolve_icon_source(config, project_root) and not icons_dir_legacy:
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
            print(f"  warn: Logo not found: {candidate}")
    elif icon_path:
        candidate = os.path.join(project_root, icon_path)
        if os.path.isfile(candidate):
            logo_src = candidate
            print(f"  No logo configured — using icon as logo")
    if logo_src:
        out_name = logo_output_filename(logo_src)
        out_path = os.path.join(output_dir, out_name)
        # Capture the written logo's pixel dimensions so copy_templates
        # can substitute them into the brand-img element's HTML
        # width/height attributes (CLS prevention — browsers reserve
        # layout box from these before image bytes arrive). Returns
        # (None, None) for unrecognised SVGs / Pillow-missing / etc.;
        # copy_templates falls back to omitting the attributes in that
        # case, accepting the small CLS risk over emitting wrong dims.
        config["_brand_img_dims"] = process_logo(logo_src, out_path)

    # Icons — generate from source image or copy from legacy icons_dir.
    # Source resolution (icon: → logo: → none) is shared with the HTML
    # icons-block strip in copy_templates via resolve_icon_source().
    icons_dir_legacy = config.get("icons_dir", "")
    icon_path = resolve_icon_source(config, project_root)
    if icon_path and icon_path != config.get("icon", ""):
        # The fallback fired — the resolved source is the logo, not
        # an explicit icon: setting. Log it so the curator knows.
        print(f"  No icon configured — using logo as icon source")
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
            print(f"  warn: Icons directory not found: {icons_src}")
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
            print(f"  warn: Sprites {sprite_version} not found at {ver_src}")
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
        print(f"  warn: Sprites not found at {sprites_src}")
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
        print(f"  warn: clip-arrow SDF assets missing — continuation "
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


def _print_dry_run_summary(config, args, output_dir, cache_dir):
    """Print what the build WOULD do, then exit 0.

    Runs after validate_config has accepted the YAML but before any
    Overpass query / tile fetch / file write. Useful for catching
    config errors and previewing the build's external footprint
    without committing to a long run.
    """
    print(f"Dry run for: {config['title']}")
    print(f"  slug:        {config['slug']}")
    print(f"  output_dir:  {output_dir}")
    print(f"  cache_dir:   {cache_dir}")
    print()

    # load_config resolves logo/icon/osm_file/custom_routes[].geometry
    # to absolute paths. For display we want the bare filename (matches
    # what the user wrote in the YAML) and only fall back to the full
    # path if the file's missing.
    def _display_path(abs_path):
        return os.path.basename(abs_path) if os.path.isfile(abs_path) \
            else f"{os.path.basename(abs_path)}  → MISSING (looked for {abs_path})"

    # ---- OSM data source ----
    print("OSM data source:")
    if config.get("osm_file"):
        print(f"  Local OSM file: {_display_path(config['osm_file'])}")
    else:
        print("  Overpass API")
        print(f"    relations: {config.get('relations') or []}")
        for key in ("clipped_relations",
                    "winter_relations", "summer_relations",
                    "emergency_access_relations"):
            ids = config.get(key) or []
            if ids:
                print(f"    {key}: {ids}")
        custom = config.get("custom_routes") or []
        if custom:
            print(f"    custom_routes ({len(custom)}):")
            for entry in custom:
                geom = entry.get("geometry") or ""
                if not geom:
                    print(f"      - id={entry.get('id')}: NO GEOMETRY PATH")
                    continue
                print(f"      - id={entry.get('id')} geometry={_display_path(geom)}")
    print()

    # ---- POI fetching ----
    print("POI fetching (gated by show_* keys):")
    for key, default in (("show_markers", True), ("show_features", True),
                         ("show_parking", True), ("show_trailheads", True),
                         ("show_toilets", True), ("show_drinking_water", True)):
        on = bool(config.get(key, default))
        print(f"  {key}: {'YES' if on else 'no'}")
    if config.get("show_trailheads", True):
        th = config.get("trailheads") or []
        if th:
            print(f"    trailheads from config: {len(th)} point(s)")
    if config.get("show_parking", True):
        pk = config.get("parking") or []
        if pk:
            print(f"    parking from config: {len(pk)} point(s)")
    print()

    # ---- Tile generation ----
    print("Tile generation:")
    if args.skip_basemap:
        print("  basemap: SKIPPED (--skip-basemap)")
    else:
        bm_zoom = config.get("basemap_maxzoom", 15)
        print(f"  basemap: pan_bbox extracted to maxzoom {bm_zoom}")
    if args.skip_terrain or not config.get("show_terrain", True):
        reason = ("--skip-terrain" if args.skip_terrain
                  else "show_terrain: false")
        print(f"  terrain: SKIPPED ({reason})")
    else:
        tr_zoom = config.get("terrain_maxzoom", 12)
        print(f"  terrain: pan_bbox extracted to maxzoom {tr_zoom}")
    print()

    # ---- Route stats ----
    want_dist = bool(config.get("show_route_distance"))
    want_elev = bool(config.get("show_route_elevation"))
    if want_dist or want_elev:
        print("Per-route stats:")
        if want_dist:
            print("  distance: computed (haversine, no API)")
        if want_elev:
            print("  elevation gain + loss: USGS 3DEP getSamples "
                  "(network calls, ~1 per route at 5m sampling)")
        print()

    # ---- Branding assets ----
    print("Branding assets:")
    for key in ("logo", "icon"):
        path = config.get(key) or ""
        if not path:
            print(f"  {key}: (none)")
        else:
            print(f"  {key}: {_display_path(path)}")
    print()

    # ---- PWA / sharing ----
    print("Runtime features:")
    print(f"  pwa: {bool(config.get('pwa', True))}")
    print(f"  pwa_install_prompt: {bool(config.get('pwa_install_prompt', True))}")
    print(f"  share_button: {bool(config.get('share_button', True))}")
    print(f"  url_hash: {bool(config.get('url_hash', False))}")
    print(f"  distance_units: {config.get('distance_units', 'mi')}")
    print()

    print("Dry run complete — no files written, no network calls made.")


def main():
    parser = argparse.ArgumentParser(description="Build MTB trail map")
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument("--force", action="store_true", help="Force re-fetch all data (including Overpass cache)")
    parser.add_argument("--trails", action="store_true", help="Re-fetch trail data from OSM (uses Overpass cache). POIs are rebuilt on every build regardless of this flag.")
    parser.add_argument("--skip-terrain", action="store_true", help="Skip terrain tile generation")
    parser.add_argument("--skip-basemap", action="store_true", help="Skip basemap extraction")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config and print what would be fetched / generated, then exit. "
                             "No Overpass calls, no tile downloads, no file writes.")
    parser.add_argument("--minify", action="store_true",
                        help="Minify app.js and style.css in the build output. "
                             "Off by default for fast local-iteration builds + readable "
                             "debugging output. tools/build_and_deploy.sh passes this "
                             "automatically when building for deploy.")
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
        print(f"Cleared Overpass cache: {cache_dir}")

    print(f"Building map: {config['title']}")
    print(f"Output: {output_dir}")
    print()

    # Step 1: Fetch trails
    trails_path = os.path.join(output_dir, "trails.geojson")
    auto_refetch_reason = None
    if not (args.force or args.trails or not os.path.exists(trails_path)):
        # Cached file exists; check if config inputs have changed since
        # it was fetched. A mismatched fingerprint promotes "use cache"
        # into "refetch" so adding a relation (or swapping osm_file,
        # editing direction_schedule, etc.) doesn't silently produce a
        # stale build. Missing sidecar is the legacy-build backfill
        # path — reuses the cache and writes a fingerprint on next
        # save.
        needs, reason = _trails_needs_refetch(trails_path, config)
        if needs:
            auto_refetch_reason = reason
    if (args.force or args.trails or not os.path.exists(trails_path)
            or auto_refetch_reason):
        if auto_refetch_reason:
            print(f"Trails: refetching — {auto_refetch_reason}")
        trails_geojson = fetch_trails(config, trails_path, cache_dir)
        # Record the fetch fingerprint so the next build can detect
        # a config change and refetch automatically. Written after
        # fetch_trails succeeds so a partial/aborted fetch doesn't
        # leave a stale fingerprint claiming "all good."
        _save_signature(trails_path, _trails_fetch_fingerprint(config))
    else:
        print(f"Trails: Using existing {trails_path}")
        with open(trails_path) as f:
            trails_geojson = json.load(f)
        # Backfill the fingerprint sidecar for legacy builds so the
        # next config change triggers a clean refetch. No-op if the
        # sidecar already matches (overwriting with the same value).
        if _load_signature(trails_path) is None:
            _save_signature(trails_path, _trails_fetch_fingerprint(config))

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
            with open(pois_path) as f:
                pois_data = json.load(f)
            for feat in pois_data.get("features", []):
                ptype = (feat.get("properties") or {}).get("poi_type")
                if not ptype:
                    continue
                poi_counts[ptype] = poi_counts.get(ptype, 0) + 1
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  warn: could not count pois.geojson: {exc}")
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
    config["_poi_counts"] = poi_counts

    # Accent colour: stash the resolved hex (or None for "use
    # framework default") so inject_config_into_template can emit it
    # as CONFIG.accentColor. resolve_accent_color also handles "auto"
    # (Pillow-based logo derivation, cached per-source-hash) and the
    # WCAG contrast warning for both light + dark sheet backgrounds.
    config["_accent_color"] = resolve_accent_color(
        config, project_root, cache_dir)

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
        print(
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
        for f in (trails_geojson.get("features") or []):
            ow = (f.get("properties") or {}).get("oneway")
            if ow in ("yes", True, "-1", "reversible"):
                oneway_count += 1
        if oneway_count > 0:
            print(
                f"  warn: Map has {oneway_count} one-way trail segment(s) "
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
    arrows_restricted = _apply_event_mode_to_feature_oneway(
        config, trails_geojson)

    # Compute per-route distance/elevation stats if either gate is on.
    # Runs after enrichment so custom_routes are included in the totals.
    # compute_route_stats also handles cleanup when a previously-enabled
    # gate has been turned off (strips stale fields). Idempotent.
    from compute_route_stats import compute_and_attach as compute_route_stats
    stats_changed = compute_route_stats(trails_geojson, config, cache_dir)

    if enriched or stats_changed or arrows_restricted:
        with open(trails_path, "w") as f:
            json.dump(trails_geojson, f, separators=(",", ":"))
        custom_count = len(config.get("custom_routes") or [])
        bits = []
        if enriched:
            bits.append("bucket flags"
                        + (f" + {custom_count} custom route"
                           f"{'s' if custom_count != 1 else ''}"
                           if custom_count else ""))
        if stats_changed:
            bits.append("route stats")
        if arrows_restricted:
            bits.append("event-mode arrow restriction")
        print(f"  Enriched {os.path.basename(trails_path)} "
              f"with {' and '.join(bits)}")

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
    if (not config.get("show_markers", True)
            and not config.get("show_features", True)
            and not config.get("show_parking", True)
            and not config.get("show_trailheads", True)):
        print("POIs: Skipped (all POI layers disabled)")
        # Write empty GeoJSON so the viewer doesn't 404
        with open(pois_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    else:
        fetch_pois(config, pois_path, cache_dir)
    print()

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
    if args.skip_basemap:
        post_messages.append("Basemap: Skipped (--skip-basemap)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(
            basemap_path, basemap_bbox, basemap_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                print(f"Basemap: regenerating ({reason})")

            def _do_basemap():
                fetch_basemap(config, basemap_path)
                _save_signature(basemap_path, basemap_sig)

            fetch_tasks.append(("basemap", _do_basemap))
        else:
            size_mb = os.path.getsize(basemap_path) / (1024 * 1024)
            post_messages.append(
                f"Basemap: Using existing {basemap_path} ({size_mb:.1f} MB)")

    # ---- Terrain planning ----
    if not config.get("show_terrain", True):
        post_messages.append(
            "Terrain: Disabled in config (show_terrain: false)")
    elif args.skip_terrain:
        post_messages.append("Terrain: Skipped (--skip-terrain)")
    else:
        needs_regen, reason = _pmtiles_needs_regen(
            terrain_path, terrain_bbox, terrain_maxzoom)
        if args.force or needs_regen:
            if not args.force and reason:
                print(f"Terrain: regenerating ({reason})")

            def _do_terrain():
                if fetch_terrain(config, terrain_path):
                    _save_signature(terrain_path, terrain_sig)

            fetch_tasks.append(("terrain", _do_terrain))
        else:
            size_mb = os.path.getsize(terrain_path) / (1024 * 1024)
            post_messages.append(
                f"Terrain: Using existing {terrain_path} ({size_mb:.1f} MB)")

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
        print(line)
    print()

    # Step 5: Copy templates and assets. Order matters: copy_assets
    # runs first because it stashes processed-logo dimensions on
    # config["_brand_img_dims"] which copy_templates reads when
    # substituting the brand-img <img> width/height/fetchpriority
    # attributes. Swapping the order leaves brand_dims as None and
    # the brand-img tag emits without dimension hints (CLS regression).
    print("Assembling output...")
    copy_assets(config, output_dir)
    copy_templates(config, output_dir, trails_geojson)
    print()

    # Step 5.5: Minify app.js + style.css when --minify is set. Runs
    # AFTER copy_templates (which writes the files we minify) and
    # BEFORE generate_service_worker (which hashes file contents into
    # CACHE_VERSION — so the SW's hash refers to the final minified
    # bytes the rider downloads). Vendor libs (download_vendor_libs
    # below) are NOT touched — we serve whatever upstream ships.
    if args.minify:
        print("Minifying assets...")
        minify_results = _minify_assets(output_dir)
        for line in minify_results:
            print(line)
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
                "No web manifest (site.webmanifest) — set 'icon:' (or "
                "'logo:') in your config so icons + manifest are generated "
                "from a source image"
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
