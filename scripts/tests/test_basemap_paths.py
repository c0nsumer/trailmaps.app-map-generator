"""Generated basemap paths (scripts/basemap_paths.py).

Offline: no Overpass, and the one test that tiles skips where
tippecanoe, tile-join or the pmtiles CLI is not installed.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basemap_paths as bp  # noqa: E402
from pmtiles_util import find_pmtiles_cli  # noqa: E402
from validate_config import (  # noqa: E402
    DEFAULT_BASEMAP_SOURCE,
    effective_basemap_source,
)

# About 1 m per 0.00001 degree of latitude here, which keeps the
# distances below readable.
LAT, LON = 46.5, -87.6
STEP = 0.0001  # about 11 m north, 7.7 m east


def _line(*pts):
    return [[LON + x * STEP, LAT + y * STEP] for x, y in pts]


def _trails(features, routes):
    return {"type": "FeatureCollection", "features": features, "metadata": {"routes": routes}}


def _route_feature(route_id, coords):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"route_id": route_id},
    }


SUMMER = {"summer": True, "winter": False, "emergency": False}
WINTER = {"summer": False, "winter": True, "emergency": False}
BOUNDS = (LON - 0.01, LAT - 0.01, LON + 0.02, LAT + 0.02)


def test_default_is_generated_and_key_is_honored():
    assert DEFAULT_BASEMAP_SOURCE == "generated"
    assert effective_basemap_source({}) == "generated"
    assert effective_basemap_source({"basemap_source": "protomaps"}) == "protomaps"


def test_to_props_follows_the_protomaps_schema():
    assert bp.to_props({"highway": "path", "name": "Cheese Grater"}) == {
        "kind": "path",
        "kind_detail": "path",
        "min_zoom": 14,
        "name": "Cheese Grater",
    }
    assert bp.to_props({"highway": "track"})["min_zoom"] == 13
    sidewalk = bp.to_props({"highway": "footway", "footway": "sidewalk"})
    assert (sidewalk["kind_detail"], sidewalk["min_zoom"]) == ("sidewalk", 15)
    private = bp.to_props({"highway": "track", "access": "private"})
    assert (private["access"], private["min_zoom"]) == ("private", 16)
    assert bp.to_props({"man_made": "pier"})["kind_detail"] == "pier"
    bridge = bp.to_props({"highway": "path", "bridge": "yes", "oneway": "yes", "tunnel": "no"})
    assert bridge["is_bridge"] is True and bridge["oneway"] == "yes" and "is_tunnel" not in bridge


def test_tile_cover_grows_outward_to_whole_tiles():
    w, s, e, n = bp.tile_cover_bounds(BOUNDS, 12)
    assert w <= BOUNDS[0] and s <= BOUNDS[1] and e >= BOUNDS[2] and n >= BOUNDS[3]
    for value in (bp._tile_x(w, 12), bp._tile_x(e, 12), bp._tile_y(s, 12), bp._tile_y(n, 12)):
        assert value == pytest.approx(round(value), abs=1e-6), "edges sit on z12 tile lines"
    # no more than one tile of growth on any side
    assert bp._tile_x(BOUNDS[0], 12) - bp._tile_x(w, 12) < 1
    assert bp._tile_x(e, 12) - bp._tile_x(BOUNDS[2], 12) <= 1


def test_local_file_wins_per_way_and_adds_its_own():
    fetched = {
        1: ({"highway": "path"}, [(0, 0), (1, 1)]),
        2: ({"highway": "track"}, [(2, 2), (3, 3)]),
    }
    nodes = {10: (0.0, 0.0, {}), 11: (5.0, 5.0, {}), 12: (6.0, 6.0, {})}
    ways = {
        1: {"id": 1, "nd_refs": [10, 11], "tags": {"highway": "path", "name": "Moved"}},
        -7: {"id": -7, "nd_refs": [11, 12], "tags": {"highway": "path"}},
        99: {"id": 99, "nd_refs": [10, 12], "tags": {"highway": "residential"}},
    }
    merged = bp.merge_sources(fetched, bp.local_file_ways(nodes, ways))
    assert merged[1] == ({"highway": "path", "name": "Moved"}, [(0.0, 0.0), (5.0, 5.0)])
    assert merged[2] == fetched[2], "a way the file lacks keeps live OSM's version"
    assert -7 in merged, "a trail mapped locally and not uploaded is a path too"
    assert 99 not in merged, "only path-class ways"


def _flag_lengths(features, zoom=15):
    """Meters per flag set among the features emitted for one tile zoom."""
    plane = bp._Plane(LAT)
    out = {}
    for f in features:
        if f["tippecanoe"]["minzoom"] != zoom:
            continue
        flags = tuple(k for k in ("tm_s", "tm_w", "tm_e") if k in f["properties"])
        assert f["geometry"]["type"] == "MultiLineString"
        length = sum(
            bp.LineString(plane.to_m(part)).length for part in f["geometry"]["coordinates"]
        )
        out[flags] = out.get(flags, 0) + length
    return out


def test_a_drawn_stretch_is_flagged_and_the_rest_is_not():
    # One 10-step way; the route draws its first 6 steps (about 66 m).
    ways = {1: ({"highway": "path"}, [tuple(c) for c in _line((0, 0), (0, 10))])}
    trails = _trails([_route_feature(5, _line((0, 0), (0, 6)))], {"5": SUMMER})
    features, stats = bp.build_features(ways, trails, BOUNDS, 9, 15)
    lengths = _flag_lengths(features)
    assert lengths[("tm_s",)] == pytest.approx(66, abs=4)
    assert lengths[()] == pytest.approx(44, abs=4)
    assert stats["drawn_m"] == pytest.approx(66, abs=4)


def test_buckets_flag_independently():
    ways = {1: ({"highway": "path"}, [tuple(c) for c in _line((0, 0), (0, 10))])}
    trails = _trails(
        [_route_feature(5, _line((0, 0), (0, 6))), _route_feature(6, _line((0, 4), (0, 10)))],
        {"5": SUMMER, "6": WINTER},
    )
    lengths = _flag_lengths(bp.build_features(ways, trails, BOUNDS, 9, 15)[0])
    assert set(lengths) == {("tm_s",), ("tm_s", "tm_w"), ("tm_w",)}
    # steps 4 to 6 are drawn by both, plus the tolerance at each end
    assert lengths[("tm_s", "tm_w")] == pytest.approx(22 + 2 * bp.DRAWN_TOLERANCE_M, abs=3)


def test_a_crossing_or_a_junction_touch_is_not_a_drawn_stretch():
    # The route runs north; a footway crosses it at right angles and a
    # side trail leaves from it. Each is within 2 m of the route for a
    # few meters only.
    ways = {
        1: ({"highway": "footway"}, [tuple(c) for c in _line((-3, 5), (3, 5))]),
        2: ({"highway": "path"}, [tuple(c) for c in _line((0, 2), (8, 2))]),
    }
    trails = _trails([_route_feature(5, _line((0, 0), (0, 10)))], {"5": SUMMER})
    features, stats = bp.build_features(ways, trails, BOUNDS, 9, 15)
    assert stats["drawn_m"] == 0
    assert set(_flag_lengths(features)) == {()}


def test_a_route_beside_a_path_does_not_flag_it():
    # 7.7 m east of the route: a sidewalk beside a road the route follows.
    ways = {1: ({"highway": "footway"}, [tuple(c) for c in _line((1, 0), (1, 10))])}
    trails = _trails([_route_feature(5, _line((0, 0), (0, 10)))], {"5": SUMMER})
    assert bp.build_features(ways, trails, BOUNDS, 9, 15)[1]["drawn_m"] == 0


def test_names_start_at_tile_zoom_14_and_zooms_follow_min_zoom():
    ways = {
        1: ({"highway": "track", "name": "Old Grade"}, [tuple(c) for c in _line((0, 0), (0, 10))]),
        2: (
            {"highway": "footway", "footway": "sidewalk"},
            [tuple(c) for c in _line((2, 0), (2, 10))],
        ),
    }
    features, _ = bp.build_features(ways, _trails([], {}), BOUNDS, 9, 15)
    track = {
        f["tippecanoe"]["minzoom"]: f["properties"]
        for f in features
        if f["properties"]["kind_detail"] == "track"
    }
    assert sorted(track) == [12, 13, 14, 15]
    assert "name" not in track[12] and "name" not in track[13]
    assert track[14]["name"] == track[15]["name"] == "Old Grade"
    sidewalk = sorted(
        f["tippecanoe"]["minzoom"] for f in features if f["properties"]["kind_detail"] == "sidewalk"
    )
    assert sidewalk == [14, 15], "min_zoom 15 first appears in tile zoom 14"
    assert all(f["tippecanoe"]["minzoom"] == f["tippecanoe"]["maxzoom"] for f in features)


def test_signature_follows_trails_and_buckets_not_stats():
    base = _trails([_route_feature(5, _line((0, 0), (0, 6)))], {"5": dict(SUMMER, distance_m=100)})
    same = _trails([_route_feature(5, _line((0, 0), (0, 6)))], {"5": dict(SUMMER, distance_m=999)})
    moved = _trails([_route_feature(5, _line((0, 0), (0, 7)))], {"5": SUMMER})
    winter = _trails([_route_feature(5, _line((0, 0), (0, 6)))], {"5": WINTER})
    sig = lambda t: bp.input_signature("bbox", t, "/nonexistent")  # noqa: E731
    assert sig(base) == sig(same)
    assert sig(base) != sig(moved)
    assert sig(base) != sig(winter)
    assert bp.is_generated_signature(sig(base))
    assert not bp.is_generated_signature("bbox=1,2,3,4;maxzoom=15;minzoom=9")


def test_missing_tools_name_the_config_key(monkeypatch):
    monkeypatch.setattr(bp.shutil, "which", lambda name: None)
    with pytest.raises(bp.BasemapPathsError) as e:
        bp.require_tools()
    assert "basemap_source: protomaps" in str(e.value)


@pytest.mark.skipif(
    not all(bp.find_tools()) or not find_pmtiles_cli(),
    reason="needs tippecanoe, tile-join and the pmtiles CLI",
)
def test_join_replaces_paths_and_keeps_everything_else(tmp_path):
    # A stand-in "Protomaps extract": one road, one path, one lake.
    # Built with bare names from inside tmp_path, as the module does,
    # because tile-join carries its inputs' command lines forward.
    src = tmp_path / "src.geojson"
    road = {"kind": "minor_road", "name": "Division Street"}
    old_path = {"kind": "path", "name": "Protomaps' own path"}
    feats = [
        {
            "type": "Feature",
            "properties": road,
            "tippecanoe": {"layer": "roads"},
            "geometry": {"type": "LineString", "coordinates": _line((-5, -5), (5, 5))},
        },
        {
            "type": "Feature",
            "properties": old_path,
            "tippecanoe": {"layer": "roads"},
            "geometry": {"type": "LineString", "coordinates": _line((0, 0), (0, 10))},
        },
        {
            "type": "Feature",
            "properties": {"kind": "lake"},
            "tippecanoe": {"layer": "water"},
            "geometry": {"type": "Polygon", "coordinates": [_line((2, 2), (4, 2), (4, 4), (2, 2))]},
        },
    ]
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    extract = tmp_path / "extract.pmtiles"
    subprocess.run(
        [bp.find_tools()[0], "-q", "-f", "-o", "extract.pmtiles", "-Z12", "-z15", "src.geojson"],
        check=True,
        cwd=tmp_path,
    )

    ways = {
        1: ({"highway": "path", "name": "Generated"}, [tuple(c) for c in _line((0, 0), (0, 10))])
    }
    trails = _trails([_route_feature(5, _line((0, 0), (0, 6)))], {"5": SUMMER})
    features, _ = bp.build_features(ways, trails, BOUNDS, 12, 15)
    out = tmp_path / "basemap.pmtiles"
    bp.tile_and_join(features, str(extract), str(out), BOUNDS, 12, 15)

    decoded = subprocess.run(
        ["tippecanoe-decode", "-z15", "-Z15", str(out)], capture_output=True, text=True, check=True
    ).stdout
    names = set()
    layers = set()

    def walk(node):
        if node.get("type") == "Feature":
            names.add(node["properties"].get("name") or node["properties"].get("kind"))
        else:
            if "layer" in node.get("properties", {}):
                layers.add(node["properties"]["layer"])
            for child in node.get("features", []):
                walk(child)

    walk(json.loads(decoded))
    assert "Generated" in names and "Division Street" in names and "lake" in names
    assert "Protomaps' own path" not in names
    assert layers == {"roads", "water"}, "generated paths join the existing roads layer"
    meta = subprocess.run(
        [find_pmtiles_cli(), "show", str(out)], capture_output=True, text=True
    ).stdout
    assert str(tmp_path) not in meta, "no local path leaks into the archive metadata"


@pytest.mark.skipif(
    not all(bp.find_tools()) or not find_pmtiles_cli(),
    reason="needs tippecanoe, tile-join and the pmtiles CLI",
)
def test_a_truncated_archive_is_refused_and_nothing_is_replaced(tmp_path, monkeypatch):
    src = tmp_path / "src.geojson"
    feat = {
        "type": "Feature",
        "properties": {"kind": "minor_road"},
        "tippecanoe": {"layer": "roads"},
        "geometry": {"type": "LineString", "coordinates": _line((-5, -5), (5, 5))},
    }
    src.write_text(json.dumps({"type": "FeatureCollection", "features": [feat]}))
    subprocess.run(
        [bp.find_tools()[0], "-q", "-f", "-o", "extract.pmtiles", "-Z12", "-z15", "src.geojson"],
        check=True,
        cwd=tmp_path,
    )
    extract = tmp_path / "extract.pmtiles"
    assert bp.archive_ok(str(extract))

    # What a full disk leaves: the whole header over a body cut short.
    cut = tmp_path / "cut.pmtiles"
    cut.write_bytes(extract.read_bytes()[: extract.stat().st_size // 2])
    assert not bp.archive_ok(str(cut))
    assert not bp.archive_ok(str(tmp_path / "missing.pmtiles"))

    # tile-join reporting success over such a file must not reach the map.
    out = tmp_path / "basemap.pmtiles"
    out.write_bytes(b"the previous good basemap")
    real_copy = bp.shutil.copy2

    def truncating_copy(src_path, dst_path, *a, **k):
        real_copy(src_path, dst_path, *a, **k)
        if str(dst_path).endswith(".tmp"):
            data = open(dst_path, "rb").read()
            open(dst_path, "wb").write(data[: len(data) // 2])
        return dst_path

    monkeypatch.setattr(bp.shutil, "copy2", truncating_copy)
    ways = {1: ({"highway": "path"}, [tuple(c) for c in _line((0, 0), (0, 10))])}
    features, _ = bp.build_features(ways, _trails([], {}), BOUNDS, 12, 15)
    work = tmp_path / "work"
    with pytest.raises(bp.BasemapPathsError) as e:
        bp.tile_and_join(features, str(extract), str(out), BOUNDS, 12, 15, work_root=str(work))
    assert "pmtiles verify" in str(e.value)
    assert out.read_bytes() == b"the previous good basemap"
    assert not (tmp_path / "basemap.pmtiles.tmp").exists()
    assert work.is_dir() and not list(work.iterdir()), "work dir is under work_root and cleaned up"


def test_one_feature_per_attribute_group_and_no_ids():
    # Protomaps packs roads this way, and it is most of the size: a
    # feature per trail made a basemap 9 percent larger than the plain
    # extract, a feature per group under 2.
    ways = {
        i: ({"highway": "path"}, [tuple(c) for c in _line((i * 3, 0), (i * 3, 5))])
        for i in range(1, 6)
    }
    ways[9] = ({"highway": "path", "name": "Named"}, [tuple(c) for c in _line((30, 0), (30, 5))])
    features, stats = bp.build_features(ways, _trails([], {}), BOUNDS, 9, 15)
    at15 = [f for f in features if f["tippecanoe"]["minzoom"] == 15]
    assert len(at15) == 2, "five unnamed paths share one feature; the named one has its own"
    assert sorted(len(f["geometry"]["coordinates"]) for f in at15) == [1, 5]
    assert all("id" not in f for f in features)
    assert stats["lines"] > stats["pieces"]
