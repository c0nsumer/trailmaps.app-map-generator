#!/usr/bin/env python3
"""Generate self-hosted terrain/hillshade tiles as PMTiles.

Primary path: extract pre-built Terrarium terrain tiles for the
configured bounding box from the Mapterhorn project using ``pmtiles
extract`` — no GDAL stack needed, and the only path that works with a
stock ``requirements.txt`` install.

Fallback: build the tiles locally from SRTM elevation data (download,
reproject to Web Mercator, encode as Terrarium RGB, package as
PMTiles). Requires extra dependencies not in requirements.txt:
  pip install elevation rasterio rio-rgbify rio-pmtiles
  (Also requires GDAL system libraries)

Internal build sub-stage: build.py imports and calls fetch_terrain()
directly; the ``__main__`` CLI exists only for standalone debugging.
"""

import os
import shutil
import subprocess
import sys

import cli
import console
import yaml
from pmtiles_util import extract, find_pmtiles_cli

# Mapterhorn (Protomaps terrain) — pre-built Terrarium-encoded RGB PMTiles
# This is the simpler alternative to building from SRTM
MAPTERHORN_URL = "https://download.mapterhorn.com/planet.pmtiles"


def load_config(config_path):
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_from_mapterhorn(bbox, output_path, maxzoom=12):
    """Extract terrain tiles from Mapterhorn (Protomaps' terrain PMTiles).

    This is the simplest approach — Mapterhorn provides pre-built terrain
    RGB tiles in Terrarium encoding, packaged as PMTiles. We just extract
    the bounding box we need.
    """
    pmtiles_cli = find_pmtiles_cli()
    if not pmtiles_cli:
        console.step("ERROR: pmtiles CLI not found.")
        console.step("Install: go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest")
        sys.exit(1)

    # Pad bbox for terrain (need surrounding context for hillshade edge tiles)
    pad = 0.05
    padded = [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad]

    terrain_url = os.environ.get("MAPTERHORN_URL", MAPTERHORN_URL)

    # Atomic (via pmtiles_util.extract): a failed/interrupted extract
    # can't leave a partial terrain.pmtiles at the deploy path. That
    # matters more here than for the basemap — terrain failure is
    # NON-fatal (build.py continues without hillshade), so a partial
    # file wouldn't stop the build and would be precached and shipped.
    return extract(pmtiles_cli, terrain_url, output_path, padded, maxzoom)


def build_from_srtm(bbox, output_path, maxzoom=12):
    """Build terrain tiles from SRTM data using GDAL + rio-rgbify.

    This is the full pipeline approach for when you want maximum control
    or Mapterhorn is unavailable.

    Requires: elevation, rasterio, rio-rgbify, rio-pmtiles, GDAL
    """
    try:
        import elevation as elev
    except ImportError:
        console.error("'elevation' package not installed.")
        console.info("Install: pip install elevation rasterio rio-rgbify rio-pmtiles")
        return False

    cache_dir = os.path.join(os.path.dirname(output_path) or ".", ".terrain_cache")
    os.makedirs(cache_dir, exist_ok=True)

    dem_path = os.path.join(cache_dir, "dem.tif")
    dem_mercator_path = os.path.join(cache_dir, "dem_3857.tif")
    terrarium_path = os.path.join(cache_dir, "terrarium.tif")

    # Pad bbox for context
    pad = 0.05
    padded = [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad]

    # Step 1: Download SRTM data
    console.info("Downloading SRTM elevation data...")
    bounds = (padded[0], padded[1], padded[2], padded[3])
    try:
        elev.clip(bounds=bounds, output=dem_path, product="SRTM3")
    except Exception as e:
        console.info(f"ERROR downloading SRTM: {e}")
        return False

    # Step 2: Reproject to Web Mercator
    console.info("Reprojecting to Web Mercator...")
    result = subprocess.run(
        [
            "gdalwarp",
            "-t_srs",
            "EPSG:3857",
            "-r",
            "cubicspline",
            "-overwrite",
            dem_path,
            dem_mercator_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.error(f"gdalwarp failed: {result.stderr}")
        return False

    # Step 3: Encode as Terrarium RGB. This MUST match the client's
    # raster-dem `encoding: "terrarium"` (app.js addTerrainLayers) and the
    # primary Mapterhorn path, which is Terrarium too. Mapbox Terrain-RGB
    # (the old `-b -10000 -i 0.1`) would be silently misdecoded by the
    # terrarium reader into garbage elevations and a broken hillshade.
    console.info("Encoding as Terrarium RGB...")
    result = subprocess.run(
        [
            "rio",
            "rgbify",
            "-e",
            "terrarium",
            dem_mercator_path,
            terrarium_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.error(f"rio rgbify failed: {result.stderr}")
        return False

    # Step 4: Package as PMTiles. Atomic for the same reason as the
    # Mapterhorn path: write to a .tmp sibling and rename into place
    # only on success, so a failure here can't leave a partial archive
    # at the deploy path.
    console.info("Packaging as PMTiles...")
    tmp_path = output_path + ".tmp"
    result = subprocess.run(
        [
            "rio",
            "pmtiles",
            terrarium_path,
            tmp_path,
            "--format",
            "PNG",
            "--resampling",
            "bilinear",
            "--maxzoom",
            str(maxzoom),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not os.path.exists(tmp_path):
        console.error(f"rio pmtiles failed: {result.stderr}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    os.replace(tmp_path, output_path)

    # Cleanup cache
    shutil.rmtree(cache_dir, ignore_errors=True)
    return True


def fetch_terrain(config_or_path, output_path):
    """Main entry point: generate terrain PMTiles."""
    config = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
    # Use pan_bbox (looser envelope) so terrain covers the whole area the
    # user can pan to, matching the basemap extraction footprint.
    bbox = config.get("pan_bbox") or config["bbox"]
    maxzoom = config.get("terrain_maxzoom", 12)

    console.step(f"Generating terrain tiles for {config['name']}...")
    console.info(f"Bbox: {bbox}")
    console.info(f"Max zoom: {maxzoom}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Try Mapterhorn first (simplest, no GDAL needed)
    console.info("Attempting Mapterhorn extract (pre-built terrain tiles)...")
    if extract_from_mapterhorn(bbox, output_path, maxzoom):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        console.info(f"Wrote {output_path} ({size_mb:.1f} MB)")
        return True

    # Fall back to SRTM pipeline
    console.info("Mapterhorn extract failed, trying SRTM pipeline...")
    if build_from_srtm(bbox, output_path, maxzoom):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        console.info(f"Wrote {output_path} ({size_mb:.1f} MB)")
        return True

    console.warn("Could not generate terrain tiles.")
    console.info("The map will work without terrain — hillshade will be disabled.")
    return False


if __name__ == "__main__":
    parser = cli.config_output_parser("Generate terrain/hillshade PMTiles for the configured bbox.")
    args = parser.parse_args()

    config = load_config(args.config)
    output = args.output or os.path.join("build", config["slug"], "terrain.pmtiles")
    fetch_terrain(args.config, output)
