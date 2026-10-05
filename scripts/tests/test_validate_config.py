"""Tests for validate_config.py - a representative slice of the config linter.

The validator is large and was previously untested; these cover the core
contract (clean config passes; common mistakes are caught) rather than every
rule.

Run from repo root:
    python -m pytest scripts/tests/test_validate_config.py -v
"""

import contextlib
import os
import tempfile

from validate_config import assert_spec_coverage, validate_config

# A minimal valid LineString FeatureCollection, used to satisfy the
# geometry path-existence + content checks in route-only test configs.
_GEOJSON = (
    '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
    '"geometry":{"type":"LineString","coordinates":[[-85.3,42.3],[-85.31,42.31]]}}]}'
)


@contextlib.contextmanager
def _geojson_file():
    """Yield the path to a temp .json holding a valid LineString FC."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(_GEOJSON)
        path = f.name
    try:
        yield path
    finally:
        os.unlink(path)

# Smallest config the validator accepts cleanly (identity + one relation).
BASE = {"name": "T", "slug": "t", "title": "T Map", "relations": [12345678]}


def _errors(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    errors, _warnings = validate_config(cfg)
    return errors


def _warnings(**overrides):
    cfg = dict(BASE)
    cfg.update(overrides)
    _errors, warnings = validate_config(cfg)
    return warnings


def test_minimal_config_is_valid():
    errors, _ = validate_config(dict(BASE))
    assert errors == [], errors


def test_unknown_top_level_key_rejected():
    assert any("totally_unknown_key" in e for e in _errors(totally_unknown_key=1))


def test_reversed_bbox_rejected():
    # west must be < east; a reversed bbox should be flagged.
    assert any("bbox" in e for e in _errors(bbox=[10.0, 20.0, 5.0, 25.0]))


def test_wrong_scalar_type_rejected():
    assert any("show_distance" in e for e in _errors(show_distance="yes"))


# --- geometry source: relations | custom_routes | event_mode.routes -------

def test_event_mode_routes_without_relations_is_valid():
    # A race/event map can ship a GeoJSON route alone, no OSM relations.
    with _geojson_file() as geom:
        cfg = {
            "name": "E",
            "slug": "e",
            "title": "E Map",
            "event_mode": {
                "routes": [
                    {"id": "course", "name": "Course", "color": "#d00", "geometry": geom}
                ]
            },
        }
        errors, _ = validate_config(cfg)
    assert errors == [], errors


def test_custom_routes_without_relations_is_valid():
    with _geojson_file() as geom:
        cfg = {
            "name": "C",
            "slug": "c",
            "title": "C Map",
            "custom_routes": [
                {"id": "loop", "name": "Loop", "color": "#08c", "geometry": geom}
            ],
        }
        errors, _ = validate_config(cfg)
    assert errors == [], errors


def test_custom_route_oneway_reversible_rejected():
    # 'reversible' needs a direction_schedule.per_route entry, which is
    # keyed by OSM relation id; a custom route can never satisfy it, so
    # blessing it here guaranteed a template_inject build failure.
    with _geojson_file() as geom:
        base = {"id": "loop", "name": "Loop", "color": "#08c", "geometry": geom}
        for ow, ok in (("yes", True), ("-1", True), ("", True), ("reversible", False)):
            cfg = {
                "name": "C",
                "slug": "c",
                "title": "C Map",
                "custom_routes": [dict(base, oneway=ow)],
            }
            errors, _ = validate_config(cfg)
            if ok:
                assert errors == [], (ow, errors)
            else:
                assert any("reversible" in e for e in errors), errors


def test_no_geometry_source_rejected():
    # No relations, no custom_routes, no event_mode.routes → nothing to render.
    cfg = {"name": "N", "slug": "n", "title": "N Map"}
    errors, _ = validate_config(cfg)
    assert any("relations" in e or "geometry source" in e for e in errors), errors


def test_empty_relations_without_other_source_rejected():
    # The structurally-useless `relations: []` still fails on its own.
    assert any("relations" in e for e in _errors(relations=[]))


# --- relation_names (per-route display-name overrides) ---------------------

def test_relation_names_valid():
    assert _errors(relation_names={12345678: "Mountain Bike Trail"}) == []


def test_relation_names_non_int_key_rejected():
    errors = _errors(relation_names={"not-an-id": "Trail"})
    assert any("relation_names" in e and "not an OSM relation ID" in e for e in errors), errors


def test_relation_names_empty_value_rejected():
    errors = _errors(relation_names={12345678: "  "})
    assert any("relation_names[12345678]" in e for e in errors), errors


def test_relation_names_non_string_value_rejected():
    errors = _errors(relation_names={12345678: 42})
    assert any("relation_names[12345678]" in e for e in errors), errors


# --- event_mode.gpx (downloadable course files) ----------------------------

@contextlib.contextmanager
def _gpx_file():
    """Yield the path to a temp .gpx file (content irrelevant - the
    validator only checks existence; files are copied verbatim)."""
    with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
        f.write("<gpx/>")
        path = f.name
    try:
        yield path
    finally:
        os.unlink(path)


def _gpx_errors(gpx):
    """Validate BASE + an event_mode block carrying `gpx`. `featured`
    satisfies event mode's routes-or-featured requirement so the only
    errors under test are the gpx ones."""
    return _errors(event_mode={"featured": [12345678], "gpx": gpx})


def test_event_gpx_valid():
    with _gpx_file() as p:
        assert _gpx_errors({"routes": [{"name": "Course", "file": p}]}) == []


def test_event_gpx_missing_name_rejected():
    with _gpx_file() as p:
        errors = _gpx_errors({"routes": [{"file": p}]})
    assert any("gpx.routes[0].name" in e for e in errors), errors


def test_event_gpx_reserved_source_key_rejected():
    # `relation:` / `route:` are reserved for the deferred generation
    # feature - rejected with a forward-looking message, not silently
    # accepted or treated as a generic unknown key.
    errors = _gpx_errors({"routes": [{"name": "Course", "relation": -129}]})
    assert any("not implemented" in e for e in errors), errors


def test_event_gpx_duplicate_basename_rejected():
    # Filenames are preserved into the build's gpx/ dir, so two entries
    # sharing a basename would silently overwrite each other.
    with _gpx_file() as p:
        errors = _gpx_errors(
            {"routes": [{"name": "A", "file": p}, {"name": "B", "file": p}]}
        )
    assert any("duplicate filename" in e for e in errors), errors


def test_event_gpx_missing_file_rejected():
    errors = _gpx_errors(
        {"routes": [{"name": "Course", "file": "/nonexistent/course.gpx"}]}
    )
    assert any("file not found" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Nested-dict sub-schemas (the 2026-07 QA review's confirmed validator holes:
# each of these passed invalid input cleanly before)
# ---------------------------------------------------------------------------


def test_title_optional():
    # `title` is an optional override; build.load_config derives
    # "{name} Map" when it is absent.
    cfg = dict(BASE)
    del cfg["title"]
    errors, _ = validate_config(cfg)
    assert errors == [], errors


def test_hub_colors_validated():
    assert any("hub_color" in e for e in _errors(hub_color="#zzzzzz"))
    assert any("hub_text_color" in e for e in _errors(hub_text_color="#zzzzzz"))
    assert any("hub_border_color" in e for e in _errors(hub_border_color="#zzzzzz"))


def test_marker_shape_validated():
    assert any("marker_shape" in e for e in _errors(marker_shape="triangle"))
    assert _errors(marker_shape="box") == []
    assert _errors(marker_shape="pill") == []
    assert _errors(marker_shape="circle") == []
    assert _errors(marker_shape="diamond") == []


def test_default_trail_color_dict_shape_validated():
    errs = _errors(default_trail_color={"colour": "#123456", "pattern": "2 2", "cap": "rond"})
    assert any("default_trail_color.colour" in e and "'color'" in e for e in errs), errs
    assert any("default_trail_color.pattern" in e for e in errs), errs
    assert any("default_trail_color.cap" in e for e in errs), errs
    assert (
        _errors(default_trail_color={"color": "#123456", "pattern": [2, 2], "cap": "round"}) == []
    )


def test_per_route_spec_shape_validated():
    errs = _errors(direction_schedule={"per_route": {123: {"reverse_day": ["monday"]}}})
    assert any("reverse_day" in e and "did you mean" in e for e in errs), errs
    # A spec whose reverse_days didn't parse becomes an EMPTY override
    # (disabling reversal for the route) - so its absence is an error...
    assert any("missing reverse_days" in e for e in errs), errs
    # ...while an explicit empty list is the documented opt-out.
    assert _errors(direction_schedule={"per_route": {123: {"reverse_days": []}}}) == []


def test_dashed_relations_dict_shape_validated():
    errs = _errors(dashed_relations={456: {"pattern": [4, 2], "colors": "#000000", "colurs": 1}})
    assert any("colurs" in e for e in errs), errs
    assert any("dashed_relations[456].colors" in e for e in errs), errs
    assert (
        _errors(dashed_relations={456: {"pattern": [4, 2], "colors": ["#000000", "#ffffff"]}})
        == []
    )


def test_about_description_rejected_with_migration_hint():
    """`about.description` was retired for `welcome.body` when the About
    modal became a purely technical surface; the error must say where
    the text goes so a curator can fix the config without archaeology."""
    errors = _errors(about={"description": "An unofficial map."})
    assert any("about.description" in e and "welcome.body" in e for e in errors), errors


def test_about_curator_and_links_still_accepted():
    errors = _errors(
        about={
            "curator": {"name": "A Curator", "url": "https://example.com"},
            "links": [{"label": "Trail Org", "url": "https://example.org"}],
        }
    )
    assert errors == [], errors


def test_welcome_body_accepted():
    errors = _errors(welcome={"body": "An unofficial map of the trails."})
    assert errors == [], errors


def test_route_key_is_a_boolean():
    assert not any("route_key" in e for e in _errors(route_key=False))
    assert any("route_key" in e for e in _errors(route_key="no"))


def test_lane_renderer_is_a_retired_key_with_its_own_message():
    # Either former value: the key is gone, and the curator is told to
    # delete the line, once, not also "unknown top-level key".
    for value in ("native", "plugin"):
        errors = [e for e in _errors(lane_renderer=value) if "lane_renderer" in e]
        assert len(errors) == 1
        assert "removed" in errors[0]
        assert "unknown top-level key" not in errors[0]


def test_suppress_basemap_path_labels_is_a_retired_key_with_its_own_message():
    # Both spellings, either value: gone, one message, told to delete.
    for key in ("suppress_basemap_path_labels", "suppress_path_labels"):
        for value in (True, False):
            errors = [e for e in _errors(**{key: value}) if key in e]
            assert len(errors) == 1
            assert "removed" in errors[0] and "Delete the line" in errors[0]
            assert "unknown top-level key" not in errors[0]


def test_basemap_source_is_a_retired_key_with_its_own_message():
    for value in ("generated", "protomaps"):
        errors = [e for e in _errors(basemap_source=value) if "basemap_source" in e]
        assert len(errors) == 1
        assert "removed" in errors[0] and "Delete the line" in errors[0]
        assert "unknown top-level key" not in errors[0]


def test_distance_units_is_a_retired_key_with_its_own_message():
    for value in ("mi", "km"):
        errors = [e for e in _errors(distance_units=value) if "distance_units" in e]
        assert len(errors) == 1
        assert "removed" in errors[0]
        assert "unknown top-level key" not in errors[0]


# --- event_mode.pois[].directions ------------------------------------------


def _event_poi_errors(**poi):
    entry = {"name": "Event Parking", "coordinates": [-83.1, 42.4], **poi}
    return _errors(event_mode={"featured": [12345678], "pois": [entry]})


def test_event_poi_directions_is_optional_and_boolean():
    assert not any("pois" in e for e in _event_poi_errors())
    assert not any("pois" in e for e in _event_poi_errors(directions=True))
    assert not any("pois" in e for e in _event_poi_errors(directions=False))
    assert any("directions" in e for e in _event_poi_errors(directions="yes"))
    assert any("directions" in e for e in _event_poi_errors(directions=1))


def test_event_poi_directions_defaults_off_in_the_poi_data():
    # The popup offers "Get Directions" only where the curator asked:
    # event parking is driven to, a start line is a plain flag.
    from fetch_pois import build_pois_geojson

    pois = [
        {"name": "Start / Finish", "coordinates": [-83.1, 42.4]},
        {"name": "Event Parking", "coordinates": [-83.2, 42.5], "directions": True},
        {"name": "Aid 1", "coordinates": [-83.3, 42.6], "directions": False},
    ]
    fc = build_pois_geojson({"elements": []}, [], [], config_event_pois=pois)
    got = {f["properties"]["name"]: f["properties"]["directions"] for f in fc["features"]}
    assert got == {"Start / Finish": False, "Event Parking": True, "Aid 1": False}


# --- color_by: route | difficulty --------------------------------------------


def test_color_by_route_and_difficulty_are_valid():
    assert _errors(color_by="route") == []
    assert _errors(color_by="difficulty") == []


def test_color_by_legacy_values_rejected_as_ordinary_junk():
    # 1: no alias, no special message - the same "must be one of" error
    # any other unknown value gets.
    for value in ("relation", "trail"):
        errors = [e for e in _errors(color_by=value) if "color_by" in e]
        assert len(errors) == 1
        assert "must be one of" in errors[0], errors


# --- show_distance ---------------------------------------------------------


def test_show_distance_is_valid():
    assert _errors(show_distance=True) == []


# --- difficulty-map-only validation rules -----------------------------------


def test_difficulty_map_rejects_event_mode():
    errors = _errors(color_by="difficulty", event_mode={"featured": [12345678]})
    assert any("event_mode" in e for e in errors), errors


def test_routes_map_allows_event_mode():
    assert _errors(event_mode={"featured": [12345678]}) == []


def test_difficulty_map_rejects_default_labels_routes():
    errors = _errors(color_by="difficulty", default_labels="routes")
    assert any("default_labels" in e for e in errors), errors


def test_difficulty_map_rejects_forced_labels_routes():
    errors = _errors(color_by="difficulty", forced_labels="routes")
    assert any("forced_labels" in e for e in errors), errors


def test_difficulty_map_allows_trails_and_none_labels():
    assert _errors(color_by="difficulty", default_labels="trails") == []
    assert _errors(color_by="difficulty", default_labels="none") == []
    assert _errors(color_by="difficulty", forced_labels="trails") == []


def test_difficulty_map_rejects_show_trails_false():
    errors = _errors(color_by="difficulty", show_trails=False)
    assert any("show_trails" in e for e in errors), errors


def test_routes_map_allows_show_trails_false():
    assert _errors(show_trails=False) == []


def test_difficulty_map_empty_relation_colors_does_not_warn():
    warnings = _warnings(color_by="difficulty", relation_colors={})
    assert not any("relation_colors" in w for w in warnings), warnings


def test_routes_map_relation_colors_no_difficulty_warning():
    warnings = _warnings(relation_colors={12345678: "#ff0000"})
    assert not any("relation_colors" in w for w in warnings), warnings


# --- color_by_route / color_by_difficulty ------------------------------------


def test_color_mode_lists_are_valid_keys():
    custom = [{"id": "my-route", "name": "Mine", "color": "#888888", "geometry": "mine.geojson"}]
    errors = _errors(color_by="difficulty", color_by_route=[12345678, "my-route"],
                     custom_routes=custom)
    assert not any("color_by_route" in e for e in errors), errors
    assert _errors(color_by_difficulty=[12345678]) == []


def test_color_mode_list_string_must_name_a_custom_route():
    errors = _errors(color_by="difficulty", color_by_route=["paved"])
    assert any("not a custom_routes" in e for e in errors), errors
    inline = {"routes": [{"id": "paved", "name": "Paved", "color": "#888888",
                          "geometry": "paved.geojson"}]}
    errors = _errors(color_by="difficulty", color_by_route=["paved"], event_mode=inline)
    assert not any("not a custom_routes" in e for e in errors), errors


def test_color_mode_lists_reject_non_list():
    assert any("color_by_route" in e for e in _errors(color_by_route=12345678))


def test_id_in_both_color_mode_lists_is_an_error():
    errors = _errors(color_by_route=[12345678], color_by_difficulty=[12345678])
    assert any("12345678" in e and "color_by" in e for e in errors), errors


def test_id_in_default_mode_list_is_redundant_warning():
    warnings = _warnings(color_by="difficulty", color_by_difficulty=[12345678])
    assert any("color_by_difficulty" in w and "redundant" in w for w in warnings), warnings
    warnings = _warnings(color_by_route=[12345678])
    assert any("color_by_route" in w and "redundant" in w for w in warnings), warnings


def test_exception_list_entry_is_not_redundant():
    warnings = _warnings(color_by="difficulty", color_by_route=[12345678])
    assert not any("redundant" in w for w in warnings), warnings


def test_relation_colors_on_difficulty_mode_relation_warns():
    warnings = _warnings(color_by="difficulty", relation_colors={12345678: "#ff0000"})
    assert any("relation_colors" in w and "ignored" in w for w in warnings), warnings
    warnings = _warnings(color_by_difficulty=[12345678], dashed_relations={12345678: [2, 2]})
    assert any("dashed_relations" in w and "ignored" in w for w in warnings), warnings


def test_relation_colors_on_route_mode_exception_does_not_warn():
    warnings = _warnings(
        color_by="difficulty",
        color_by_route=[12345678],
        relation_colors={12345678: "#ff0000"},
    )
    assert not any("relation_colors" in w for w in warnings), warnings


def test_event_mode_rejected_with_difficulty_exception_list():
    errors = _errors(color_by_difficulty=[12345678], event_mode={"featured": [12345678]})
    assert any("event_mode" in e for e in errors), errors


def test_route_mode_relation_lifts_the_no_route_mode_checks():
    kwargs = dict(
        color_by="difficulty",
        color_by_route=[12345678],
        default_labels="routes",
        forced_labels="routes",
        show_trails=False,
    )
    assert _errors(**kwargs) == []


def test_trail_labels_on_an_inline_only_event_map_warn():
    # Inline event routes are bare geometry with no way names.
    with _geojson_file() as geom:
        routes = [{"id": "course", "name": "Course", "color": "#d00", "geometry": geom}]
        for key in ("default_labels", "forced_labels"):
            cfg = {"name": "E", "slug": "e", "title": "E Map",
                   "event_mode": {"routes": routes}, key: "trails"}
            errors, warnings = validate_config(cfg)
            assert errors == [], errors
            assert any(key in w and "no way names" in w for w in warnings), warnings


def test_trail_labels_on_an_event_map_with_a_featured_relation_do_not_warn():
    warnings = _warnings(event_mode={"featured": [12345678]}, default_labels="trails")
    assert not any("no way names" in w for w in warnings), warnings


def test_every_known_key_reaches_the_runtime_or_is_declared_build_only():
    # Catches a key added to the validator that never reaches the template
    # injector, which otherwise breaks silently in the browser.
    assert assert_spec_coverage() is True


# ---------------------------------------------------------------------------
# Retired keys from the 2026-10 prune
# ---------------------------------------------------------------------------

_PRUNED = {
    "show_elevation": True,
    "base_layers": [],
    "url_hash": True,
    "map_dim_on_highlight": False,
    "highlight_glow": False,
    "scrim_opacity": 0.5,
    "share_button": False,
    "pwa": False,
    "pwa_install_prompt": False,
    "min_zoom": 9,
    "max_zoom": 19,
    "basemap_maxzoom": 14,
    "terrain_maxzoom": 11,
    "output_dir": "build/x",
}


def test_pruned_keys_are_retired_with_their_own_message():
    for key, value in _PRUNED.items():
        errors = [e for e in _errors(**{key: value}) if key in e]
        assert len(errors) == 1, (key, errors)
        assert "removed" in errors[0] and "Delete the line" in errors[0], errors[0]
        assert "unknown top-level key" not in errors[0], errors[0]


# --- a key with no value ------------------------------------------------

def test_a_key_with_no_value_is_an_error():
    # `pan_padding:` parses as null. It used to validate, then crash the
    # build, because config.get(key, default) answers None for a present
    # key; `show_distance:` silently turned distances off the same way.
    for key in ("pan_padding", "bbox", "trailheads", "show_distance", "event_mode"):
        errors = _errors(**{key: None})
        assert len(errors) == 1, (key, errors)
        assert key in errors[0] and "has no value" in errors[0]


def test_a_required_key_with_no_value_is_reported_once():
    errors = _errors(name=None)
    assert len(errors) == 1
    assert "required" in errors[0]


# --- shapes that used to pass or crash ------------------------------------

def test_dash_pattern_is_exactly_two_non_negative_numbers():
    for bad in ([2], [], [-1, 2], [0, 0], [2, 2, 2], ["2", 2]):
        assert any("dashed_relations[456]" in e for e in _errors(dashed_relations={456: bad})), bad
        assert any("pattern" in e for e in _errors(dashed_relations={456: {"pattern": bad}})), bad
        assert any("default_trail_color.pattern" in e
                   for e in _errors(default_trail_color={"pattern": bad})), bad
    for good in ([2, 2], [0, 2], [4, 0], [1.5, 3]):
        assert _errors(dashed_relations={456: good}) == [], good


def test_a_non_string_top_level_key_is_a_validation_error():
    errors, _ = validate_config({**BASE, 2024: "oops"})
    assert len(errors) == 1 and "2024" in errors[0], errors


def test_a_whitespace_only_name_is_missing():
    errors = _errors(name="   ")
    assert any("name" in e and "missing or empty" in e for e in errors), errors


def test_about_curator_and_links_reject_unknown_keys():
    errors = _errors(about={"curator": {"name": "A", "uri": "x"},
                            "links": [{"label": "L", "url": "https://e.org", "href": 1}],
                            "link": []})
    assert any("about.curator.uri" in e for e in errors), errors
    assert any("about.links[0].href" in e for e in errors), errors
    assert any("about.link" in e and "did you mean" in e for e in errors), errors
    # A retired key keeps its own message and is not reported twice.
    assert len(_errors(about={"author": {"name": "A"}})) == 1


def test_point_entries_reject_unknown_keys_and_check_directions_url():
    pt = {"name": "P", "coordinates": [-85.3, 42.3]}
    assert _errors(trailheads=[dict(pt, directions_url="https://maps.example/x")],
                   parking=[dict(pt, directions_url="http://maps.example/y")],
                   hubs=[pt]) == []
    errors = _errors(trailheads=[dict(pt, direction_url="https://x")],
                     parking=[dict(pt, directions_url=5)],
                     hubs=[dict(pt, directions_url="https://x")])
    assert any("trailheads[0].direction_url" in e for e in errors), errors
    assert any("parking[0].directions_url" in e and "http" in e for e in errors), errors
    assert any("hubs[0].directions_url" in e for e in errors), errors
    assert any("directions_url" in e for e in _errors(parking=[dict(pt, directions_url="javascript:x")]))


def test_unquoted_custom_route_oneway_says_quote_it():
    with _geojson_file() as geom:
        cfg = {"name": "C", "slug": "c", "custom_routes": [
            {"id": "loop", "name": "Loop", "color": "#08c", "geometry": geom, "oneway": -1}]}
        errors, _ = validate_config(cfg)
    assert any("quote the value" in e for e in errors), errors
