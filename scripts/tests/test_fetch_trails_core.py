"""Tests for the pure core of fetch_trails.py on small hand-built inputs.

The geometry helpers decide what every rider sees: which ways fuse into one
feature, where a line is cut at the map edge, and which direction a
one-way arrow points.
"""

import pytest
from fetch_trails import (
    _compass_bearing,
    _expand_through_supers,
    _is_bike_only,
    _is_freeride,
    _resolve_oneway,
    build_geojson,
    build_way_to_relations_map,
    clip_line_to_bbox,
    gather_relation_ids,
    merge_consecutive_ways,
)

BBOX = [0, 0, 10, 10]


def _way(*coords, **tags):
    return {"coords": [list(c) for c in coords], "tags": tags}


# --- _is_freeride ---------------------------------------------------------


def test_freeride_is_found_anywhere_in_the_mtb_type_list():
    assert _is_freeride({"mtb:type": "freeride"})
    assert _is_freeride({"mtb:type": "allmountain; freeride"})


def test_downhill_and_untagged_ways_are_not_freeride():
    assert not _is_freeride({"mtb:type": "downhill;singletrack"})
    assert not _is_freeride({})


# --- _resolve_oneway -------------------------------------------------------


def test_oneway_bicycle_wins_over_oneway():
    assert _resolve_oneway({"oneway": "yes", "oneway:bicycle": "no"}) == "no"


def test_oneway_falls_back_to_the_generic_tag():
    assert _resolve_oneway({"oneway": "-1"}) == "-1"


def test_oneway_is_empty_when_neither_tag_is_set():
    assert _resolve_oneway({"name": "x"}) == ""


# --- _expand_through_supers and gather_relation_ids --------------------------


def test_super_relations_are_replaced_by_their_children():
    assert _expand_through_supers([1, 2], {1: [5, 6]}) == {2, 5, 6}


def test_leaf_ids_pass_through_unchanged():
    assert _expand_through_supers([1, 2], {}) == {1, 2}


def test_gather_folds_every_bucket_into_the_fetch_set():
    config = {
        "relations": [1, 2],
        "winter_relations": [2, 3],
        "summer_relations": [4],
        "emergency_access_relations": [5],
        "clipped_relations": [9],
    }
    ids, clipped = gather_relation_ids(config)
    assert sorted(ids) == [1, 2, 3, 4, 5]
    assert clipped == [9]


def test_gather_on_an_empty_config_returns_empty_lists():
    assert gather_relation_ids({}) == ([], [])


# --- merge_consecutive_ways ------------------------------------------------


def test_consecutive_same_name_ways_fuse_into_one_segment():
    ways = {1: _way((0, 0), (1, 0), name="A"), 2: _way((1, 0), (2, 0), name="A")}
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert seg["coords"] == [[0, 0], [1, 0], [2, 0]]
    assert seg["way_ids"] == [1, 2]
    assert seg["shared_routes"] == [10]
    assert seg["trail_name"] == "A"


def test_a_way_digitized_backward_is_flipped_to_fuse():
    ways = {1: _way((0, 0), (1, 0)), 2: _way((2, 0), (1, 0))}
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert seg["coords"] == [[0, 0], [1, 0], [2, 0]]


def _fused(ways, order):
    """Fusion of ``ways`` as one relation listing them in ``order``."""
    listed = {wid: ways[wid] for wid in order}
    segs = merge_consecutive_ways(listed, {wid: {10, 11} for wid in listed})
    return sorted((s["way_ids"], s["coords"]) for s in segs)


def test_a_shared_ring_fuses_the_same_way_in_any_member_order():
    # Two relations list the halves of a loop in opposite order. Each must
    # fuse the pair identically, or the runtime sees two runs for one trail.
    ways = {1: _way((0, 0), (1, 1), (0, 2), name="Ring"),
            2: _way((0, 2), (-1, 1), (0, 0), name="Ring")}
    assert _fused(ways, [1, 2]) == _fused(ways, [2, 1])


def test_a_shared_wye_fuses_the_same_way_in_any_member_order():
    # Three same-named ways meet at one node; which pair fuses must not
    # depend on which way a relation lists first.
    ways = {1: _way((0, 0), (1, 0), name="Wye"),
            2: _way((1, 0), (2, 1), name="Wye"),
            3: _way((1, 0), (2, -1), name="Wye")}
    results = {repr(_fused(ways, order))
               for order in ([1, 2, 3], [2, 3, 1], [3, 1, 2], [3, 2, 1], [2, 1, 3])}
    assert len(results) == 1


