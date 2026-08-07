"""Tests for the PMTiles extraction zoom lower bound.

The property under test: archives never ship tiles below the zoom the
app can actually reach (min_zoom clamps the camera inside maxBounds),
and a min_zoom config edit - or upgrading past a pre-minzoom build -
re-extracts instead of silently reusing the old archive.

Run from repo root:
    python -m pytest scripts/tests/test_pmtiles_zoom_bounds.py -v
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pmtiles_util  # noqa: E402
from cache_signatures import (  # noqa: E402
    _bbox_signature,
    _pmtiles_needs_regen,
    _save_signature,
)
from pmtiles_util import extract_minzoom  # noqa: E402

BBOX = [-88.0, 46.0, -87.0, 47.0]


def test_extract_minzoom_default():
    # Default min_zoom is 10 (template_inject.py), so the floor is 9.
    assert extract_minzoom({}) == 9


def test_extract_minzoom_explicit():
    assert extract_minzoom({"min_zoom": 12}) == 11


def test_extract_minzoom_fractional_floors_first():
    assert extract_minzoom({"min_zoom": 10.5}) == 9


def test_extract_minzoom_never_negative():
    assert extract_minzoom({"min_zoom": 0}) == 0


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
