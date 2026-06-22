"""Logo image processing for the build pipeline.

Resamples a raster logo to ~2x its on-screen render size, or copies an SVG
with an explicit intrinsic size, and derives the output filename. Pillow is
imported lazily inside process_logo so the engine still runs without it.
Extracted from build.py; consumed by the template/asset-copy step.
"""

import os
import re
import shutil

import console

# Logo render bounding box. The brand-img CSS at templates/style.css:1982
# bounds it to max-width: 200px, max-height: 48px on the map overlay.
# About modal uses the same image, similar bounds. We resample the source
# to ~2× this on its longer side so retina displays render cleanly without
# us shipping the original (often much larger) source.
#
# LOGO_DESKTOP_H was previously 80 — that left square icons (e.g. DTE's
# icon-as-logo fallback) processed to 160×160 = ~12KB, but the actual
# rendered size is 48×48. Lowered to 48 to match the CSS max-height,
# which shrinks square-icon outputs to ~96×96 = ~1-2KB, a ~10× saving
# (Lighthouse image-delivery-insight ~11KB savings on DTE). Wide
# wordmarks unchanged — they remain width-bound at 200px.
LOGO_DESKTOP_W = 200
LOGO_DESKTOP_H = 48


def logo_output_filename(source_path):
    """Return the filename the logo asset should be written as in the build dir.

    SVG sources are left as SVG (the browser scales them cleanly via CSS and
    Pillow can't rasterize them without extra deps). Everything else is
    normalized to WebP so the output is predictable for templating.
    """
    ext = os.path.splitext(source_path)[1].lower()
    return "logo.svg" if ext == ".svg" else "logo.webp"


