"""Predictable mistakes and faults end in one clear message, never a
traceback, and a failed build never leaves something deployable.

Offline: external tools are shimmed, the network is stubbed, and the
assertions are on exit status, files, and that a message names the
offending key or file (never on whole console lines).
"""

import contextlib
import io
import os
import stat

import config_io
import console
import fetch_terrain
import osm_parser
import overpass
import pytest
import requests
import validate_config
from test_build_output import TINY_OSM
from test_offline_build import BASE_YAML, _build, _map_dir
from urllib3.exceptions import NewConnectionError

import build


@pytest.fixture(autouse=True)
def _no_vendor_download(monkeypatch):
    monkeypatch.setattr(build, "download_vendor_libs", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _reset_console():
    yield
    console.set_verbosity()


def _failing_pmtiles(tmp_path):
    shim = tmp_path / "pmtiles"
    shim.write_text("#!/bin/sh\necho boom >&2\nexit 1\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return str(shim)


# --- terrain -----------------------------------------------------------------


def test_failed_terrain_regen_keeps_the_old_file_and_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch_terrain, "find_pmtiles_cli", lambda: _failing_pmtiles(tmp_path))
    terrain = tmp_path / "terrain.pmtiles"
    terrain.write_bytes(b"previous archive")
    sig = tmp_path / "terrain.pmtiles.sig"
    sig.write_text("old-signature\n")
    config = {"name": "T", "bbox": [0, 0, 1, 1]}
    with pytest.raises(SystemExit) as exc:
        build._do_terrain(config, str(terrain), "new-signature")
    assert exc.value.code != 0
    assert terrain.read_bytes() == b"previous archive"
    assert sig.read_text() == "old-signature\n"
    assert not (tmp_path / "terrain.pmtiles.tmp").exists()


def test_failed_first_terrain_extract_stays_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_terrain, "find_pmtiles_cli", lambda: _failing_pmtiles(tmp_path))
    terrain = tmp_path / "terrain.pmtiles"
    build._do_terrain({"name": "T", "bbox": [0, 0, 1, 1]}, str(terrain), "sig")
    assert not terrain.exists()
    assert not (tmp_path / "terrain.pmtiles.sig").exists()


# --- bbox / points -----------------------------------------------------------


