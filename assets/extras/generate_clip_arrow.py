#!/usr/bin/env python3
"""One-shot generator for the SDF clip-continuation arrowhead.

Run this only when the arrowhead shape needs to change. The output PNGs
(clip-arrow.sdf.png and clip-arrow.sdf@2x.png) are committed assets; the
build pipeline reads them via scripts/inject_clip_arrow.py and pastes them
into each theme's sprite atlas.

What this produces is a Signed Distance Field encoding of a filled triangle
arrowhead pointing UP (north) at 0° rotation. MapLibre interprets pixel
values such that 128 = the boundary of the shape, < 128 = outside, > 128 =
inside, with the unit being "distance from edge in pixels at the sprite's
native resolution." That lets MapLibre's `icon-color` cleanly tint the
shape at any size without rasterisation artefacts.

Implementation: analytic signed distance to a triangle. For each output
pixel we compute the actual Euclidean distance to the nearest triangle
edge (positive inside, negative outside) using the standard min-distance-
to-segment formula. No rasterisation, no scipy, no oversampling needed —
the result is exact to floating-point precision.
"""

import os

import numpy as np
from PIL import Image


def _signed_distance_to_triangle(xx, yy, verts):
    """Signed Euclidean distance from each (xx, yy) pixel to a triangle.

    Positive inside the triangle, negative outside. Implemented as
    min(distance-to-edge across the three edges) with sign flipped by an
    inside-outside test.

    verts: list of three (x, y) tuples in CCW or CW order — sign convention
    is normalised by the inside test, so winding doesn't matter.
    """
    px, py = xx, yy
    edge_dists = []
    for i in range(3):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % 3]
        # Vector from A to B and from A to P
        ex, ey = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        # Project onto edge, clamp to [0, 1]
        seg_len_sq = ex * ex + ey * ey
        t = np.clip((wx * ex + wy * ey) / seg_len_sq, 0.0, 1.0)
        # Closest point on edge, then distance
        cx = ax + t * ex
        cy = ay + t * ey
        edge_dists.append(np.hypot(px - cx, py - cy))
    dist = np.minimum.reduce(edge_dists)

    # Inside test using the cross-product sign trick (works for any winding).
    def side(ax, ay, bx, by, px, py):
        return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    s0 = side(*verts[0], *verts[1], px, py)
    s1 = side(*verts[1], *verts[2], px, py)
    s2 = side(*verts[2], *verts[0], px, py)
    inside = ((s0 >= 0) & (s1 >= 0) & (s2 >= 0)) | \
             ((s0 <= 0) & (s1 <= 0) & (s2 <= 0))
    return np.where(inside, dist, -dist)


def generate_sdf(size, radius=8):
    """Generate an SDF PNG of an upward-pointing arrowhead triangle.

    Args:
        size: Output edge length in pixels (square image).
        radius: SDF falloff radius in output pixels. MapLibre's standard
            buffer is 8 — at the boundary pixel value is 128, and ±`radius`
            pixels away the value reaches 0 or 255.

    Returns:
        PIL.Image (mode 'L', size×size).
    """
    inset = size / 8.0  # leaves room for SDF gradient
    verts = [
        (size / 2.0, inset),                # top tip
        (inset, size - inset),              # bottom-left
        (size - inset, size - inset),       # bottom-right
    ]

    # Pixel centres at (x+0.5, y+0.5)
    yy, xx = np.indices((size, size), dtype=np.float64)
    xx += 0.5
    yy += 0.5

    signed = _signed_distance_to_triangle(xx, yy, verts)

    # Map to MapLibre's SDF byte range: 128 = boundary, +radius → 255,
    # -radius → 0, clamped at the extremes.
    out = 128.0 + signed * (127.0 / radius)
    out = np.clip(out, 0, 255).astype(np.uint8)

    return Image.fromarray(out, mode="L")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))

    # 1x: 16×16 — close to the size of existing sprites (most are 19×19)
    one_x = generate_sdf(16, radius=6)
    one_x.save(os.path.join(here, "clip-arrow.sdf.png"))
    print(f"Wrote clip-arrow.sdf.png ({one_x.size})")

    # 2x: 32×32 retina
    two_x = generate_sdf(32, radius=8)
    two_x.save(os.path.join(here, "clip-arrow.sdf@2x.png"))
    print(f"Wrote clip-arrow.sdf@2x.png ({two_x.size})")
