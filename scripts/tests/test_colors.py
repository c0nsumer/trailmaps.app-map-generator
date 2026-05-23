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
    _LIGHT_TARGET_CONTRAST,
    _best_text_color,
    _contrast_ratio,
    _darken_for_contrast,
    _hex_to_rgb,
    _lighten_for_contrast,
    _palette_from_base,
    _relative_luminance,
    _rgb_to_hex,
    resolve_accent_palette,
)

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
DARK_BG = (28, 28, 30)  # --sheet-bg in dark mode (#1c1c1e)


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


def test_lighten_reaches_target_against_dark():
    # A dark colour that fails AA against the dark sheet gets lightened
    # until it passes. #005088 is the worst real-world offender (2.03).
    dark = (0, 80, 136)
    assert _contrast_ratio(dark, DARK_BG) < 4.5  # precondition: starts failing
    out = _lighten_for_contrast(dark, target_contrast=4.5, against=DARK_BG)
    assert _contrast_ratio(out, DARK_BG) >= 4.5


def test_best_text_color_flips_with_accent_lightness():
    # Deep accent → white text; light accent → near-black text.
    assert _best_text_color((20, 40, 90)) == "#FFFFFF"
    assert _best_text_color((150, 200, 240)) == "#14140F"


def test_vividness_deepens_auto_light_shade():
    # Task 7: the "auto" light shade is deepened past the 4.5 AA floor so
    # it reads vivid, not muddy. A green raw that clears 4.5 early still
    # gets pushed to the vividness target.
    p = _palette_from_base((72, 152, 32), darken_light=True)
    assert _contrast_ratio(_hex_to_rgb(p["light"]), WHITE) >= _LIGHT_TARGET_CONTRAST


def test_vividness_skips_verbatim_light_shade():
    # Explicit hex / framework default (darken_light=False) are verbatim —
    # vividness must NOT deepen them.
    p = _palette_from_base((72, 152, 32), darken_light=False)
    assert p["light"] == "#489820"


def test_darken_sat_boost_keeps_saturation():
    # With a sat boost the deepened colour's saturation is >= the base's
    # (vivid, not greyed out), and it still clears the target.
    import colorsys

    base = (72, 152, 32)
    _, _, s0 = colorsys.rgb_to_hls(*(x / 255 for x in base))
    out = _darken_for_contrast(base, target_contrast=5.5, against=WHITE, sat_boost=0.015)
    _, _, s1 = colorsys.rgb_to_hls(*(x / 255 for x in out))
    assert s1 >= s0
    assert _contrast_ratio(out, WHITE) >= 5.5


def test_auto_palette_invariants_across_input_space():
    """The real guarantee for UNSEEN logos. You can't eyeball an accent
    derived from a logo you don't have, but you can prove the contract
    holds for every colour derive_accent could pick. Swept densely
    offline (~8.8k points); this guards the bounds against regression.

    For any derivable raw pick, the auto palette must:
      - keep the logo's hue (we only move lightness/saturation),
      - clear the light-shade target vs white (white-text pills),
      - clear AA vs the dark sheet (links/fills in dark mode),
      - carry an on-accent text colour that clears AA on its own fill.
    """
    import colorsys

    for deg in range(0, 360, 10):
        h = deg / 360.0
        for lightness in (0.25, 0.40, 0.55, 0.70):
            for sat in (0.3, 0.6, 1.0):
                raw = tuple(int(round(x * 255)) for x in colorsys.hls_to_rgb(h, lightness, sat))
                mx, mn = max(raw), min(raw)
                # Only inputs derive_accent would actually emit (skips
                # near-white, near-black, and desaturated picks).
                if mx < 30 or (mx > 240 and mn > 200) or (mx - mn) / mx < 0.20:
                    continue
                p = _palette_from_base(raw, darken_light=True)
                lr, dr = _hex_to_rgb(p["light"]), _hex_to_rgb(p["dark"])
                ctx = (deg, lightness, sat, p)
                assert _contrast_ratio(lr, WHITE) >= _LIGHT_TARGET_CONTRAST - 0.05, ctx
                assert _contrast_ratio(dr, DARK_BG) >= 4.45, ctx
                assert _contrast_ratio(lr, _hex_to_rgb(p["onLight"])) >= 4.5, ctx
                assert _contrast_ratio(dr, _hex_to_rgb(p["onDark"])) >= 4.5, ctx
                for shade in (lr, dr):
                    hs, _, ss = colorsys.rgb_to_hls(*(x / 255 for x in shade))
                    if ss > 0.05:  # hue is only meaningful when saturated
                        drift = min(abs(hs - h), 1 - abs(hs - h)) * 360
                        assert drift <= 6.0, (ctx, shade, drift)


def test_resolve_palette_unset_uses_framework_default():
    # No accent_color → framework-default palette (never None), with the
    # default used verbatim for the light shade.
    p = resolve_accent_palette({}, "/tmp", "/tmp")
    assert set(p) == {"light", "dark", "onLight", "onDark"}
    assert p["light"] == "#1D6FA5"


def test_resolve_palette_explicit_hex_light_verbatim():
    # Explicit hex is the LIGHT shade verbatim (light mode unchanged);
    # only the dark shade is derived, and it clears AA vs the dark sheet.
    p = resolve_accent_palette({"accent_color": "#005088"}, "/tmp", "/tmp")
    assert p["light"] == "#005088"
    assert _contrast_ratio(_hex_to_rgb(p["dark"]), DARK_BG) >= 4.5


def test_resolve_palette_auto_without_raster_falls_back():
    # "auto" with no raster source → framework-default palette, not None.
    p = resolve_accent_palette({"accent_color": "auto"}, "/tmp", "/tmp")
    assert p["light"] == "#1D6FA5"


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
