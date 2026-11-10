"""A rebuild must not touch files whose bytes did not change.

Caddy derives ETags from mtime and size and rsync -a carries mtime, so a
rewritten-but-identical file makes every rider re-download it. These tests
build a tiny offline map twice (minify and precompress on) and compare
mtimes, never console output.
"""

import contextlib
import io
import os

import console
import pytest
import stable_mtimes
from test_offline_build import BASE_YAML, _map_dir

import build


@pytest.fixture(autouse=True)
def _no_vendor_download(monkeypatch):
    monkeypatch.setattr(build, "download_vendor_libs", lambda *a, **k: None)


def _build(root):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build.main([
                str(root / "tiny.yaml"), "--output-dir", str(root / "out"),
                "--cache-dir", str(root / "cache"), "--no-basemap", "--no-terrain",
            ])
    finally:
        console.set_verbosity()


def _mtimes(out):
    found = {}
    for base, _dirs, files in os.walk(out):
        for fname in files:
            path = os.path.join(base, fname)
            found[os.path.relpath(path, out)] = os.stat(path).st_mtime_ns
    return found


def test_a_no_change_rebuild_leaves_every_mtime_alone(tmp_path):
    root = _map_dir(tmp_path)
    _build(root)
    first = _mtimes(root / "out")
    # Sidecars are part of what must stay put.
    assert any(name.endswith((".gz", ".br")) for name in first)
    _build(root)
    assert _mtimes(root / "out") == first


def test_a_real_change_moves_only_the_files_that_depend_on_it(tmp_path):
    root = _map_dir(tmp_path)
    _build(root)
    first = _mtimes(root / "out")
    (root / "tiny.yaml").write_text(BASE_YAML.replace("#2E6B0F", "#8B1A1A"))
    _build(root)
    second = _mtimes(root / "out")
    assert set(second) == set(first)
    moved = {name for name in first if second[name] != first[name]}
    # The accent lands in the injected page, and its sidecars follow it.
    assert "index.html" in moved
    assert any(name.startswith("index.html.") for name in moved)
    assert len(moved) < len(first)


def test_a_sidecar_for_a_file_no_longer_shipped_is_removed(tmp_path):
    root = _map_dir(tmp_path)
    _build(root)
    orphan = root / "out" / "gone.js.gz"
    orphan.write_bytes(b"stale")
    _build(root)
    assert not orphan.exists()


def test_restore_keeps_old_mtime_only_for_equal_bytes(tmp_path):
    same = tmp_path / "same.txt"
    changed = tmp_path / "changed.txt"
    same.write_text("a")
    changed.write_text("a")
    os.utime(same, ns=(1_000_000_000, 1_000_000_000))
    os.utime(changed, ns=(1_000_000_000, 1_000_000_000))
    before = stable_mtimes.snapshot(tmp_path)
    same.write_text("a")
    changed.write_text("b")
    stable_mtimes.restore(before)
    assert same.stat().st_mtime_ns == 1_000_000_000
    assert changed.stat().st_mtime_ns != 1_000_000_000
