#!/usr/bin/env python3
"""Extract basemap tiles from Protomaps planet PMTiles.

Uses the pmtiles CLI to extract vector tiles for the configured bounding
box from the Protomaps planet file. The output is a PMTiles file that can
be served statically.
"""

import os
import shutil
import subprocess
import sys
from datetime import date, timedelta

import requests
import yaml

PROTOMAPS_BUILD_BASE = "https://build.protomaps.com"
# How many days back to search for an available build
MAX_SEARCH_DAYS = 30


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def find_latest_protomaps_build():
    """Find the latest available Protomaps planet build by date.

    Checks today first, then walks backwards day-by-day using lightweight
    HEAD requests until an available build is found.
    """
    tomorrow = date.today() + timedelta(days=1)
    print("  Finding latest Protomaps build...")
    for days_back in range(MAX_SEARCH_DAYS):
        check_date = tomorrow - timedelta(days=days_back)
        filename = check_date.strftime("%Y%m%d") + ".pmtiles"
        url = f"{PROTOMAPS_BUILD_BASE}/{filename}"
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                print(f"  Found build: {filename}" + (" (today)" if days_back == 0 else f" ({days_back}d old)"))
                return url
        except requests.RequestException:
            continue
    print(f"  warn: No Protomaps build found in the last {MAX_SEARCH_DAYS} days.")
    print(f"  Check https://maps.protomaps.com/builds/ for available builds.")
    return None


def find_pmtiles_cli():
    """Find the pmtiles CLI binary."""
    path = shutil.which("pmtiles")
    if path:
        return path

    # Check common install locations
    for candidate in [
        os.path.expanduser("~/go/bin/pmtiles"),
        "/usr/local/bin/pmtiles",
        "/opt/homebrew/bin/pmtiles",
    ]:
        if os.path.isfile(candidate):
            return candidate

    return None


def fetch_basemap(config_or_path, output_path, planet_url=None):
    """Extract basemap tiles for the configured bounding box."""
    config = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
    # Use the pan_bbox (looser envelope) so basemap tiles cover the full
    # area the user can pan to, not just the tight initial-view bbox.
    # Fall back to bbox when called with a pre-pan_bbox config.
    bbox = config.get("pan_bbox") or config["bbox"]
    maxzoom = config.get("basemap_maxzoom", 15)

    # Pad the bbox slightly to ensure edge tiles are included
    pad = 0.02
    padded_bbox = [
        bbox[0] - pad,  # west
        bbox[1] - pad,  # south
        bbox[2] + pad,  # east
        bbox[3] + pad,  # north
    ]

    planet = planet_url or os.environ.get("PROTOMAPS_PLANET_URL")
    if not planet:
        planet = find_latest_protomaps_build()
        if not planet:
            print("  ERROR: Could not find an available Protomaps basemap build.")
            sys.exit(1)

    print(f"Extracting basemap for {config['name']}...")
    print(f"  Bbox: {padded_bbox} (padded from {bbox})")
    print(f"  Max zoom: {maxzoom}")
    print(f"  Source: {planet}")

    pmtiles_cli = find_pmtiles_cli()
    if not pmtiles_cli:
        print("ERROR: pmtiles CLI not found.")
        print("Install it with: go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest")
        print("Or download from: https://github.com/protomaps/go-pmtiles/releases")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    bbox_str = f"{padded_bbox[0]},{padded_bbox[1]},{padded_bbox[2]},{padded_bbox[3]}"

    cmd = [
        pmtiles_cli, "extract",
        planet,
        output_path,
        f"--bbox={bbox_str}",
        f"--maxzoom={maxzoom}",
    ]

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ERROR: pmtiles extract failed:")
        print(f"  stdout: {result.stdout}")
        print(f"  stderr: {result.stderr}")
        sys.exit(1)

    if result.stdout:
        print(f"  {result.stdout.strip()}")
    if result.stderr:
        # pmtiles outputs progress to stderr
        for line in result.stderr.strip().split("\n"):
            if line.strip():
                print(f"  {line.strip()}")

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  Wrote {output_path} ({size_mb:.1f} MB)")
    else:
        print(f"  ERROR: Output file not created: {output_path}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <config.yaml> [output.pmtiles] [planet_url]")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)
    output = sys.argv[2] if len(sys.argv) > 2 else os.path.join("build", config["slug"], "basemap.pmtiles")
    planet = sys.argv[3] if len(sys.argv) > 3 else None

    fetch_basemap(config_path, output, planet)
