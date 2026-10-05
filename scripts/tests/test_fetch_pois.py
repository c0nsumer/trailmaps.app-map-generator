"""build_pois_geojson: which OSM tags become which poi_type, and which
show_* flag drops each one. Pure and offline: the Overpass response is a
hand-built `elements` list."""

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
