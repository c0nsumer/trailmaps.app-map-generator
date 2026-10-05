#!/usr/bin/env python3
"""Generate self-hosted terrain/hillshade tiles as PMTiles.

Extracts pre-built Terrarium terrain tiles for the configured bounding
box from the Mapterhorn project using ``pmtiles extract``. Terrain is
optional: when the extract fails, the build warns and the map ships
without hillshade.

Internal build sub-stage: build.py imports and calls fetch_terrain()
directly; the ``__main__`` CLI exists only for standalone debugging.
"""

import os

import cli
import console
from cache_signatures import _clear_signature
from config_io import load_config_for_fetch
from pmtiles_util import EXTRACT_MINZOOM, TERRAIN_MAXZOOM, extract, find_pmtiles_cli

# Mapterhorn (Protomaps terrain) - pre-built Terrarium-encoded RGB PMTiles
MAPTERHORN_URL = "https://download.mapterhorn.com/planet.pmtiles"


def extract_from_mapterhorn(bbox, output_path, maxzoom=TERRAIN_MAXZOOM, minzoom=0):
    """Extract terrain tiles from Mapterhorn (Protomaps' terrain PMTiles).

    This is the simplest approach - Mapterhorn provides pre-built terrain
    RGB tiles in Terrarium encoding, packaged as PMTiles. We just extract
    the bounding box we need.
    """
    # Missing CLI is a soft failure, NOT sys.exit: terrain is non-fatal
    # by design (build.py continues without hillshade), and an exit
    # here killed the whole build - or re-raised SystemExit out of the
    # parallel-fetch thread pool - when only the terrain layer was at
    # stake. Returning False reaches fetch_terrain's could-not-generate
    # warning.
    pmtiles_cli = find_pmtiles_cli()
    if not pmtiles_cli:
        console.error("pmtiles CLI not found - cannot extract terrain.")
        console.info("Install: go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest")
        return False

    # Pad bbox for terrain (need surrounding context for hillshade edge tiles)
    pad = 0.05
    padded = [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad]

    terrain_url = os.environ.get("MAPTERHORN_URL", MAPTERHORN_URL)

    # Atomicity matters more here than for the basemap: terrain failure
    # is NON-fatal, so a partial file would not stop the build and would
    # be precached and shipped.
    return extract(pmtiles_cli, terrain_url, output_path, padded, maxzoom, minzoom)


def fetch_terrain(config_or_path, output_path):
    """Main entry point: generate terrain PMTiles."""
    config = (config_or_path if isinstance(config_or_path, dict)
              else load_config_for_fetch(config_or_path))
    # Use pan_bbox (looser envelope) so terrain covers the whole area the
    # user can pan to, matching the basemap extraction footprint.
    bbox = config.get("pan_bbox") or config["bbox"]
    maxzoom = TERRAIN_MAXZOOM
    minzoom = EXTRACT_MINZOOM

    console.step(f"Generating terrain tiles for {config['name']}...", detail=True)
    console.detail(f"Bbox: {bbox}")
    console.detail(f"Zoom range: {minzoom}-{maxzoom}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    console.detail("Attempting Mapterhorn extract (pre-built terrain tiles)...")
    if extract_from_mapterhorn(bbox, output_path, maxzoom, minzoom):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        console.detail(f"Wrote {output_path} ({size_mb:.1f} MB)")
        return True

    console.warn("Could not generate terrain tiles.")
    console.info("The map will work without terrain - hillshade will be disabled.")
    return False


if __name__ == "__main__":
    parser = cli.config_output_parser("Generate terrain/hillshade PMTiles for the configured bbox.")
    args = parser.parse_args()
    console.set_verbosity(verbose=True)

    config = load_config_for_fetch(args.config)
    output = args.output or os.path.join("build", config["slug"], "terrain.pmtiles")
    # The CLI extracts the config bbox, not build.py's pan_bbox, and writes
    # no signature: an old one must not vouch for this file.
    _clear_signature(output)
    fetch_terrain(config, output)
