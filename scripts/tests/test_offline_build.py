"""Behavior of full offline builds of a tiny .osm map.

Each test runs build.main end to end with no network: the trails and
POIs come from a local .osm file, the basemap and terrain are skipped,
and the vendor download is stubbed. The assertions are on what the
build produces and reuses, never on its console output.
"""

import contextlib
import io
import json
import os
import re

import console
import pytest
from test_build_output import TINY_OSM

import build

# A GPX-converted route: every position carries elevation.
ROUTE_3D = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "LineString",
        "coordinates": [[-87.6, 46.5, 210.0], [-87.605, 46.505, 215.5], [-87.61, 46.51, 220.0]],
    },
}

BASE_YAML = (
    "name: Tiny Trails\nslug: tiny\nrelations:\n  - -190\nosm_file: tiny.osm\n"
    "accent_color: '#2E6B0F'\n"
)


@pytest.fixture(autouse=True)
def _no_vendor_download(monkeypatch):
    monkeypatch.setattr(build, "download_vendor_libs", lambda *a, **k: None)


def _map_dir(tmp_path, extra_yaml=""):
    root = tmp_path / "map"
    root.mkdir()
    (root / "tiny.osm").write_text(TINY_OSM)
    (root / "route3d.geojson").write_text(json.dumps(ROUTE_3D))
    (root / "tiny.yaml").write_text(BASE_YAML + extra_yaml)
    return root


def _build(root):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build.main([
                str(root / "tiny.yaml"), "--output-dir", str(root / "out"),
                "--cache-dir", str(root / "cache"), "--no-basemap", "--no-terrain",
                "--no-minify", "--no-precompress",
            ])
    finally:
        console.set_verbosity()
    with open(root / "out" / "trails.geojson", encoding="utf-8") as f:
        return json.load(f)


def test_a_custom_route_with_elevation_builds_and_ships_2d(tmp_path):
    root = _map_dir(
        tmp_path,
        "custom_routes:\n  - id: gpx\n    name: GPX Route\n    color: '#0055AA'\n"
        "    geometry: route3d.geojson\n",
    )
    trails = _build(root)
    custom = [f for f in trails["features"] if f["properties"].get("route_id") == "gpx"]
    assert custom
    assert all(len(p) == 2 for f in custom for p in f["geometry"]["coordinates"])
    assert trails["metadata"]["routes"]["gpx"]["distance_m"] > 0


def test_an_event_mode_map_builds_offline(tmp_path):
    root = _map_dir(
        tmp_path,
        "event_mode:\n  direction_arrows: true\n  routes:\n"
        "    - id: race\n      name: Race Course\n      color: '#D32F2F'\n"
        "      geometry: route3d.geojson\n"
        "  pois:\n    - name: Start\n      coordinates: [-87.6, 46.5]\n",
    )
    trails = _build(root)
    routes = trails["metadata"]["routes"]
    assert "race" in routes
    race = [f for f in trails["features"] if f["properties"].get("route_id") == "race"]
    assert race and all(f["properties"]["oneway"] == "yes" for f in race)
    app_js = (root / "out" / "app.js").read_text(encoding="utf-8")
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});\n", app_js, re.S).group(1))
    assert config["eventModeActive"] is True


def test_a_direction_schedule_edit_reuses_the_trails_base(tmp_path):
    root = _map_dir(tmp_path)
    _build(root)
    src = root / "out" / "trails.src.geojson"
    sig = root / "out" / "trails.src.geojson.sig"
    before = (src.read_bytes(), sig.read_bytes(), os.stat(src).st_mtime_ns,
              os.stat(sig).st_mtime_ns)
    diff_dir = root / "cache" / "osm_diff"
    diff_before = sorted(p.name for p in diff_dir.rglob("*")) if diff_dir.exists() else []

    with open(root / "tiny.yaml", "a", encoding="utf-8") as f:
        f.write("direction_schedule:\n  reverse_days: [tuesday]\n")
    _build(root)

    after = (src.read_bytes(), sig.read_bytes(), os.stat(src).st_mtime_ns,
             os.stat(sig).st_mtime_ns)
    assert after == before
    diff_after = sorted(p.name for p in diff_dir.rglob("*")) if diff_dir.exists() else []
    assert diff_after == diff_before