def test_a_one_way_chain_keeps_its_direction_in_any_member_order():
    ways = {1: _way((0, 0), (1, 0), oneway="yes"),
            2: _way((1, 0), (2, 0), oneway="yes")}
    for order in ([1, 2], [2, 1]):
        (seg,) = merge_consecutive_ways({w: ways[w] for w in order}, {1: {10}, 2: {10}})
        assert seg["coords"] == [[0, 0], [1, 0], [2, 0]]


def test_a_name_change_splits_the_segment():
    ways = {1: _way((0, 0), (1, 0), name="A"), 2: _way((1, 0), (2, 0), name="B")}
    segs = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert [s["trail_name"] for s in segs] == ["A", "B"]


def test_bike_only_needs_bikes_allowed_foot_no_and_no_horse():
    assert _is_bike_only({"bicycle": "designated", "foot": "no", "horse": "no"})
    assert _is_bike_only({"bicycle": "yes", "foot": "no"})
    assert _is_bike_only({"bicycle": "permissive", "foot": "no"})
    assert not _is_bike_only({"bicycle": "designated", "foot": "no", "horse": "permissive"})
    assert not _is_bike_only({"bicycle": "designated", "foot": "yes", "horse": "no"})
    assert not _is_bike_only({"bicycle": "designated", "foot": "no", "horse": "yes"})
    assert not _is_bike_only({"foot": "no"})
    assert not _is_bike_only({})


def test_a_bike_only_change_splits_the_segment():
    ways = {1: _way((0, 0), (1, 0), name="A", bicycle="designated", foot="yes"),
            2: _way((1, 0), (2, 0), name="A", bicycle="designated", foot="no")}
    segs = merge_consecutive_ways(ways, {1: {10}, 2: {10}}, itrs=True)
    assert sorted(s["bike_only"] for s in segs) == [False, True]
    # An IMBA map never draws the plate, so the same pair stays one feature.
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert seg["bike_only"] is False


def test_a_freeride_change_splits_the_segment():
    ways = {1: _way((0, 0), (1, 0), name="A"),
            2: _way((1, 0), (2, 0), name="A", **{"mtb:type": "freeride"})}
    segs = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert sorted(s["freeride"] for s in segs) == [False, True]


def test_different_route_membership_splits_the_segment():
    ways = {1: _way((0, 0), (1, 0)), 2: _way((1, 0), (2, 0))}
    segs = merge_consecutive_ways(ways, {1: {10}, 2: {10, 11}})
    assert [s["shared_routes"] for s in segs] == [[10], [10, 11]]


def test_oneway_ways_never_fuse_against_their_direction():
    # Reversing way 2 to glue it on would invert its tagged direction.
    ways = {
        1: _way((0, 0), (1, 0), oneway="yes"),
        2: _way((2, 0), (1, 0), oneway="yes"),
    }
    assert len(merge_consecutive_ways(ways, {1: {10}, 2: {10}})) == 2


def test_oneway_ways_fuse_when_they_agree_on_direction():
    ways = {
        1: _way((0, 0), (1, 0), oneway="yes"),
        2: _way((1, 0), (2, 0), oneway="yes"),
    }
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert seg["oneway"] == "yes"


def test_one_way_and_two_way_ways_stay_separate():
    ways = {1: _way((0, 0), (1, 0), oneway="yes"), 2: _way((1, 0), (2, 0))}
    assert len(merge_consecutive_ways(ways, {1: {10}, 2: {10}})) == 2


def test_merging_nothing_returns_nothing():
    assert merge_consecutive_ways({}, {}) == []


# --- clip_line_to_bbox -----------------------------------------------------


def test_a_line_inside_the_bbox_is_returned_unclipped():
    assert clip_line_to_bbox([[1, 1], [5, 5]], BBOX) == [([[1, 1], [5, 5]], False, False)]


def test_a_line_leaving_the_bbox_is_cut_at_the_edge_and_flagged():
    assert clip_line_to_bbox([[5, 5], [15, 5]], BBOX) == [([[5, 5], [10, 5]], False, True)]


