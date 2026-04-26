#!/usr/bin/env python3
"""Automatic icon generation for MTB Trail Map Framework.

Generates all favicon/icon variants from a single square source image
using Pillow. Optionally generates safari-pinned-tab.svg via potrace.

Usage as standalone:
    python scripts/generate_icons.py source.png output_dir/ "Map Title" "ShortName"
"""

import json
import os
import shutil
import subprocess
import tempfile

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
        print("  NOTE: potrace not found, skipping safari-pinned-tab.svg")
        print("        Install: brew install potrace (macOS) or apt install potrace (Linux)")
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
        print(f"  WARNING: potrace failed: {e.stderr.decode().strip()}")
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
    - `id` gives the PWA a stable identity. Without it, the identity
      derives from `start_url`, which can drift if the deploy path
      changes (`/test/<slug>/` → `/<slug>/`), making Android treat the
      "moved" install as a brand-new app. We pin id to the absolute
      slug-rooted path so it stays stable across deploy moves.
    - The 192 + 512 icon pair is required for a real WebAPK install.
      Without 512, Chrome silently degrades to a bare home-screen
      shortcut and Android shows the package name in the uninstall
      toast (the "Uninstalled com.android..." behaviour).
    """
    slug = config.get("slug", "map")
    name = config.get("name", "Map")
    title = config.get("title", "Trail Map")
    manifest = {
        "name": title,
        "short_name": name,
        "id": f"/{slug}/",
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
        print("  WARNING: Pillow not installed — skipping icon generation")
        print("           Install: pip install Pillow")
        return False

    if not os.path.isfile(source_path):
        print(f"  WARNING: Icon source not found: {source_path}")
        return False

    img = Image.open(source_path)

    # Validate square aspect ratio
    if img.width != img.height:
        print(f"  ERROR: Icon source must be square (got {img.width}x{img.height})")
        print(f"         Crop or pad to square dimensions: {source_path}")
        return False

    if img.width < 256:
        print(f"  WARNING: Icon source is {img.width}x{img.height}, recommend at least 256x256")

    # Ensure RGBA for consistent processing
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    count = generate_png_icons(img, output_dir)
    generate_favicon_ico(img, output_dir)
    has_svg = generate_safari_pinned_tab(img, output_dir)
    generate_manifest(config, output_dir)

    parts = [f"{count} PNGs", "favicon.ico", "manifest"]
    if has_svg:
        parts.append("pinned-tab SVG")
    print(f"  Generated icons: {', '.join(parts)}")
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <source_image> <output_dir> [title] [short_name]")
        sys.exit(1)

    source = sys.argv[1]
    out = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "Trail Map"
    name = sys.argv[4] if len(sys.argv) > 4 else "Map"

    os.makedirs(out, exist_ok=True)
    cfg = {"title": title, "name": name}
    generate_icons(source, out, cfg)
