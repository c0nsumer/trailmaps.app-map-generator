"""Colour math and accent-colour derivation for the build pipeline.

Pure WCAG helpers (relative luminance, contrast ratio, hex<->rgb, HSL
darken/lighten) plus the logo-derived accent resolution. Extracted from
build.py so the colour logic is independently testable and the
orchestrator stays lean. ``resolve_accent_palette`` is the only entry
point the build needs; the rest are internal helpers.
"""

import hashlib
import json
import os

import console


def _relative_luminance(rgb):
    """WCAG relative luminance for an (r, g, b) triple in 0–255.

    Used by the accent-color contrast warning + the auto-darken loop
    in derive_accent. Formula from WCAG 2.x; sRGB linearisation +
    Rec. 709 luma weights.
    """

    def _channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(rgb_a, rgb_b):
    """WCAG contrast ratio between two colours."""
    la = _relative_luminance(rgb_a)
    lb = _relative_luminance(rgb_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(hex_str):
    """Parse '#RRGGBB' → (r, g, b)."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _darken_for_contrast(rgb, target_contrast=4.5, against=(255, 255, 255)):
    """Darken an RGB triple in HSL-space until it hits target_contrast
    against `against` (white by default — the assumption is white text
    on accent background). Returns the (possibly modified) triple.

    Walks lightness down in small steps; bails after a fixed number of
    iterations to avoid pathological inputs spinning forever.
    """
    import colorsys

    r, g, b = (c / 255.0 for c in rgb)
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    for _ in range(40):
        rgb = tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, lightness, s))
        if _contrast_ratio(rgb, against) >= target_contrast:
            return rgb
        lightness -= 0.025
        if lightness < 0:
            lightness = 0
            break
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, lightness, s))


def derive_accent(image_path):
    """Pick a single accent colour from a raster logo.

    Algorithm: thumbnail to 100×100 for speed, walk every pixel,
    discard near-white / near-black / desaturated / transparent
    contributions, pick the most common surviving colour. Returns
    a (r, g, b) triple or None if no candidate qualifies (caller
    falls back to the framework default).

    SVG inputs aren't supported — Pillow can't open them. The
    caller is expected to fall back to the icon (typically PNG)
    when the logo is vector.
    """
    try:
        from PIL import Image
    except ImportError:
        console.warn("Pillow not installed — cannot derive accent_color")
        return None
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as exc:
        console.warn(f"could not open {image_path} for accent derivation: {exc}")
        return None
    img.thumbnail((100, 100))
    # Iterate raw RGBA bytes — Image.getdata() is deprecated in
    # Pillow 11+ and slated for removal in Pillow 14. tobytes() returns
    # a flat byte buffer in (R, G, B, A) order which we walk in groups
    # of four. Faster too, no per-pixel tuple allocation.
    pixels = img.tobytes()
    counts = {}
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
        if a < 128:
            continue
        # Skip near-white (likely background)
        if max(r, g, b) > 240 and min(r, g, b) > 200:
            continue
        # Skip near-black (likely outline / shadow)
        if max(r, g, b) < 30:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == 0:
            continue
        # Skip desaturated (looks grey, not branding)
        if (mx - mn) / mx < 0.20:
            continue
        # Quantise to nearest 8 to consolidate near-identical pixels
        key = (r & 0xF8, g & 0xF8, b & 0xF8)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


# Framework default accent — used verbatim as the light shade when no
# accent_color is configured, and as the base for the derived dark
# shade. Keep in sync with --accent-light in templates/style.css :root.
FRAMEWORK_DEFAULT_ACCENT = "#1D6FA5"

# On-accent text tokens: white, or a near-black (softer than pure #000,
# matching the iOS/Material "on-primary" convention) for light accents.
_ON_ACCENT_LIGHT = "#FFFFFF"
_ON_ACCENT_DARK = "#14140F"

# Dark-mode sheet background (#1c1c1e) — the surface the dark-mode accent
# shade must read against. Matches --sheet-bg in style.css's dark block.
_DARK_SHEET_BG = (28, 28, 30)


def _lighten_for_contrast(rgb, target_contrast=4.5, against=_DARK_SHEET_BG):
    """Lighten an RGB triple in HSL-space until it hits target_contrast
    against `against` (the dark sheet bg by default — the assumption is
    the accent reading as a link / fill on the dark sheet). Mirror of
    _darken_for_contrast for the dark-mode accent shade.

    Walks lightness UP in small steps; eases saturation down once the
    colour is already light so a maxed-out hue lands pastel rather than
    neon. Bails after a fixed number of iterations to avoid pathological
    inputs spinning forever.
    """
    import colorsys

    r, g, b = (c / 255.0 for c in rgb)
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    for _ in range(40):
        rgb = tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, lightness, s))
        if _contrast_ratio(rgb, against) >= target_contrast:
            return rgb
        lightness += 0.025
        # Past ~0.7 lightness, ease saturation down so very light accents
        # don't read fluorescent.
        if lightness > 0.7:
            s = max(0.0, s - 0.02)
        if lightness >= 1.0:
            lightness = 1.0
            break
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, lightness, s))


def _best_text_color(accent_rgb):
    """Return the on-accent text colour (#fff or the near-black token)
    with the higher WCAG contrast against the given accent fill. Deep
    accents → white; light accents → near-black, the way Material/iOS
    flip on-primary in dark themes."""
    white = (255, 255, 255)
    near_black = _hex_to_rgb(_ON_ACCENT_DARK)
    if _contrast_ratio(accent_rgb, white) >= _contrast_ratio(accent_rgb, near_black):
        return _ON_ACCENT_LIGHT
    return _ON_ACCENT_DARK


def _palette_from_base(base_rgb, darken_light):
    """Derive the 4-value accent palette from a single base (r, g, b).

    - light shade: the base verbatim, OR darkened to AA vs white text
      when `darken_light` (the "auto" pixel-pick path — keeps white-on-
      accent pills legible). Explicit hex / framework default are NOT
      darkened, so light mode stays exactly as configured.
    - dark shade: the base LIGHTENED until it reads AA against the dark
      sheet. Derived from the same base (not the darkened light shade)
      so the two lightness adjustments don't compound.
    - on-accent: #fff or near-black per shade, whichever contrasts more.

    Returns {"light", "dark", "onLight", "onDark"} as hex strings.
    """
    if darken_light:
        light_rgb = _darken_for_contrast(base_rgb, target_contrast=4.5, against=(255, 255, 255))
    else:
        light_rgb = base_rgb
    dark_rgb = _lighten_for_contrast(base_rgb, target_contrast=4.5, against=_DARK_SHEET_BG)
    return {
        "light": _rgb_to_hex(light_rgb),
        "dark": _rgb_to_hex(dark_rgb),
        "onLight": _best_text_color(light_rgb),
        "onDark": _best_text_color(dark_rgb),
    }


def resolve_accent_palette(config, project_root, cache_dir):
    """Resolve `accent_color` config into a 4-value palette for runtime.

    Returns a dict of hex strings:
        {"light": ..., "dark": ..., "onLight": ..., "onDark": ...}

    The accent serves two roles that conflict in dark mode — white-text
    pills want a DARK accent, while links / focus rings on the dark
    sheet want a LIGHT accent. So we derive a deep light-mode shade and
    a lightened dark-mode shade from one base, plus the best on-accent
    text colour for each; style.css maps the active pair by
    [data-color-scheme]. Always returns a palette (never None): the
    unset / failed-derivation cases fall back to the framework default.

    Base selection:
      - omitted → framework default #1d6fa5, used verbatim for light.
      - explicit hex → the hex, used verbatim for light (preserves
        curator intent exactly; only the dark shade is derived).
      - "auto" → logo/icon-derived pixel pick (cached as the RAW pick),
        darkened for the light shade so white text stays AA-legible.
    """
    base_rgb, darken_light, is_default = _resolve_accent_base(config, project_root, cache_dir)
    palette = _palette_from_base(base_rgb, darken_light)
    # Only nag about contrast for an accent the curator actually chose
    # (explicit hex / successful auto-derive). The framework-default
    # fallback is ours to get right, not something to warn about on
    # every default-accent build.
    if not is_default:
        _warn_low_contrast_palette(palette)
    return palette


def _resolve_accent_base(config, project_root, cache_dir):
    """Resolve config → (base_rgb, darken_light, is_default).

    base_rgb is the (r, g, b) the palette is derived from; darken_light
    says whether the LIGHT shade should be darkened for white-text
    legibility (True only for the "auto" pixel-pick — explicit hex and
    the framework default are trusted verbatim so light mode is
    unchanged); is_default flags the framework-default fallback (unset,
    or "auto" that couldn't produce a colour) so the caller can skip the
    low-contrast warning for a colour the curator didn't pick.
    """
    raw = config.get("accent_color")
    if raw is None:
        return _hex_to_rgb(FRAMEWORK_DEFAULT_ACCENT), False, True
    if raw != "auto":
        # Explicit hex: validator already checked the format.
        return _hex_to_rgb(raw.upper()), False, False
    pick = _derive_accent_cached(config, project_root, cache_dir)
    if pick is None:
        return _hex_to_rgb(FRAMEWORK_DEFAULT_ACCENT), False, True
    return pick, True, False


def _derive_accent_cached(config, project_root, cache_dir):
    """Run derive_accent on the logo (falling back to icon), cached by
    source content hash. Returns the RAW (r, g, b) pixel pick —
    pre-contrast-adjustment — so the palette can be recomputed cheaply
    on every build without re-walking the image, and so palette-math
    changes never need a cache-version bump. Returns None (with a
    warning) when no raster source exists or no colour qualifies.
    """
    logo_p = config.get("logo") or ""
    icon_p = config.get("icon") or ""
    candidates = []
    for rel in (logo_p, icon_p):
        if not rel:
            continue
        abs_path = os.path.join(project_root, rel)
        if not os.path.isfile(abs_path):
            continue
        if abs_path.lower().endswith(".svg"):
            # Pillow can't read SVG — try the next candidate.
            continue
        candidates.append(abs_path)
    if not candidates:
        console.warn(
            "accent_color: 'auto' requires a raster logo or icon "
            "(PNG/WebP/JPG); none found. Falling back to framework default."
        )
        return None

    source = candidates[0]

    # Cache by content hash so re-builds skip the Pillow walk. We store
    # the RAW pick (not a finished shade) — see the docstring.
    accent_cache_dir = os.path.join(cache_dir, "derive_accent")
    os.makedirs(accent_cache_dir, exist_ok=True)
    with open(source, "rb") as f:
        src_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    cache_path = os.path.join(accent_cache_dir, f"{src_hash}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cached = json.load(f)
            raw_pick = cached.get("raw")
            # Old-format entries stored a darkened "hex" and no "raw" —
            # treat those as a miss and re-derive (one-time migration).
            if isinstance(raw_pick, list) and len(raw_pick) == 3:
                return tuple(raw_pick)
        except (OSError, json.JSONDecodeError):
            pass

    rgb = derive_accent(source)
    if rgb is None:
        console.warn(
            f"accent_color: 'auto' could not pick a colour from "
            f"{os.path.basename(source)} (logo may be greyscale or fully "
            "neutral). Falling back to framework default."
        )
        return None
    try:
        with open(cache_path, "w") as f:
            json.dump({"raw": list(rgb), "source": os.path.basename(source)}, f)
    except OSError:
        pass
    console.info(
        f"accent_color: derived raw {_rgb_to_hex(rgb)} from {os.path.basename(source)}"
    )
    return tuple(rgb)


def _warn_low_contrast_palette(palette):
    """Print a build-time warning if either derived accent shade fails
    WCAG AA against its OWN background: the light shade vs the white
    sheet, the dark shade vs the dark sheet (#1c1c1e).

    Each shade only ever renders in its own scheme, so — unlike the old
    single-colour check — we no longer test one value against both
    backgrounds. The on-accent text colour is picked for max contrast by
    construction, so only the foreground-on-sheet role (links / focus
    rings) needs a warning.
    """
    THRESHOLD = 4.5  # WCAG AA for normal text
    try:
        light_rgb = _hex_to_rgb(palette["light"])
        dark_rgb = _hex_to_rgb(palette["dark"])
    except (ValueError, IndexError, KeyError):
        return
    light_ratio = _contrast_ratio(light_rgb, (255, 255, 255))
    dark_ratio = _contrast_ratio(dark_rgb, _DARK_SHEET_BG)
    problems = []
    if light_ratio < THRESHOLD:
        problems.append(f"light shade {palette['light']} vs white sheet = {light_ratio:.2f}")
    if dark_ratio < THRESHOLD:
        problems.append(f"dark shade {palette['dark']} vs dark sheet = {dark_ratio:.2f}")
    if problems:
        console.warn(
            "accent_color: "
            + "; ".join(problems)
            + f" (target >= {THRESHOLD:.1f}). Links / focus rings may be "
            "hard to read in that scheme."
        )