def test_a_line_entering_the_bbox_flags_its_start():
    assert clip_line_to_bbox([[-5, 5], [5, 5]], BBOX) == [([[0, 5], [5, 5]], True, False)]


def test_a_line_outside_the_bbox_vanishes():
    assert clip_line_to_bbox([[20, 20], [30, 30]], BBOX) == []


def test_a_line_that_exits_and_re_enters_becomes_two_segments():
    line = [[5, 5], [15, 5], [15, 8], [5, 8]]
    assert clip_line_to_bbox(line, BBOX) == [
        ([[5, 5], [10, 5]], False, True),
        ([[10, 8], [5, 8]], True, False),
    ]


# --- build_geojson ---------------------------------------------------------


def _build(relations, all_ways):
    return build_geojson(relations, all_ways, build_way_to_relations_map(all_ways))


def test_features_carry_the_route_and_segment_properties():
    relations = {10: {"name": "Loop", "colour": "red"}}
    fc = _build(relations, {10: {1: _way((0, 0), (1, 0), name="A", **{"mtb:scale:imba": "2"})}})
    (feat,) = fc["features"]
    assert feat["geometry"] == {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}
    assert feat["properties"] == {
        "route_id": 10,
        "route_name": "Loop",
        "route_colour": "red",
        "trail_name": "A",
        "shared_routes": [10],
        "imba_difficulty": "2",
        "freeride": False,
        "itrs_difficulty": "",
        "bike_only": False,
        "oneway": "",
        "way_ids": [1],
    }


def test_oneway_minus_one_becomes_a_forward_oneway_with_reversed_coordinates():
    relations = {10: {"name": "Loop", "colour": None}}
    fc = _build(relations, {10: {1: _way((0, 0), (1, 0), oneway="-1")}})
    (feat,) = fc["features"]
    assert feat["geometry"]["coordinates"] == [[1, 0], [0, 0]]
    assert feat["properties"]["oneway"] == "yes"


def test_a_way_shared_by_two_routes_is_emitted_once_per_route():
    relations = {10: {"name": "A", "colour": None}, 11: {"name": "B", "colour": None}}
    shared = _way((0, 0), (1, 0))
    fc = _build(relations, {10: {1: shared}, 11: {1: shared}})
    assert [(f["properties"]["route_id"], f["properties"]["shared_routes"]) for f in fc[
        "features"
    ]] == [(10, [10, 11]), (11, [10, 11])]


def test_relations_are_ordered_by_name_and_empty_ones_are_skipped():
    relations = {
        10: {"name": "Zed", "colour": None},
        11: {"name": "Alpha", "colour": None},
        12: {"name": "Empty", "colour": None},
    }
    all_ways = {10: {1: _way((0, 0), (1, 0))}, 11: {2: _way((5, 5), (6, 5))}, 12: {}}
    fc = _build(relations, all_ways)
    assert [f["properties"]["route_name"] for f in fc["features"]] == ["Alpha", "Zed"]


def test_a_line_that_only_touches_the_bbox_yields_nothing():
    # Both lines meet the box at one point: no length, no arrowhead.
    assert clip_line_to_bbox([[-1, 9], [1, 11]], BBOX) == []
    assert clip_line_to_bbox([[-5, 5], [0, 5], [-5, 6]], BBOX) == []


def test_a_repeated_vertex_on_the_edge_does_not_set_the_bearing():
    # The first two points coincide; the arrowhead reads seg[1] -> seg[0].
    assert clip_line_to_bbox([[0, 5], [0, 5], [5, 5], [15, 5]], BBOX) == [
        ([[0, 5], [5, 5], [10, 5]], False, True),
    ]


# --- fetch stages, Overpass canned -----------------------------------------


def test_a_ways_response_with_no_ways_stops_the_fetch(tmp_path, monkeypatch, capsys):
    import fetch_trails
    import pytest

    rels = {"elements": [{"type": "relation", "id": 1, "tags": {"name": "A"},
                          "members": [{"type": "way", "ref": 5, "role": ""}]}]}
    ways = {"elements": [{"type": "relation", "id": 1}]}
    monkeypatch.setattr(fetch_trails, "overpass_query",
                        lambda q, c, **kw: rels if kw["label"] == "relations" else ways)
    out = tmp_path / "trails.geojson"
    with pytest.raises(SystemExit):
        fetch_trails.fetch_trails({"name": "M", "slug": "m", "relations": [1]}, str(out),
                                  cache_dir=str(tmp_path))
    assert "returned no ways" in capsys.readouterr().out
    assert not out.exists()


