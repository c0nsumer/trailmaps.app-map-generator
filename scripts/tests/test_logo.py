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
