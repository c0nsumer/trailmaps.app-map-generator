"""Tests for osm_diff.py — snapshot comparison for refresh vetting.

Covers the two design properties the module exists to guarantee:

* a re-merge that moves ways between merged features (because a tag changed
  the merge signature) must NOT read as wholesale churn, and
* vertex-level geometry noise must NOT be reported as an edit.

Everything here is offline: diff_snapshots takes parsed dicts.

Run from repo root:
    python -m pytest scripts/tests/test_osm_diff.py -v
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from osm_diff import diff_snapshots, format_report, summarize  # noqa: E402


def _feature(way_ids, coords, *, route_id="1", trail="", imba="", oneway=""):
    return {
        "type": "Feature",
        "properties": {
            "route_id": route_id,
            "trail_name": trail,
            "imba_difficulty": imba,
            "oneway": oneway,
            "way_ids": list(way_ids),
            "shared_routes": [route_id],
        },
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _snap(features, routes=None, ts="2026-07-01T00:00:00Z"):
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "routes": routes if routes is not None else {"1": {
                "name": "Main Loop", "colour": "orange", "ref": "",
                "seasonal": "",
            }},
            "data_timestamp": ts,
        },
    }


# A ~780 m leg and a ~780 m continuation, at UP Michigan latitudes.
_LEG_A = [[-87.60, 46.50], [-87.59, 46.50]]
_LEG_B = [[-87.59, 46.50], [-87.58, 46.50]]


def test_identical_snapshots_report_no_change():
    snap = _snap([_feature([10, 11], _LEG_A, trail="Bootjack")])
    diff = diff_snapshots(snap, snap)
    assert diff["changed"] is False
    assert summarize(diff) == [
        "OSM data is unchanged since the previous snapshot."]
    assert "No route, way, trail, or tag changes." in format_report(diff, "x")


def test_remerge_after_tag_change_is_not_reported_as_churn():
    """The core guarantee: keying on way_ids, not merged features.

    Before, ways 10+11 fuse into one feature. After, way 11 gains an IMBA
    rating so the merge signature differs and it splits into its own
    feature. A feature-level diff would call that one removal plus two
    additions; a way-level diff sees only the tag change.
    """
    before = _snap([_feature([10, 11], _LEG_A + _LEG_B[1:], trail="Bootjack")])
    after = _snap([
        _feature([10], _LEG_A, trail="Bootjack"),
        _feature([11], _LEG_B, trail="Bootjack", imba="3"),
    ])
    diff = diff_snapshots(before, after)
    assert diff["ways_added"] == []
    assert diff["ways_removed"] == []
    assert diff["way_count_old"] == diff["way_count_new"] == 2
    assert diff["trails_added"] == []
    assert diff["trails_removed"] == []
    assert diff["tag_changes"] == [{
        "way_id": "11", "trail": "Bootjack",
        "tag": "mtb:scale:imba", "old": "", "new": "3",
    }]


def test_vertex_noise_is_not_a_length_change():
    """A sub-metre vertex nudge must not surface as an edit."""
    nudged = [[-87.60, 46.50], [-87.59, 46.500002]]
    before = _snap([_feature([10], _LEG_A, trail="Bootjack")])
    after = _snap([_feature([10], nudged, trail="Bootjack")])
    diff = diff_snapshots(before, after)
    assert diff["length_changes"] == []
    assert diff["changed"] is False


def test_real_extension_is_a_length_change():
    before = _snap([_feature([10], _LEG_A, trail="Bootjack")])
    after = _snap([_feature([10], _LEG_A + _LEG_B[1:], trail="Bootjack")])
    diff = diff_snapshots(before, after)
    assert len(diff["length_changes"]) == 1
    change = diff["length_changes"][0]
    assert change["trail"] == "Bootjack"
    assert change["delta_m"] > 700
    assert diff["changed"] is True


def test_trail_rename_detected_from_surviving_way():
    before = _snap([_feature([10], _LEG_A, trail="Old Name")])
    after = _snap([_feature([10], _LEG_A, trail="New Name")])
    diff = diff_snapshots(before, after)
    assert diff["trail_renames"] == [
        {"old": "Old Name", "new": "New Name", "ways": 1}]
    # A rename must not double-report as an add plus a remove.
    assert diff["trails_added"] == []
    assert diff["trails_removed"] == []


def test_added_and_removed_ways_and_trails():
    before = _snap([_feature([10], _LEG_A, trail="Bootjack")])
    after = _snap([
        _feature([10], _LEG_A, trail="Bootjack"),
        _feature([12], _LEG_B, trail="New Trail"),
    ])
    diff = diff_snapshots(before, after)
    assert diff["ways_added"] == ["12"]
    assert diff["ways_removed"] == []
    assert diff["trails_added"] == ["New Trail"]
    assert diff["way_count_old"] == 1
    assert diff["way_count_new"] == 2

    # And the reverse direction.
    back = diff_snapshots(after, before)
    assert back["ways_removed"] == ["12"]
    assert back["trails_removed"] == ["New Trail"]


def test_route_metadata_changes():
    before = _snap([_feature([10], _LEG_A)], routes={
        "1": {"name": "Main Loop", "colour": "orange", "ref": "",
              "seasonal": ""},
        "2": {"name": "Gone", "colour": "blue", "ref": "", "seasonal": ""},
    })
    after = _snap([_feature([10], _LEG_A)], routes={
        "1": {"name": "Main Loop", "colour": "red", "ref": "",
              "seasonal": "winter"},
        "3": {"name": "Fresh", "colour": "green", "ref": "", "seasonal": ""},
    })
    diff = diff_snapshots(before, after)
    assert diff["routes_added"] == [("3", "Fresh")]
    assert diff["routes_removed"] == [("2", "Gone")]
    fields = {(c["field"], c["old"], c["new"]) for c in diff["route_changes"]}
    assert fields == {("colour", "orange", "red"),
                      ("seasonal", "", "winter")}


def test_shared_geometry_counted_once_in_total_length():
    """A run carried by two routes is emitted per route; length must not
    double-count it, or every shared trail would inflate the total."""
    one_route = _snap([_feature([10], _LEG_A, route_id="1", trail="Shared")])
    two_routes = _snap([
        _feature([10], _LEG_A, route_id="1", trail="Shared"),
        _feature([10], _LEG_A, route_id="2", trail="Shared"),
    ])
    a = diff_snapshots(one_route, one_route)
    b = diff_snapshots(two_routes, two_routes)
    assert abs(a["total_length_new_m"] - b["total_length_new_m"]) < 0.001
    # Way membership still records both routes.
    assert b["way_count_new"] == 1


def test_oneway_change_reported():
    before = _snap([_feature([10], _LEG_A, trail="Bootjack")])
    after = _snap([_feature([10], _LEG_A, trail="Bootjack", oneway="yes")])
    diff = diff_snapshots(before, after)
    assert diff["tag_changes"] == [{
        "way_id": "10", "trail": "Bootjack",
        "tag": "oneway", "old": "", "new": "yes",
    }]


def test_report_states_when_a_list_is_truncated():
    """A silently capped list reads as 'that's everything' when it isn't."""
    before = _snap([_feature([10], _LEG_A)])
    after = _snap([_feature([10], _LEG_A)] + [
        _feature([100 + i], _LEG_B, trail=f"Trail {i}") for i in range(60)
    ])
    diff = diff_snapshots(before, after)
    report = format_report(diff, "big")
    assert "and 20 more not listed" in report


def test_empty_snapshots_do_not_crash():
    empty = {"type": "FeatureCollection", "features": [], "metadata": {}}
    diff = diff_snapshots(empty, empty)
    assert diff["changed"] is False
    assert diff["way_count_new"] == 0
    # Report generation must survive a metadata-less snapshot too.
    assert "unknown" in format_report(diff, "empty")


def test_malformed_geometry_is_skipped_not_fatal():
    bad = _snap([{
        "type": "Feature",
        "properties": {"route_id": "1", "way_ids": [10], "trail_name": "Bad"},
        "geometry": {"type": "Point", "coordinates": [-87.6, 46.5]},
    }])
    diff = diff_snapshots(bad, bad)
    assert diff["total_length_new_m"] == 0.0
    assert diff["way_count_new"] == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
