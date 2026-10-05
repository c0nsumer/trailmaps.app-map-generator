"""Tests for build.generate_service_worker - the precache list, cache
versioning, and config injection that every PWA rider depends on.

A regression here ships silently (the SW is generated, never executed at
build time), so the contract is pinned offline against a synthetic output
tree and a synthetic record of what the build shipped.

Run from repo root:
    python -m pytest scripts/tests/test_generate_service_worker.py -v
"""

import json
import os
import re

import pytest

import build

# A representative build tree: page + app code, one precachable glyph and
# one cache-on-fetch glyph, a tile archive, plus every class of file that
# must stay out: build-only artifacts and precompression sidecars (also
# inside a shipped directory), files the orchestrator writes after the
# engine runs, and a file an earlier build left behind.
TREE = {
    "index.html": b"<html>page</html>",
    "app.js": b"console.log('app');",
    "fonts/Noto Sans Regular/0-255.pbf": b"glyphs-basic",
    "fonts/Noto Sans Regular/256-511.pbf": b"glyphs-extended",
    "fonts/Noto Sans Regular/0-255.pbf.gz": b"precompression sidecar",
    "fonts/Noto Sans Regular/glyphs.tmp": b"interrupted write",
    "basemap.pmtiles": b"tile-archive-bytes",
    "trails.src.geojson": b"build-only base",
    "basemap.pmtiles.sig": b"signature sidecar",
    "extract.tmp": b"interrupted atomic write",
    "app.js.gz": b"precompression sidecar",
    "og-image.jpg": b"orchestrator social card",
    "poster.pdf": b"orchestrator poster",
    "logo-9.webp": b"a removed additional logo",
}

# What the build recorded via template_inject.ship.
SHIPPED = {"index.html", "app.js", "fonts/", "basemap.pmtiles"}


def _make_tree(root, files=TREE):
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)


# Build the tree before each test via an autouse fixture, keeping the
# test bodies about the contract, not setup.
@pytest.fixture(autouse=True)
def _tree(tmp_path):
    _make_tree(str(tmp_path))


def _generate(root, slug="t", shipped=SHIPPED):
    build.generate_service_worker({"slug": slug, "_shipped": set(shipped)}, root)
    with open(os.path.join(root, "sw.js"), encoding="utf-8") as f:
        sw = f.read()
    m = re.search(r"const SW_CONFIG = (\{.*?\n\});", sw, re.S)
    assert m, "SW_CONFIG injection missing from generated sw.js"
    return json.loads(m.group(1)), sw


def test_config_injection_shape(tmp_path):
    cfg, sw = _generate(str(tmp_path), slug="fliptest")
    # The slug scopes cache names on the shared origin; the version is
    # the 12-hex content hash.
    assert cfg["CACHE_SCOPE"] == "fliptest"
    assert re.fullmatch(r"[0-9a-f]{12}", cfg["CACHE_VERSION"])
    assert "/*__SW_CONFIG__*/" not in sw


def test_precache_list_contents(tmp_path):
    cfg, _ = _generate(str(tmp_path))
    urls = cfg["PRECACHE_URLS"]
    assert urls[0] == "./"
    assert "app.js" in urls
    # Only the Basic Latin glyph range precaches; the rest cache-on-fetch.
    # Entries are URLs, spelled as a browser serializes the raw path.
    assert "fonts/Noto%20Sans%20Regular/0-255.pbf" in urls
    assert "fonts/Noto%20Sans%20Regular/256-511.pbf" not in urls
    # Build-only artifacts and sidecars never reach the list.
    for excluded in ("trails.src.geojson", "basemap.pmtiles.sig",
                     "extract.tmp", "app.js.gz", "sw.js",
                     "fonts/Noto%20Sans%20Regular/0-255.pbf.gz",
                     "fonts/Noto%20Sans%20Regular/glyphs.tmp"):
        assert excluded not in urls, excluded
    # The multi-MB archives trail the list so small assets cache first,
    # and they feed the Range handler's suffix-match set.
    assert urls[-1] == "basemap.pmtiles"
    assert cfg["PMTILES_FILES"] == ["basemap.pmtiles"]


