"""Tests for the `color_by` / `default_labels` / `show_distance` injector defaults.

`color_by` defaults to `"route"` (a routes map). On a difficulty map
(`color_by: difficulty`) the trail name is the only name there is, so an
unset `default_labels` shows it on a first visit instead of the routes-map
default of `"none"`. An explicit `default_labels` always wins.

Run from repo root:
    python -m pytest scripts/tests/test_difficulty_map_inject.py -v
"""

import copy

from conftest import MINIMAL_CONFIG, inject_config


def test_color_by_defaults_to_route():
    obj = inject_config(dict(MINIMAL_CONFIG))
    assert obj["colorBy"] == "route"


def test_default_labels_stays_none_on_a_routes_map():
    obj = inject_config(dict(MINIMAL_CONFIG))
    assert obj["defaultLabels"] == "none"


def test_default_labels_becomes_trails_on_a_difficulty_map():
    config = dict(MINIMAL_CONFIG, color_by="difficulty")
    obj = inject_config(config)
    assert obj["defaultLabels"] == "trails"
    assert obj["colorBy"] == "difficulty"


def test_explicit_default_labels_is_honored_on_a_difficulty_map():
    config = dict(MINIMAL_CONFIG, color_by="difficulty", default_labels="none")
    obj = inject_config(config)
    assert obj["defaultLabels"] == "none"


def test_show_distance_reaches_the_runtime():
    # Difficulty maps sum per-rating key distances at runtime, so the
    # build-time stats gate is emitted too.
    assert inject_config(dict(MINIMAL_CONFIG))["showDistance"] is True
    assert inject_config(dict(MINIMAL_CONFIG, show_distance=False))["showDistance"] is False


# ----- relation_colors and OSM colour on a difficulty map -----

STYLED_TRAILS = {
    "metadata": {
        "routes": {
            "1": {"name": "Overridden Trail", "colour": "#ff0000", "ref": "", "seasonal": ""},
            "2": {"name": "Untouched Trail", "colour": "#00ff00", "ref": "", "seasonal": ""},
            "3": {"name": "Plain Trail", "colour": None, "ref": "", "seasonal": ""},
        },
    },
    "features": [],
}


def _routes_of(config, trails=STYLED_TRAILS):
    # Deep copy: the injector mutates the metadata it is given.
    return inject_config(config, copy.deepcopy(trails))["routes"]


def test_relation_color_overrides_osm_colour_on_a_route_mode_relation():
    config = dict(
        MINIMAL_CONFIG, color_by="difficulty", color_by_route=[1], relation_colors={1: "#0000ff"}
    )
    routes = _routes_of(config)
    assert routes["1"]["colour"] == "#0000ff"
    assert routes["1"]["colorBy"] == "route"


def test_osm_colour_passes_through_on_a_difficulty_map():
    config = dict(MINIMAL_CONFIG, color_by="difficulty", relation_colors={1: "#0000ff"})
    routes = _routes_of(config)
    assert routes["2"]["colour"] == "#00ff00"
    assert routes["3"]["colour"] is None


def test_osm_colour_passes_through_on_a_routes_map():
    routes = _routes_of(dict(MINIMAL_CONFIG, relation_colors={1: "#0000ff"}))
    assert routes["1"]["colour"] == "#0000ff"
    assert routes["2"]["colour"] == "#00ff00"
    assert routes["3"]["colour"] is None


def test_custom_route_colour_passes_through_on_a_difficulty_map():
    trails = {
        "metadata": {
            "routes": {"custom-1": {"name": "Custom Loop", "colour": "#abcdef", "isCustom": True}},
        },
        "features": [],
    }
    routes = _routes_of(dict(MINIMAL_CONFIG, color_by="difficulty"), trails)
    assert routes["custom-1"]["colour"] == "#abcdef"


# ----- per-route colorBy stamp -----


def _config_obj_with_routes(config, routes, expansions=None):
    trails = {
        "metadata": {"routes": routes, "super_relation_expansions": expansions or {}},
        "features": [],
    }
    return inject_config(config, trails)


def _routes():
    return {
        "1": {"name": "A", "colour": "#111111"},
        "2": {"name": "B"},
        "3": {"name": "C"},
        "my-route": {"name": "Mine", "isCustom": True},
    }


def test_every_route_is_stamped_with_the_default_mode():
    obj = _config_obj_with_routes(dict(MINIMAL_CONFIG), _routes())
    assert {r["colorBy"] for r in obj["routes"].values()} == {"route"}
    obj = _config_obj_with_routes(dict(MINIMAL_CONFIG, color_by="difficulty"), _routes())
    assert {r["colorBy"] for r in obj["routes"].values()} == {"difficulty"}


def test_color_by_route_list_overrides_the_default_including_custom_routes():
    config = dict(MINIMAL_CONFIG, color_by="difficulty", color_by_route=[1, "my-route"])
    modes = {k: r["colorBy"] for k, r in _config_obj_with_routes(config, _routes())["routes"].items()}
    assert modes == {"1": "route", "2": "difficulty", "3": "difficulty", "my-route": "route"}


def test_super_relation_in_a_list_fans_out_and_a_listed_leaf_wins():
    config = dict(MINIMAL_CONFIG, color_by_difficulty=[99, 3], color_by_route=[3])
    routes = _routes()
    obj = _config_obj_with_routes(config, routes, {"99": ["1", "2", "3"]})
    modes = {k: r["colorBy"] for k, r in obj["routes"].items()}
    assert modes["1"] == modes["2"] == "difficulty"
    # 3 is listed directly in color_by_route, which beats the fan-out.
    assert modes["3"] == "route"
    assert modes["my-route"] == "route"


def test_osm_colour_is_kept_in_difficulty_default_for_route_mode_relation():
    config = dict(MINIMAL_CONFIG, color_by="difficulty", color_by_route=[1])
    obj = _config_obj_with_routes(config, _routes())
    assert obj["routes"]["1"]["colour"] == "#111111"


def test_default_labels_follows_whether_any_route_is_in_route_mode():
    routes = _routes()
    assert _config_obj_with_routes(dict(MINIMAL_CONFIG, color_by="difficulty"), routes)[
        "defaultLabels"
    ] == "trails"
    config = dict(MINIMAL_CONFIG, color_by="difficulty", color_by_route=[1])
    assert _config_obj_with_routes(config, routes)["defaultLabels"] == "none"


def test_default_labels_reads_routes_on_an_event_map():
    # Event mode no longer locks the label mode, so an unset default keeps
    # the course named on a first visit, as the lock did.
    config = dict(MINIMAL_CONFIG, relations=[1, 2], event_mode={"featured": [1]})
    assert _config_obj_with_routes(config, _routes())["defaultLabels"] == "routes"


def test_explicit_default_labels_survives_on_an_event_map():
    config = dict(MINIMAL_CONFIG, relations=[1, 2], event_mode={"featured": [1]}, default_labels="trails")
    assert _config_obj_with_routes(config, _routes())["defaultLabels"] == "trails"
