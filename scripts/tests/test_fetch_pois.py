"""build_pois_geojson: which OSM tags become which poi_type, and which
show_* flag drops each one. Pure and offline: the Overpass response is a
hand-built `elements` list."""

import console
import pytest
from fetch_pois import POI_SHOW_FLAGS, build_pois_geojson

# (poi_type, OSM tags, the show_* flag that drops it). Each node sits far
# from the others so the 10 m duplicate collapse never merges two of them.
_CASES = [
    ("trail_marker", {"tourism": "information", "information": "guidepost"}, "show_markers"),
    ("trail_marker", {"highway": "emergency_access_point"}, "show_markers"),
    ("feature", {"tourism": "attraction"}, "show_features"),
    ("toilet", {"amenity": "toilets"}, "show_toilets"),
    ("drinking_water", {"amenity": "drinking_water"}, "show_drinking_water"),
    ("bicycle_repair_station", {"amenity": "bicycle_repair_station"},
     "show_bicycle_repair_stations"),
]


def _elements():
    out = []
    for i, (_ptype, tags, _flag) in enumerate(_CASES):
        out.append({"type": "node", "id": i + 1, "lat": 46.0 + i * 0.01,
                    "lon": -88.0, "tags": dict(tags, name=f"N{i}")})
    return {"elements": out}


def _types(config=None, osm=None):
    fc = build_pois_geojson(osm or _elements(), [], [], config=config)
    return [f["properties"]["poi_type"] for f in fc["features"]]


def test_each_recognized_tag_gets_its_poi_type():
    fc = build_pois_geojson(_elements(), [], [])
    got = [(f["properties"]["name"], f["properties"]["poi_type"]) for f in fc["features"]]
    assert got == [(f"N{i}", c[0]) for i, c in enumerate(_CASES)]


@pytest.mark.parametrize("ptype,flag", sorted({(c[0], c[2]) for c in _CASES}))
def test_a_false_show_flag_drops_only_its_type(ptype, flag):
    assert flag in POI_SHOW_FLAGS
    kept = _types({flag: False})
    assert ptype not in kept
    expected = [c[0] for c in _CASES if c[0] != ptype]
    assert kept == expected


def test_a_way_is_placed_at_its_center_and_an_untagged_way_is_skipped():
    osm = {"elements": [
        {"type": "way", "id": 1, "center": {"lat": 46.5, "lon": -88.5},
         "tags": {"amenity": "toilets"}},
        {"type": "way", "id": 2, "tags": {"amenity": "toilets"}},
        {"type": "node", "id": 3, "lat": 46.7, "lon": -88.7, "tags": {"amenity": "bench"}},
    ]}
    fc = build_pois_geojson(osm, [], [])
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [[-88.5, 46.5]]


def test_config_pois_gate_on_their_flags_and_event_pois_do_not():
    parking = [{"name": "P", "coordinates": [-88.0, 46.0]}]
    heads = [{"name": "T", "coordinates": [-88.1, 46.1]}]
    hubs = [{"name": "H", "coordinates": [-88.2, 46.2]}]
    event = [{"name": "E", "coordinates": [-88.3, 46.3]}]

    def types(cfg):
        fc = build_pois_geojson({"elements": []}, parking, heads, hubs, event, cfg)
        return [f["properties"]["poi_type"] for f in fc["features"]]

    assert types(None) == ["parking", "trailhead", "hub", "event"]
    assert types({"show_parking": False}) == ["trailhead", "hub", "event"]
    assert types({"show_trailheads": False}) == ["parking", "hub", "event"]
    assert types({"show_hubs": False}) == ["parking", "trailhead", "event"]
    assert types({k: False for k in POI_SHOW_FLAGS}) == ["event"]


def test_event_poi_directions_defaults_off_in_the_poi_data():
    # The popup offers "Get Directions" only where the curator asked:
    # event parking is driven to, a start line is a plain flag.
    pois = [
        {"name": "Start / Finish", "coordinates": [-83.1, 42.4]},
        {"name": "Event Parking", "coordinates": [-83.2, 42.5], "directions": True},
        {"name": "Aid 1", "coordinates": [-83.3, 42.6], "directions": False},
    ]
    fc = build_pois_geojson({"elements": []}, [], [], config_event_pois=pois)
    got = {f["properties"]["name"]: f["properties"]["directions"] for f in fc["features"]}
    assert got == {"Start / Finish": False, "Event Parking": True, "Aid 1": False}


# --- ITRS exposure points -----------------------------------------------------

_BBOX = [-87.7, 46.4, -87.5, 46.6]
_ITRS = {"difficulty_scale": "itrs"}
_VERTICES = {(-87.6, 46.5): "Highlands", (-87.6, 46.501): "", (-87.6, 46.502): "Highlands"}


def _node(node_id, level, lat=46.5, lon=-87.6):
    return {"type": "node", "id": node_id, "lat": lat, "lon": lon,
            "tags": {"mtb:scale:itrs:exposure": str(level)}}


def _exposure(elements, config=None, vertices=None):
    fc = build_pois_geojson({"elements": elements}, [], [], config=_ITRS if config is None else config,
                            trail_vertices=_VERTICES if vertices is None else vertices)
    return [f["properties"] for f in fc["features"] if f["properties"]["poi_type"] == "exposure"]