def _copy_svg_with_intrinsic_size(source_path, output_path):
    """Copy an SVG, ensuring the root <svg> tag has pixel width/height.

    HTML <img> needs intrinsic pixel dimensions (or a definite aspect ratio)
    for `width:auto; height:auto; max-width/max-height` CSS to compute a
    non-zero render box. Many designer-exported SVGs use width="100%"
    height="100%" with only a viewBox; loaded via <img> with auto sizing
    they collapse to 0x0. We patch the root tag so width/height match the
    viewBox in pixels. The viewBox is preserved so nothing about the
    rendered geometry changes -- this purely gives the browser an
    intrinsic size to anchor layout.

    If the SVG already has non-percentage width/height, or has no viewBox
    to derive from, the file is copied verbatim.

    Returns (width, height) as integers when the SVG has known pixel
    dimensions (either definite width/height attributes or a viewBox we
    could parse), otherwise (None, None). Callers use this to set the
    brand-img element's HTML width/height attributes for CLS prevention.
    """

    try:
        with open(source_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        console.warn(f"Could not read SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return (None, None)

    svg_open = re.search(r"<svg\b[^>]*>", text)
    if not svg_open:
        shutil.copy2(source_path, output_path)
        console.info(f"Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)

    tag = svg_open.group(0)
    width_m = re.search(r'\bwidth\s*=\s*"([^"]*)"', tag)
    height_m = re.search(r'\bheight\s*=\s*"([^"]*)"', tag)
    viewbox_m = re.search(r'\bviewBox\s*=\s*"([^"]*)"', tag)

    def _is_definite_pixel(v):
        if not v:
            return False
        v = v.strip()
        if v.endswith("%"):
            return False
        # Bare number or px / pt / em etc. -- the browser treats these as
        # definite intrinsic sizes.
        return bool(re.match(r"^-?\d+(\.\d+)?", v))

    def _parse_pixel(v):
        """Extract a numeric pixel value from "100", "100px", "100.5pt", etc.
        Returns int or None. Used to recover dimensions from existing
        width/height attributes for the brand-img HTML attribute.
        """
        if not v:
            return None
        m = re.match(r"^-?\d+(\.\d+)?", v.strip())
        if not m:
            return None
        try:
            return int(round(float(m.group(0))))
        except ValueError:
            return None

    has_definite = (
        width_m
        and _is_definite_pixel(width_m.group(1))
        and height_m
        and _is_definite_pixel(height_m.group(1))
    )

    if has_definite or not viewbox_m:
        shutil.copy2(source_path, output_path)
        console.info(f"Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        if has_definite:
            return (_parse_pixel(width_m.group(1)), _parse_pixel(height_m.group(1)))
        return (None, None)

    parts = viewbox_m.group(1).split()
    if len(parts) != 4:
        shutil.copy2(source_path, output_path)
        console.info(f"Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)
    try:
        vb_w = float(parts[2])
        vb_h = float(parts[3])
    except ValueError:
        shutil.copy2(source_path, output_path)
        console.info(f"Copied logo.svg ({os.path.getsize(source_path)} bytes, vector)")
        return (None, None)

    # Format pixel values without a trailing ".0" when integer-valued.
    def _fmt(v):
        return str(int(v)) if v.is_integer() else str(v)

    new_w = _fmt(vb_w)
    new_h = _fmt(vb_h)

    new_tag = tag
    if width_m:
        new_tag = re.sub(r'\bwidth\s*=\s*"[^"]*"', f'width="{new_w}"', new_tag, count=1)
    else:
        new_tag = new_tag[:-1] + f' width="{new_w}">'
    if height_m:
        new_tag = re.sub(r'\bheight\s*=\s*"[^"]*"', f'height="{new_h}"', new_tag, count=1)
    else:
        new_tag = new_tag[:-1] + f' height="{new_h}">'

    new_text = text[: svg_open.start()] + new_tag + text[svg_open.end() :]
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError as e:
        console.warn(f"Could not write SVG ({e}) — copying verbatim")
        shutil.copy2(source_path, output_path)
        return (None, None)

    console.info(
        f"Wrote logo.svg ({new_w}×{new_h} from viewBox, "
        f"{os.path.getsize(output_path)} bytes, vector)"
    )
    return (int(round(vb_w)), int(round(vb_h)))


def process_logo(source_path, output_path):
    """Resample a logo source down to ~2× its on-screen render size and save.

    For raster sources the output is always WebP (even if the input was PNG/
    JPEG/etc.) with render dimensions inside the LOGO_DESKTOP_W × LOGO_DESKTOP_H
    box: wide wordmarks (aspect wider than the 200×48 box, ≈ 4.17:1) are
    width-bound at 200 px, anything else is height-bound at 48 px. We never
    upscale; small sources are saved at their original dimensions.

    SVG sources skip Pillow entirely; they are copied with one defensive
    rewrite -- if the root <svg> uses percentage width/height (or omits them)
    we substitute pixel values derived from the viewBox so the browser has an
    intrinsic size for our `width: auto; height: auto; max-*` CSS to anchor.
    Without this, e.g. `<svg width="100%" height="100%" viewBox="0 0 600 600">`
    renders at 0x0 inside an <img>.

    Returns (width, height) integers — the dimensions actually written to
    output_path — for the caller to substitute into the brand-img HTML
    width/height attributes (CLS prevention). Returns (None, None) when
    dimensions can't be determined (e.g., Pillow missing, SVG without
    pixel size + viewBox, IO failure); callers should fall back to
    omitting the attributes.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".svg":
        return _copy_svg_with_intrinsic_size(source_path, output_path)

    try:
        from PIL import Image
    except ImportError:
        console.warn(f"Pillow not installed — copying logo verbatim to {output_path}")
        shutil.copy2(source_path, output_path)
        return (None, None)

    try:
        img = Image.open(source_path)
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            raise ValueError(f"invalid logo dimensions {src_w}x{src_h}")

        aspect = src_w / src_h
        # Bounding-box decision: wider than the target box -> width-bound,
        # else height-bound. (target box aspect = LOGO_DESKTOP_W /
        # LOGO_DESKTOP_H — see constants at top of file)
        if aspect >= LOGO_DESKTOP_W / LOGO_DESKTOP_H:
            render_w = LOGO_DESKTOP_W
            render_h = LOGO_DESKTOP_W / aspect
        else:
            render_h = LOGO_DESKTOP_H
            render_w = LOGO_DESKTOP_H * aspect

        # Resample to 2× the render's longer side for retina sharpness;
        # never upscale.
        target_long = 2 * max(render_w, render_h)
        long_side = max(src_w, src_h)
        if long_side > target_long:
            scale = target_long / long_side
            new_size = (
                max(1, int(round(src_w * scale))),
                max(1, int(round(src_h * scale))),
            )
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # Preserve alpha when present; otherwise convert to RGB so WEBP
        # encoding doesn't choke on palette-based modes.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")

        img.save(output_path, "WEBP", quality=90, method=6)
        out_w, out_h = img.size
        console.info(f"Wrote logo.webp ({out_w}×{out_h}, {os.path.getsize(output_path)} bytes)")
        return (out_w, out_h)
    except Exception as e:
        console.warn(f"Failed to process logo ({e}) — copying source verbatim")
        try:
            shutil.copy2(source_path, output_path)
        except Exception as e2:
            console.error(f"Could not write logo: {e2}")
        return (None, None)
