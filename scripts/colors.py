"""Colour math and accent-colour derivation for the build pipeline.

Pure WCAG helpers (relative luminance, contrast ratio, hex<->rgb, HSL
darkening) plus the logo-derived accent resolution. Extracted from build.py
so the colour logic is independently testable and the orchestrator stays
lean. ``resolve_accent_color`` is the only entry point the build needs; the
rest are internal helpers.
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


def resolve_accent_color(config, project_root, cache_dir):
    """Resolve `accent_color` config to a final hex string for runtime.

    Returns one of:
      - None: use framework default (#2980b9)
      - "#RRGGBB" string: explicit accent colour to inject into CONFIG

    Handles three input forms:
      - omitted: returns None
      - explicit hex: returned verbatim (uppercased)
      - "auto": derive from logo or icon, cache, auto-darken if low
        contrast against white text. Falls back to None with a
        warning if neither raster source exists or no candidate
        colour qualifies.

    Also prints a build-time warning if the resolved colour has
    poor contrast against either light (#ffffff) or dark (#1c1c1e)
    backgrounds — both schemes need to read cleanly since dark mode
    is on the roadmap.
    """
    raw = config.get("accent_color")
    if raw is None:
        return None
    if raw != "auto":
        # Explicit hex: validator already checked the format.
        result = raw.upper()
        _warn_low_contrast_accent(result)
        return result

    # "auto" path — derive from logo, falling back to icon.
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

    # Cache by content hash so re-builds skip the Pillow walk.
    accent_cache_dir = os.path.join(cache_dir, "derive_accent")
    os.makedirs(accent_cache_dir, exist_ok=True)
    with open(source, "rb") as f:
        src_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    cache_path = os.path.join(accent_cache_dir, f"{src_hash}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cached = json.load(f)
            result = cached.get("hex")
            if result:
                _warn_low_contrast_accent(result)
                return result
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

    # Auto-darken so white text on the accent stays readable (WCAG AA).
    rgb = _darken_for_contrast(rgb, target_contrast=4.5, against=(255, 255, 255))
    result = _rgb_to_hex(rgb)
    try:
        with open(cache_path, "w") as f:
            json.dump({"hex": result, "source": os.path.basename(source)}, f)
    except OSError:
        pass
    console.info(f"accent_color: derived {result} from {os.path.basename(source)}")
    _warn_low_contrast_accent(result)
    return result


def _warn_low_contrast_accent(hex_color):
    """Print a build-time warning if the accent has poor contrast
    against either the light-mode or dark-mode sheet background.

    Both schemes need to read cleanly because the accent is used
    for active toggle pills, focus rings, link colour, etc. — all
    surfaces that overlay the sheet background and need to stand
    out in either mode.
    """
    try:
        rgb = _hex_to_rgb(hex_color)
    except (ValueError, IndexError):
        return
    light_bg = (255, 255, 255)
    dark_bg = (28, 28, 30)  # matches --sheet-bg in dark mode
    light_ratio = _contrast_ratio(rgb, light_bg)
    dark_ratio = _contrast_ratio(rgb, dark_bg)
    THRESHOLD = 4.5  # WCAG AA for normal text
    if light_ratio < THRESHOLD or dark_ratio < THRESHOLD:
        console.warn(
            f"accent_color {hex_color} contrast vs light bg = "
            f"{light_ratio:.2f}, vs dark bg = {dark_ratio:.2f} (target "
            f">= {THRESHOLD:.1f}). Active pills / focus rings / links may "
            "be hard to read on one or both schemes."
        )
