"""Tests for the font coverage machinery in font_trimmer.py.

Covers the UI-webfont coverage check (sidecar-driven, no font-parsing
deps) and the canvas-side uncovered-range warning. Offline: everything
runs against the committed assets/webfonts/ sidecar and tmp_path
fixtures, never the network.
"""

import json
import os

from font_trimmer import (
    check_webfont_coverage,
    collect_text_from_config,
    load_webfont_coverage,
    warn_uncovered_canvas_ranges,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEBFONTS_DIR = os.path.join(REPO_ROOT, "assets", "webfonts")

def test_committed_webfont_assets_consistent():
    # The @font-face in templates/style.css hard-references this
    # filename; a rename must break loudly here, not at runtime.
    woff2 = os.path.join(WEBFONTS_DIR, "Inter-latin.woff2")
    assert os.path.isfile(woff2), "bundled UI webfont missing"
    with open(os.path.join(REPO_ROOT, "templates", "style.css"), encoding="utf-8") as f:
        assert "webfonts/Inter-latin.woff2" in f.read()

    sidecar = os.path.join(WEBFONTS_DIR, "Inter-latin.coverage.json")
    with open(sidecar, encoding="utf-8") as f:
        data = json.load(f)
    assert data["font"] == "Inter-latin.woff2"
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


def test_no_basemap_trims_quietly_from_trails_and_pois(tmp_path, capsys):
    # A --no-basemap build into a fresh directory has no basemap.pmtiles.
    # No basemap labels can render then, so that is no problem to warn
    # about, and the trim keeps the 0-255 baseline.
    from font_trimmer import copy_trimmed_fonts

    fonts_src = tmp_path / "fonts"
    face = fonts_src / "Noto Sans Regular"
    face.mkdir(parents=True)
    for name in ("0-255.pbf", "256-511.pbf"):
        (face / name).write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    copy_trimmed_fonts(str(out), str(fonts_src))
    assert "warn" not in capsys.readouterr().out
    assert sorted(os.listdir(out / "fonts" / "Noto Sans Regular")) == ["0-255.pbf"]


def test_a_damaged_basemap_does_not_crash_the_font_scan(tmp_path, capsys):
    # A fetch cut short leaves bytes that are not an archive. The scan is
    # best-effort: it warns and returns what it has.
    from font_trimmer import collect_text_from_pmtiles

    for content in (b"", b"not a pmtiles archive at all, just bytes" * 8):
        bad = tmp_path / "basemap.pmtiles"
        bad.write_bytes(content)
        assert collect_text_from_pmtiles(str(bad)) == set()
    assert "Could not read basemap" in capsys.readouterr().out


def test_basemap_chars_reuses_the_scan_until_the_archive_changes(tmp_path, monkeypatch):
    import font_trimmer

    scans = []

    def fake_scan(path, fields, minzoom):
        scans.append(path)
        return {"a", "é"}, True

    monkeypatch.setattr(font_trimmer, "_scan_pmtiles", fake_scan)
    archive = tmp_path / "basemap.pmtiles"
    archive.write_bytes(b"tiles")
    (tmp_path / "basemap.pmtiles.sig").write_text("bbox=1;maxzoom=15\n")

    assert font_trimmer.basemap_chars(str(archive), minzoom=9) == {"a", "é"}
    assert font_trimmer.basemap_chars(str(archive), minzoom=9) == {"a", "é"}
    assert len(scans) == 1
    # A new signature (the basemap step regenerated it) or other scan
    # bounds mean a new answer.
    (tmp_path / "basemap.pmtiles.sig").write_text("bbox=2;maxzoom=15\n")
    font_trimmer.basemap_chars(str(archive), minzoom=9)
    font_trimmer.basemap_chars(str(archive), minzoom=10)
    assert len(scans) == 3
    # The remembered answer is a build-only artifact, never deployed.
    from build import _is_build_only_artifact

    assert _is_build_only_artifact("basemap.pmtiles" + font_trimmer.GLYPH_SCAN_SUFFIX)


def test_basemap_chars_never_remembers_a_partial_scan(tmp_path, monkeypatch):
    import font_trimmer

    scans = []
    monkeypatch.setattr(
        font_trimmer, "_scan_pmtiles", lambda *a: scans.append(1) or ({"a"}, False))
    archive = tmp_path / "basemap.pmtiles"
    archive.write_bytes(b"tiles")
    (tmp_path / "basemap.pmtiles.sig").write_text("sig\n")
    font_trimmer.basemap_chars(str(archive))
    font_trimmer.basemap_chars(str(archive))
    assert len(scans) == 2
