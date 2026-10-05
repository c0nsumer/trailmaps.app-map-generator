"""Tests for osm_parser.py on a tiny inline .osm file.

This parser feeds every OSM-file map, so it must produce the same shapes
the Overpass path does.
"""

import pytest
from osm_parser import (
    detect_super_expansions,
    extract_pois,
    extract_relations,
    extract_source_relations,
    extract_ways,
    parse_osm_file,
    relation_info,
    resolve_relations,
)

OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6'>
  <node id='1' lat='46.50' lon='-87.60' />
  <node id='2' lat='46.51' lon='-87.61' />
  <node id='3' lat='46.52' lon='-87.62' />
  <node id='4' lat='46.50' lon='-87.60'>
    <tag k='tourism' v='information' />
    <tag k='information' v='guidepost' />
  </node>
  <node id='5' lat='50.00' lon='-80.00'>
    <tag k='tourism' v='information' />
    <tag k='information' v='guidepost' />
  </node>
  <node id='6' lat='46.5000' lon='-87.6000' />
  <node id='7' lat='46.5100' lon='-87.6000' />
  <node id='8' lat='46.5100' lon='-87.6100' />
  <node id='9' lat='46.5000' lon='-87.6100' />
  <way id='20'>
    <nd ref='1' /><nd ref='2' /><nd ref='3' />
    <tag k='highway' v='path' />
    <tag k='name' v='Ridge' />
  </way>
  <way id='21'>
    <nd ref='3' /><nd ref='99' />
    <tag k='highway' v='path' />
  </way>
  <way id='22'>
    <nd ref='6' /><nd ref='7' /><nd ref='8' /><nd ref='9' /><nd ref='6' />
    <tag k='amenity' v='toilets' />
  </way>
  <relation id='100'>
    <member type='way' ref='20' role='' />
    <member type='way' ref='21' role='' />
    <member type='way' ref='555' role='' />
    <member type='node' ref='1' role='' />
    <tag k='type' v='route' />
    <tag k='name' v='Ridge Loop' />
    <tag k='colour' v='#aa0000' />
    <tag k='ref' v='R1' />
  </relation>
  <relation id='101'>
    <member type='way' ref='20' role='' />
    <tag k='type' v='route' />
  </relation>
  <relation id='200'>
    <member type='relation' ref='100' role='' />
    <member type='relation' ref='101' role='' />
    <member type='relation' ref='999' role='' />
    <tag k='type' v='superroute' />
  </relation>
