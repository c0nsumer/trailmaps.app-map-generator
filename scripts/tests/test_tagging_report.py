"""Tests for tagging_report.py — OSM data-quality notes.

The checks that matter most here are the ones that keep the report from
nagging: an ordinary branch junction must not read as a broken connection,
and a map with no difficulty tagging at all must not be told to add some.

Offline: audit() takes parsed dicts.

Run from repo root:
    python -m pytest scripts/tests/test_tagging_report.py -v
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tagging_report import audit, format_report, summarize  # noqa: E402


def _feature(way_ids, coords, *, route_id="1", trail="", imba=""):
    return {
        "type": "Feature",
        "properties": {
            "route_id": route_id,
            "trail_name": trail,
            "imba_difficulty": imba,
            "way_ids": list(way_ids),
        },
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _snap(features, routes=None):
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "routes": routes if routes is not None else {
                "1": {"name": "Main Loop", "colour": "orange"},
            },
        },
    }


_CFG = {"slug": "t", "show_difficulty": True}


def test_clean_data_reports_nothing():
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]],
                           trail="Bootjack", imba="2")])
    f = audit(snap, None, _CFG)
    assert f["total"] == 0
    assert summarize(f) == []
    assert "No issues found." in format_report(f, "t")


def test_shared_node_junction_is_not_a_gap():
    """The main false-positive risk. Three ways meeting at one node leave the
    greedy chainer with a leftover branch; those chains share an endpoint
    exactly, so they must fall below the gap floor."""
    j = [-87.59, 46.50]
    snap = _snap([
        _feature([10], [[-87.60, 46.50], j]),
        _feature([11], [j, [-87.58, 46.50]]),
        _feature([12], [j, [-87.59, 46.51]]),
    ])
    f = audit(snap, None, _CFG)
    assert f["probable_gaps"] == []


def test_near_miss_endpoints_are_a_gap():
    """Two ways ending ~3 m apart without sharing a node."""
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.5900, 46.50]]),
        # ~0.00004 deg lon at this latitude is roughly 3 m.
        _feature([11], [[-87.58996, 46.50], [-87.58, 46.50]]),
    ])
    f = audit(snap, None, _CFG)
    assert len(f["probable_gaps"]) == 1
    gap = f["probable_gaps"][0]
    assert 0.01 < gap["distance_m"] <= 10.0
    assert gap["route_name"] == "Main Loop"
    assert "unconnected" in format_report(f, "t").lower()


def test_far_apart_endpoints_are_not_a_gap():
    """Two genuinely separate trail ends must not be flagged."""
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.59, 46.50]]),
        _feature([11], [[-87.50, 46.50], [-87.49, 46.50]]),
    ])
    f = audit(snap, None, _CFG)
    assert f["probable_gaps"] == []


def test_no_ratings_anywhere_does_not_nag():
    """A map where nobody tagged difficulty gets no missing-rating list —
    that would be asking for tags on the renderer's behalf, and the
    Difficulty control is auto-hidden on such maps anyway."""
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.59, 46.50]], trail="A"),
        _feature([11], [[-87.50, 46.50], [-87.49, 46.50]], trail="B"),
    ])
    f = audit(snap, None, _CFG)
    assert f["named_trails_missing_rating"] == []


def test_partial_rating_coverage_reports_the_gaps():
    """Partial coverage IS actionable: someone rated most and skipped these."""
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.59, 46.50]], trail="Rated",
                 imba="3"),
        _feature([11], [[-87.50, 46.50], [-87.49, 46.50]], trail="Skipped"),
    ])
    f = audit(snap, None, _CFG)
    assert f["named_trails_missing_rating"] == ["Skipped"]


def test_unnamed_ways_are_never_asked_to_be_named_or_rated():
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.59, 46.50]], trail="Rated",
                 imba="3"),
        _feature([11], [[-87.50, 46.50], [-87.49, 46.50]]),  # unnamed connector
    ])
    f = audit(snap, None, _CFG)
    assert f["named_trails_missing_rating"] == []


def test_missing_rating_suppressed_when_map_hides_difficulty():
    snap = _snap([
        _feature([10], [[-87.60, 46.50], [-87.59, 46.50]], trail="Rated",
                 imba="3"),
        _feature([11], [[-87.50, 46.50], [-87.49, 46.50]], trail="Skipped"),
    ])
    f = audit(snap, None, {"slug": "t", "show_difficulty": False})
    assert f["named_trails_missing_rating"] == []


def test_invalid_rating_reported_even_when_difficulty_hidden():
    """An out-of-range value is wrong on its own terms, not as a style issue."""
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]],
                           trail="Weird", imba="7")])
    f = audit(snap, None, {"slug": "t", "show_difficulty": False})
    assert f["invalid_ratings"] == [("10", "Weird", "7")]
    assert "valid values are 0-5" in format_report(f, "t")


def test_relation_missing_name_and_colour():
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]],
                           route_id="9")],
                 routes={"9": {"name": "", "colour": ""}})
    f = audit(snap, None, _CFG)
    assert f["routes_missing_name"] == ["9"]
    assert f["routes_missing_colour"] == [("9", "")]


def test_orphan_trail_marker_detected_and_scoped_to_on_trail_types():
    trails = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]])])
    pois = {"features": [
        # ~1.5 km north of the trail: an orphan.
        {"properties": {"poi_type": "trail_marker", "ref": "23"},
         "geometry": {"type": "Point", "coordinates": [-87.595, 46.5135]}},
        # On the trail: fine.
        {"properties": {"poi_type": "trail_marker", "ref": "24"},
         "geometry": {"type": "Point", "coordinates": [-87.595, 46.50]}},
        # Parking is curator-placed and off-trail BY DESIGN — never flagged.
        {"properties": {"poi_type": "parking", "name": "Lot A"},
         "geometry": {"type": "Point", "coordinates": [-87.50, 46.60]}},
    ]}
    f = audit(trails, pois, _CFG)
    names = [o["name"] for o in f["orphan_pois"]]
    assert names == ["23"]


def test_report_states_when_a_list_is_truncated():
    feats = [_feature([1], [[-87.60, 46.50], [-87.59, 46.50]], trail="Rated",
                      imba="2")]
    for i in range(50):
        lon = -87.0 - i * 0.01
        feats.append(_feature([100 + i], [[lon, 46.0], [lon + 0.001, 46.0]],
                              trail=f"Trail {i:02d}"))
    f = audit(_snap(feats), None, _CFG)
    assert len(f["named_trails_missing_rating"]) == 50
    assert "and 10 more not listed" in format_report(f, "t")


def test_singular_wording():
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]])],
                 routes={"9": {"name": "X", "colour": ""}})
    f = audit(snap, None, _CFG)
    assert "1 relation with no colour" in summarize(f)


def test_empty_and_malformed_input_do_not_crash():
    assert audit({"features": [], "metadata": {}}, None, _CFG)["total"] == 0
    weird = {"features": [{
        "properties": {"route_id": "1", "way_ids": [1]},
        "geometry": {"type": "Point", "coordinates": [-87.6, 46.5]},
    }], "metadata": {"routes": {"1": {"name": "N", "colour": "red"}}}}
    assert audit(weird, None, _CFG)["total"] == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
