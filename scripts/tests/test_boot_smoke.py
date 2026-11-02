"""Boot the built example map in headless Chromium and check boot finished.

The templates are plain JS with no build step. ESLint no-undef (test_eslint)
catches undefined identifiers statically, but an exception thrown inside the
map's style.load handler still ships silently: it kills the panel reveal and
the rest of boot. This test builds the example offline, serves it, and
asserts that no page error fired and the boot chain's visible results exist.

Skips (rather than fails) without Node, node_modules/playwright-core, or a
Chromium that Playwright already cached: like the lint, this is optional dev
tooling. Nothing here downloads a browser. Set TRAILMAPS_CHROMIUM to use a
specific executable.
"""

import contextlib
import glob
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT = REPO_ROOT / "node_modules" / "playwright-core"
DRIVER = Path(__file__).with_name("boot_smoke.mjs")
EXAMPLE = REPO_ROOT / "configs" / "example" / "example.yaml"

# Newest first within each pattern; the headless shell is lighter, so it wins.
BROWSER_GLOBS = [
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium_headless_shell-*/chrome-headless-shell-linux/chrome-headless-shell",
    "chromium-*/chrome-linux64/chrome",
    "chromium-*/chrome-linux/chrome",
]


def _find_chromium():
    override = os.environ.get("TRAILMAPS_CHROMIUM")
    if override:
        return override if os.path.isfile(override) else None
    cache = os.path.expanduser("~/.cache/ms-playwright")
    for pattern in BROWSER_GLOBS:
        # Revision numbers are same-width, so a string sort is newest-last.
        hits = sorted(glob.glob(os.path.join(cache, pattern)), reverse=True)
        if hits:
            return hits[0]
    return None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port, proc, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
            return True
        time.sleep(0.05)
    return False


def _build_example(tmp_path):
    import console
    from test_shipped_outputs import _seed_cache

    import build

    cache_dir = str(tmp_path / "cache")
    out_dir = str(tmp_path / "out")
    if not _seed_cache(cache_dir):
        pytest.skip("no warm cache for configs/example (build it once first)")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build.main([
                str(EXAMPLE), "--output-dir", out_dir, "--cache-dir", cache_dir,
                "--no-basemap", "--no-terrain", "--no-minify", "--no-precompress",
                "--quiet",
            ])
    finally:
        console.set_verbosity()
    return out_dir


def test_example_map_boots_headless(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed; boot smoke test is optional dev tooling")
    if not PLAYWRIGHT.exists():
        pytest.skip("playwright-core not installed (run `corepack pnpm install`)")
    chromium = _find_chromium()
    if chromium is None:
        pytest.skip("no cached Chromium under ~/.cache/ms-playwright "
                    "(set TRAILMAPS_CHROMIUM to an executable)")

    out_dir = _build_example(tmp_path)

    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "serve.py"), out_dir,
         "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait_for_port(port, server), "dev server did not start"
        proc = subprocess.run(
            [node, str(DRIVER), f"http://127.0.0.1:{port}/", chromium],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    finally:
        server.terminate()
        server.wait(timeout=10)

    if proc.returncode == 2:
        pytest.skip(f"Chromium would not launch here:\n{proc.stderr}")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    shown = json.dumps(result, indent=2)

    assert result["pageErrors"] == [], shown
    assert result["consoleErrors"] == [], shown
    assert result["panelPresent"], shown
    assert result["optionsFab"], shown
    assert result["finderRows"] > 0, shown
    zoom = result["zoom"]
    assert isinstance(zoom, (int, float)) and math.isfinite(zoom), shown
