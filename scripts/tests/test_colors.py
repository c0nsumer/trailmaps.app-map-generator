"""Tests for colors.py — WCAG colour math + accent resolution.

Run from repo root:
    python -m pytest scripts/tests/test_colors.py -v
Or as a script:
    python scripts/tests/test_colors.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from colors import (  # noqa: E402
    _contrast_ratio,
    _darken_for_contrast,
    _hex_to_rgb,
    _relative_luminance,
    _rgb_to_hex,
    resolve_accent_color,
)

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def test_hex_rgb_roundtrip():
    for hex_in in ("#000000", "#FFFFFF", "#2980B9", "#1C1C1E"):
        assert _rgb_to_hex(_hex_to_rgb(hex_in)) == hex_in


def test_hex_to_rgb_parses_channels():
    assert _hex_to_rgb("#2980B9") == (0x29, 0x80, 0xB9)


def test_relative_luminance_bounds():
    assert abs(_relative_luminance(WHITE) - 1.0) < 1e-9
    assert _relative_luminance(BLACK) == 0.0


def test_contrast_ratio_extremes():
    # White on black is the maximum WCAG ratio, 21:1.
    assert round(_contrast_ratio(WHITE, BLACK), 1) == 21.0
    # A colour against itself is 1:1.
    assert abs(_contrast_ratio((40, 40, 40), (40, 40, 40)) - 1.0) < 1e-9


def test_contrast_ratio_symmetric():
    a, b = (200, 30, 30), WHITE
    assert _contrast_ratio(a, b) == _contrast_ratio(b, a)


def test_darken_reaches_target_against_white():
    # A light colour that fails AA against white gets darkened until it passes.
    light = (120, 200, 255)
    assert _contrast_ratio(light, WHITE) < 4.5  # precondition: starts failing
    out = _darken_for_contrast(light, target_contrast=4.5, against=WHITE)
    assert _contrast_ratio(out, WHITE) >= 4.5


def test_darken_keeps_dark_color_passing():
    dark = (10, 20, 40)
    out = _darken_for_contrast(dark, target_contrast=4.5, against=WHITE)
    assert _contrast_ratio(out, WHITE) >= 4.5


def test_resolve_accent_none_when_unset():
    assert resolve_accent_color({}, "/tmp", "/tmp") is None


def test_resolve_accent_explicit_hex_uppercased():
    assert resolve_accent_color({"accent_color": "#2980b9"}, "/tmp", "/tmp") == "#2980B9"


def test_resolve_accent_auto_without_raster_is_none():
    # "auto" with no logo/icon files present → None (framework default).
    assert resolve_accent_color({"accent_color": "auto"}, "/tmp", "/tmp") is None


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
