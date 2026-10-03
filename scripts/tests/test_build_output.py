"""Shape of build.py's console output at each verbosity level.

The default is one line per stage plus every note, warning and error: the
website orchestrator streams it for dozens of maps, so the per-relation
listing, every "Wrote" line and the file table are --verbose only. These
tests pin that shape with a real (offline) build of a tiny .osm map, so a
new unconditional info() line fails here instead of bloating every log.
"""

import contextlib
import io

import console
import pmtiles_util
import pytest

import build

TINY_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='JOSM'>
  <node id='-1' lat='46.5' lon='-87.6' />
  <node id='-2' lat='46.51' lon='-87.61' />
  <way id='-3'>
    <nd ref='-1' />
    <nd ref='-2' />
    <tag k='highway' v='path' />
  </way>
  <relation id='-190'>
    <member type='way' ref='-3' role='' />
    <tag k='type' v='route' />
    <tag k='route' v='mtb' />
    <tag k='name' v='Tiny Loop' />
    <tag k='colour' v='#FF0000' />
  </relation>
</osm>
"""

VERBOSE_ONLY = ["Wrote ", "Using cached response", "BUILD SUMMARY", "Stage A", "Copied "]


def _run(tmp_path, name, *flags):
    root = tmp_path / name
    root.mkdir()
    (root / "tiny.osm").write_text(TINY_OSM)
    (root / "tiny.yaml").write_text(
        "name: Tiny Trails\nslug: tiny\nrelations:\n  - -190\nosm_file: tiny.osm\n"
        "accent_color: '#79AF13'\n"
    )
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            build.main([
                str(root / "tiny.yaml"), "--output-dir", str(root / "out"),
                "--cache-dir", str(root / "cache"), "--no-basemap", "--no-terrain",
                "--no-minify", "--no-precompress", *flags,
            ])
    finally:
        console.set_verbosity()
    return buf.getvalue().splitlines()


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("output")
    mp = pytest.MonkeyPatch()
    # Vendor libraries are a network download; the output shape does not depend on them.
    mp.setattr(build, "download_vendor_libs", lambda *a, **k: None)
    try:
        return {
            "default": _run(tmp, "default"),
            "verbose": _run(tmp, "verbose", "--verbose"),
            "quiet": _run(tmp, "quiet", "--quiet"),
        }
    finally:
        mp.undo()


def test_default_is_compact(outputs):
    lines = outputs["default"]
    assert len(lines) <= 15, "\n".join(lines)
    assert any(ln.startswith("Building Tiny Trails") for ln in lines)
    assert any(ln.startswith("  Trails: 1 relations") for ln in lines)
    assert any(ln.startswith("  POIs: ") for ln in lines)
    assert any(ln.startswith("  Basemap: skipped") for ln in lines)
    assert any(ln.startswith("  Terrain: skipped") for ln in lines)
    assert any(ln.startswith("Built in ") for ln in lines)


def test_default_hides_verbose_lines(outputs):
    text = "\n".join(outputs["default"])
    for marker in VERBOSE_ONLY:
        assert marker not in text, marker


# A deliberate warning: this accent is too light against the white sheet.
ACCENT_WARN = "  warn: accent_color: light shade #79AF13 vs white sheet = "


def test_default_keeps_the_accent_warning(outputs):
    assert any(ln.startswith(ACCENT_WARN) for ln in outputs["default"])
    assert any(ln.startswith(ACCENT_WARN) for ln in outputs["verbose"])


def test_verbose_restores_the_full_log(outputs):
    text = "\n".join(outputs["verbose"])
    # "Using cached response" needs Overpass; see test_cached_overpass_response_*.
    for marker in [m for m in VERBOSE_ONLY if m != "Using cached response"]:
        assert marker in text, marker
    assert "Building map: Tiny Trails" in text
    assert "Tiny Loop (-190) colour=#FF0000" in text
    assert len(outputs["verbose"]) > 3 * len(outputs["default"])


def test_quiet_prints_only_warnings_and_errors(outputs):
    assert any(ln.startswith(ACCENT_WARN) for ln in outputs["quiet"])
    for ln in outputs["quiet"]:
        assert ln.startswith(("  warn:", "  error:")), ln


@pytest.mark.parametrize("verbose", [False, True])
def test_cached_overpass_response_line_is_verbose_only(tmp_path, capsys, verbose):
    import overpass

    cache = str(tmp_path)
    q = "[out:json];node(1);out;"
    with open(overpass.cache_path(q, cache), "w", encoding="utf-8") as f:
        f.write('{"elements": []}')
    overpass.drain_outcomes()
    console.set_verbosity(verbose=verbose)
    try:
        overpass.query(q, cache_dir=cache)
    finally:
        console.set_verbosity()
    assert ("Using cached response" in capsys.readouterr().out) is verbose
    assert overpass.describe_outcomes(overpass.drain_outcomes()) == "cached just now"


def test_quiet_and_verbose_are_exclusive():
    with pytest.raises(SystemExit):
        build.main(["x.yaml", "--quiet", "--verbose"])


def testrel_path_is_relative_only_inside_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert console.rel_path(str(tmp_path / "build" / "x")) == "build/x"
    outside = tmp_path.parent / "elsewhere"
    assert console.rel_path(str(outside)) == str(outside)


def test_failed_extract_still_prints_captured_output(monkeypatch, tmp_path, capsys):
    class Failed:
        returncode = 1
        stdout = "partial stdout"
        stderr = "go: boom from the CLI"

    monkeypatch.setattr(pmtiles_util.subprocess, "run", lambda *a, **k: Failed())
    out = str(tmp_path / "o.pmtiles")
    assert not pmtiles_util.extract("pmtiles", "https://x/p.pmtiles", out, [0, 0, 1, 1], 12, 9)
    text = capsys.readouterr().out
    assert "pmtiles extract failed" in text
    assert "partial stdout" in text and "boom from the CLI" in text


def test_successful_extract_prints_no_progress_replay(monkeypatch, tmp_path, capsys):
    class Ok:
        returncode = 0
        stdout = "stdout noise"
        stderr = "fetching chunks 50%\nfetching chunks 100%"

    def fake_run(cmd, **kwargs):
        with open(cmd[3], "w", encoding="utf-8") as f:
            f.write("tiles")
        return Ok()

    monkeypatch.setattr(pmtiles_util.subprocess, "run", fake_run)
    out = str(tmp_path / "o.pmtiles")
    assert pmtiles_util.extract("pmtiles", "https://x/p.pmtiles", out, [0, 0, 1, 1], 12, 9)
    assert "fetching chunks" not in capsys.readouterr().out
