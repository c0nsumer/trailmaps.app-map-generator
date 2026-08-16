"""CONFIG.markerShape emission (marker_shape config key).

Trail-marker chip shape: "box" (default), "pill", "circle", or
"diamond". Plain scalar
pass-through via CONFIG_SPEC, same as marker_color/marker_text_color/
marker_border_color.

Run from repo root:
    python -m pytest scripts/tests/test_marker_shape.py -v
"""

import json
import os
import re
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_inject import inject_config_into_template  # noqa: E402

TRAILS = {"metadata": {"routes": {}}, "features": []}

BASE = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
    "center": [0, 0],
}


def _config_obj(config):
    out = inject_config_into_template("/*__CONFIG__*/", config, dict(TRAILS))
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def test_marker_shape_defaults_to_box():
    assert _config_obj(dict(BASE))["markerShape"] == "box"


def test_marker_shape_pill_passed_through():
    assert _config_obj({**BASE, "marker_shape": "pill"})["markerShape"] == "pill"


def test_marker_shape_circle_passed_through():
    assert _config_obj({**BASE, "marker_shape": "circle"})["markerShape"] == "circle"


def test_marker_shape_diamond_passed_through():
    assert _config_obj({**BASE, "marker_shape": "diamond"})["markerShape"] == "diamond"
