"""Tests for the `color_by` / `default_labels` / `show_distance` injector defaults,
and the ITRS-era difficulty keys an IMBA map emits.

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


# ----- difficulty_scale: per-scale rating scans -----

def _rated(**props):
    return {"type": "Feature", "properties": props, "geometry": None}


MIXED_TRAILS = {
    "metadata": {"routes": {}},
    "features": [
        _rated(imba_difficulty="2", itrs_difficulty="3"),
        _rated(imba_difficulty="0", freeride=True, itrs_difficulty=""),
        _rated(imba_difficulty="9", itrs_difficulty="7"),
        _rated(imba_difficulty="", itrs_difficulty="1", bike_only=True),
    ],
}


def test_bike_only_reaches_config_on_itrs_maps_only():
    trails = copy.deepcopy(MIXED_TRAILS)
    assert inject_config(dict(MINIMAL_CONFIG), trails)["hasBikeOnlyTrails"] is False
    trails = copy.deepcopy(MIXED_TRAILS)
    itrs = inject_config(dict(MINIMAL_CONFIG, difficulty_scale="itrs"), trails)
    assert itrs["hasBikeOnlyTrails"] is True


def test_difficulty_scale_defaults_to_imba():
    assert inject_config(dict(MINIMAL_CONFIG))["difficultyScale"] == "imba"


def test_imba_scan_ignores_the_itrs_keys():
    obj = inject_config(dict(MINIMAL_CONFIG), copy.deepcopy(MIXED_TRAILS))
    assert obj["difficultyRatings"] == [2]
    assert obj["freerideRatings"] == [0]
    assert obj["exposureLevels"] == []
    assert obj["hasDifficultyTrails"] is True


def test_itrs_scan_reads_the_itrs_key_in_range():
    config = dict(MINIMAL_CONFIG, difficulty_scale="itrs")
    obj = inject_config(config, copy.deepcopy(MIXED_TRAILS))
    assert obj["difficultyScale"] == "itrs"
    assert obj["difficultyRatings"] == [1, 3]
    assert obj["freerideRatings"] == []
    assert obj["hasDifficultyTrails"] is True


def test_itrs_map_with_only_imba_tags_has_no_difficulty_trails():
    trails = {"metadata": {"routes": {}}, "features": [_rated(imba_difficulty="3")]}
    obj = inject_config(dict(MINIMAL_CONFIG, difficulty_scale="itrs"), trails)
    assert obj["hasDifficultyTrails"] is False
    assert obj["difficultyRatings"] == []


def test_itrs_out_of_range_value_keeps_the_toggle_like_imba():
    """Any non-empty value keeps the toggle on either scale; only the key
    strip loses the glyph it has no shape for."""
    trails = {"metadata": {"routes": {}}, "features": [_rated(itrs_difficulty="7")]}
    obj = inject_config(dict(MINIMAL_CONFIG, difficulty_scale="itrs"), trails)
    assert obj["hasDifficultyTrails"] is True
    assert obj["difficultyRatings"] == []


def test_itrs_exposure_alone_does_not_turn_the_toggle_on():
    # A leftover way field is not read; exposure has its own toggle.
    trails = {"metadata": {"routes": {}}, "features": [_rated(itrs_exposure="2")]}
    obj = inject_config(dict(MINIMAL_CONFIG, difficulty_scale="itrs"), trails)
    assert obj["hasDifficultyTrails"] is False
    assert obj["difficultyRatings"] == []


def test_exposure_levels_come_from_the_poi_data_on_itrs_maps():
    config = dict(MINIMAL_CONFIG, difficulty_scale="itrs", _exposure_levels=[4, 2])
    obj = inject_config(config, copy.deepcopy(MIXED_TRAILS))
    assert obj["exposureLevels"] == [2, 4]
    assert obj["showExposurePoints"] is True


def test_show_exposure_points_follows_the_scale():
    assert inject_config(dict(MINIMAL_CONFIG))["showExposurePoints"] is False
    itrs = inject_config(dict(MINIMAL_CONFIG, difficulty_scale="itrs"))
    assert itrs["showExposurePoints"] is True and itrs["exposureLevels"] == []


def test_an_imba_map_carries_the_itrs_keys_at_their_defaults():
    """An unconfigured (IMBA) map emits the ITRS-era keys with values that
    leave the runtime on the IMBA scale: no exposure levels, no bike-only
    trails."""
    obj = inject_config(dict(MINIMAL_CONFIG))
    assert obj["difficultyScale"] == "imba"
    assert obj["exposureLevels"] == []
    assert obj["showExposurePoints"] is False
    assert obj["hasBikeOnlyTrails"] is False
