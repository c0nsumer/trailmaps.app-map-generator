"""Shared geodesy + geometry-key primitives for the build pipeline.

Single source of truth for the Earth-radius constant, the
great-circle distance formula, and the two hashable-key helpers the
lane-assignment pipeline sorts by (natural_key, coord_key).
Previously duplicated across fetch_pois.py / compute_route_stats.py
(haversine) and route_order.py / parallel_routes.py /
corridor_baselines.py (keys); consolidating here means one place to
tune - and, for the keys, one exact output for every consumer, which
matters because lane order depends on it.

The runtime (templates/app.js) carries its own haversine because
JavaScript and Python don't share modules. It uses the WGS84
equatorial radius (6378137 m) rather than the mean radius (6371000)
this module uses; the two differ by ~0.1%, which is below the
accuracy floor of any trail-scale measurement we care about.
"""

import math
import re

# Mean Earth radius in meters. Matches the convention used by every
# Python script in the build pipeline. Off by ~0.1% from the WGS84
# equatorial radius (templates/app.js:EARTH_RADIUS_M = 6378137) - the
# inconsistency is intentional and irrelevant at trail scales.
EARTH_R_M = 6371000.0


def haversine_m(lng1, lat1, lng2, lat2):
    """Great-circle distance between two (lng, lat) points, in meters.

    Standard haversine formula; accuracy ~0.5% over distances up to
    a few thousand km, well within the trail-scale tolerance the
    framework cares about.
    """
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return EARTH_R_M * 2 * math.asin(math.sqrt(a))


def point_to_segment_m(plng, plat, alng, alat, blng, blat):
    """Distance in meters from a point to the segment AB.

    Mirrors ``pointToSegmentDistance`` in templates/app.js. Projects onto a
    local equirectangular plane centered on the point (longitude scaled by
    cos(lat)) and solves in that plane, which is accurate well past the
    few-hundred-meter range these checks use and avoids the cost of a proper
    cross-track formula.

    A vertex-only distance would be wrong here in a way that matters: a long
    straight way carries very few vertices, so a point lying exactly ON such a
    way can sit hundreds of meters from its nearest vertex. Rail trails and
    road segments are routinely drawn that way.
    """
    m_per_deg_lat = 110540.0
    m_per_deg_lng = 111320.0 * math.cos(math.radians(plat))
    ax = (alng - plng) * m_per_deg_lng
    ay = (alat - plat) * m_per_deg_lat
    bx = (blng - plng) * m_per_deg_lng
    by = (blat - plat) * m_per_deg_lat
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        # Degenerate segment: fall back to the endpoint distance.
        return math.hypot(ax, ay)
    # Projection parameter of the origin (the point) onto AB, clamped to the
    # segment so the result is a distance to the SEGMENT, not to its line.
    t = -(ax * dx + ay * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(cx, cy)


def point_to_polyline_m(plng, plat, line, stop_below=None):
    """Shortest distance in meters from a point to a polyline.

    ``line`` is a sequence of [lng, lat] pairs. ``stop_below`` short-circuits
    as soon as a segment lands within that distance, which is what keeps an
    "is this anywhere near the network?" scan cheap for the common case where
    the answer is yes.

    Returns None for a line with fewer than two points.
    """
    best = None
    for i in range(1, len(line)):
        a = line[i - 1]
        b = line[i]
        try:
            d = point_to_segment_m(plng, plat, a[0], a[1], b[0], b[1])
        except (TypeError, IndexError):
            continue
        if best is None or d < best:
            best = d
        if stop_below is not None and best <= stop_below:
            return best
    return best


def natural_key(s):
    """Numeric-aware natural-sort key matching app.js ``ROUTE_ID_COMPARE``.

    Split route ID into runs of digits / non-digits and emit each as
    a ``(type_marker, value)`` pair: ``(0, int)`` for digit runs,
    ``(1, str)`` for non-digit runs. The type marker ensures sorting
    works on MIXED-TYPE id lists (e.g. OSM relation ids like
    ``"12345678"`` alongside custom event-mode ids like
    ``"event_stage_1"``) - without it, Python 3 raises
    ``TypeError`` when comparing ``int`` with ``str`` in tuple
    element-wise comparison.

    Sort behavior:
      ``"1"`` < ``"2"`` < ``"10"``      (numeric order within digit runs)
      ``"123"`` < ``"foo"``             (digit runs sort before strings)
      ``"foo123"`` < ``"foo456"``       (numeric order within prefix-grouped digits)

    Every lane-assignment consumer (route_order, parallel_routes,
    corridor_baselines) MUST use this exact implementation: lane order
    depends on the key's output, so two drifting copies would assign
    the same route different lanes in different passes.
    """
    s = str(s)
    parts = re.split(r"(\d+)", s)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p)


def coord_key(coord, precision=7):
    """Hashable key for a [lon, lat] coordinate. Round to ~1 cm to
    absorb float-equality wobble at junction nodes. Shared by every
    adjacency/transition detector so they agree on what "the same
    node" means."""
    return (round(coord[0], precision), round(coord[1], precision))
