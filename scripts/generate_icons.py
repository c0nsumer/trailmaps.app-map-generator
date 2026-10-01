#!/usr/bin/env python3
"""Automatic icon generation for the trailmaps.app Map Generator.

Generates all favicon/icon variants from a single source image using
Pillow. Optionally generates safari-pinned-tab.svg via potrace.

Source may be any aspect ratio - non-square images are auto-padded
to square (centered, transparent background) before resizing. Use
a square source if you want a specific framing; otherwise the
auto-pad gives reasonable defaults.

Usage as standalone:
    python scripts/generate_icons.py source.png output_dir/ "Map Title" "ShortName"
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import tempfile

import console
from colors import FRAMEWORK_DEFAULT_ACCENT

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None

# Icon sizes to generate: (filename, width, height, composite_on_white).
# The 192 + 512 pair satisfies Chrome's WebAPK install criteria for
# Android - without 512x512, Chrome can fall back to a "shortcut"
# install that has weaker integration (e.g. shows the package name
# rather than the app name in the uninstall toast).
ICON_SIZES = [
    ("icons/apple-touch-icon.png", 180, 180, True),
    ("icons/favicon-32x32.png", 32, 32, False),
    ("icons/favicon-16x16.png", 16, 16, False),
    ("icons/android-chrome-192x192.png", 192, 192, False),
    ("icons/android-chrome-512x512.png", 512, 512, False),
]


# Palette quantization guard: the 256-color file ships only when it is
# BOTH smaller and within this max per-channel error against the
# true-color original. Flat logo art passes easily (production logos
# measured max error 15/255 for a ~5x size cut, ~392 KB → ~70 KB of
# precached icons per map); gradient-heavy art fails the guard and
# keeps the true-color save.
QUANT_MAX_CHANNEL_ERROR = 32


def _save_png(img, out_path):
    """Save a PNG, palette-quantized when that is visually lossless.

    Encodes both variants in memory and writes whichever the guard
    picks; alpha participates in the error measurement, so a soft
    drop-shadow that dithers badly also keeps true color.
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()

    # FASTOCTREE is the only Pillow quantizer that keeps an alpha channel.
    quant = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    qbuf = io.BytesIO()
    quant.save(qbuf, format="PNG", optimize=True)
    qdata = qbuf.getvalue()

    diff = ImageChops.difference(img.convert("RGBA"), quant.convert("RGBA"))
    max_err = max(hi for _lo, hi in (band.getextrema() for band in diff.split()))
    if len(qdata) < len(data) and max_err <= QUANT_MAX_CHANNEL_ERROR:
        data = qdata

    with open(out_path, "wb") as f:
        f.write(data)


def _composite_on_white(img):
    """Composite an RGBA image onto a white background."""
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg.convert("RGB")
    return img.convert("RGB")


def _pad_to_square(img):
    """Pad a non-square image to square by centering on a transparent
    canvas of side = max(width, height). Returns the original image
    unchanged if already square. The transparent padding flows
    through correctly for every icon variant we generate:

    - PNG icons with composite_on_white=False (favicons, Android
      Chrome): transparent padding preserved, icon shows the logo
      against whatever background the platform paints (usually
      transparent → renders against the address bar / launcher bg).
    - PNG icons with composite_on_white=True (apple-touch-icon,
      which iOS forbids transparency on): the existing
      _composite_on_white step pastes the padded RGBA onto a white
      background, so transparent padding becomes white - same
      result as if the curator had padded the source to white
      themselves.
    - favicon.ico: ICO format supports transparency natively.
    - safari-pinned-tab.svg: the padded RGBA is composited onto white
      before the bilevel convert (convert("1") drops alpha, which
      would turn transparent padding black), so potrace traces just
      the logo silhouette.
    """
    if img.width == img.height:
        return img
    side = max(img.width, img.height)
    src = img if img.mode == "RGBA" else img.convert("RGBA")
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    x = (side - src.width) // 2
    y = (side - src.height) // 2
    canvas.paste(src, (x, y), src)
    return canvas


