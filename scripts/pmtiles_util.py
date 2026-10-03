"""Shared helpers for the pmtiles CLI (basemap + terrain extraction).

Extraction goes to a ``.tmp`` sibling and is renamed into place only on
success. A partial ``.pmtiles`` at the deploy path would be swept into
the precache list and shipped to riders, so the path only ever holds a
complete archive or nothing.
"""

import os
import shutil
import subprocess

import console

# Fixed per-map zoom bounds. The app's own min/max zoom live in the
# runtime template; MIN_ZOOM here must match the camera clamp there.
MIN_ZOOM = 10
BASEMAP_MAXZOOM = 15
TERRAIN_MAXZOOM = 12

# Lowest tile zoom worth shipping, shared by basemap and terrain. The app
# clamps to MIN_ZOOM inside maxBounds, so tiles below that are
# unreachable: at map zoom z the 512px vector basemap loads tiles at
# floor(z) and the 256px raster-dem at floor(z)+1, so MIN_ZOOM - 1 keeps
# one spare level under both archives. Without the bound, both ship
# z0-z9 world tiles (~0.5-0.6 MB basemap + ~2.6-3.1 MB terrain per
# map) that no rider can ever pan out far enough to see.
EXTRACT_MINZOOM = max(0, MIN_ZOOM - 1)


def find_pmtiles_cli():
    """Find the pmtiles CLI binary, or None if not installed."""
    path = shutil.which("pmtiles")
    if path:
        return path
    for candidate in [
        os.path.expanduser("~/go/bin/pmtiles"),
        "/usr/local/bin/pmtiles",
        "/opt/homebrew/bin/pmtiles",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def extract(pmtiles_cli, source_url, output_path, bbox, maxzoom, minzoom=0):
    """Run ``pmtiles extract`` atomically. Returns True on success.

    Extracts to ``<output_path>.tmp`` and ``os.replace``s into place
    only when the CLI exits 0 and the file exists - an interrupted or
    failed extract can never leave a partial archive at the deploy
    path. Any ``.tmp`` residue (this run's failure, or a previous
    run's interruption) is removed. ``.tmp`` files are also excluded
    from the service-worker precache and the deploy rsync as a second
    fence (see _is_build_only_artifact in build.py).

    The CLI's stdout/stderr is captured and printed only on failure:
    pmtiles reports a redrawn progress bar on stderr, which is pure noise
    in a build log when the extract succeeds.
    """
    tmp_path = output_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    cmd = [
        pmtiles_cli,
        "extract",
        source_url,
        tmp_path,
        f"--bbox={bbox_str}",
        f"--minzoom={minzoom}",
        f"--maxzoom={maxzoom}",
    ]
    console.detail(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            console.error("pmtiles extract failed:")
            console.info(f"stdout: {result.stdout}")
            console.info(f"stderr: {result.stderr}")
            return False

        if not os.path.exists(tmp_path):
            console.error(f"pmtiles extract produced no output: {tmp_path}")
            return False
        os.replace(tmp_path, output_path)
        return True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
