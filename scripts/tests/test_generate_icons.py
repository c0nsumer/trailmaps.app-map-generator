"""Tests for generate_icons.py - maskable-icon bleed + manifest colour.

Run from repo root:
    python -m pytest scripts/tests/test_generate_icons.py -v
Or as a script:
    python scripts/tests/test_generate_icons.py
"""

import json
import os
import sys
import tempfile

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generate_icons import (  # noqa: E402
    _composite_on_white,
    _detect_bleed_color,
    _rgba_to_hex,
    _save_png,
    generate_favicon_ico,
    generate_icons,
    generate_maskable_icon,
)
from PIL import Image  # noqa: E402

GREEN = (58, 107, 62, 255)  # #3a6b3e - the placeholder's full-bleed field
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
    # Sources are often RGB (no alpha) - must still be seen as full-bleed.
    rgb = Image.new("RGB", (256, 256), GREEN[:3])
    assert _detect_bleed_color(rgb) == GREEN


def test_maskable_full_bleed_has_no_white_ring():
    """Regression: a full-bleed green source must produce a maskable
    icon that is green to the corner - not a green square floating on a
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
    to match the manifest background_color and apple-touch composite.
    Tolerance ±2: the palette save (_save_png) may shift channels by a
    hair when anti-aliased near-whites share the white's palette cell;
    the guarded property is the bleed field, not bit-exact white."""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "icons"))
        generate_maskable_icon(_logo_on((0, 0, 0, 0), 512), d)
        out = os.path.join(d, "icons", "android-chrome-maskable-512x512.png")
        m = Image.open(out).convert("RGBA")
        assert all(abs(a - b) <= 2 for a, b in zip(m.getpixel((0, 0)), WHITE))


def _read_manifest_bg(output_dir):
    with open(os.path.join(output_dir, "icons", "site.webmanifest"), encoding="utf-8") as f:
        return json.load(f)["background_color"]


def test_manifest_background_matches_full_bleed_icon():
    """End-to-end: a full-bleed green source yields a manifest
    background_color matching its field, so the PWA launch splash matches
    the maskable tile instead of flashing white."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.png")
        _full_bleed(GREEN, 512).save(src)
        generate_icons(src, d, {"name": "M", "title": "Map"})
        assert _read_manifest_bg(d) == "#3a6b3e"


def test_manifest_background_white_for_transparent_logo():
    """A transparent-backplate logo keeps the white default splash."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.png")
        _logo_on((0, 0, 0, 0), 512).save(src)
        generate_icons(src, d, {"name": "M", "title": "Map"})
        assert _read_manifest_bg(d) == "#ffffff"


def test_rgba_to_hex_drops_alpha():
    assert _rgba_to_hex(GREEN) == "#3a6b3e"
    assert _rgba_to_hex((255, 255, 255)) == "#ffffff"


def test_favicon_ico_carries_all_three_frames():
    # Pillow's ICO writer drops any requested size larger than the
    # base image; with the 16x16 saved first, favicon.ico shipped as a
    # single 16x16 frame. Pin the full 16/32/48 set.
    with tempfile.TemporaryDirectory() as tmp:
        generate_favicon_ico(_logo_on(WHITE), tmp)
        ico = Image.open(os.path.join(tmp, "favicon.ico"))
        assert ico.info["sizes"] == {(16, 16), (32, 32), (48, 48)}


def _flat_logo(size=512):
    """Logo-like flat art: a few flat fields, LANCZOS-downscaled so the
    edges anti-alias into the color spread real logo sources have."""
    big = Image.new("RGBA", (size * 2, size * 2), WHITE)
    big.paste(Image.new("RGBA", (500, 500), (20, 80, 200, 255)), (100, 100))
    big.paste(Image.new("RGBA", (400, 400), GREEN), (500, 500))
    big.paste(Image.new("RGBA", (300, 200), (200, 30, 30, 255)), (600, 150))
    return big.resize((size, size), Image.LANCZOS)


def test_save_png_quantizes_flat_art():
    # Flat logo art must ship palette-quantized (smaller file) while
    # staying inside the invisible-error guard.
    from generate_icons import QUANT_MAX_CHANNEL_ERROR
    from PIL import ImageChops

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "icon.png")
        img = _flat_logo()
        _save_png(img, out)
        saved = Image.open(out)
        assert saved.mode == "P", "flat art should save palettized"
        diff = ImageChops.difference(img, saved.convert("RGBA"))
        max_err = max(hi for _lo, hi in (band.getextrema() for band in diff.split()))
        assert max_err <= QUANT_MAX_CHANNEL_ERROR


def test_save_png_keeps_true_color_for_gradients():
    # A smooth two-axis gradient needs far more than 256 colors;
    # quantizing it either dithers past the error threshold or grows
    # the file, so the true-color save must win.
    import io

    grad = Image.new("RGBA", (512, 512))
    grad.putdata(
        [(x % 256, y % 256, (x + y) % 256, 255) for y in range(512) for x in range(512)]
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "icon.png")
        _save_png(grad, out)
        saved = Image.open(out)
        assert saved.mode != "P", "gradient art should stay true-color"
        buf = io.BytesIO()
        grad.save(buf, format="PNG", optimize=True)
        assert os.path.getsize(out) == len(buf.getvalue())


def test_trace_composite_turns_transparency_white():
    # The safari-pinned-tab bitmap must composite onto white before
    # convert("1"): bare convert("1") drops alpha, so a transparent
    # background traced as a solid black rectangle.
    transparent = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    bilevel = _composite_on_white(transparent).convert("1")
    assert bilevel.getpixel((0, 0)) == 255  # white, not black
    # Sanity check on the hazard this guards against:
    assert transparent.convert("1").getpixel((0, 0)) == 0


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