def _query_text(monkeypatch, **kwargs):
    import fetch_pois

    seen = []
    monkeypatch.setattr(fetch_pois, "overpass_query", lambda q, *a, **k: seen.append(q) or {})
    fetch_pois.fetch_pois_from_osm(_BBOX, **kwargs)
    return seen[0]


def test_the_exposure_statement_is_added_on_itrs_maps_only(monkeypatch):
    imba = _query_text(monkeypatch)
    itrs = _query_text(monkeypatch, itrs=True)
    assert "mtb:scale:itrs:exposure" not in imba
    stmt = '  node["mtb:scale:itrs:exposure"](46.4,-87.7,46.6,-87.5);\n'
    # After every existing statement, so an IMBA map's query text (its cache
    # key) is untouched and an ITRS map's other categories keep their order.
    assert itrs == imba.replace(");\nout center;", stmt + ");\nout center;")
    assert 'amenity"="toilets"' not in _query_text(monkeypatch, itrs=True, standard=False)


def test_levels_two_to_four_are_kept_with_the_official_name():
    props = _exposure([_node(1, 2), _node(2, 3, lat=46.501), _node(3, 4, lat=46.502)])
    assert [(p["level"], p["name"]) for p in props] == [
        (2, "High risk of serious injury"),
        (3, "Life threatening consequences"),
        (4, "Fatal consequences"),
    ]
    assert props[0]["osm_id"] == 1


def test_level_one_is_dropped_silently_and_out_of_range_with_a_note(capsys, monkeypatch):
    monkeypatch.setattr(console, "_verbosity", console.NORMAL)
    props = _exposure([_node(1, 1), _node(2, 5), _node(3, "blue"), _node(4, 3)])
    assert [p["osm_id"] for p in props] == [4]
    out = capsys.readouterr()
    text = out.out + out.err
    assert "2 exposure point(s) with a value outside 1-4 ignored (node ids 2, 3)" in text
    assert "node ids 1" not in text


def test_a_vertex_match_attaches_the_trail_name():
    props = _exposure([_node(1, 2), _node(2, 3, lat=46.501)])
    assert [p["trail"] for p in props] == ["Highlands", ""]


def test_a_node_off_the_trails_is_dropped_with_a_note(capsys, monkeypatch):
    monkeypatch.setattr(console, "_verbosity", console.NORMAL)
    props = _exposure([_node(1, 2), _node(2, 3, lat=46.9)])
    assert [p["osm_id"] for p in props] == [1]
    out = capsys.readouterr()
    assert "1 exposure point(s) on no trail of this map (node ids 2)" in out.out + out.err


def test_a_shared_vertex_goes_to_the_trail_passing_through_then_alphabetical():
    from fetch_pois import _trail_vertex_names

    def line(name, coords):
        return {"type": "Feature", "properties": {"trail_name": name},
                "geometry": {"type": "LineString", "coordinates": coords}}

    # Zulu passes through (0, 0); Alpha and Mike end there. Interior wins
    # over file order; among trails that only end there, the first name.
    fc = {"type": "FeatureCollection", "features": [
        line("Mike", [[1, 1], [0, 0]]),
        line("Zulu", [[-1, 0], [0, 0], [1, 0]]),
        line("Alpha", [[0, 1], [0, 0]]),
        line("Mike", [[2, 2], [3, 3]]),
        line("Alpha", [[3, 3], [4, 4]]),
    ]}
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        json.dump(fc, f)
    names = _trail_vertex_names(f.name)
    assert names[(0.0, 0.0)] == "Zulu"
    assert names[(3.0, 3.0)] == "Alpha"


def test_an_imba_map_builds_no_exposure_points():
    assert _exposure([_node(1, 3)], config={}) == []


def test_exposure_points_are_never_merged_by_the_duplicate_collapse():
    props = _exposure([_node(1, 2), _node(2, 2)])
    assert len(props) == 2


def test_the_local_osm_path_yields_the_fixture_nodes(tmp_path):
    import json
    import os

    from fetch_pois import fetch_pois
    from fetch_trails import fetch_trails

    osm = os.path.join(os.path.dirname(__file__), "itrs_fixture.osm")
    config = {"name": "H", "slug": "h", "relations": [-190], "osm_file": osm,
              "bbox": _BBOX, "difficulty_scale": "itrs"}
    fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    fc = fetch_pois(config, str(tmp_path / "pois.geojson"), cache_dir=str(tmp_path / "c"))
    exposure = [f["properties"] for f in fc["features"] if f["properties"]["poi_type"] == "exposure"]
    # Node -5 (level 1) is below the threshold; node -6 (level 3) sits on Highlands.
    assert [(p["level"], p["trail"], p["osm_id"]) for p in exposure] == [(3, "Highlands", -6)]
    on_disk = json.loads((tmp_path / "pois.geojson").read_text())
    assert on_disk["features"] == fc["features"]
    # The same file on an IMBA map yields none.
    config.pop("difficulty_scale")
    fc = fetch_pois(config, str(tmp_path / "pois2.geojson"), cache_dir=str(tmp_path / "c"))
    assert not [f for f in fc["features"] if f["properties"]["poi_type"] == "exposure"]