</osm>
"""


@pytest.fixture
def parsed(tmp_path):
    path = tmp_path / "t.osm"
    path.write_text(OSM, encoding="utf-8")
    return parse_osm_file(str(path))


def test_parse_reads_nodes_ways_and_relations(parsed):
    nodes, ways, relations = parsed
    assert nodes[1] == (-87.60, 46.50, {})
    assert nodes[4][2] == {"tourism": "information", "information": "guidepost"}
    assert ways[20] == {"id": 20, "nd_refs": [1, 2, 3], "tags": {"highway": "path", "name": "Ridge"}}
    assert relations[100]["members"][0] == {"type": "way", "ref": 20, "role": ""}
    assert relations[100]["tags"]["name"] == "Ridge Loop"


def test_relation_info_defaults_the_name_and_leaves_colour_unset():
    assert relation_info(7, {}) == {
        "id": 7,
        "name": "Route 7",
        "colour": None,
        "ref": "",
        "seasonal": "",
    }


def test_leaf_relation_resolves_to_its_own_info(parsed):
    resolved, expansions = extract_source_relations(parsed, [100])
    assert expansions == {}
    assert resolved[100]["name"] == "Ridge Loop"
    assert resolved[100]["colour"] == "#aa0000"
    assert resolved[100]["ref"] == "R1"


def test_super_relation_expands_to_children_that_exist(parsed):
    resolved, expansions = extract_source_relations(parsed, [200])
    # 999 is not in the file, so it is not a child.
    assert expansions == {200: [100, 101]}
    assert sorted(resolved) == [100, 101]


def test_missing_relation_is_skipped_with_a_warning(parsed, capsys):
    resolved, _ = extract_source_relations(parsed, [100, 4242])
    assert sorted(resolved) == [100]
    out = capsys.readouterr()
    assert "4242" in out.out + out.err


def test_detect_super_expansions_ignores_leaves(parsed):
    _, _, relations = parsed
    assert detect_super_expansions([100, 200], relations) == {200: [100, 101]}


def test_ways_resolve_node_refs_to_lon_lat_pairs(parsed):
    all_ways = extract_ways(parsed, [100])
    assert all_ways[100][20]["coords"] == [[-87.60, 46.50], [-87.61, 46.51], [-87.62, 46.52]]
    assert all_ways[100][20]["tags"]["name"] == "Ridge"


def test_ways_with_under_two_known_nodes_and_missing_ways_are_dropped(parsed):
    # Way 21 keeps one resolvable node; way 555 does not exist.
    assert list(extract_ways(parsed, [100])[100]) == [20]


def test_pois_inside_the_bbox_include_nodes_and_building_centroids(parsed):
    elements = extract_pois(parsed, [-88, 46, -87, 47])["elements"]
    by_id = {(e["type"], e["id"]) for e in elements}
    # Node 5 is outside the bbox; node 6-9 form the toilets building.
    assert by_id == {("node", 4), ("way", 22)}
    building = next(e for e in elements if e["type"] == "way")
    assert building["center"] == {"lon": pytest.approx(-87.605), "lat": pytest.approx(46.505)}


def test_an_empty_name_tag_gets_the_fallback_name():
    assert relation_info(7, {"name": ""})["name"] == "Route 7"


def test_objects_deleted_in_josm_are_skipped(tmp_path):
    path = tmp_path / "d.osm"
    path.write_text(
        "<osm version='0.6'>"
        "<node id='1' lat='46.5' lon='-87.6' action='delete'>"
        "<tag k='tourism' v='information' /><tag k='information' v='guidepost' /></node>"
        "<node id='2' lat='46.5' lon='-87.6' action='modify' />"
        "<relation id='9' action='delete'><tag k='name' v='Gone' /></relation>"
        "</osm>",
        encoding="utf-8",
    )
    nodes, _ways, relations = parse_osm_file(str(path))
    assert list(nodes) == [2]
    assert relations == {}


def _rel(*children, ways=()):
    members = [{"type": "relation", "ref": c, "role": ""} for c in children]
    members += [{"type": "way", "ref": w, "role": ""} for w in ways]
    return {"members": members}


def test_nested_supers_listed_together_resolve_to_leaves_only():
    # S1 holds S2 and C1, S2 holds C2; the config lists both supers.
    available = {1: _rel(2, 10), 2: _rel(20), 10: _rel(ways=[1]), 20: _rel(ways=[2])}
    members, clipped, expansions = resolve_relations([1, 2], [], available)
    assert sorted(members) == [10, 20]
    assert clipped == {}
    assert expansions == {1: [2, 10], 2: [20]}


def test_a_relation_in_both_lists_stays_a_source_route():
    available = {10: _rel(ways=[1]), 11: _rel(ways=[2])}
    members, clipped, _ = resolve_relations([10], [10, 11], available)
    assert list(members) == [10]
    assert list(clipped) == [11]


def test_a_relation_cycle_resolves_to_nothing():
    available = {1: _rel(2), 2: _rel(1)}
    assert resolve_relations([1, 2], [], available)[:2] == ({}, {})


def test_the_file_path_resolves_like_the_overpass_path(parsed):
    # 200 holds 100 and 101; listing 100 as clipped too keeps it a source route.
    members, clipped, expansions = extract_relations(parsed, [200], [100])
    assert sorted(members) == [100, 101]
    assert clipped == {}
    assert expansions == {200: [100, 101]}
    assert members[100]["name"] == "Ridge Loop"
