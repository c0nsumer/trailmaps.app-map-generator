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


# ---------------------------------------------------------------------------
# Elevation: hysteresis accumulator, smoothing, no-data, 3DEP splicing
# ---------------------------------------------------------------------------

import compute_route_stats as crs  # noqa: E402
from compute_route_stats import (  # noqa: E402
    _chain_segments,
    _fetch_elevations_batched,
    _gain_loss_from_samples,
    _smooth_elevations,
    compute_elevations,
)


def test_chain_segments_reassembles_a_loop():
    # A closed loop delivered as three segments in arbitrary order and
    # direction (the shape OSM relations actually produce) must chain
    # into one continuous closed traversal — every hidden break makes
    # gain/loss diverge on the loop.
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


def test_loop_gain_equals_loss():
    # A closed loop climbing steeply (2 m steps) and descending gently
    # (0.4 m steps). The old per-delta threshold discarded every
    # descent delta (< 1 m), reporting loss = 0 for terrain that
    # descends 10 m; the hysteresis accumulator commits the descent as
    # it drifts past the band, so gain == loss on a loop.
    climb = [float(x) for x in range(0, 11, 2)]
    descent = [10 - 0.4 * i for i in range(1, 26)]
    gain, loss = _gain_loss_from_samples(climb + descent)
    assert gain == loss == 8  # true 10 less smoothing endpoint shave


def test_no_usable_samples_returns_none_not_zero():
    # All-NoData (e.g. non-US route) must be distinguishable from a
    # genuinely flat route.
    assert _gain_loss_from_samples([None, None, None]) is None
    # Valid samples separated by breaks never form a connected pair.
    assert _gain_loss_from_samples([5.0, None, 6.0]) is None
    # A genuinely flat route IS (0, 0).
    assert _gain_loss_from_samples([100.0, 100.0, 100.0]) == (0, 0)


def test_break_markers_survive_smoothing_and_block_deltas():
    # None is preserved and the window never blends across it. An
    # earlier version filled a lone None with the average of its
    # neighbors, bridging every segment break.
    out = _smooth_elevations([0.0, 10.0, None, 50.0, 60.0], 3)
    assert out[2] is None
    assert out[1] == 5.0  # avg(0, 10) — never reaches 50
    assert out[3] == 55.0  # avg(50, 60) — never reaches 10
    # Two plateaus 100 m apart across a break: no phantom gain.
    assert _gain_loss_from_samples([0.0, 0.0, 0.0, None, 100.0, 100.0, 100.0]) == (0, 0)


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_splice_places_samples_by_location_id(monkeypatch):
    # The service may omit a point and return the rest out of order.
    # Placement must key on locationId — an enumeration index would
    # shift every elevation after the gap onto the wrong coordinate.
    coords = [[-83.0, 42.0], [-83.001, 42.0], None, [-83.002, 42.0]]
    resp = _FakeResp(
        {
            "samples": [
                {"locationId": 2, "value": "300.0"},  # out of order
                {"locationId": 0, "value": "100.0"},
                # locationId 1 omitted entirely by the service
            ]
        }
    )
    monkeypatch.setattr(crs, "_last_api_call", 0.0)
    monkeypatch.setattr(crs.requests, "post", lambda *a, **k: resp)
    out = _fetch_elevations_batched(coords)
    # valid points 0,1,3 ↔ locationIds 0,1,2; break marker passes through
    assert out == [100.0, None, None, 300.0]


def _elev_fc():
    # ~1.1 km segment so _subsample_route yields multiple samples.
    seg = [[-83.0, 42.0], [-83.0, 42.01]]
    return (
        {
            "type": "FeatureCollection",
            "features": [_feat("100", seg)],
            "metadata": {"routes": {"100": {"name": "Loop", "colour": "red"}}},
        },
        [seg],
    )


def test_all_nodata_cached_as_marker_and_omitted(tmp_path, monkeypatch):
    g, coord_lines = _elev_fc()
    sampled = crs._subsample_route(coord_lines, crs.SAMPLE_INTERVAL_M, crs.MAX_SAMPLES_PER_ROUTE)
    resp = _FakeResp(
        {"samples": [{"locationId": i, "value": "NoData"} for i in range(len(sampled))]}
    )
    monkeypatch.setattr(crs, "_last_api_call", 0.0)
    monkeypatch.setattr(crs.requests, "post", lambda *a, **k: resp)
    out = compute_elevations(g, str(tmp_path))
    assert out == {}  # omitted, NOT (0, 0)
    cache_path = crs._elev_cache_path(str(tmp_path), "100", crs._hash_coords(sampled))
    with open(cache_path, encoding="utf-8") as f:
        cached = __import__("json").load(f)
    assert cached["elevation_gain_m"] is None  # no-data marker persisted


def test_no_data_cache_marker_skips_api(tmp_path, monkeypatch):
    g, coord_lines = _elev_fc()
    sampled = crs._subsample_route(coord_lines, crs.SAMPLE_INTERVAL_M, crs.MAX_SAMPLES_PER_ROUTE)
    cache_path = crs._elev_cache_path(str(tmp_path), "100", crs._hash_coords(sampled))
    os.makedirs(os.path.dirname(cache_path))
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write('{"elevation_gain_m": null, "elevation_loss_m": null, "samples": 5}')

    def _boom(*a, **k):
        raise AssertionError("API must not be called for a cached no-data route")

    monkeypatch.setattr(crs.requests, "post", _boom)
    assert compute_elevations(g, str(tmp_path)) == {}


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
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
