"""Tests for validate_config.py — a representative slice of the config linter.

The validator is large and was previously untested; these cover the core
contract (clean config passes; common mistakes are caught) rather than every
rule.

Run from repo root:
    python -m pytest scripts/tests/test_validate_config.py -v
Or as a script:
    python scripts/tests/test_validate_config.py
"""

import contextlib
import os
import sys
import tempfile

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from validate_config import validate_config  # noqa: E402

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


def test_minimal_config_is_valid():
    errors, _ = validate_config(dict(BASE))
    assert errors == [], errors


def test_unknown_top_level_key_rejected():
    assert any("totally_unknown_key" in e for e in _errors(totally_unknown_key=1))


def test_reversed_bbox_rejected():
    # west must be < east; a reversed bbox should be flagged.
    assert any("bbox" in e for e in _errors(bbox=[10.0, 20.0, 5.0, 25.0]))


def test_wrong_scalar_type_rejected():
    assert any("zoom" in e for e in _errors(zoom="not-a-number"))


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
    """Yield the path to a temp .gpx file (content irrelevant — the
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
    # feature — rejected with a forward-looking message, not silently
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


def test_title_suffix_rejected_as_unknown():
    # `title_suffix` existed briefly (2026-07) and was removed before
    # release: branding the <title> is the deploying site's job, done in
    # post-processing, not an engine config concern. The unknown-key check
    # must flag it so a stale yaml fails loud instead of silently
    # un-branding.
    assert any("title_suffix" in e for e in _errors(title_suffix=" | example.org"))


def test_hub_colors_validated():
    assert any("hub_color" in e for e in _errors(hub_color="#zzzzzz"))
    assert any("hub_text_color" in e for e in _errors(hub_text_color="#zzzzzz"))
    assert any("hub_border_color" in e for e in _errors(hub_border_color="#zzzzzz"))


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
    # (disabling reversal for the route) — so its absence is an error...
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


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