def test_gpx_link_and_precache_entry_are_one_url(tmp_path):
    # The Cache API matches on the URL, so the page's download link and
    # the precache entry must be the same string whatever the official
    # filename carries. A link that escaped "(" beside a raw precache
    # entry missed the cache offline.
    from template_inject import gpx_download_entries

    names = ["LONG Race Course (2026 STC).gpx", "50% #1 loop? [draft] é.gpx"]
    _make_tree(str(tmp_path), {"gpx/" + n: b"<gpx/>" for n in names})
    cfg, _ = _generate(str(tmp_path), shipped=SHIPPED | {"gpx/"})
    config = {"event_mode": {"gpx": {"routes": [
        {"name": "Course", "file": "/abs/" + n} for n in names]}}}
    links = [meta["url"] for _src, _base, meta in gpx_download_entries(config)]

    assert links[0] == "gpx/LONG%20Race%20Course%20(2026%20STC).gpx"
    assert "#" not in links[1] and "?" not in links[1]
    for link in links:
        assert link in cfg["PRECACHE_URLS"]
        assert cfg["PRECACHE_BYTES"][link] == len(b"<gpx/>")


def test_precache_bytes_keyed_by_url(tmp_path):
    cfg, _ = _generate(str(tmp_path))
    # "./" is the page itself, weighted by index.html's size.
    assert cfg["PRECACHE_BYTES"]["./"] == len(TREE["index.html"])
    assert cfg["PRECACHE_BYTES"]["app.js"] == len(TREE["app.js"])


def test_index_html_precached_only_as_root_seed(tmp_path):
    # The document precaches once, as the "./" seed every entry point
    # navigates to. The walked "index.html" duplicated the same bytes
    # in the cache, the readiness total, and the install download. It
    # must still deploy and bust the cache when edited.
    root = str(tmp_path)
    v1, _ = _generate(root)
    assert "index.html" not in v1["PRECACHE_URLS"]
    assert v1["PRECACHE_URLS"][0] == "./"
    with open(os.path.join(root, "index.html"), "wb") as f:
        f.write(b"<html>edited page</html>")
    v2, _ = _generate(root)
    assert v1["CACHE_VERSION"] != v2["CACHE_VERSION"]


def test_unrecorded_files_neither_precached_nor_hashed(tmp_path):
    # Files the build did not record - the orchestrator's social card and
    # poster, a logo an earlier build wrote - never cost a rider bandwidth,
    # and regenerating one must not bust every rider's cache.
    root = str(tmp_path)
    v1, _ = _generate(root)
    for unrecorded in ("og-image.jpg", "poster.pdf", "logo-9.webp"):
        assert unrecorded not in v1["PRECACHE_URLS"], unrecorded
        assert unrecorded not in v1["PRECACHE_BYTES"], unrecorded
    with open(os.path.join(root, "poster.pdf"), "wb") as f:
        f.write(b"regenerated poster")
    v2, _ = _generate(root)
    assert v1["CACHE_VERSION"] == v2["CACHE_VERSION"]


def test_recorded_but_missing_file_is_skipped(tmp_path):
    root = str(tmp_path)
    cfg, _ = _generate(root, shipped=SHIPPED | {"pois.geojson"})
    assert "pois.geojson" not in cfg["PRECACHE_URLS"]


def test_cache_version_covers_non_precached_files(tmp_path):
    # CACHE_VERSION hashes ALL deployed files, not just the precache
    # subset: a changed cache-on-fetch glyph must still bust the cache.
    root = str(tmp_path)
    v1, _ = _generate(root)
    with open(os.path.join(root, "fonts/Noto Sans Regular/256-511.pbf"), "wb") as f:
        f.write(b"changed extended glyphs")
    v2, _ = _generate(root)
    assert v1["CACHE_VERSION"] != v2["CACHE_VERSION"]


def test_cache_version_stable_across_reruns(tmp_path):
    # The generated sw.js itself must stay out of the hash, or every
    # rebuild over a prior output tree would bust every rider's cache
    # with nothing changed.
    root = str(tmp_path)
    v1, _ = _generate(root)
    v2, _ = _generate(root)
    assert v1["CACHE_VERSION"] == v2["CACHE_VERSION"]
