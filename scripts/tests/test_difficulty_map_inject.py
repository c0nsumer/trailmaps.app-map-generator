"""Tests for the `color_by` / `default_labels` injector defaults.

`color_by` defaults to `"route"` (a routes map). On a difficulty map
(`color_by: difficulty`) the trail name is the only name there is, so an
unset `default_labels` shows it on a first visit instead of the routes-map
default of `"none"`. An explicit `default_labels` always wins.

Run from repo root:
    python -m pytest scripts/tests/test_difficulty_map_inject.py -v
"""

import json
import os
import re
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_inject import inject_config_into_template  # noqa: E402

TRAILS = {"metadata": {"routes": {}}, "features": []}

# Smallest config inject_config_into_template accepts: every CONFIG_SPEC
# entry with a None default is a required read.
BASE = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
    "center": [0, 0],
}


def _config_obj(config):
    """Run the injector and parse the CONFIG object back out."""
    out = inject_config_into_template("/*__CONFIG__*/", config, dict(TRAILS))
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def test_color_by_defaults_to_route():
    obj = _config_obj(dict(BASE))
    assert obj["colorBy"] == "route"


def test_default_labels_stays_none_on_a_routes_map():
    obj = _config_obj(dict(BASE))
    assert obj["defaultLabels"] == "none"


def test_default_labels_becomes_trails_on_a_difficulty_map():
    config = dict(BASE, color_by="difficulty")
    obj = _config_obj(config)
    assert obj["defaultLabels"] == "trails"
    assert obj["colorBy"] == "difficulty"


def test_explicit_default_labels_is_honored_on_a_difficulty_map():
    config = dict(BASE, color_by="difficulty", default_labels="none")
    obj = _config_obj(config)
    assert obj["defaultLabels"] == "none"
