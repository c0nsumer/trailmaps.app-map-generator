"""Tests for compute_route_stats.py - per-route distance.

The distance tests fence a bug that has happened before: distance
computed on geometry where a route's ways appear more than once,
inflating rider-facing stats.

Run from repo root:
    python -m pytest scripts/tests/test_compute_route_stats.py -v
"""

from compute_route_stats import (
    _chain_segments,
    compute_and_attach,
    compute_distances,
)
from geodesy import haversine_m

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


def test_attach_writes_distance_only_when_enabled():
    g = _fc()
    assert not compute_and_attach(g, {})
    assert "distance_m" not in g["metadata"]["routes"]["100"]
    assert compute_and_attach(g, {"show_distance": True})
    assert g["metadata"]["routes"]["100"]["distance_m"] > 0


def test_chain_segments_reassembles_a_loop():
    # A closed loop delivered as three segments in arbitrary order and
    # direction (the shape OSM relations actually produce) must chain
    # into one continuous closed traversal.
    a, b, c, d = [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]
    chains = _chain_segments(
        [
            [a, b],
            [c, b],  # reversed relative to traversal
            [c, d, a],
        ]
    )
    assert chains == [[a, b, c, d, a]]


def test_chain_segments_keeps_disconnected_apart_and_drops_degenerate():
    a, b = [0.0, 0.0], [1.0, 0.0]
    x, y = [5.0, 5.0], [6.0, 5.0]
    chains = _chain_segments([[a, b], [[9.0, 9.0]], [x, y]])
    assert chains == [[a, b], [x, y]]  # 1 cm apart is NOT a join; 1-pt seg dropped
