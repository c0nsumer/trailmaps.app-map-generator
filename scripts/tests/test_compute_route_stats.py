"""Tests for compute_route_stats.py — per-route distance and the
canonical-geometry guard.

The guard is the regression fence for a bug that has now happened
TWICE (once via shared_routes double-counting, once via the multi-mode
subway expansion): distance/elevation computed on geometry where a
route's ways appear more than once, inflating rider-facing stats.

Run from repo root:
    python -m pytest scripts/tests/test_compute_route_stats.py -v
Or as a script:
    python scripts/tests/test_compute_route_stats.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from compute_route_stats import compute_and_attach, compute_distances  # noqa: E402
from geodesy import haversine_m  # noqa: E402

# Two segments of route 100; the second is shared with route 200 and
# emitted once per parent (route_id 100 and route_id 200), matching
# build_geojson's one-feature-per-parent-relation shape.
_SEG_A = [[-83.44, 42.67], [-83.45, 42.68]]
_SEG_B = [[-83.45, 42.68], [-83.46, 42.68]]


def _feat(route_id, coords, **extra_props):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"route_id": route_id, "shared_routes": [route_id], **extra_props},
    }


def _fc():
    return {
        "type": "FeatureCollection",
        "features": [
            _feat("100", _SEG_A),
            _feat("100", _SEG_B, shared_routes=["100", "200"]),
            _feat("200", _SEG_B, shared_routes=["100", "200"]),
        ],
        "metadata": {
            "routes": {
                "100": {"name": "Big Loop", "colour": "red"},
                "200": {"name": "Connector", "colour": "blue"},
            }
        },
    }


def _line_m(coords):
    return sum(
        haversine_m(lon1, lat1, lon2, lat2)
        for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:])
    )


def test_compute_distances_counts_each_route_once():
    distances = compute_distances(_fc())
    assert distances["100"] == round(_line_m(_SEG_A) + _line_m(_SEG_B))
    assert distances["200"] == round(_line_m(_SEG_B))


def test_attach_writes_distance_and_strips_when_disabled():
    g = _fc()
    assert compute_and_attach(g, {"show_route_distance": True}, None)
    assert g["metadata"]["routes"]["100"]["distance_m"] > 0
    # Gate turned off: stale fields are stripped.
    assert compute_and_attach(g, {}, None)
    assert "distance_m" not in g["metadata"]["routes"]["100"]


@pytest.mark.parametrize("marker", ["isStub", "_subwayHostVariant"])
def test_refuses_subway_expanded_geometry(marker):
    # The multi-mode subway pass replaces a truncated host with one
    # full-length variant PER MODE, all carrying the host's route_id.
    # Stats computed on that output count the host once per mode — the
    # guard must refuse rather than silently inflate.
    g = _fc()
    g["features"].append(_feat("100", _SEG_B, **{marker: True, "mode": "summer"}))
    with pytest.raises(ValueError, match="canonical"):
        compute_and_attach(g, {"show_route_distance": True}, None)


if __name__ == "__main__":
    sys.exit(os.system(f"{sys.executable} -m pytest {os.path.abspath(__file__)} -v") >> 8)
