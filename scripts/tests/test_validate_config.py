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
