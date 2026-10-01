"""Tests for `default_visible` layer-toggle defaulting.

Unset (omitted or null) used to mean "everything off." The owner
decided unset should instead mean a sensible first-visit set (markers,
trailheads, hubs, parking, toilets, water, repair stations, direction
arrows), so a rider discovers those layers without opening Options.
`[]` stays the explicit bare-map opt-out; `"all"` and an explicit list
keep their existing meanings.

Run from repo root:
    python -m pytest scripts/tests/test_default_visible.py -v
"""

import json
import os
import re
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_inject import inject_config_into_template
from validate_config import DEFAULT_FIRST_VISIT_LAYERS, DEFAULT_VISIBLE_LAYERS

TRAILS = {"metadata": {"routes": {}}, "features": []}

# Smallest config inject_config_into_template accepts: every CONFIG_SPEC
# entry with a None default is a required read.
BASE = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
}


def _config_obj(config):
    """Run the injector and parse the CONFIG object back out."""
    out = inject_config_into_template("/*__CONFIG__*/", config, dict(TRAILS))
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def test_default_first_visit_layers_is_subset_of_default_visible_layers():
    # Guards against DEFAULT_FIRST_VISIT_LAYERS drifting to name a
    # layer the runtime / validator doesn't otherwise recognize.
    assert DEFAULT_FIRST_VISIT_LAYERS <= DEFAULT_VISIBLE_LAYERS


def test_unset_default_visible_yields_first_visit_set():
    config = dict(BASE)
    obj = _config_obj(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_FIRST_VISIT_LAYERS)


def test_null_default_visible_yields_first_visit_set():
    config = dict(BASE, default_visible=None)
    obj = _config_obj(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_FIRST_VISIT_LAYERS)


def test_empty_list_default_visible_stays_bare_map():
    config = dict(BASE, default_visible=[])
    obj = _config_obj(config)
    assert obj["defaultVisible"] == []


def test_all_default_visible_expands_to_every_layer():
    config = dict(BASE, default_visible="all")
    obj = _config_obj(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_VISIBLE_LAYERS)


def test_explicit_list_default_visible_passes_through():
    config = dict(BASE, default_visible=["parking", "difficulty"])
    obj = _config_obj(config)
    assert obj["defaultVisible"] == ["parking", "difficulty"]
