"""Tests for the trail-data refetch decision in cache_signatures.py.

Contract: a config edit that changes what fetch_trails() consumes
(relations, osm_file, schedules) forces a refetch, a styling edit never
does, and a trails.geojson changed behind the build's back is refetched.
"""

import pytest
from cache_signatures import (
    _pmtiles_needs_regen,
    _save_signature,
    _trails_content_hash,
    _trails_fetch_fingerprint,
    _trails_needs_refetch,
)

CONFIG = {
    "relations": [3, 1, 2],
    "bbox": [-83.5, 42.6, -83.4, 42.7],
    "relation_colors": {1: "#ff0000"},
}


@pytest.fixture
def built(tmp_path):
    """A trails.geojson with the sidecar a successful build would leave."""
    path = str(tmp_path / "trails.src.geojson")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"features": []}')
    _save_signature(
        path,
        _trails_fetch_fingerprint(CONFIG) + "\ntrails-content=" + _trails_content_hash(path),
    )
    return path


def test_fingerprint_ignores_relation_order():
    shuffled = dict(CONFIG, relations=[2, 3, 1])
    assert _trails_fetch_fingerprint(shuffled) == _trails_fetch_fingerprint(CONFIG)


def test_fingerprint_changes_with_every_fetch_input():
    base = _trails_fetch_fingerprint(CONFIG)
    for key, value in (
        ("relations", [1, 2, 3, 4]),
        ("clipped_relations", [1]),
        ("winter_relations", [1]),
        ("summer_relations", [2]),
        ("emergency_access_relations", [3]),
        ("osm_file", "trails.osm"),
        ("direction_schedule", {"1": "odd"}),
    ):
        assert _trails_fetch_fingerprint(dict(CONFIG, **{key: value})) != base, key


def test_fingerprint_ignores_styling_and_custom_routes():
    base = _trails_fetch_fingerprint(CONFIG)
    edited = dict(CONFIG, relation_colors={1: "#0000ff"}, custom_routes=[{"name": "x"}])
    assert _trails_fetch_fingerprint(edited) == base


def test_content_hash_of_a_missing_file_is_none(tmp_path):
    assert _trails_content_hash(str(tmp_path / "nope.geojson")) is None


def test_unchanged_config_and_file_reuse_the_cache(built):
    assert _trails_needs_refetch(built, CONFIG) == (False, None)


def test_changed_relations_trigger_a_refetch(built):
    needs, reason = _trails_needs_refetch(built, dict(CONFIG, relations=[1, 2, 3, 4]))
    assert needs
    assert "config inputs changed" in reason


def test_styling_edit_does_not_trigger_a_refetch(built):
    config = dict(CONFIG, relation_colors={1: "#0000ff"})
    assert _trails_needs_refetch(built, config) == (False, None)


def test_bbox_edit_leaves_trails_alone_but_re_extracts_tiles(built, tmp_path):
    # Trails are fetched by relation id, so the bbox only keys the tiles.
    moved = dict(CONFIG, bbox=[-84.0, 42.6, -83.4, 42.7])
    assert _trails_needs_refetch(built, moved) == (False, None)

    tiles = str(tmp_path / "basemap.pmtiles")
    with open(tiles, "wb") as f:
        f.write(b"x")
    _save_signature(tiles, "bbox=-83.5000,42.6000,-83.4000,42.7000;maxzoom=14;minzoom=0")
    assert _pmtiles_needs_regen(tiles, CONFIG["bbox"], 14) == (False, None)
    assert _pmtiles_needs_regen(tiles, moved["bbox"], 14)[0] is True


def test_file_changed_behind_the_build_triggers_a_refetch(built):
    with open(built, "w", encoding="utf-8") as f:
        f.write('{"features": [{"truncated": true}]}')
    needs, reason = _trails_needs_refetch(built, CONFIG)
    assert needs
    assert "changed on disk" in reason


def test_missing_file_triggers_a_refetch(tmp_path):
    assert _trails_needs_refetch(str(tmp_path / "gone.geojson"), CONFIG) == (True, "file missing")


def test_missing_sidecar_triggers_a_refetch(tmp_path):
    # Without the sidecar neither the fingerprint nor the content guard
    # can vouch for the base, so a relation added since would be missed.
    path = tmp_path / "trails.src.geojson"
    path.write_text("{}", encoding="utf-8")
    needs, reason = _trails_needs_refetch(str(path), CONFIG)
    assert needs
    assert "sidecar missing" in reason


def test_sidecar_without_a_content_line_is_trusted(tmp_path):
    path = str(tmp_path / "trails.src.geojson")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    _save_signature(path, _trails_fetch_fingerprint(CONFIG))
    assert _trails_needs_refetch(path, CONFIG) == (False, None)
