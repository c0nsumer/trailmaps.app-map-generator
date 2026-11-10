"""compute_bbox_from_trails sizes the map margin from the trail extent.

The margin is 3% of the larger extent, floored at 0.001 degrees and capped
at 0.01 (~1 km), so a huge system does not pull in distant terrain.
"""

import pytest

from build import compute_bbox_from_trails


def _fc(*points):
    return {"features": [{"geometry": {"coordinates": [list(p) for p in points]}}]}


def test_margin_is_a_fraction_of_the_extent():
    # Extent 0.1 -> 0.003 margin, between the floor and the cap.
    assert compute_bbox_from_trails(_fc((0, 0), (0.1, 0.05))) == [-0.003, -0.003, 0.103, 0.053]


def test_margin_is_capped_for_a_large_system():
    # Extent 1.0 would give 0.03; the cap holds it at 0.01.
    assert compute_bbox_from_trails(_fc((0, 0), (1, 0.5))) == [-0.01, -0.01, 1.01, 0.51]


def test_margin_has_a_floor_for_a_tiny_system():
    assert compute_bbox_from_trails(_fc((0, 0), (0.001, 0.001))) == [-0.001, -0.001, 0.002, 0.002]


def test_no_geometry_is_an_error():
    with pytest.raises(ValueError):
        compute_bbox_from_trails({"features": []})
