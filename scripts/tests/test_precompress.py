"""Tests for build.precompress_assets - the .gz/.br sidecar pass.

The properties under test: both encodings are written for compressible
assets (brotli is what Safari riders actually get; zstd never was),
stale sidecars from earlier builds are swept (including retired .zst
ones), and incompressible or tiny files gain no sidecar.

Run from repo root:
    python -m pytest scripts/tests/test_precompress.py -v
"""

import gzip
import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brotli  # noqa: E402

from build import precompress_assets  # noqa: E402


def _plant(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_writes_gzip_and_brotli_sidecars(tmp_path):
    root = str(tmp_path)
    raw = b'{"type":"FeatureCollection","features":[]}' * 100
    _plant(root, "trails.geojson", raw)
    precompress_assets(root)
    with open(os.path.join(root, "trails.geojson.gz"), "rb") as f:
        assert gzip.decompress(f.read()) == raw
    with open(os.path.join(root, "trails.geojson.br"), "rb") as f:
        assert brotli.decompress(f.read()) == raw
    assert not os.path.exists(os.path.join(root, "trails.geojson.zst"))


def test_stale_sidecars_swept_including_zst(tmp_path):
    # A rebuild over a pre-swap output dir must clear retired .zst
    # sidecars and any orphan whose original no longer exists.
    root = str(tmp_path)
    _plant(root, "app.js", b"var x = 1;" * 500)
    _plant(root, "app.js.zst", b"old zstd sidecar")
    _plant(root, "gone.css.gz", b"orphan for a removed file")
    precompress_assets(root)
    assert not os.path.exists(os.path.join(root, "app.js.zst"))
    assert not os.path.exists(os.path.join(root, "gone.css.gz"))
    assert os.path.exists(os.path.join(root, "app.js.gz"))
    assert os.path.exists(os.path.join(root, "app.js.br"))


def test_small_and_binary_files_skipped(tmp_path):
    root = str(tmp_path)
    _plant(root, "tiny.json", b"{}")  # under PRECOMPRESS_MIN_BYTES
    _plant(root, "basemap.pmtiles", os.urandom(4096))  # Range must keep working
    _plant(root, "icons/icon.png", os.urandom(4096))  # already compressed
    precompress_assets(root)
    for rel in ("tiny.json", "basemap.pmtiles", "icons/icon.png"):
        assert not os.path.exists(os.path.join(root, rel + ".gz")), rel
        assert not os.path.exists(os.path.join(root, rel + ".br")), rel
