"""Tests for logo.process_logo."""

from logo import process_logo
from PIL import Image, ImageDraw


def test_indexed_png_logo_is_resampled_smoothly(tmp_path):
    # Pillow resamples a mode "P" image with nearest-neighbor whatever
    # filter is asked for, so the downscale must come after the
    # conversion. A two-color disc then gains antialiased edge pixels.
    src = tmp_path / "logo.png"
    disc = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((100, 100, 1100, 1100), fill=(200, 30, 30, 255))
    disc.quantize(colors=4).save(src, transparency=0)
    assert Image.open(src).mode == "P"

    out = tmp_path / "logo.webp"
    process_logo(str(src), str(out))

    alpha = Image.open(out).convert("RGBA").getchannel("A")
    assert len(alpha.getcolors()) > 2


def test_corner_ratio_rounds_the_corners_and_keeps_the_middle(tmp_path):
    # The bundled placeholder is a full-bleed square; its on-page copy
    # gets rounded corners while the edges' midpoints stay opaque.
    src = tmp_path / "tile.png"
    Image.new("RGB", (512, 512), (58, 107, 62)).save(src)

    out = tmp_path / "logo.webp"
    w, h = process_logo(str(src), str(out), corner_ratio=7 / 26)

    alpha = Image.open(out).convert("RGBA").getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((w - 1, h - 1)) == 0
    assert alpha.getpixel((w // 2, 0)) == 255
    assert alpha.getpixel((w // 2, h // 2)) == 255
    assert len(alpha.getcolors()) > 2  # antialiased edge


def test_no_corner_ratio_leaves_a_square_logo_opaque(tmp_path):
    src = tmp_path / "tile.png"
    Image.new("RGB", (512, 512), (58, 107, 62)).save(src)

    out = tmp_path / "logo.webp"
    process_logo(str(src), str(out))

    assert Image.open(out).convert("RGBA").getpixel((0, 0))[3] == 255
