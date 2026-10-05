"""Tests for enrichment.py - relation_names display-name overrides and the
per-relation override typo guard.

Run from repo root:
    python -m pytest scripts/tests/test_enrichment.py -v
"""




from enrichment import _enrich_trails_geojson

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
                    "trail_name": "",
                    "shared_routes": [12562142],
                    "imba_difficulty": "",
                    "oneway": "",
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
    changed = _enrich_trails_geojson({"relation_names": {12562142: "Mountain Bike Trail"}}, g)
    assert changed
    assert g["metadata"]["routes"]["12562142"]["name"] == "Mountain Bike Trail"
    assert all(f["properties"]["route_name"] == "Mountain Bike Trail" for f in g["features"])


def test_no_override_leaves_osm_name():
    g = _fc()
    _enrich_trails_geojson({}, g)
    assert g["metadata"]["routes"]["12562142"]["name"] == _OSM_NAME
    assert g["features"][0]["properties"]["route_name"] == _OSM_NAME


def test_typo_guard_warns_on_unknown_and_super_relation_keys(capsys):
    g = _fc()
    cfg = {"relation_names": {111: "X"}, "relation_colors": {999: "blue"}}
    _enrich_trails_geojson(cfg, g)
    out = capsys.readouterr().out
    assert "relation_names[111]" in out and "no such route" in out
    # 999 is a super-relation parent: the warning should point at the child.
    assert "relation_colors[999]" in out and "12562142" in out


def test_typo_guard_covers_dashes_and_schedules(capsys):
    g = _fc()
    cfg = {
        "dashed_relations": {111: True},
        "direction_schedule": {"per_route": {222: {"reverse_days": ["mon"]},
                                             999: {"reverse_days": ["tue"]}}},
    }
    _enrich_trails_geojson(cfg, g)
    out = capsys.readouterr().out
    assert "dashed_relations[111]" in out and "no such route" in out
    assert "direction_schedule.per_route[222]" in out
    # A super-relation parent is a valid per_route key: the schedule fans
    # out to its children, so no warning.
    assert "per_route[999]" not in out


def test_typo_guard_silent_for_known_keys(capsys):
    g = _fc()
    _enrich_trails_geojson({"relation_colors": {12562142: "blue"}}, g)
    assert "warn" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Enrichment keeps the canonical features
# ---------------------------------------------------------------------------

_A = [-83.44, 42.67]
_B = [-83.45, 42.68]
_C = [-83.46, 42.69]


def _shared_corridor_fc():
    """Two routes sharing the A-B run, the second traversing it B->A so
    a corridor alignment pass would rewrite its vertex order."""

    def feat(rid, name, colour, coords, shared):
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "route_id": rid,
                "route_name": name,
                "route_colour": colour,
                "trail_name": "",
                "shared_routes": shared,
                "imba_difficulty": "",
                "oneway": "",
                "way_ids": [],
            },
        }

    return {
        "type": "FeatureCollection",
        "features": [
            feat(1, "One", "red", [_A, _B], [1, 2]),
            feat(1, "One", "red", [_B, _C], [1]),
            feat(2, "Two", "blue", [_B, _A], [1, 2]),
        ],
        "metadata": {
            "routes": {
                "1": {"name": "One", "colour": "red", "ref": "", "seasonal": ""},
                "2": {"name": "Two", "colour": "blue", "ref": "", "seasonal": ""},
            },
        },
    }


def test_enrichment_keeps_canonical_features():
    # The browser lays out the lanes from the canonical features, so
    # enrichment must not add, realign or reverse any: a route that
    # travels a shared way the other way keeps its own vertex order.
    g = _shared_corridor_fc()
    _enrich_trails_geojson({}, g)
    assert len(g["features"]) == 3
    two = [f for f in g["features"] if str(f["properties"]["route_id"]) == "2"]
    assert two[0]["geometry"]["coordinates"] == [_B, _A], "travel direction preserved"


def test_custom_route_oneway_minus_one_reverses_the_line(tmp_path):
    # "-1" is one-way against the drawn direction. The runtime draws
    # arrows along the line for "yes" and knows no other value, so the
    # line is reversed, as fetch_trails does for an OSM way.
    import json

    geom = tmp_path / "course.geojson"
    line = [[-87.60, 46.50], [-87.59, 46.50], [-87.58, 46.51]]
    geom.write_text(json.dumps({
        "type": "Feature", "properties": {},
        "geometry": {"type": "LineString", "coordinates": line}}))

    def enrich(oneway):
        trails = {"type": "FeatureCollection", "features": [], "metadata": {"routes": {}}}
        _enrich_trails_geojson({"custom_routes": [{
            "id": "course", "name": "Course", "color": "#ff0000",
            "geometry": str(geom), "oneway": oneway}]}, trails)
        (feat,) = trails["features"]
        return feat["properties"]["oneway"], feat["geometry"]["coordinates"]

    assert enrich("yes") == ("yes", line)
    assert enrich("-1") == ("yes", line[::-1])
