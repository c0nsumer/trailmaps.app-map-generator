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
# The injector no longer drops OSM colour= on a difficulty default: a
# route-mode relation honors it, and the runtime ignores the colour of a
# difficulty-mode relation through its colorBy stamp.

STYLED_TRAILS = {
    "metadata": {
        "routes": {
            # Has both an OSM colour tag and a curator override: the
            # override should win.
            "1": {"name": "Overridden Trail", "colour": "#ff0000", "ref": "", "seasonal": ""},
            # Has an OSM colour tag but no override: colour passes through.
            "2": {"name": "Untouched Trail", "colour": "#00ff00", "ref": "", "seasonal": ""},
            # No OSM colour tag and no override: stays colourless.
            "3": {"name": "Plain Trail", "colour": None, "ref": "", "seasonal": ""},
        },
    },
    "features": [],
}


def test_relation_colors_override_a_route_mode_relation_on_a_difficulty_default():
    config = dict(
        BASE, color_by="difficulty", color_by_route=[1], relation_colors={1: "#0000ff"}
    )
    obj_trails = json.loads(json.dumps(STYLED_TRAILS))  # deep copy per call
    out = inject_config_into_template("/*__CONFIG__*/", config, obj_trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    # The curator's override wins over the OSM colour tag, and the
    # relation resolves to route mode, where the runtime reads it.
    assert routes["1"]["colour"] == "#0000ff"
    assert routes["1"]["colorBy"] == "route"


def test_osm_colour_is_no_longer_dropped_on_a_difficulty_default_map():
    # The runtime decides per relation (CONFIG.routes[id].colorBy) whether
    # the colour is read, so the injector passes OSM colours through.
    config = dict(BASE, color_by="difficulty", relation_colors={1: "#0000ff"})
    obj_trails = json.loads(json.dumps(STYLED_TRAILS))
    out = inject_config_into_template("/*__CONFIG__*/", config, obj_trails)
    routes = json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))["routes"]

    assert routes["2"]["colour"] == "#00ff00"
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


# ----- per-route colorBy stamp -----


def _config_obj_with_routes(config, routes, expansions=None):
    trails = {
        "metadata": {"routes": routes, "super_relation_expansions": expansions or {}},
        "features": [],
    }
    out = inject_config_into_template("/*__CONFIG__*/", config, trails)
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def _routes():
    return {
        "1": {"name": "A", "colour": "#111111"},
        "2": {"name": "B"},
        "3": {"name": "C"},
        "my-route": {"name": "Mine", "isCustom": True},
    }


def test_every_route_is_stamped_with_the_default_mode():
    obj = _config_obj_with_routes(dict(BASE), _routes())
    assert {r["colorBy"] for r in obj["routes"].values()} == {"route"}
    obj = _config_obj_with_routes(dict(BASE, color_by="difficulty"), _routes())
    assert {r["colorBy"] for r in obj["routes"].values()} == {"difficulty"}


def test_color_by_route_list_overrides_the_default_including_custom_routes():
    config = dict(BASE, color_by="difficulty", color_by_route=[1, "my-route"])
    modes = {k: r["colorBy"] for k, r in _config_obj_with_routes(config, _routes())["routes"].items()}
    assert modes == {"1": "route", "2": "difficulty", "3": "difficulty", "my-route": "route"}


def test_super_relation_in_a_list_fans_out_and_a_listed_leaf_wins():
    config = dict(BASE, color_by_difficulty=[99, 3], color_by_route=[3])
    routes = _routes()
    obj = _config_obj_with_routes(config, routes, {"99": ["1", "2", "3"]})
    modes = {k: r["colorBy"] for k, r in obj["routes"].items()}
    assert modes["1"] == modes["2"] == "difficulty"
    # 3 is listed directly in color_by_route, which beats the fan-out.
    assert modes["3"] == "route"
    assert modes["my-route"] == "route"


def test_osm_colour_is_kept_in_difficulty_default_for_route_mode_relation():
    config = dict(BASE, color_by="difficulty", color_by_route=[1])
    obj = _config_obj_with_routes(config, _routes())
    assert obj["routes"]["1"]["colour"] == "#111111"


def test_default_labels_follows_whether_any_route_is_in_route_mode():
    routes = _routes()
    assert _config_obj_with_routes(dict(BASE, color_by="difficulty"), routes)[
        "defaultLabels"
    ] == "trails"
    config = dict(BASE, color_by="difficulty", color_by_route=[1])
    assert _config_obj_with_routes(config, routes)["defaultLabels"] == "none"


def test_default_labels_reads_routes_on_an_event_map():
    # Event mode no longer locks the label mode, so an unset default keeps
    # the course named on a first visit, as the lock did.
    config = dict(BASE, relations=[1, 2], event_mode={"featured": [1]})
    assert _config_obj_with_routes(config, _routes())["defaultLabels"] == "routes"


def test_explicit_default_labels_survives_on_an_event_map():
    config = dict(BASE, relations=[1, 2], event_mode={"featured": [1]}, default_labels="trails")
    assert _config_obj_with_routes(config, _routes())["defaultLabels"] == "trails"
