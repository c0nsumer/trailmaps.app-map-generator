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

import cli
import console
import requests
import yaml

PROTOMAPS_BUILD_BASE = "https://build.protomaps.com"
# How many days back to search for an available build
MAX_SEARCH_DAYS = 30


def load_config(config_path):
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_latest_protomaps_build():
    """Find the latest available Protomaps planet build by date.

    Checks today first, then walks backwards day-by-day using lightweight
    HEAD requests until an available build is found.
    """
    tomorrow = date.today() + timedelta(days=1)
    console.info("Finding latest Protomaps build...")
    for days_back in range(MAX_SEARCH_DAYS):
        check_date = tomorrow - timedelta(days=days_back)
        filename = check_date.strftime("%Y%m%d") + ".pmtiles"
        url = f"{PROTOMAPS_BUILD_BASE}/{filename}"
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                console.info(
                    f"Found build: {filename}"
                    + (" (today)" if days_back == 0 else f" ({days_back}d old)")
                )
                return url
        except requests.RequestException:
            continue
    console.warn(f"No Protomaps build found in the last {MAX_SEARCH_DAYS} days.")
    console.info("Check https://maps.protomaps.com/builds/ for available builds.")
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
            console.error("Could not find an available Protomaps basemap build.")
            sys.exit(1)

    console.step(f"Extracting basemap for {config['name']}...")
    console.info(f"Bbox: {padded_bbox} (padded from {bbox})")
    console.info(f"Max zoom: {maxzoom}")
    console.info(f"Source: {planet}")

    pmtiles_cli = find_pmtiles_cli()
    if not pmtiles_cli:
        console.step("ERROR: pmtiles CLI not found.")
        console.step(
            "Install it with: go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest"
        )
        console.step("Or download from: https://github.com/protomaps/go-pmtiles/releases")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    bbox_str = f"{padded_bbox[0]},{padded_bbox[1]},{padded_bbox[2]},{padded_bbox[3]}"

    cmd = [
        pmtiles_cli,
        "extract",
        planet,
        output_path,
        f"--bbox={bbox_str}",
        f"--maxzoom={maxzoom}",
    ]

    console.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        console.error("pmtiles extract failed:")
        console.info(f"stdout: {result.stdout}")
        console.info(f"stderr: {result.stderr}")
        sys.exit(1)

    if result.stdout:
        console.info(f"{result.stdout.strip()}")
    if result.stderr:
        # pmtiles outputs progress to stderr
        for line in result.stderr.strip().split("\n"):
            if line.strip():
                console.info(f"{line.strip()}")

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        console.info(f"Wrote {output_path} ({size_mb:.1f} MB)")
    else:
        console.error(f"Output file not created: {output_path}")
        sys.exit(1)


if __name__ == "__main__":
    parser = cli.config_output_parser("Extract basemap tiles from a Protomaps planet build.")
    parser.add_argument(
        "planet_url", nargs="?", help="Planet build URL (default: auto-detect the latest)"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    output = args.output or os.path.join("build", config["slug"], "basemap.pmtiles")
    fetch_basemap(args.config, output, args.planet_url)
