"""Event-mode fan-out: which routes are featured and how every other
route is muted, from the config's point of view."""

from event_mode import (
    _apply_event_mode_to_custom_routes,
    _apply_event_mode_to_feature_oneway,
    _apply_event_mode_to_relations,
)


def _trails():
    return {"metadata": {
        "routes": {"100": {}, "201": {}, "202": {}, "300": {}, "stage1": {"isCustom": True}},
        "super_relation_expansions": {"200": [201, 202]},
    }}


def test_featured_routes_stay_styled_and_background_routes_are_muted():
    config = {
        "event_mode": {"featured": [100, 200]},
        "relation_colors": {300: "#ff0000"},
    }
    trails = _trails()
    _apply_event_mode_to_relations(config, trails)
    routes = trails["metadata"]["routes"]
    # A featured id and both children of a featured super-relation are flagged.
    assert [rid for rid, info in routes.items() if info.get("featured")] == ["100", "201", "202"]
    # Featured routes get no background entries.
    for rid in (100, 201, 202):
        assert rid not in config["dashed_relations"]
        assert rid not in config["relation_colors"]
    # The background route is dashed; the curator's explicit color wins.
    assert config["relation_colors"][300] == "#ff0000"
    assert 300 in config["dashed_relations"]
    # A custom route is left to the pre-enrichment pass.
    assert "stage1" not in config["dashed_relations"]


def test_inline_routes_are_featured_and_other_custom_routes_are_muted():
    config = {
        "event_mode": {"routes": [{"id": "stage1", "color": "#00ff00", "geojson": "a.json"}]},
        "custom_routes": [{"id": "other", "color": "#0000ff", "geojson": "b.json"}],
    }
    _apply_event_mode_to_custom_routes(config)
    by_id = {r["id"]: r for r in config["custom_routes"]}
    assert set(by_id) == {"other", "stage1"}
    assert by_id["stage1"]["color"] == "#00ff00" and "dashed" not in by_id["stage1"]
    assert by_id["other"]["color"] == "gray"
    assert by_id["other"]["dashed"] == [0, 2]


def test_direction_arrows_stay_only_on_featured_ways():
    config = {"event_mode": {"featured": [200], "direction_arrows": True}}
    trails = _trails()
    trails["features"] = [
        {"properties": {"route_id": "201", "oneway": "yes"}},
        {"properties": {"route_id": "300", "oneway": "yes"}},
        {"properties": {"route_id": "300", "shared_routes": ["202"], "oneway": "yes"}},
    ]
    assert _apply_event_mode_to_feature_oneway(config, trails) is True
    assert [f["properties"]["oneway"] for f in trails["features"]] == ["yes", "", "yes"]