def generate_png_icons(source_img, output_dir):
    """Generate all PNG icon sizes from the source image."""
    icons_dir = os.path.join(output_dir, "icons")
    os.makedirs(icons_dir, exist_ok=True)

    count = 0
    for filename, w, h, on_white in ICON_SIZES:
        resized = source_img.resize((w, h), Image.Resampling.LANCZOS)
        if on_white:
            resized = _composite_on_white(resized)
        out_path = os.path.join(output_dir, filename)
        _save_png(resized, out_path)
        count += 1

    # Retired icons: output dirs persist between builds (that's what
    # makes basemap reuse work) and the service-worker precache list
    # is built by walking the tree, so a file dropped from ICON_SIZES
    # would otherwise keep shipping to riders forever on rebuilt maps.
    # Same pattern as build.py's stale-terrain removal.
    for stale in ("icons/android-chrome-256x256.png",):
        stale_path = os.path.join(output_dir, stale)
        if os.path.exists(stale_path):
            os.remove(stale_path)

    return count


def _detect_bleed_color(img, default=(255, 255, 255, 255)):
    """Pick the maskable bleed color from the source's corners.

    A *full-bleed* source - an opaque background painted edge-to-edge,
    like the bicycle placeholder's green field - must bleed in its own
    background color. Filling the maskable margin with white instead
    leaves the logo's square sitting on a white field, and the OEM
    circle/squircle mask then reveals that white as a ring: the logo
    appears as "a square floating on a white circle" (the exact PWA
    symptom this guards against).

    A logo on a transparent (or white) backplate keeps the white
    `default`, which matches the manifest `background_color` and the
    apple-touch-icon's white composite, so nothing else changes.

    Heuristic: sample the four corners a little inside the edge (to skip
    any anti-aliased rim). If all four are opaque and identical, the
    source is full-bleed and that shared color is the bleed; otherwise
    fall back to `default`.
    """
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    w, h = rgba.size
    inset = max(1, min(w, h) // 64)
    corners = [
        rgba.getpixel((inset, inset)),
        rgba.getpixel((w - 1 - inset, inset)),
        rgba.getpixel((inset, h - 1 - inset)),
        rgba.getpixel((w - 1 - inset, h - 1 - inset)),
    ]
    first = corners[0]
    if first[3] == 255 and all(c == first for c in corners):
        return first
    return default


def _rgba_to_hex(color):
    """Format an (R, G, B, …) tuple as a ``#rrggbb`` string. Alpha is
    dropped - PWA manifest colors are opaque."""
    r, g, b = color[0], color[1], color[2]
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_maskable_icon(source_img, output_dir, size=512, safe_ratio=0.8, bg_color=None):
    """Generate a maskable PWA icon (Android home-screen tile).

    Android applies an OEM-specific mask shape (circle on Pixel,
    squircle on Samsung, teardrop, rounded square, etc.) to maskable
    icons, and may clip up to ~10% of each edge. The W3C maskable-icon
    spec requires meaningful content to fit inside the inner
    80%-diameter safe zone; everything outside is bleed used to fill
    the tile edge-to-edge under any mask.

    Without a maskable icon, Chrome on Android wraps the (non-maskable)
    icon in its own white circle as a safe fallback - which is why a
    plain `purpose: "any"` icon renders as a small badge floating in a
    larger white circle instead of filling the home-screen tile.

    The source is scaled to `safe_ratio` of the canvas, centered, and
    the surrounding margin is filled with `bg_color`. When `bg_color`
    is None (the default) it is auto-detected from the source corners
    via `_detect_bleed_color`: a full-bleed source (e.g. the bicycle
    placeholder's green field) bleeds in its own color so the tile is a
    solid field edge-to-edge under any mask, while a logo on a
    transparent/white backplate keeps white - matching the manifest
    `background_color` and the apple-touch-icon's white composite.
    """
    if bg_color is None:
        bg_color = _detect_bleed_color(source_img)
    inner = int(size * safe_ratio)
    canvas = Image.new("RGBA", (size, size), bg_color)

    src = source_img.copy()
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    src.thumbnail((inner, inner), Image.Resampling.LANCZOS)

    x = (size - src.width) // 2
    y = (size - src.height) // 2
    canvas.paste(src, (x, y), src)

    out_path = os.path.join(output_dir, "icons", f"android-chrome-maskable-{size}x{size}.png")
    _save_png(canvas, out_path)


def generate_favicon_ico(source_img, output_dir):
    """Generate a multi-resolution favicon.ico in the output root."""
    # Pillow ICO plugin needs specific sizes
    sizes = [(16, 16), (32, 32), (48, 48)]
    imgs = []
    for size in sizes:
        resized = source_img.resize(size, Image.Resampling.LANCZOS)
        if resized.mode != "RGBA":
            resized = resized.convert("RGBA")
        imgs.append(resized)

    ico_path = os.path.join(output_dir, "favicon.ico")
    # The LARGEST frame must be the base image: Pillow's ICO writer
    # silently drops any requested size bigger than the base (with the
    # 16x16 first, favicon.ico shipped as a single-frame 16x16), so
    # save the 48x48 and append the smaller frames.
    imgs[-1].save(ico_path, format="ICO", sizes=sizes, append_images=imgs[:-1])


def generate_safari_pinned_tab(source_img, output_dir):
    """Generate safari-pinned-tab.svg using potrace.

    Returns True if generated, False if potrace is unavailable.
    """
    if not shutil.which("potrace"):
        console.note("potrace not found, skipping safari-pinned-tab.svg")
        console.info("      Install: brew install potrace (macOS) or apt install potrace (Linux)")
        return False

    svg_path = os.path.join(output_dir, "icons", "safari-pinned-tab.svg")

    # Composite onto white BEFORE the bilevel convert: convert("1")
    # drops alpha outright, so a transparent pixel (0,0,0,0) lands as
    # BLACK, and potrace traced the entire transparent background
    # (including _pad_to_square's padding) as a filled rectangle
    # instead of the logo silhouette.
    trace_img = _composite_on_white(
        source_img.resize((256, 256), Image.Resampling.LANCZOS)
    ).convert("1")

    with tempfile.NamedTemporaryFile(suffix=".pbm", delete=False) as tmp:
        tmp_path = tmp.name
        trace_img.save(tmp_path)

    try:
        subprocess.run(
            ["potrace", tmp_path, "-s", "-o", svg_path],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        console.warn(f"potrace failed: {e.stderr.decode().strip()}")
        return False
    finally:
        os.unlink(tmp_path)


def generate_manifest(config, output_dir, bg_color=None):
    """Generate a PWA web manifest with app name from config.

    The manifest drives Chrome's WebAPK install on Android: the uninstall
    toast and home-screen label come from these fields.

    - `name` shows in the install prompt and the uninstall toast.
    - `short_name` shows under the home-screen icon.
    - `id` is omitted intentionally, and adding one is a trap. Chrome
      falls back to start_url as the identity, which resolves to
      `/<slug>/`. Two ways of pinning it explicitly are wrong:

      1. `"id": "/<slug>/"` is outside the manifest's scope when the
         manifest is served from a different path. Per Chrome's
         installability docs an id outside scope "may report an
         installability warning", and a field test on a Pixel 8 showed
         Chrome suppressing install prompts entirely. A wrong id
         produces no build error, only riders who quietly stop seeing
         the install prompt.
      2. `"id": "../"` looks like it would echo start_url. It does not.
         Per the manifest spec a relative id is parsed against the
         ORIGIN of start_url, so `../` resolves to the origin root:
         shared by every map, and different from the default. It would
         fork every existing install and collide all maps onto one
         identity.
         https://www.w3.org/TR/appmanifest/#id-member

      Leaving id absent keeps identity pinned to start_url. If start_url
      or this manifest's own path ever moves, that same commit must add
      `"id"` set to the OLD resolved start_url (e.g. `"/<slug>/"`) and
      verify it lands inside the resolved scope; that preserves existing
      installs.
    - The 192 + 512 icon pair is required for a real WebAPK install.
      Without 512, Chrome silently degrades to a bare home-screen
      shortcut and Android shows the package name in the uninstall
      toast.
    """
    name = config.get("name", "Map")
    title = config.get("title", "Trail Map")
    # background_color paints the PWA launch splash. Match it to the icon's
    # detected bleed field (passed from generate_icons) so a full-bleed
    # colored icon - e.g. the green placeholder - gets a splash matching
    # its tile instead of a white flash. A transparent/white-backplate logo
    # resolves to the white default, so the common case is unchanged.
    # theme_color paints the installed-WebAPK status bar from launch
    # until the page's own meta theme-color takes over. Use the light
    # accent shade so the bar is branded (not white) from the first
    # frame; the in-page bootstrap + app.js then keep the meta in sync
    # per light/dark scheme. Fallback mirrors style.css's framework
    # default for direct/standalone callers without a resolved palette.
    background_color = _rgba_to_hex(bg_color) if bg_color else "#ffffff"
    theme_color = (config.get("_accent_palette") or {}).get("light") or FRAMEWORK_DEFAULT_ACCENT
    manifest = {
        "name": title,
        "short_name": name,
        "start_url": "../",
        "scope": "../",
        "display": "standalone",
        "background_color": background_color,
        "theme_color": theme_color,
        "icons": [
            {
                "src": "android-chrome-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "android-chrome-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "android-chrome-maskable-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    icons_dir = os.path.join(output_dir, "icons")
    os.makedirs(icons_dir, exist_ok=True)
    manifest_path = os.path.join(icons_dir, "site.webmanifest")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def generate_icons(source_path, output_dir, config):
    """Main entry point: generate all icon variants from a single source image.

    Returns True if icons were generated, False if Pillow is unavailable.
    """
    if Image is None:
        console.warn("Pillow not installed - skipping icon generation")
        console.info("         Install: pip install Pillow")
        return False

    if not os.path.isfile(source_path):
        console.warn(f"Icon source not found: {source_path}")
        return False

    try:
        img = Image.open(source_path)
    except Exception as e:
        # Pillow raises UnidentifiedImageError for SVG / PDF / other
        # vector formats, plus a handful of image-format-specific
        # decode errors. Catch broadly: any failure here means we
        # can't generate icons from this source. Caller (build.py)
        # will fail the PWA-manifest check and warn the curator.
        console.warn(f"Cannot read icon source {source_path}")
        console.info(f"         {type(e).__name__}: {e}")
        console.info("         Pillow-readable formats: PNG, WebP, JPEG, GIF, BMP, TIFF")
        return False

    # Auto-pad non-square sources to a transparent square (side = max(w, h))
    # so any aspect ratio flows through. The print line tells the curator
    # padding happened; a tighter crop or colored background means
    # pre-processing the source.
    if img.width != img.height:
        side = max(img.width, img.height)
        console.info(
            f"Icon source {img.width}x{img.height} is not square - "
            f"padding to {side}x{side} with transparent background."
        )
        img = _pad_to_square(img)

    if img.width < 256:
        console.warn(f"Icon source is {img.width}x{img.height}, recommend at least 256x256")

    # Ensure RGBA for consistent processing
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    # Detect the icon's bleed field once and feed it to both the maskable
    # tile (so it fills edge-to-edge under any OEM mask) and the manifest
    # (so the PWA splash background matches a full-bleed colored icon).
    bleed = _detect_bleed_color(img)
    count = generate_png_icons(img, output_dir)
    generate_maskable_icon(img, output_dir, bg_color=bleed)
    count += 1
    generate_favicon_ico(img, output_dir)
    has_svg = generate_safari_pinned_tab(img, output_dir)
    generate_manifest(config, output_dir, bg_color=bleed)

    parts = [f"{count} PNGs", "favicon.ico", "manifest"]
    if has_svg:
        parts.append("pinned-tab SVG")
    console.info(f"Generated icons: {', '.join(parts)}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate favicon/icon variants and a PWA manifest from a source image."
    )
    parser.add_argument("source_image", help="Path to the source image")
    parser.add_argument("output_dir", help="Directory to write icons into")
    parser.add_argument(
        "title", nargs="?", default="Trail Map", help="Manifest title (default: Trail Map)"
    )
    parser.add_argument(
        "short_name", nargs="?", default="Map", help="Manifest short_name (default: Map)"
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    generate_icons(
        args.source_image, args.output_dir, {"title": args.title, "name": args.short_name}
    )
