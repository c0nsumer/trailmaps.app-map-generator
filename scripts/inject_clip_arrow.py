"""Inject the SDF clip-continuation arrowhead into copied sprite atlases.

The framework ships pre-built Protomaps sprite atlases (assets/sprites/v4/)
in five theme variants × two pixel ratios. Those atlases are raster-only —
none of the icons have `sdf: true` — so we can't tint any of them at runtime.

This module extends each *copied* atlas in `build/{slug}/sprites/v4/` (we
never modify the source assets in `assets/sprites/v4/`) with one extra
icon called `clip-arrow`, sourced from `assets/extras/clip-arrow.sdf.png`
(and `…@2x.png`). The SDF data is placed in the alpha channel of a new
strip appended to the bottom of each atlas PNG; the JSON metadata gets a
`clip-arrow` entry with `sdf: true` so MapLibre will tint it via
`icon-color`.

Idempotent: if `clip-arrow` is already present in an atlas's JSON, that
atlas is left alone. This keeps repeated builds cheap and prevents the
PNG from growing on every rerun.
"""

import json
import os

import console
from PIL import Image

SPRITE_NAME = "clip-arrow"


def _sdf_to_rgba(sdf_img):
    """Convert a single-channel SDF PNG into an RGBA tile MapLibre can read.

    MapLibre SDF sprites store the distance field in the alpha channel; the
    RGB channels are ignored once `icon-color` is applied. We fill RGB with
    white for legibility if anything ever falls back to default rendering.
    """
    if sdf_img.mode != "L":
        sdf_img = sdf_img.convert("L")
    w, h = sdf_img.size
    rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    # Drop the SDF byte values straight into the alpha channel.
    rgba.putalpha(sdf_img)
    return rgba


def _inject_one(atlas_png_path, atlas_json_path, sdf_img, pixel_ratio):
    """Inject an SDF tile into a single atlas (PNG + JSON pair).

    Returns True if a change was made, False if the atlas already had the
    icon (idempotent no-op).
    """
    with open(atlas_json_path, encoding="utf-8") as f:
        meta = json.load(f)

    if SPRITE_NAME in meta:
        return False  # already injected on a previous build

    atlas = Image.open(atlas_png_path)
    if atlas.mode != "RGBA":
        # Palette-mode atlases (white/black/grayscale @2x) need to become
        # RGBA before they can carry a partial-alpha SDF tile. Conversion
        # preserves all existing icon pixels.
        atlas = atlas.convert("RGBA")

    tile = _sdf_to_rgba(sdf_img)
    tw, th = tile.size
    aw, ah = atlas.size

    # Append the tile in a fresh strip below the existing atlas. This is
    # simpler than hunting for unused regions and the extra pixels cost
    # is trivial (a 16×N strip is ~64 bytes uncompressed).
    new_w = max(aw, tw)
    new_h = ah + th
    extended = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    extended.paste(atlas, (0, 0))
    extended.paste(tile, (0, ah))
    extended.save(atlas_png_path)

    meta[SPRITE_NAME] = {
        "x": 0,
        "y": ah,
        "width": tw,
        "height": th,
        "pixelRatio": pixel_ratio,
        "sdf": True,
    }
    with open(atlas_json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, separators=(",", ":"))

    return True


def inject_clip_arrow(sprites_dir, sdf_1x_path, sdf_2x_path):
    """Add the clip-arrow SDF icon to every theme atlas in sprites_dir.

    Args:
        sprites_dir: Directory containing the per-theme {name}.png/.json
            and {name}@2x.png/.json pairs (typically build/{slug}/sprites/v4).
        sdf_1x_path: Path to clip-arrow.sdf.png (the 1x source tile).
        sdf_2x_path: Path to clip-arrow.sdf@2x.png (the 2x source tile).

    Silently skips atlases where the JSON already contains `clip-arrow`
    (idempotent re-runs).
    """
    if not os.path.isdir(sprites_dir):
        return

    sdf_1x = Image.open(sdf_1x_path)
    sdf_2x = Image.open(sdf_2x_path)

    injected = 0
    skipped = 0
    for fname in sorted(os.listdir(sprites_dir)):
        if not fname.endswith(".json"):
            continue
        base = fname[:-5]  # strip ".json"
        json_path = os.path.join(sprites_dir, fname)
        png_path = os.path.join(sprites_dir, base + ".png")
        if not os.path.exists(png_path):
            continue

        # Pixel ratio is encoded in the filename suffix.
        if base.endswith("@2x"):
            tile, ratio = sdf_2x, 2
        else:
            tile, ratio = sdf_1x, 1

        if _inject_one(png_path, json_path, tile, ratio):
            injected += 1
        else:
            skipped += 1

    if injected or skipped:
        msg = f"Injected clip-arrow into {injected} atlas(es)"
        if skipped:
            msg += f" ({skipped} already had it)"
        console.info(msg)
