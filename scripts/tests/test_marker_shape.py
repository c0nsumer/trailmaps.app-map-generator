"""CONFIG.markerShape emission (marker_shape config key).

Trail-marker chip shape: "box" (default), "pill", "circle", or
"diamond". Plain scalar
pass-through via CONFIG_SPEC, same as marker_color/marker_text_color/
marker_border_color.

Run from repo root:
    python -m pytest scripts/tests/test_marker_shape.py -v
"""

from conftest import MINIMAL_CONFIG, inject_config


def test_marker_shape_defaults_to_box():
    assert inject_config(dict(MINIMAL_CONFIG))["markerShape"] == "box"


def test_marker_shape_pill_passed_through():
    assert inject_config({**MINIMAL_CONFIG, "marker_shape": "pill"})["markerShape"] == "pill"


def test_marker_shape_circle_passed_through():
    assert inject_config({**MINIMAL_CONFIG, "marker_shape": "circle"})["markerShape"] == "circle"


def test_marker_shape_diamond_passed_through():
    assert inject_config({**MINIMAL_CONFIG, "marker_shape": "diamond"})["markerShape"] == "diamond"
