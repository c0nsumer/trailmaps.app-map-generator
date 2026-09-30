#!/usr/bin/env python3
"""One-shot generator for the SDF clip-continuation arrowhead.

Run this only when the arrowhead shape needs to change. The output PNGs
(clip-arrow.sdf.png and clip-arrow.sdf@2x.png) are committed assets; the
build pipeline reads them via scripts/inject_clip_arrow.py and pastes them
into each theme's sprite atlas.

What this produces is a Signed Distance Field encoding of an
arrowhead-with-back-notch shape pointing UP (north) at 0° rotation.
The shape is the same one drawn by `drawArrow()` in templates/app.js
for on-trail direction arrows - chunky, with a shallow notch in the
back edge so the head reads as a solid arrow rather than a thin
chevron. Using the same shape for both on-trail direction arrows and
clip-continuation arrowheads keeps the visual vocabulary consistent.

MapLibre interprets pixel values such that 191 (0.75) = the boundary of
the shape, < 191 = outside, > 191 = inside, with the unit being "distance
from edge in pixels at the sprite's native resolution." That lets
MapLibre's `icon-color` cleanly tint the shape at any size without
rasterisation artefacts; `icon-halo-color` + `icon-halo-width` then
draw an outline within the SDF's gradient zone outside the boundary.

Implementation: analytic signed distance to a polygon. For each output
pixel we compute the actual Euclidean distance to the nearest polygon
edge (positive inside, negative outside). The inside test uses
ray-casting (parity of horizontal-ray crossings) so non-convex
polygons like the arrowhead-with-notch work correctly - the
cross-product sign test that worked for the old triangle would
mis-classify pixels inside the notch's concave region.
"""

import os

import numpy as np
from PIL import Image


def _point_inside_polygon(px, py, verts):
    """Ray-casting inside test for an arbitrary (possibly non-convex)
    polygon. Returns a boolean array matching the shape of px/py.

    For each pixel, count how many polygon edges a horizontal ray
    extending to +∞ crosses. Odd parity = inside.
    """
    inside = np.zeros_like(px, dtype=bool)
    n = len(verts)
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        # Edge straddles the pixel's y-line if one endpoint is above
        # and the other is below (strictly, to avoid counting horizontal
        # segments and to handle vertices on the ray cleanly using the
        # standard "is the lower endpoint inclusive, upper endpoint
        # exclusive" convention).
        crosses_y = ((ay > py) != (by > py))
        # x-coordinate of the intersection on the edge at height py
        # (only meaningful when crosses_y, but we compute everywhere
        # and mask).
        with np.errstate(divide="ignore", invalid="ignore"):
            x_int = ax + (py - ay) * (bx - ax) / (by - ay)
        inside ^= crosses_y & (px < x_int)
    return inside


def _signed_distance_to_polygon(xx, yy, verts):
    """Signed Euclidean distance from each (xx, yy) pixel to a polygon.

    Positive inside, negative outside. Works for non-convex polygons.

    verts: list of (x, y) tuples in any winding order.
    """
    edge_dists = []
    n = len(verts)
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        wx, wy = xx - ax, yy - ay
        seg_len_sq = ex * ex + ey * ey
        # Project P onto edge AB, clamp to [0, 1] for nearest-on-segment.
        t = np.clip((wx * ex + wy * ey) / max(seg_len_sq, 1e-12),
                    0.0, 1.0)
        cx = ax + t * ex
        cy = ay + t * ey
        edge_dists.append(np.hypot(xx - cx, yy - cy))
    dist = np.minimum.reduce(edge_dists)
    inside = _point_inside_polygon(xx, yy, verts)
    return np.where(inside, dist, -dist)


def _arrowhead_verts(size):
    """Return the four vertices of the arrowhead-with-notch polygon,
    oriented with the tip pointing UP (toward y=0, image-coordinate
    "up"). The shape mirrors templates/app.js drawArrow() - same
    proportions (tip:back ratio, notch depth, half-width at back) so
    on-trail direction arrows and clip arrows look the same.

    drawArrow's natural orientation has the tip pointing +X; we
    rotate -90° (counter-clockwise in image coords) so the tip
    points up. Vertices are listed CCW around the polygon (tip,
    right-back, notch, left-back), which keeps `inside` test logic
    independent of winding anyway.
    """
    s = float(size)
    cx = s / 2.0
    # drawArrow proportions, rotated to tip-up:
    #   tip   : (s*0.94, s/2)        →  (s/2,   s*0.06)
    #   t-back: (s*0.14, s/2 - s*0.42) →  (s*0.92, s*0.86)
    #   notch : (s*0.32, s/2)        →  (s/2,   s*0.68)
    #   b-back: (s*0.14, s/2 + s*0.42) →  (s*0.08, s*0.86)
    return [
        (cx,           s * 0.06),   # tip (top center)
        (s * 0.92,     s * 0.86),   # right-back corner
        (cx,           s * 0.68),   # back notch (above the back edge)
        (s * 0.08,     s * 0.86),   # left-back corner
    ]


def generate_sdf(shape, canvas, radius, inset):
    """Generate an SDF PNG of an upward-pointing arrowhead-with-notch.

    The arrowhead is `shape` pixels tall and wide, centered on a
    `canvas` pixel square, and the field follows MapLibre's glyph
    convention: 191 (0.75) at the visible edge, 255 two eighths of
    `radius` inside it, 0 six eighths of `radius` outside it. MapLibre
    assumes `radius` is 8 icon pixels, so a field with a larger radius
    renders every halo width and blur `radius / 8` times wider than
    nominal, and lets a halo reach that much further before the field
    clamps. The canvas must leave at least 0.75 * radius around the
    shape, or the clamp lands inside the icon box and a wide halo tints
    the whole box.

    `inset` (pixels) moves the visible edge inside the drawn polygon.
    The earlier field put the polygon edge at 128, so MapLibre's 0.75
    threshold ate half the old radius per side; the inset reproduces
    that visible shape, which is the one the maps were tuned with.

    Returns:
        PIL.Image (mode 'L', canvas x canvas).
    """
    margin = (canvas - shape) / 2.0
    verts = [(x + margin, y + margin) for x, y in _arrowhead_verts(shape)]

    # Pixel centres at (x+0.5, y+0.5)
    yy, xx = np.indices((canvas, canvas), dtype=np.float64)
    xx += 0.5
    yy += 0.5

    signed = _signed_distance_to_polygon(xx, yy, verts)

    out = 191.0 + (signed - inset) * (256.0 / radius)
    out = np.clip(out, 0, 255).astype(np.uint8)

    return Image.fromarray(out, mode="L")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))

    # Twice MapLibre's assumed radius (8 icon px): halos and blurs on
    # this icon render at twice their nominal width and can reach twice
    # as far, which the lift's yellow rim needs (templates/app.js,
    # clip-arrow-glow layers). The shape is the 16 px arrowhead the
    # maps were tuned with, on a 40 px canvas (12 px margin, at least
    # 0.75 * radius). The inset keeps the visible arrowhead at the size
    # the old field gave it: half its radius per side (1 px at 1x,
    # 1.5 px at 2x, as those fields differed).
    one_x = generate_sdf(shape=16, canvas=40, radius=16, inset=1.0)
    one_x.save(os.path.join(here, "clip-arrow.sdf.png"))
    print(f"Wrote clip-arrow.sdf.png ({one_x.size})")

    two_x = generate_sdf(shape=32, canvas=80, radius=32, inset=1.5)
    two_x.save(os.path.join(here, "clip-arrow.sdf@2x.png"))
    print(f"Wrote clip-arrow.sdf@2x.png ({two_x.size})")