def _failing_build(root):
    """Run the tiny build, expect it to exit nonzero, return what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        with pytest.raises(SystemExit) as exc:
            build.main([
                str(root / "tiny.yaml"), "--output-dir", str(root / "out"),
                "--cache-dir", str(root / "cache"), "--no-basemap", "--no-terrain",
                "--no-minify", "--no-precompress",
            ])
    assert exc.value.code != 0
    return buf.getvalue()


def test_a_swapped_bbox_fails_the_build_and_says_so(tmp_path):
    # TINY_OSM sits at lon -87.6, lat 46.5; this is the lat-first spelling.
    root = _map_dir(tmp_path, "bbox: [46.49, -87.62, 46.52, -87.59]\n")
    text = _failing_build(root)
    assert "bbox" in text and "swapped" in text and "longitude first" in text
    assert not (root / "out" / "build-complete.sig").exists()


def test_a_bbox_on_the_trails_builds(tmp_path):
    root = _map_dir(tmp_path, "bbox: [-87.62, 46.49, -87.59, 46.52]\n")
    _build(root)
    assert (root / "out" / "build-complete.sig").exists()


def test_a_bbox_nowhere_near_says_check_values(tmp_path):
    root = _map_dir(tmp_path, "bbox: [10, 10, 11, 11]\n")
    text = _failing_build(root)
    assert "none of this map's trails" in text
    assert "swapped" not in text


def _errors(extra):
    config = config_io.safe_load_config(BASE_YAML + extra)
    errors, _warnings = validate_config.validate_config(config)
    # The fixture has no tiny.osm beside it; that is not what these test.
    return "\n".join(e for e in errors if "osm_file" not in e)


def test_a_lat_first_parking_point_is_reported():
    errors = _errors("bbox: [-87.62, 46.49, -87.59, 46.52]\n"
                     "parking:\n  - name: Lot\n    coordinates: [46.5, -87.6]\n")
    assert "parking[0].coordinates" in errors and "swapped" in errors


def test_a_parking_point_on_the_map_is_fine():
    assert _errors("bbox: [-87.62, 46.49, -87.59, 46.52]\n"
                   "parking:\n  - name: Lot\n    coordinates: [-87.6, 46.5]\n") == ""


def test_a_swapped_pan_bbox_is_reported():
    errors = _errors("bbox: [-87.62, 46.49, -87.59, 46.52]\n"
                     "pan_bbox: [46.4, -87.7, 46.6, -87.5]\n")
    assert "pan_bbox" in errors and "swapped" in errors


# --- wrong types, bad files --------------------------------------------------


@pytest.mark.parametrize("line", [
    "color_by: [route]", "default_labels: {a: 1}", "forced_labels: [none]",
    "difficulty_scale: [imba]", "marker_shape: [box]", "default_color_scheme: [dark]",
    "logo: 5", "icon: true", "custom_routes: 5", "additional_logos: true",
    "event_mode: {routes: 5, gpx: {routes: 7}, background_style: {cap: [1]}}",
    "default_trail_color: {cap: [1]}",
])
def test_wrong_typed_values_are_reported_not_raised(tmp_path, line):
    root = _map_dir(tmp_path, line + "\n")
    config = build.load_config(str(root / "tiny.yaml"))
    errors, _ = validate_config.validate_config(config, config_path=str(root / "tiny.yaml"))
    assert errors


def test_duplicate_key_names_the_key_and_both_lines(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("name: A\nslug: a\nname: B\n")
    with pytest.raises(config_io.ConfigReadError) as exc:
        config_io.parse_config_yaml(str(p))
    assert "'name'" in str(exc.value) and "1" in str(exc.value) and "3" in str(exc.value)


def test_duplicate_key_in_a_nested_mapping_is_found(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("event_mode:\n  poi_color: red\n  poi_color: blue\n")
    with pytest.raises(config_io.ConfigReadError):
        config_io.parse_config_yaml(str(p))


def test_yaml_merge_keys_still_load(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("a: &x {p: 1}\nb:\n  <<: *x\n  p: 2\n")
    assert config_io.parse_config_yaml(str(p))["b"] == {"p": 2}


def test_non_utf8_config_is_a_read_error_naming_the_file(tmp_path):
    p = tmp_path / "latin1.yaml"
    p.write_bytes("name: Café\n".encode("latin-1"))
    errors, _ = validate_config.validate_config_file(str(p))
    assert errors and "latin1.yaml" in errors[0] and "UTF-8" in errors[0]


@pytest.mark.parametrize("content", [
    TINY_OSM[:200], "", '{"type": "FeatureCollection"}',
    TINY_OSM.replace(" lon='-87.6'", ""),
])
def test_a_broken_osm_file_exits_naming_the_file(tmp_path, capsys, content):
    p = tmp_path / "broken.osm"
    p.write_text(content)
    with pytest.raises(SystemExit) as exc:
        osm_parser.parse_osm_file(str(p))
    assert exc.value.code != 0
    out = capsys.readouterr()
    assert "broken.osm" in out.out + out.err


# --- offline -----------------------------------------------------------------


def test_vendor_download_failure_is_one_message(tmp_path, monkeypatch, capsys):
    monkeypatch.undo()  # restore the real downloader for this test

    def offline(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(requests, "get", offline)
    with pytest.raises(SystemExit) as exc:
        build.download_vendor_libs(str(tmp_path / "out"), str(tmp_path / "cache"))
    assert exc.value.code != 0
    out = capsys.readouterr()
    assert "vendor/" in out.out + out.err


def _no_route():
    inner = NewConnectionError(None, "Failed to establish a new connection")
    return requests.ConnectionError(inner)


def test_cannot_connect_recognizes_unreachable_not_timeouts():
    assert overpass.cannot_connect(_no_route())
    assert not overpass.cannot_connect(requests.ReadTimeout("slow"))
    assert not overpass.cannot_connect(requests.ConnectionError("Connection aborted"))


def test_first_attempt_connection_failure_does_not_retry(tmp_path, monkeypatch):
    def offline(*a, **k):
        raise _no_route()

    def no_sleep(_):
        raise AssertionError("slept before giving up on an unreachable host")

    monkeypatch.setattr(requests, "post", offline)
    monkeypatch.setattr(overpass.time, "sleep", no_sleep)
    with pytest.raises(SystemExit) as exc:
        overpass.query("[out:json];", cache_dir=str(tmp_path), label="trails")
    assert exc.value.code != 0


def test_server_errors_still_retry(tmp_path, monkeypatch):
    sleeps = []

    class Resp:
        status_code = 503
        text = "busy"

        def raise_for_status(self):
            raise requests.HTTPError("503")

    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(overpass.time, "sleep", sleeps.append)
    with pytest.raises(SystemExit):
        overpass.query("[out:json];", cache_dir=str(tmp_path))
    assert sleeps == overpass.RETRY_BACKOFF[: overpass.MAX_RETRIES - 1]


# --- completion marker -------------------------------------------------------


def test_marker_is_written_last_and_not_shipped(tmp_path):
    root = _map_dir(tmp_path)
    _build(root)
    out = root / "out"
    assert (out / build.BUILD_MARKER).exists()
    assert build.BUILD_MARKER not in (out / "sw.js").read_text(encoding="utf-8")
    assert build._is_build_only_artifact(build.BUILD_MARKER)


def test_a_failed_rebuild_leaves_no_marker(tmp_path, monkeypatch):
    root = _map_dir(tmp_path)
    _build(root)
    marker = root / "out" / build.BUILD_MARKER
    assert marker.exists()

    def boom(*a, **k):
        raise RuntimeError("killed midway")

    monkeypatch.setattr(build, "_stage_pwa", boom)
    with pytest.raises(RuntimeError):
        _build(root)
    assert not marker.exists()


def test_deploy_script_checks_the_marker():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "build_and_deploy.sh")
    with open(path, encoding="utf-8") as f:
        assert build.BUILD_MARKER in f.read()
