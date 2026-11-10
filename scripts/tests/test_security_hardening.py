"""Hardening for data that reaches the built site or the build machine:
OSM colours, pinned vendor libraries, SVG logos, event GPX files, and
curator text that looks like an engine placeholder. All offline."""

import hashlib
import os

import pytest
from conftest import MINIMAL_CONFIG
from osm_parser import plain_css_color, relation_info
from template_inject import _process_index_html
from validate_config import validate_config

import build

BASE = {"name": "T", "slug": "t", "title": "T Map", "relations": [12345678]}


def _errors(**overrides):
    errors, _warnings = validate_config({**BASE, **overrides})
    return errors


# --- OSM colour ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "url(http://x/y.png)",
        "red;background:url(x)",
        "expression(alert(1))",
        "a" * 5000,
        "<img src=x onerror=alert(1)>",
        "red\n",
        "rgb(url(x))",
        "",
    ],
)
def test_hostile_osm_colour_is_dropped(value):
    assert relation_info(1, {"colour": value})["colour"] is None


@pytest.mark.parametrize(
    "value",
    ["#ABC", "#aabbcc", "#aabbccdd", "red", "DarkGreen",
     "rgb(0, 128, 0)", "hsl(120 50% 50% / 0.5)"],
)
def test_plain_osm_colour_survives_unchanged(value):
    assert plain_css_color(value) == value
    assert relation_info(1, {"colour": value})["colour"] == value


def test_trailing_newline_is_rejected_by_config_validators():
    assert any("slug" in e for e in _errors(slug="abc\n"))
    assert any("default_trail_color" in e for e in _errors(default_trail_color="red\n"))


# --- vendor integrity ------------------------------------------------------


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    good = b"console.log('ok');\n"
    monkeypatch.setattr(build, "VENDOR_LIBS", {"lib.js": "https://example.invalid/lib.js"})
    monkeypatch.setattr(build, "VENDOR_SHA256", {"lib.js": hashlib.sha256(good).hexdigest()})
    tag = hashlib.sha256(b"https://example.invalid/lib.js").hexdigest()[:8]
    cache = tmp_path / "cache"
    cached = cache / "vendor" / f"lib.js.{tag}"
    cached.parent.mkdir(parents=True)

    class Resp:
        status_code = 200
        content = good

        def raise_for_status(self):
            pass

    monkeypatch.setattr(build.requests, "get", lambda *a, **k: Resp())
    return good, str(cache), cached, str(tmp_path / "out"), Resp


def test_good_cached_vendor_file_passes(vendor):
    good, cache, cached, out, _ = vendor
    cached.write_bytes(good)
    build.download_vendor_libs(out, cache)
    assert open(os.path.join(out, "vendor", "lib.js"), "rb").read() == good


def test_corrupted_cached_vendor_file_fails_the_build(vendor):
    good, cache, cached, out, _ = vendor
    cached.write_bytes(good + b"// tampered")
    with pytest.raises(SystemExit):
        build.download_vendor_libs(out, cache)


def test_bad_vendor_download_fails_and_is_not_cached(vendor):
    _, cache, cached, out, Resp = vendor
    Resp.content = b"evil()"
    with pytest.raises(SystemExit):
        build.download_vendor_libs(out, cache)
    assert not cached.exists()


def test_good_vendor_download_is_cached(vendor):
    good, cache, cached, out, _ = vendor
    build.download_vendor_libs(out, cache)
    assert cached.read_bytes() == good


# --- SVG logos -------------------------------------------------------------

CLEAN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
    'width="40" height="40" viewBox="0 0 40 40">'
    "<style>.a{fill:url(#g)}</style>"
    '<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
    '<use xlink:href="#r"/><rect id="r" class="a" width="40" height="40"/></svg>'
)


def _svg_cfg(tmp_path, body, key="logo"):
    svg = tmp_path / "l.svg"
    svg.write_text(body, encoding="utf-8")
    if key == "additional_logos":
        return {"additional_logos": [{"path": str(svg)}]}
    return {key: str(svg)}


@pytest.mark.parametrize(
    "body",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><SVG:Script xmlns:SVG="http://www.w3.org/2000/svg"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<a xlink:href="JaVa\tscript:alert(1)"><rect/></a></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
        "<svg><unclosed></svg>",
    ],
)
@pytest.mark.parametrize("key", ["logo", "icon", "additional_logos"])
def test_unsafe_svg_fails_validation(tmp_path, body, key):
    errors = _errors(**_svg_cfg(tmp_path, body, key))
    assert any(key in e and "SVG" in e for e in errors), errors


def test_clean_svg_passes_and_is_copied_byte_for_byte(tmp_path):
    assert _errors(**_svg_cfg(tmp_path, CLEAN_SVG)) == []
    from logo import process_logo

    out = tmp_path / "logo.svg"
    process_logo(str(tmp_path / "l.svg"), str(out))
    assert out.read_bytes() == CLEAN_SVG.encode()


# --- event GPX -------------------------------------------------------------


def _gpx_errors(path):
    return _errors(
        event_mode={"featured": [12345678], "gpx": {"routes": [{"name": "C", "file": path}]}}
    )


def test_gpx_path_without_gpx_extension_is_rejected(tmp_path):
    canary = tmp_path / "id_canary"
    canary.write_text("<gpx/>")
    errors = _gpx_errors(str(tmp_path / ".." / tmp_path.name / "id_canary"))
    assert any("event_mode.gpx.routes[0].file" in e and ".gpx" in e for e in errors), errors


def test_gpx_named_file_that_is_not_gpx_is_rejected(tmp_path):
    fake = tmp_path / "course.gpx"
    fake.write_text("-----BEGIN PRIVATE KEY-----")
    errors = _gpx_errors(str(fake))
    assert any("event_mode.gpx.routes[0].file" in e and "not a GPX" in e for e in errors), errors
    other = tmp_path / "other.gpx"
    other.write_text("<kml/>")
    assert any("not a GPX" in e for e in _gpx_errors(str(other)))


def test_real_gpx_passes(tmp_path):
    ok = tmp_path / "course.GPX"
    ok.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk/></gpx>')
    assert _gpx_errors(str(ok)) == []


# --- placeholder re-expansion ----------------------------------------------


def test_title_with_engine_placeholder_stays_literal():
    config = {**MINIMAL_CONFIG, "title": "A __COLOR_SCHEME_BOOTSTRAP__ __BRAND_IMG_DIMS__ \"q\""}
    template = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "index.html")
    with open(template, encoding="utf-8") as f:
        html = _process_index_html(f.read(), config)
    assert "<title>A __COLOR_SCHEME_BOOTSTRAP__ __BRAND_IMG_DIMS__ \"q\"</title>" in html
    assert html.count('getItem("') == 1  # one bootstrap script
    assert 'id="brand-title">A __COLOR_SCHEME_BOOTSTRAP__ __BRAND_IMG_DIMS__ &quot;q&quot;<' in html
