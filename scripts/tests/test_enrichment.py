"""Tests for enrichment.py — relation_names display-name overrides and the
per-relation override typo guard.

Run from repo root:
    python -m pytest scripts/tests/test_enrichment.py -v
Or as a script:
    python scripts/tests/test_enrichment.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from enrichment import _enrich_trails_geojson  # noqa: E402

_OSM_NAME = "Pontiac Lake Recreation Area Mountain Bike Trail"


def _fc():
    """Minimal single-route FeatureCollection in the shape fetch_trails
    emits (pre-enrichment base), including a super-relation expansion
    entry so the typo guard's parent-ID special case is exercisable."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-83.44, 42.67], [-83.45, 42.68]],
                },
                "properties": {
                    "route_id": 12562142,
                    "route_name": _OSM_NAME,
                    "route_colour": "red",
                    "route_ref": "",
                    "trail_name": "",
                    "shared_routes": [12562142],
                    "imba_difficulty": "",
                    "oneway": "",
                    "segment_index": 0,
                    "way_ids": [],
                },
            },
        ],
        "metadata": {
            "routes": {
                "12562142": {"name": _OSM_NAME, "colour": "red", "ref": "", "seasonal": ""},
            },
            "super_relation_expansions": {"999": ["12562142"]},
        },
    }


def test_relation_names_renames_metadata_and_features():
    g = _fc()
    changed = _enrich_trails_geojson({"relation_names": {12562142: "Mountain Bike Trail"}}, g, ".")
    assert changed
    assert g["metadata"]["routes"]["12562142"]["name"] == "Mountain Bike Trail"
    real = [f for f in g["features"] if not f["properties"].get("isStub")]
    assert real and all(f["properties"]["route_name"] == "Mountain Bike Trail" for f in real)


def test_no_override_leaves_osm_name():
    g = _fc()
    _enrich_trails_geojson({}, g, ".")
    assert g["metadata"]["routes"]["12562142"]["name"] == _OSM_NAME
    assert g["features"][0]["properties"]["route_name"] == _OSM_NAME


def test_typo_guard_warns_on_unknown_and_super_relation_keys(capsys):
    g = _fc()
    cfg = {"relation_names": {111: "X"}, "relation_colors": {999: "blue"}}
    _enrich_trails_geojson(cfg, g, ".")
    out = capsys.readouterr().out
    assert "relation_names[111]" in out and "no such route" in out
    # 999 is a super-relation parent: the warning should point at the child.
    assert "relation_colors[999]" in out and "12562142" in out


def test_typo_guard_silent_for_known_keys(capsys):
    g = _fc()
    _enrich_trails_geojson({"relation_colors": {12562142: "blue"}}, g, ".")
    assert "warn" not in capsys.readouterr().out


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