def test_a_rejected_query_fails_at_once_without_retrying(tmp_path, monkeypatch, capsys):
    import overpass
    import pytest

    class Resp:
        status_code = 400
        text = ("<?xml version='1.0'?>\n<html><body>\n"
                "<p><strong>Error</strong>: line 3: parse error: ';' expected</p>\n")

    calls = []
    monkeypatch.setattr(overpass.requests, "post", lambda *a, **k: calls.append(1) or Resp())
    monkeypatch.setattr(overpass.time, "sleep", lambda s: pytest.fail("must not retry"))
    with pytest.raises(SystemExit):
        overpass.query("[out:json];bad", cache_dir=str(tmp_path), label="ways")
    assert len(calls) == 1
    out = capsys.readouterr()
    assert "HTTP 400): Error: line 3: parse error: ';' expected" in out.out + out.err


def test_an_exposure_change_does_not_split_the_segment():
    # Exposure rates spots (nodes), so the way tag, a max, is not read.
    ways = {
        1: _way((0, 0), (1, 0), name="A", **{"mtb:scale:itrs:exposure": "1"}),
        2: _way((1, 0), (2, 0), name="A", **{"mtb:scale:itrs:exposure": "3"}),
    }
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}}, itrs=True)
    assert "itrs_exposure" not in seg


def test_an_itrs_change_splits_the_segment():
    ways = {
        1: _way((0, 0), (1, 0), name="A", **{"mtb:scale:itrs:technical_difficulty": "2"}),
        2: _way((1, 0), (2, 0), name="A", **{"mtb:scale:itrs:technical_difficulty": "3"}),
    }
    assert len(merge_consecutive_ways(ways, {1: {10}, 2: {10}}, itrs=True)) == 2


def test_an_imba_map_neither_reads_nor_splits_on_itrs_tags():
    ways = {
        1: _way((0, 0), (1, 0), name="A", **{"mtb:scale:itrs:technical_difficulty": "2"}),
        2: _way((1, 0), (2, 0), name="A", **{"mtb:scale:itrs:technical_difficulty": "3"}),
    }
    (seg,) = merge_consecutive_ways(ways, {1: {10}, 2: {10}})
    assert seg["itrs_difficulty"] == ""


def test_untagged_ways_carry_empty_itrs_properties():
    ways = {1: _way((0, 0), (1, 0), name="A")}
    (seg,) = merge_consecutive_ways(ways, {1: {10}})
    assert seg["itrs_difficulty"] == ""


def test_itrs_tags_reach_the_feature_properties(tmp_path):
    import json
    import os

    from fetch_trails import fetch_trails

    osm = os.path.join(os.path.dirname(__file__), "itrs_fixture.osm")
    config = {"name": "H", "slug": "h", "relations": [-190], "osm_file": osm,
              "difficulty_scale": "itrs"}
    out = tmp_path / "trails.geojson"
    fetch_trails(config, str(out), cache_dir=str(tmp_path / "c"))
    props = [f["properties"] for f in json.loads(out.read_text())["features"]]
    # The tagged pair differs only in exposure, which no longer splits, so it
    # is one feature; the untagged third way differs from it.
    assert sorted(p["itrs_difficulty"] for p in props) == ["", "2"]
    assert all("itrs_exposure" not in p for p in props)
    assert all(p["imba_difficulty"] == "" for p in props)


# --- _compass_bearing -----------------------------------------------------

def test_compass_bearing_cardinals():
    assert _compass_bearing([0, 0], [0, 1]) == pytest.approx(0.0)
    assert _compass_bearing([0, 0], [1, 0]) == pytest.approx(90.0)
    assert _compass_bearing([0, 0], [0, -1]) == pytest.approx(180.0)
    assert _compass_bearing([0, 0], [-1, 0]) == pytest.approx(270.0)


def test_compass_bearing_scales_longitude_by_cos_of_latitude():
    # One degree north and one east at ~60.5 N: east is only cos(60.5 deg)
    # = 0.4924 as long as north, so atan2(0.4924, 1) = 26.22 degrees, not 45.
    assert _compass_bearing([0, 60], [1, 61]) == pytest.approx(26.2167, abs=0.001)
