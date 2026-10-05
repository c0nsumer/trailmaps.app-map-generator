#!/usr/bin/env python3
"""Extract basemap tiles from Protomaps planet PMTiles.

Uses the pmtiles CLI to extract vector tiles for the configured bounding
box from the Protomaps planet file. The output is a PMTiles file that can
be served statically.

Internal build sub-stage: build.py imports and calls fetch_basemap()
directly; the ``__main__`` CLI exists only for standalone debugging.
"""

import os
import sys
from datetime import date, timedelta

import cli
import console
import requests
from cache_signatures import _clear_signature
from config_io import load_config_for_fetch
from pmtiles_util import BASEMAP_MAXZOOM, EXTRACT_MINZOOM, extract, find_pmtiles_cli

PROTOMAPS_BUILD_BASE = "https://build.protomaps.com"
# How many days back to search for an available build
MAX_SEARCH_DAYS = 30

# Degrees added to each side of the extract so edge tiles are included.
# build.py cuts the generated path lines to the same padded bounds.
EXTRACT_PAD_DEG = 0.02


def find_latest_protomaps_build():
    """Find the latest available Protomaps planet build by date.

    Starts at tomorrow's date, because builds are dated in UTC and can be
    ahead of the local date, then walks backwards day-by-day using
    lightweight HEAD requests until an available build is found.
    """
    tomorrow = date.today() + timedelta(days=1)
    console.detail("Finding latest Protomaps build...")
    reached = False
    for days_back in range(MAX_SEARCH_DAYS):
        check_date = tomorrow - timedelta(days=days_back)
        filename = check_date.strftime("%Y%m%d") + ".pmtiles"
        url = f"{PROTOMAPS_BUILD_BASE}/{filename}"
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
        except requests.RequestException:
            continue
        reached = True
        if resp.status_code == 200:
            age = days_back - 1
            console.detail(
                f"Found build: {filename}"
                + (" (today)" if age == 0 else " (dated tomorrow)" if age < 0
                   else f" ({age}d old)")
            )
            return url
    if not reached:
        # Every request failed before any HTTP answer: the network, not
        # Protomaps, is the problem.
        console.warn(f"Could not reach the Protomaps build server ({PROTOMAPS_BUILD_BASE}).")
        return None
    console.warn(f"No Protomaps build found in the last {MAX_SEARCH_DAYS} days.")
    console.info("Check https://maps.protomaps.com/builds/ for available builds.")
    return None


def fetch_basemap(config_or_path, output_path, planet_url=None):
    """Extract basemap tiles for the configured bounding box."""
    config = (config_or_path if isinstance(config_or_path, dict)
              else load_config_for_fetch(config_or_path))
    # Use the pan_bbox (looser envelope) so basemap tiles cover the full
    # area the user can pan to, not just the tight initial-view bbox.
    # Fall back to bbox when called with a pre-pan_bbox config.
    bbox = config.get("pan_bbox") or config["bbox"]
    maxzoom = BASEMAP_MAXZOOM
    minzoom = EXTRACT_MINZOOM

    pad = EXTRACT_PAD_DEG
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

    console.step(f"Extracting basemap for {config['name']}...", detail=True)
    console.detail(f"Bbox: {padded_bbox} (padded from {bbox})")
    console.detail(f"Zoom range: {minzoom}-{maxzoom}")
    console.detail(f"Source: {planet}")

    pmtiles_cli = find_pmtiles_cli()
    if not pmtiles_cli:
        # Basemap failure IS fatal (unlike terrain) - the map is
        # unusable without it - so the sys.exit below is intentional.
        console.error("pmtiles CLI not found - cannot extract basemap.")
        console.info(
            "Install it with: go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest"
        )
        console.info("Or download from: https://github.com/protomaps/go-pmtiles/releases")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Atomic: extracts to a .tmp sibling and renames into place only on
    # success, so a failed/interrupted extract can't leave a partial
    # basemap.pmtiles for the service worker to precache and ship.
    if not extract(pmtiles_cli, planet, output_path, padded_bbox, maxzoom, minzoom):
        sys.exit(1)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    console.detail(f"Wrote {output_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    parser = cli.config_output_parser(
        "Extract basemap tiles from a Protomaps planet build.",
        output_help="Output path (default: cache/basemap/<slug>-protomaps.pmtiles)")
    parser.add_argument(
        "planet_url", nargs="?", help="Planet build URL (default: auto-detect the latest)"
    )
    args = parser.parse_args()
    console.set_verbosity(verbose=True)

    config = load_config_for_fetch(args.config)
    # The default is the plain extract build.py joins its generated lines
    # into; build/<slug>/basemap.pmtiles holds the joined archive and must
    # not be overwritten with a raw extract. The CLI writes no signature,
    # so any existing one is removed rather than left vouching for a file
    # it did not describe.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output = args.output or os.path.join(
        project_root, "cache", "basemap", f"{config['slug']}-protomaps.pmtiles")
    _clear_signature(output)
    fetch_basemap(config, output, args.planet_url)
