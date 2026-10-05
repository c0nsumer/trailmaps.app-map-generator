"""The service worker must know about every file the build writes for the map.

Builds configs/example from scratch with the network blocked and checks
that the precache list (plus the cache-on-fetch glyphs) covers every file
in the fresh output. A writer that forgets template_inject.ship would ship
a file the app needs that silently fails offline; this is where that shows.

The build reads Overpass responses and vendor libraries from the repo's
cache/, copied into a scratch cache dir, so the repo cache is never
touched. On a checkout without a warm cache for the example the test
skips, like test_eslint does without Node.

Run from repo root:
    python -m pytest scripts/tests/test_shipped_outputs.py -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_CACHE = os.path.join(REPO_ROOT, "cache")
EXAMPLE = os.path.join(REPO_ROOT, "configs", "example", "example.yaml")

# Files a fresh output never holds but a real one can: the orchestrator's
# own files and a logo an earlier build wrote.
STRAYS = ("poster.pdf", "logo-9.webp")

# The two multi-MB archives, shipped only when present in the output dir.
ARCHIVES = ("basemap.pmtiles", "terrain.pmtiles")


def _write_stub_archive(path):
    """The smallest archive the font scan can open: one empty tile."""
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import write

    header = {"tile_compression": Compression.NONE, "tile_type": TileType.MVT,
              "min_zoom": 0, "max_zoom": 0, "min_lon_e7": 0, "min_lat_e7": 0,
              "max_lon_e7": 10, "max_lat_e7": 10, "center_zoom": 0,
              "center_lon_e7": 0, "center_lat_e7": 0}
    with write(path) as w:
        w.write_tile(zxy_to_tileid(0, 0, 0), b"")
        w.finalize(header, {})


def _seed_cache(dst):
    """Copy the example's cached claims plus the vendor cache into dst.
    Returns False if anything the offline build needs is missing."""
    manifest = os.path.join(REPO_CACHE, "manifests", "example.json")
    vendor = os.path.join(REPO_CACHE, "vendor")
    if not os.path.isfile(manifest) or not os.path.isdir(vendor):
        return False
    with open(manifest, encoding="utf-8") as f:
        cats = json.load(f)["categories"]
    os.makedirs(os.path.join(dst, "manifests"))
    shutil.copy2(manifest, os.path.join(dst, "manifests"))
    shutil.copytree(vendor, os.path.join(dst, "vendor"))
    for cat in ("overpass_trails", "overpass_pois", "derive_accent"):
        for rel in cats.get(cat, []):
            src = os.path.join(REPO_CACHE, rel)
            if not os.path.isfile(src):
                return False
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(dst, rel))
    return True


def test_precache_covers_every_built_file(tmp_path):
    pytest.importorskip("pmtiles.writer")
    cache_dir = str(tmp_path / "cache")
    out_dir = str(tmp_path / "out")
    if not _seed_cache(cache_dir):
        pytest.skip("no warm cache for configs/example (build it once first)")
    os.makedirs(out_dir)
    for name in STRAYS:
        (tmp_path / "out" / name).write_bytes(b"not an engine output")
    # --no-basemap / --no-terrain serve the archives a previous build left,
    # so planting them reaches the two ship() calls for the archives. The
    # font scan opens basemap.pmtiles and aborts the build on a file that
    # is not a PMTiles archive, so both are one-empty-tile archives.
    for name in ARCHIVES:
        _write_stub_archive(str(tmp_path / "out" / name))

    # A dead proxy turns any network attempt into a failure, so a pass
    # proves the build ran offline.
    env = dict(os.environ)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[var] = "http://127.0.0.1:9"
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "build.py"), EXAMPLE,
         "--output-dir", out_dir, "--cache-dir", cache_dir,
         "--no-basemap", "--no-terrain", "--no-minify", "--no-precompress", "--quiet"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    with open(os.path.join(out_dir, "sw.js"), encoding="utf-8") as f:
        m = re.search(r"const SW_CONFIG = (\{.*?\n\});", f.read(), re.S)
    sw_config = json.loads(m.group(1))
    # The list holds URLs (template_inject.url_path); compare as paths.
    precache = {urllib.parse.unquote(u) for u in sw_config["PRECACHE_URLS"]}

    built = set()
    for root, _dirs, files in os.walk(out_dir):
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), out_dir).replace(os.sep, "/")
            built.add(rel)
    built -= {"sw.js", *STRAYS}
    built = {f for f in built if not f.endswith((".sig", ".src.geojson", ".tmp"))}
    # The page precaches as "./"; glyphs past Basic Latin cache on fetch.
    built.discard("index.html")
    built = {f for f in built
             if not (f.startswith("fonts/") and f.endswith(".pbf")
                     and not f.endswith("/0-255.pbf"))}

    assert precache - {"./"} == built
    assert set(ARCHIVES) <= precache
    assert sorted(sw_config["PMTILES_FILES"]) == sorted(ARCHIVES)
    for name in STRAYS:
        assert name not in precache
