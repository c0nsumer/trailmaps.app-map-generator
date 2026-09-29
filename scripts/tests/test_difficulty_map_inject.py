"""Tests for the `color_by` / `default_labels` / `show_distance` injector defaults.

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


def test_show_distance_reaches_the_runtime():
    # Difficulty maps sum per-rating key distances at runtime, so the
    # build-time stats gate is emitted too.
    assert _config_obj(dict(BASE))["showDistance"] is False
    assert _config_obj(dict(BASE, show_distance=True))["showDistance"] is True


# ----- relation_colors on a difficulty map -----
#
# On a difficulty map a line color is a rating, so a route's color there
# is only ever the curator's explicit relation_colors opt-in for that
# relation's unrated ways. An OSM colour= tag must not leak in: a
# relation with cached OSM colour metadata but no override ends with no
# colour, the same "absent" shape osm_parser.relation_info uses when OSM
# never carried a colour tag.

STYLED_TRAILS = {
    "metadata": {
        "routes": {
            # Has both an OSM colour tag and a curator override: the
            # override should win.
            "1": {"name": "Overridden Trail", "colour": "#ff0000", "ref": "", "seasonal": ""},
            # Has an OSM colour tag but no override: colour must drop.
            "2": {"name": "Untouched Trail", "colour": "#00ff00", "ref": "", "seasonal": ""},
            # No OSM colour tag and no override: stays colourless.
            "3": {"name": "Plain Trail", "colour": None, "ref": "", "seasonal": ""},
        },
    },
    "features": [],
}


def test_relation_colors_honored_on_a_difficulty_map():
    config = dict(BASE, color_by="difficulty", relation_colors={1: "#0000ff"})
    obj_trails = json.loads(json.dumps(STYLED_TRAILS))  # deep copy per call
    out = inject_config_into_template("/*__CONFIG__*/", config, obj_trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    # The curator's override wins, not the OSM colour tag.
    assert routes["1"]["colour"] == "#0000ff"


def test_osm_colour_does_not_leak_in_on_a_difficulty_map():
    config = dict(BASE, color_by="difficulty", relation_colors={1: "#0000ff"})
    obj_trails = json.loads(json.dumps(STYLED_TRAILS))
    out = inject_config_into_template("/*__CONFIG__*/", config, obj_trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    # No override: the OSM colour tag must not survive injection.
    assert routes["2"]["colour"] is None
    assert routes["3"]["colour"] is None


def test_osm_colour_is_unchanged_on_a_routes_map():
    # color_by unset (defaults to "route"): OSM colours pass through
    # byte-identical, override or not.
    config = dict(BASE, relation_colors={1: "#0000ff"})
    obj_trails = json.loads(json.dumps(STYLED_TRAILS))
    out = inject_config_into_template("/*__CONFIG__*/", config, obj_trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    assert routes["1"]["colour"] == "#0000ff"  # override still applies
    assert routes["2"]["colour"] == "#00ff00"  # OSM colour untouched
    assert routes["3"]["colour"] is None


def test_custom_route_colour_is_untouched_on_a_difficulty_map():
    # Custom routes carry their own curator-set colour (event_mode /
    # config-defined, not an OSM tag) and skip the int-keyed override
    # loop entirely, so the difficulty-map colour drop must not touch
    # them either.
    trails = {
        "metadata": {
            "routes": {
                "custom-1": {
                    "name": "Custom Loop",
                    "colour": "#abcdef",
                    "isCustom": True,
                },
            },
        },
        "features": [],
    }
    config = dict(BASE, color_by="difficulty")
    out = inject_config_into_template("/*__CONFIG__*/", config, trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    assert routes["custom-1"]["colour"] == "#abcdef"
