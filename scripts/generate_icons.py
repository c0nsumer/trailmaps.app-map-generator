#!/usr/bin/env python3
"""Automatic icon generation for the trailmaps.app Map Generator.

Generates all favicon/icon variants from a single source image using
Pillow. Optionally generates safari-pinned-tab.svg via potrace.

Source may be any aspect ratio — non-square images are auto-padded
to square (centered, transparent background) before resizing. Use
a square source if you want a specific framing; otherwise the
auto-pad gives reasonable defaults.

Usage as standalone:
    python scripts/generate_icons.py source.png output_dir/ "Map Title" "ShortName"
"""

import json
import os
import shutil
import subprocess
import tempfile

import console

try:
    from PIL import Image
except ImportError:
    Image = None

# Icon sizes to generate: (filename, width, height, composite_on_white).
# The 192 + 512 pair satisfies Chrome's WebAPK install criteria for
# Android — without 512x512, Chrome can fall back to a "shortcut"
# install that has weaker integration (e.g. shows the package name
# rather than the app name in the uninstall toast). 256 is kept for
# legacy reasons; some older docs/configurations reference it.
ICON_SIZES = [
    ("icons/apple-touch-icon.png", 180, 180, True),
    ("icons/favicon-32x32.png", 32, 32, False),
    ("icons/favicon-16x16.png", 16, 16, False),
    ("icons/android-chrome-192x192.png", 192, 192, False),
    ("icons/android-chrome-256x256.png", 256, 256, False),
    ("icons/android-chrome-512x512.png", 512, 512, False),
    ("icons/mstile-150x150.png", 150, 150, False),
]


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
      background, so transparent padding becomes white — same
      result as if the curator had padded the source to white
      themselves.
    - favicon.ico: ICO format supports transparency natively.
    - safari-pinned-tab.svg: convert to "1" (bilevel) treats
      transparent and white the same way (both become white in
      bitmap), so potrace traces just the logo silhouette.
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
        resized = source_img.resize((w, h), Image.LANCZOS)
        if on_white:
            resized = _composite_on_white(resized)
        out_path = os.path.join(output_dir, filename)
        resized.save(out_path, format="PNG", optimize=True)
        count += 1

    return count


def generate_maskable_icon(
    source_img, output_dir, size=512, safe_ratio=0.8, bg_color=(255, 255, 255, 255)
):
    """Generate a maskable PWA icon (Android home-screen tile).

    Android applies an OEM-specific mask shape (circle on Pixel,
    squircle on Samsung, teardrop, rounded square, etc.) to maskable
    icons, and may clip up to ~10% of each edge. The W3C maskable-icon
    spec requires meaningful content to fit inside the inner
    80%-diameter safe zone; everything outside is bleed used to fill
    the tile edge-to-edge under any mask.

    Without a maskable icon, Chrome on Android wraps the (non-maskable)
    icon in its own white circle as a safe fallback — which is why a
    plain `purpose: "any"` icon renders as a small badge floating in a
    larger white circle instead of filling the home-screen tile.

    The source is scaled to `safe_ratio` of the canvas, centered, and
    the surrounding margin is filled with `bg_color` so the tile paints
    edge-to-edge under any mask. White matches the existing manifest
    `background_color` and the apple-touch-icon's white composite, so
    iOS / Android / older fallbacks all stay visually consistent.
    """
    inner = int(size * safe_ratio)
    canvas = Image.new("RGBA", (size, size), bg_color)

    src = source_img.copy()
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    src.thumbnail((inner, inner), Image.LANCZOS)

    x = (size - src.width) // 2
    y = (size - src.height) // 2
    canvas.paste(src, (x, y), src)

    out_path = os.path.join(output_dir, "icons", f"android-chrome-maskable-{size}x{size}.png")
    canvas.save(out_path, format="PNG", optimize=True)


def generate_favicon_ico(source_img, output_dir):
    """Generate a multi-resolution favicon.ico in the output root."""
    # Pillow ICO plugin needs specific sizes
    sizes = [(16, 16), (32, 32), (48, 48)]
    imgs = []
    for size in sizes:
        resized = source_img.resize(size, Image.LANCZOS)
        if resized.mode != "RGBA":
            resized = resized.convert("RGBA")
        imgs.append(resized)

    ico_path = os.path.join(output_dir, "favicon.ico")
    # Save the first image, append the rest
    imgs[0].save(ico_path, format="ICO", sizes=sizes, append_images=imgs[1:])


