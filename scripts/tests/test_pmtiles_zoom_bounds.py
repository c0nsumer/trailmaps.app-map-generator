"""Tests for the PMTiles extraction zoom lower bound.

The property under test: archives never ship tiles below the zoom the
app can actually reach (MIN_ZOOM clamps the camera inside maxBounds),
and a minimum-zoom change - or upgrading past a pre-minzoom build -
re-extracts instead of silently reusing the old archive.

Run from repo root:
    python -m pytest scripts/tests/test_pmtiles_zoom_bounds.py -v
"""

import os

import pmtiles_util
from cache_signatures import (
    _bbox_signature,
    _pmtiles_needs_regen,
    _save_signature,
)
from pmtiles_util import BASEMAP_MAXZOOM, EXTRACT_MINZOOM, MIN_ZOOM, TERRAIN_MAXZOOM

BBOX = [-88.0, 46.0, -87.0, 47.0]


def test_zoom_constants():
    assert (MIN_ZOOM, EXTRACT_MINZOOM) == (10, 9)
    assert (BASEMAP_MAXZOOM, TERRAIN_MAXZOOM) == (15, 12)


def test_signatures_match_the_pre_constant_defaults():
    # Fleet rebuilds must not re-extract: the sidecar text a build wrote
    # when the zoom values came from config defaults (min_zoom 10,
    # basemap_maxzoom 15, terrain_maxzoom 12) must equal what the
    # constants produce now.
    import math

    old_min = max(0, math.floor(10) - 1)
    for old_max, new_max in ((15, BASEMAP_MAXZOOM), (12, TERRAIN_MAXZOOM)):
        assert _bbox_signature(BBOX, old_max, old_min) == _bbox_signature(
            BBOX, new_max, EXTRACT_MINZOOM
        )
    assert _bbox_signature(BBOX, BASEMAP_MAXZOOM, EXTRACT_MINZOOM) == (
        "bbox=-88.0000,46.0000,-87.0000,47.0000;maxzoom=15;minzoom=9"
    )
    assert _bbox_signature(BBOX, TERRAIN_MAXZOOM, EXTRACT_MINZOOM).endswith(
        ";maxzoom=12;minzoom=9"
    )


def test_signature_includes_minzoom():
    a = _bbox_signature(BBOX, 15, 9)
    b = _bbox_signature(BBOX, 15, 8)
    assert a != b
    assert "minzoom=9" in a


def test_legacy_sidecar_triggers_regen(tmp_path):
    # A sidecar written before extraction had a minzoom bound must not
    # vouch for the archive: the old file still carries the low-zoom
    # world tiles the bound exists to drop.
    archive = str(tmp_path / "basemap.pmtiles")
    with open(archive, "w", encoding="utf-8") as f:
        f.write("stub")
    legacy_sig = f"bbox={','.join(f'{v:.4f}' for v in BBOX)};maxzoom=15"
    _save_signature(archive, legacy_sig)
    needs, reason = _pmtiles_needs_regen(archive, BBOX, 15, 9)
    assert needs
    assert "signature changed" in reason


def test_matching_sidecar_reuses_archive(tmp_path):
    archive = str(tmp_path / "basemap.pmtiles")
    with open(archive, "w", encoding="utf-8") as f:
        f.write("stub")
    _save_signature(archive, _bbox_signature(BBOX, 15, 9))
    needs, reason = _pmtiles_needs_regen(archive, BBOX, 15, 9)
    assert not needs


def test_extract_passes_minzoom_to_cli(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        # Simulate the CLI writing its output file and exiting 0.
        with open(cmd[3], "w", encoding="utf-8") as f:
            f.write("tiles")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(pmtiles_util.subprocess, "run", fake_run)
    out = str(tmp_path / "out.pmtiles")
    assert pmtiles_util.extract("pmtiles", "https://example/planet.pmtiles", out, BBOX, 15, 9)
    assert "--minzoom=9" in seen["cmd"]
    assert "--maxzoom=15" in seen["cmd"]
    assert os.path.exists(out)
