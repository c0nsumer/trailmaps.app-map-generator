"""Shared geodesy + geometry-key primitives for the build pipeline.

Single source of truth for the Earth-radius constant, the
great-circle distance formula, and the two hashable-key helpers the
lane-assignment pipeline sorts by (natural_key, coord_key).
Previously duplicated across fetch_pois.py / compute_route_stats.py
(haversine) and route_order.py / parallel_routes.py /
corridor_baselines.py (keys); consolidating here means one place to
tune — and, for the keys, one exact output for every consumer, which
matters because lane order depends on it.

The runtime (templates/app.js) carries its own haversine because
JavaScript and Python don't share modules. It uses the WGS84
equatorial radius (6378137 m) rather than the mean radius (6371000)
this module uses; the two differ by ~0.1%, which is below the
accuracy floor of any trail-scale measurement we care about.
"""

import math
import re

# Mean Earth radius in metres. Matches the convention used by every
# Python script in the build pipeline. Off by ~0.1% from the WGS84
# equatorial radius (templates/app.js:EARTH_RADIUS_M = 6378137) — the
# inconsistency is intentional and irrelevant at trail scales.
EARTH_R_M = 6371000.0


def haversine_m(lng1, lat1, lng2, lat2):
    """Great-circle distance between two (lng, lat) points, in metres.

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


def natural_key(s):
    """Numeric-aware natural-sort key matching app.js ``ROUTE_ID_COMPARE``.

    Split route ID into runs of digits / non-digits and emit each as
    a ``(type_marker, value)`` pair: ``(0, int)`` for digit runs,
    ``(1, str)`` for non-digit runs. The type marker ensures sorting
    works on MIXED-TYPE id lists (e.g. OSM relation ids like
    ``"12345678"`` alongside custom event-mode ids like
    ``"event_stage_1"``) — without it, Python 3 raises
    ``TypeError`` when comparing ``int`` with ``str`` in tuple
    element-wise comparison.

    Sort behaviour:
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
