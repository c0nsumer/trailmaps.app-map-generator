"""Shared helpers for the pmtiles CLI (basemap + terrain extraction).

Previously fetch_basemap.py and fetch_terrain.py each carried their own
copy of the CLI discovery and the extract-subprocess block — and both
pointed ``pmtiles extract`` directly at the deploy-target path, so a
failed or interrupted extract left a partial ``.pmtiles`` where
generate_service_worker would sweep it into the precache list and ship
it to riders. This module is the single home for both, with the
write-atomicity fix built in: extraction goes to a ``.tmp`` sibling and
is renamed into place only on success, so the deploy path only ever
holds a complete archive (or nothing).
"""

import os
import shutil
import subprocess

import console


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


def extract(pmtiles_cli, source_url, output_path, bbox, maxzoom):
    """Run ``pmtiles extract`` atomically. Returns True on success.

    Extracts to ``<output_path>.tmp`` and ``os.replace``s into place
    only when the CLI exits 0 and the file exists — an interrupted or
    failed extract can never leave a partial archive at the deploy
    path. Any ``.tmp`` residue (this run's failure, or a previous
    run's interruption) is removed. ``.tmp`` files are also excluded
    from the service-worker sweep and the deploy rsync as a second
    fence (see _is_build_only_artifact in build.py).

    Echoes the CLI's stdout/stderr through console.info — pmtiles
    reports its progress on stderr.
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
        f"--maxzoom={maxzoom}",
    ]
    console.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            console.error("pmtiles extract failed:")
            console.info(f"stdout: {result.stdout}")
            console.info(f"stderr: {result.stderr}")
            return False

        if result.stdout:
            console.info(f"{result.stdout.strip()}")
        if result.stderr:
            # pmtiles outputs progress to stderr
            for line in result.stderr.strip().split("\n"):
                if line.strip():
                    console.info(f"{line.strip()}")

        if not os.path.exists(tmp_path):
            console.error(f"pmtiles extract produced no output: {tmp_path}")
            return False
        os.replace(tmp_path, output_path)
        return True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
