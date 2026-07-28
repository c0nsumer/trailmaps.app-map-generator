"""Tests for the font coverage machinery in font_trimmer.py.

Covers the UI-webfont coverage check (sidecar-driven, no font-parsing
deps) and the canvas-side uncovered-range warning. Offline: everything
runs against the committed assets/webfonts/ sidecar and tmp_path
fixtures, never the network.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEBFONTS_DIR = os.path.join(REPO_ROOT, "assets", "webfonts")

from font_trimmer import (  # noqa: E402
    check_webfont_coverage,
    collect_text_from_config,
    load_webfont_coverage,
    warn_uncovered_canvas_ranges,
)


def test_committed_webfont_assets_consistent():
    # The @font-face in templates/style.css hard-references this
    # filename; a rename must break loudly here, not at runtime.
    woff2 = os.path.join(WEBFONTS_DIR, "NotoSans-latin.woff2")
    assert os.path.isfile(woff2), "bundled UI webfont missing"
    with open(os.path.join(REPO_ROOT, "templates", "style.css"), encoding="utf-8") as f:
        assert "webfonts/NotoSans-latin.woff2" in f.read()

    sidecar = os.path.join(WEBFONTS_DIR, "NotoSans-latin.coverage.json")
    with open(sidecar, encoding="utf-8") as f:
        data = json.load(f)
    assert data["font"] == "NotoSans-latin.woff2"
    ranges = data["codepoint_ranges"]
    assert ranges == sorted(ranges), "sidecar ranges must be sorted"
    for start, end in ranges:
        assert 0 <= start <= end


def test_load_webfont_coverage_reads_committed_sidecar():
    covered = load_webfont_coverage(WEBFONTS_DIR)
    assert covered is not None
    # Latin subset must at minimum cover printable ASCII.
    assert all(cp in covered for cp in range(0x20, 0x7F))


def test_load_webfont_coverage_none_without_sidecar(tmp_path):
    assert load_webfont_coverage(str(tmp_path / "absent")) is None
    empty = tmp_path / "webfonts"
    empty.mkdir()
    assert load_webfont_coverage(str(empty)) is None


def test_check_webfont_coverage_clean_for_ascii():
    missing = check_webfont_coverage(set("Marquette Trails 100% #4-b"), WEBFONTS_DIR)
    assert missing == set()


def test_check_webfont_coverage_flags_uncovered_chars(capsys):
    # Runic and CJK are far outside the latin subset; whitespace and
    # controls are exempt by design.
    missing = check_webfont_coverage(set("Trail ᚠ中\n\t"), WEBFONTS_DIR)
    assert missing == {"ᚠ", "中"}
    out = capsys.readouterr().out
    assert "U+16A0" in out and "U+4E2D" in out


def test_check_webfont_coverage_skips_without_sidecar(tmp_path):
    assert check_webfont_coverage(set("abc"), str(tmp_path)) is None


def test_collect_text_from_config_walks_nested_values():
    config = {
        "name": "Blöomer",
        "routes": [{"label": "Loop α"}, {"notes": ["čž"]}],
        "count": 3,
        "flag": True,
    }
    chars = collect_text_from_config(config)
    assert {"ö", "α", "č", "ž"} <= chars
    assert "3" not in chars  # non-strings are not stringified


def _make_face(fonts_src, face, ranges):
    face_dir = fonts_src / face
    face_dir.mkdir(parents=True)
    for start, end in ranges:
        (face_dir / f"{start}-{end}.pbf").write_bytes(b"")
    return face_dir


def test_warn_uncovered_canvas_ranges_clean_when_covered(tmp_path):
    _make_face(tmp_path, "Noto Sans Regular", [(0, 255), (256, 511)])
    missing = warn_uncovered_canvas_ranges(
        set("abcé"), {(0, 255), (256, 511)}, {"Noto Sans Regular"}, str(tmp_path)
    )
    assert missing == []


def test_warn_uncovered_canvas_ranges_flags_missing_pbf(tmp_path, capsys):
    _make_face(tmp_path, "Noto Sans Regular", [(0, 255)])
    chars = set("abc中")  # CJK char needs range 19968-20223
    missing = warn_uncovered_canvas_ranges(
        chars, {(0, 255), (19968, 20223)}, {"Noto Sans Regular"}, str(tmp_path)
    )
    assert missing == [(19968, 20223)]
    out = capsys.readouterr().out
    assert "U+4E00" in out and "中" in out


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
