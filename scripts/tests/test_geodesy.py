"""Tests for the shared geodesy / sort-key primitives."""



import pytest
from geodesy import haversine_m, natural_key, point_to_polyline_m, point_to_segment_m


def test_natural_key_numeric_aware():
    """Numeric runs sort numerically, not lexically."""
    keys = ["10", "2", "100", "1"]
    assert sorted(keys, key=natural_key) == ["1", "2", "10", "100"]


def test_natural_key_mixed_numeric_and_string_ids():
    """Regression: route lists with BOTH numeric OSM relation ids AND
    custom (non-numeric) event-mode ids must sort without raising
    ``TypeError: '<' not supported between instances of 'str' and 'int'``.

    Triggered in production on sheldensbigbang where event_mode emits
    inline custom-id routes alongside OSM relation ids.
    """
    keys = ["12345678", "event_stage_1", "98765432", "intro", "5"]
    # Must not raise; order is implementation-defined but stable
    out = sorted(keys, key=natural_key)
    # Within all-numeric IDs the numeric ordering still holds:
    numeric_only = [k for k in out if k.isdigit()]
    assert numeric_only == ["5", "12345678", "98765432"]


# --- haversine_m ----------------------------------------------------------
# Known answers come from a second method (3D unit-vector dot product with
# R = 6371000 m), not from haversine_m itself.

def test_haversine_one_degree_of_latitude():
    # pi/180 * 6371000. The WGS84 equatorial radius would give 111319.5.
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111194.9266, abs=0.01)


def test_haversine_uses_each_points_own_latitude():
    # Different latitudes plus a longitude change: cos(lat1) * cos(lat2)
    # differs from cos(lat1) squared, so a copy-paste slip shows here.
    assert haversine_m(10, 0, 11, 60) == pytest.approx(6672255.81, abs=1.0)


def test_haversine_at_trail_scale():
    # 0.01 degrees of longitude at 46.5 N, the Keweenaw: ~765 m.
    assert haversine_m(-87.6, 46.5, -87.59, 46.5) == pytest.approx(765.415, abs=0.01)


# --- point_to_segment_m ---------------------------------------------------

def test_point_past_the_segment_end_measures_to_the_endpoint():
    # 0.001 degrees of longitude at the equator is 111.32 m. Projecting onto
    # the infinite line instead would report 0.
    assert point_to_segment_m(0.002, 0.0, 0.0, 0.0, 0.001, 0.0) == pytest.approx(111.32, abs=0.01)


def test_point_before_the_segment_start_measures_to_the_endpoint():
    assert point_to_segment_m(-0.001, 0.0, 0.0, 0.0, 0.001, 0.0) == pytest.approx(111.32, abs=0.01)


def test_longitude_is_scaled_by_cos_of_latitude():
    # At 60 N a degree of longitude is half as long: 0.001 deg -> 55.66 m,
    # not the 111.32 m of the equator.
    assert point_to_segment_m(0.002, 60.0, 0.0, 60.0, 0.001, 60.0) == pytest.approx(55.66, abs=0.01)


def test_point_beside_the_segment_measures_the_perpendicular():
    # 0.0001 degrees of latitude is 11.054 m.
    assert point_to_segment_m(0.0005, 60.0001, 0.0, 60.0, 0.001, 60.0) == pytest.approx(
        11.054, abs=0.01)


# --- point_to_polyline_m --------------------------------------------------

_NEAR_THEN_NEARER = [[0.0001, -0.001], [0.0001, 0.001], [0.00005, -0.0001]]


def test_stop_below_returns_once_a_segment_is_exactly_that_close():
    # The first segment passes at 11.132 m; the second passes closer. A
    # threshold equal to the first distance means "near enough", so the scan
    # stops there. The scan must stop on equality, not only below it.
    first = point_to_segment_m(0.0, 0.0, *_NEAR_THEN_NEARER[0], *_NEAR_THEN_NEARER[1])
    assert first == pytest.approx(11.132, abs=0.001)
    assert point_to_polyline_m(0.0, 0.0, _NEAR_THEN_NEARER, stop_below=first) == first
    assert point_to_polyline_m(0.0, 0.0, _NEAR_THEN_NEARER) < first
