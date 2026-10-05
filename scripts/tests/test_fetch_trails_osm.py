"""An OSM-file map whose config names relations the file does not hold
stops at the parse with a message about the ids, instead of writing an
empty trails file and failing later on the bounding box. The usual
cause is a JOSM save renumbering its negative ids."""


import pytest
from fetch_trails import fetch_trails

TINY_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='JOSM'>
  <node id='-1' lat='46.5' lon='-87.6' />
  <node id='-2' lat='46.51' lon='-87.61' />
  <way id='-3'>
    <nd ref='-1' />
    <nd ref='-2' />
    <tag k='highway' v='path' />
  </way>
  <relation id='-190'>
    <member type='way' ref='-3' role='' />
    <tag k='type' v='route' />
    <tag k='route' v='mtb' />
    <tag k='name' v='Massive Fallout 2026' />
  </relation>
</osm>
"""


def test_missing_relation_ids_stop_at_the_parse(tmp_path, capsys):
    osm = tmp_path / "mfo.osm"
    osm.write_text(TINY_OSM)
    config = {"name": "MFO", "slug": "mfo", "relations": [-70], "osm_file": str(osm)}
    with pytest.raises(SystemExit):
        fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    out = capsys.readouterr()
    assert "None of the relations [-70]" in out.out + out.err
    assert not (tmp_path / "trails.geojson").exists()


def test_present_relation_id_resolves(tmp_path):
    osm = tmp_path / "mfo.osm"
    osm.write_text(TINY_OSM)
    config = {"name": "MFO", "slug": "mfo", "relations": [-190], "osm_file": str(osm)}
    fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    assert (tmp_path / "trails.geojson").exists()


# S1 (1) holds S2 (2) and C1 (10); S2 holds C2 (20); 30 is a plain leaf.
_RELS = {1: ([2, 10], []), 2: ([20], []), 10: ([], [100]), 20: ([], [200]), 30: ([], [300])}
# Way 100 lies well outside the bbox of 30's way, so clipping route 10 drops it.
_WAYS = {100: (0.05, 0.0), 200: (0.001, 0.0), 300: (0.002, 0.0)}


def _osm_text():
    out = ["<osm version='0.6'>"]
    for wid, (lon, lat) in _WAYS.items():
        out.append(f"<node id='{wid}1' lat='{46 + lat}' lon='{-87 + lon}' />")
        out.append(f"<node id='{wid}2' lat='{46.001 + lat}' lon='{-87 + lon}' />")
        out.append(f"<way id='{wid}'><nd ref='{wid}1' /><nd ref='{wid}2' /></way>")
    for rid, (children, ways) in _RELS.items():
        out.append(f"<relation id='{rid}'><tag k='name' v='R{rid}' />")
        out += [f"<member type='relation' ref='{c}' role='' />" for c in children]
        out += [f"<member type='way' ref='{w}' role='' />" for w in ways]
        out.append("</relation>")
    out.append("</osm>")
    return "".join(out)


def _overpass(query, cache_dir, **kw):
    if kw["label"] == "relations":
        return {"elements": [
            {"type": "relation", "id": rid, "tags": {"name": f"R{rid}"},
             "members": [{"type": "relation", "ref": c, "role": ""} for c in ch]
             + [{"type": "way", "ref": w, "role": ""} for w in ws]}
            for rid, (ch, ws) in _RELS.items()]}
    elements = []
    for rid, (_ch, ws) in _RELS.items():
        if f"relation({rid})" not in query:
            continue
        elements.append({"type": "relation", "id": rid})
        for w in ws:
            lon, lat = _WAYS[w]
            elements.append({"type": "way", "id": w, "geometry": [
                {"lon": -87 + lon, "lat": 46 + lat}, {"lon": -87 + lon, "lat": 46.001 + lat}]})
    return {"elements": elements}


@pytest.mark.parametrize("relations, clipped", [
    ([1, 2], []),        # nested supers listed together
    ([30, 10], [10]),    # a relation in both lists
])
def test_the_file_and_overpass_paths_give_the_same_routes(tmp_path, monkeypatch,
                                                          relations, clipped):
    import fetch_trails

    monkeypatch.setattr(fetch_trails, "overpass_query", _overpass)
    base = {"name": "M", "slug": "m", "relations": relations, "clipped_relations": clipped}
    via_api = fetch_trails.fetch_trails(base, str(tmp_path / "a.geojson"),
                                        cache_dir=str(tmp_path / "c"))
    osm = tmp_path / "t.osm"
    osm.write_text(_osm_text(), encoding="utf-8")
    via_file = fetch_trails.fetch_trails(dict(base, osm_file=str(osm)),
                                         str(tmp_path / "b.geojson"),
                                         cache_dir=str(tmp_path / "c"))

    def routes(gj):
        return (sorted(gj["metadata"]["routes"]),
                sorted((f["properties"]["route_id"], f["geometry"]["coordinates"])
                       for f in gj["features"]))

    assert routes(via_file) == routes(via_api)


def test_a_relation_cycle_in_a_file_stops_like_overpass(tmp_path, capsys):
    osm = tmp_path / "t.osm"
    osm.write_text(
        "<osm version='0.6'>"
        "<relation id='1'><member type='relation' ref='2' role='' /></relation>"
        "<relation id='2'><member type='relation' ref='1' role='' /></relation>"
        "</osm>", encoding="utf-8")
    config = {"name": "M", "slug": "m", "relations": [1, 2], "osm_file": str(osm)}
    with pytest.raises(SystemExit):
        fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    out = capsys.readouterr()
    assert "resolved to no routes" in out.out + out.err
    assert not (tmp_path / "trails.geojson").exists()


def test_a_file_without_the_member_ways_stops_the_fetch(tmp_path, capsys):
    osm = tmp_path / "t.osm"
    osm.write_text(
        "<osm version='0.6'><relation id='-5'><member type='way' ref='-9' role='' />"
        "<tag k='name' v='Loop' /></relation></osm>", encoding="utf-8")
    config = {"name": "M", "slug": "m", "relations": [-5], "osm_file": str(osm)}
    with pytest.raises(SystemExit):
        fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    out = capsys.readouterr()
    assert "No ways of relations [-5]" in out.out + out.err
    assert not (tmp_path / "trails.geojson").exists()
