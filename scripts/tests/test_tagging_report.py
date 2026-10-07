"""Tests for tagging_report.py - OSM data-quality notes.

The checks that matter most here are the ones that keep the report from
nagging: an ordinary branch junction must not read as a broken connection,
and a map with no difficulty tagging at all must not be told to add some.

Offline: audit() takes parsed dicts.

Run from repo root:
    python -m pytest scripts/tests/test_tagging_report.py -v
"""



from tagging_report import audit, format_report, summarize


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
    """A map where nobody tagged difficulty gets no missing-rating list -
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
        # Parking is curator-placed and off-trail BY DESIGN - never flagged.
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


def test_a_relation_without_a_name_tag_is_reported():
    # relation_info names an untagged relation "Route <id>" before the snapshot.
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]], route_id="42")],
                 routes={"42": {"name": "Route 42", "colour": "red"}})
    assert audit(snap, None, _CFG)["routes_missing_name"] == ["42"]


def test_the_colour_note_states_the_osm_fact_only():
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]])],
                 routes={"1": {"name": "X", "colour": ""}})
    report = format_report(audit(snap, None, _CFG), "t")
    assert "no `colour` tag in OSM" in report
    assert "falls back" not in report


def test_a_clipped_route_cut_at_the_bbox_edge_is_not_a_gap():
    # The clip leaves two pieces ~3 m apart on the bbox edge.
    pieces = [
        _feature([10], [[0.5, 0.99], [0.50002, 1.0]], route_id="7"),
        _feature([10], [[0.50004, 1.0], [0.6, 0.99]], route_id="7"),
    ]
    routes = {"7": {"name": "Rail Trail", "colour": "red"}}
    clipped = audit(_snap(pieces, routes), None, dict(_CFG, clipped_relations=[7]))
    assert clipped["probable_gaps"] == []
    # The same pieces on a source route are still checked.
    source = audit(_snap(pieces, routes), None, dict(_CFG, relations=[7], clipped_relations=[7]))
    assert len(source["probable_gaps"]) == 1


def test_a_negative_josm_id_gets_no_osm_link():
    snap = _snap([_feature([10], [[-87.60, 46.50], [-87.59, 46.50]], route_id="-5")],
                 routes={"-5": {"name": "Route -5", "colour": ""}})
    report = format_report(audit(snap, None, _CFG), "t")
    assert "openstreetmap.org/relation/-5" not in report
    assert "relation `-5` (not uploaded)" in report


_LINE = [[-87.60, 46.50], [-87.59, 46.50]]


def test_a_way_listed_twice_is_reported_for_a_human_look():
    snap = _snap([_feature([10, 11, -12], _LINE)],
                 routes={"1": {"name": "Race Course", "colour": "red",
                               "repeated_ways": {"11": 3, "10": 2, "-12": 2}}})
    f = audit(snap, None, _CFG)
    assert f["repeated_members"] == [
        ("1", "Race Course", [("10", 2), ("11", 3), ("-12", 2)])]
    report = format_report(f, "t")
    assert "## Relations that list a way more than once (1)" in report
    assert "out-and-back course" in report
    line = next(ln for ln in report.splitlines() if "Race Course" in ln)
    assert line.startswith("- https://www.openstreetmap.org/relation/1 Race Course: ")
    # Twice is the expected shape, so only a higher count shows a multiplier;
    # a JOSM id has no page to link to.
    assert "https://www.openstreetmap.org/way/10," in line
    assert "https://www.openstreetmap.org/way/11 (3x)" in line
    assert line.endswith("way `-12`")
    assert "way/-12" not in line


def test_a_route_without_repeats_is_not_reported():
    snap = _snap([_feature([10], _LINE)])
    f = audit(snap, None, _CFG)
    assert f["repeated_members"] == []
    assert "more than once" not in format_report(f, "t")


def test_repeated_member_summary_pluralizes():
    one = {"1": {"name": "A", "colour": "red", "repeated_ways": {"10": 2}}}
    two = dict(one, **{"2": {"name": "B", "colour": "red", "repeated_ways": {"20": 2}}})
    assert summarize(audit(_snap([_feature([10], _LINE)], one), None, _CFG)) == [
        "1 relation with a way listed more than once"]
    assert summarize(audit(_snap([_feature([10], _LINE)], two), None, _CFG)) == [
        "2 relations with a way listed more than once"]


def test_the_json_sidecar_mirrors_the_report(tmp_path):
    import json

    from tagging_report import report_tagging_quality, sidecar_path

    snap = _snap([], routes={"7": {"name": "Loop", "colour": "",
                                   "repeated_ways": {"70": 2}}})
    report_tagging_quality(snap, None, {"slug": "m"}, str(tmp_path))
    data = json.loads((tmp_path / "osm_diff" / "m" / "data-notes.json").read_text())
    assert data["slug"] == "m"
    assert data["total"] == 2
    assert data["counts"]["repeated_members"] == 1
    assert data["counts"]["routes_missing_colour"] == 1
    assert "1 relation with a way listed more than once" in data["summary"]
    assert data["report"] == "data-notes.md"
    assert sidecar_path(str(tmp_path), "m").endswith("osm_diff/m/data-notes.json")

    # A clean map still writes the sidecar, with total 0, so an aggregator
    # can tell "audited and clean" from "never audited".
    report_tagging_quality(_snap([], routes={}), None, {"slug": "c"}, str(tmp_path))
    clean = json.loads((tmp_path / "osm_diff" / "c" / "data-notes.json").read_text())
    assert clean["total"] == 0 and clean["summary"] == []


def test_a_map_no_longer_audited_drops_its_stale_report(tmp_path):
    from tagging_report import report_tagging_quality

    snap = _snap([], routes={"7": {"name": "Loop", "colour": ""}})
    report_tagging_quality(snap, None, {"slug": "m"}, str(tmp_path))
    assert (tmp_path / "osm_diff" / "m" / "data-notes.json").exists()
    assert (tmp_path / "osm_diff" / "m" / "data-notes.md").exists()

    report_tagging_quality(None, None, {"slug": "m"}, str(tmp_path))
    assert not (tmp_path / "osm_diff" / "m" / "data-notes.json").exists()
    assert not (tmp_path / "osm_diff" / "m" / "data-notes.md").exists()
    # And a map that never had a report is silent about it.
    report_tagging_quality(None, None, {"slug": "never"}, str(tmp_path))