def generate_safari_pinned_tab(source_img, output_dir):
    """Generate safari-pinned-tab.svg using potrace.

    Returns True if generated, False if potrace is unavailable.
    """
    if not shutil.which("potrace"):
        console.note("potrace not found, skipping safari-pinned-tab.svg")
        console.info("      Install: brew install potrace (macOS) or apt install potrace (Linux)")
        return False

    svg_path = os.path.join(output_dir, "icons", "safari-pinned-tab.svg")

    # Convert to high-contrast 1-bit image for tracing
    trace_img = source_img.resize((256, 256), Image.LANCZOS).convert("1")

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


def generate_manifest(config, output_dir):
    """Generate a PWA web manifest with app name from config.

    The manifest drives Chrome's WebAPK install on Android — Android's
    uninstall toast and home-screen label both come from these fields.
    Notably:

    - `name` (full app name) shows in the install prompt and the
      uninstall confirmation toast.
    - `short_name` shows under the home-screen icon.
    - `id` is omitted intentionally — Chrome falls back to start_url
      as the identity. An earlier version pinned `id` to a slug-rooted
      absolute path (`/<slug>/`) anticipating a future deploy move
      from `/test/<slug>/` to `/<slug>/`, but that put the id OUTSIDE
      the manifest's scope (which resolves to `/test/<slug>/` via
      start_url). Per Chrome's installability docs, an id outside
      scope "may report an installability warning" — and field-test
      on Pixel 8 confirmed that Chrome was suppressing install
      prompts entirely. Defaulting id to start_url means the identity
      changes if/when we move the deploy path, orphaning existing
      installs (riders see Install prompt for the "new" app, end up
      with two; manual cleanup of the old one). That one-time
      migration cost is the right trade for installability working
      today.
    - The 192 + 512 icon pair is required for a real WebAPK install.
      Without 512, Chrome silently degrades to a bare home-screen
      shortcut and Android shows the package name in the uninstall
      toast (the "Uninstalled com.android..." behaviour).
    """
    name = config.get("name", "Map")
    title = config.get("title", "Trail Map")
    manifest = {
        "name": title,
        "short_name": name,
        "start_url": "../",
        "scope": "../",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": [
            {
                "src": "android-chrome-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "android-chrome-256x256.png",
                "sizes": "256x256",
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
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def generate_icons(source_path, output_dir, config):
    """Main entry point: generate all icon variants from a single source image.

    Returns True if icons were generated, False if Pillow is unavailable.
    """
    if Image is None:
        console.warn("Pillow not installed — skipping icon generation")
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

    # Non-square sources used to error out, forcing the curator to
    # crop or pad by hand. We now auto-pad to square (centered on a
    # transparent canvas of side = max(w, h)) so any logo aspect
    # ratio can flow through icon generation. The print line is the
    # curator's signal that padding happened — if they want a tighter
    # crop or a coloured background, they can pre-process the source
    # themselves; otherwise this is "good enough" for every variant.
    if img.width != img.height:
        side = max(img.width, img.height)
        console.info(
            f"Icon source {img.width}x{img.height} is not square — "
            f"padding to {side}x{side} with transparent background."
        )
        img = _pad_to_square(img)

    if img.width < 256:
        console.warn(f"Icon source is {img.width}x{img.height}, recommend at least 256x256")

    # Ensure RGBA for consistent processing
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    count = generate_png_icons(img, output_dir)
    generate_maskable_icon(img, output_dir)
    count += 1
    generate_favicon_ico(img, output_dir)
    has_svg = generate_safari_pinned_tab(img, output_dir)
    generate_manifest(config, output_dir)

    parts = [f"{count} PNGs", "favicon.ico", "manifest"]
    if has_svg:
        parts.append("pinned-tab SVG")
    console.info(f"Generated icons: {', '.join(parts)}")
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        console.step(f"Usage: {sys.argv[0]} <source_image> <output_dir> [title] [short_name]")
        sys.exit(1)

    source = sys.argv[1]
    out = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "Trail Map"
    name = sys.argv[4] if len(sys.argv) > 4 else "Map"

    os.makedirs(out, exist_ok=True)
    cfg = {"title": title, "name": name}
    generate_icons(source, out, cfg)
