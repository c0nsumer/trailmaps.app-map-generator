"""Tests for generate_icons.py — maskable-icon bleed handling.

Run from repo root:
    python -m pytest scripts/tests/test_generate_icons.py -v
Or as a script:
    python scripts/tests/test_generate_icons.py
"""

import os
import sys
import tempfile

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generate_icons import (  # noqa: E402
    _detect_bleed_color,
    generate_maskable_icon,
)
from PIL import Image  # noqa: E402

GREEN = (58, 107, 62, 255)  # #3a6b3e — the placeholder's full-bleed field
WHITE = (255, 255, 255, 255)


def _full_bleed(color, size=256):
    return Image.new("RGBA", (size, size), color)


def _logo_on(bg, size=256):
    """A centred opaque blob on a `bg` background (bg may be transparent)."""
    img = Image.new("RGBA", (size, size), bg)
    blob = Image.new("RGBA", (size // 2, size // 2), (20, 80, 200, 255))
    img.paste(blob, (size // 4, size // 4))
    return img


def test_full_bleed_source_bleeds_its_own_color():
    # The bicycle placeholder: opaque green to every edge.
    assert _detect_bleed_color(_full_bleed(GREEN)) == GREEN


def test_transparent_background_keeps_white_default():
    # A logo on transparency must NOT adopt a coloured bleed.
    assert _detect_bleed_color(_logo_on((0, 0, 0, 0))) == WHITE


def test_white_background_logo_keeps_white():
    assert _detect_bleed_color(_logo_on(WHITE)) == WHITE


def test_nonuniform_corners_fall_back_to_default():
    img = _full_bleed(GREEN)
    img.putpixel((4, 4), (10, 10, 10, 255))  # one odd corner
    assert _detect_bleed_color(img) == WHITE


def test_rgb_source_is_detected():
    # Sources are often RGB (no alpha) — must still be seen as full-bleed.
    rgb = Image.new("RGB", (256, 256), GREEN[:3])
    assert _detect_bleed_color(rgb) == GREEN


def test_maskable_full_bleed_has_no_white_ring():
    """Regression: a full-bleed green source must produce a maskable
    icon that is green to the corner — not a green square floating on a
    white field (which the OEM circle mask reveals as a white ring)."""
    with tempfile.TemporaryDirectory() as d:
        # The real pipeline creates icons/ in generate_png_icons before
        # generate_maskable_icon runs; mirror that here.
        os.makedirs(os.path.join(d, "icons"))
        generate_maskable_icon(_full_bleed(GREEN, 512), d)
        out = os.path.join(d, "icons", "android-chrome-maskable-512x512.png")
        assert os.path.isfile(out)
        m = Image.open(out).convert("RGBA")
        assert m.getpixel((0, 0)) == GREEN
        assert m.getpixel((m.width // 2, m.height // 2)) == GREEN


def test_maskable_transparent_logo_still_white():
    """No regression for transparent-background logos: bleed stays white
    to match the manifest background_color and apple-touch composite."""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "icons"))
        generate_maskable_icon(_logo_on((0, 0, 0, 0), 512), d)
        out = os.path.join(d, "icons", "android-chrome-maskable-512x512.png")
        m = Image.open(out).convert("RGBA")
        assert m.getpixel((0, 0)) == WHITE


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
