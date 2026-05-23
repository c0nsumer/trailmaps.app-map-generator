#!/usr/bin/env python3
"""Generate self-hosted terrain/hillshade tiles as PMTiles.

Downloads SRTM elevation data for the configured bounding box, reprojects
to Web Mercator, encodes as Terrain RGB, and packages as PMTiles for
MapLibre GL JS hillshade rendering.

Dependencies:
  pip install elevation rasterio rio-rgbify rio-pmtiles
  (Also requires GDAL system libraries)

Alternative: If the full pipeline is too complex to install, this script
can also download pre-built terrain PMTiles from Protomaps' Mapterhorn
project using pmtiles extract.
"""

import os
import shutil
import subprocess
import sys

import cli
import console
import yaml

# Mapterhorn (Protomaps terrain) — pre-built terrain RGB PMTiles
# This is the simpler alternative to building from SRTM
MAPTERHORN_URL = "https://download.mapterhorn.com/planet.pmtiles"


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def find_pmtiles_cli():
    """Find the pmtiles CLI binary."""
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
    bbox_str = f"{padded[0]},{padded[1]},{padded[2]},{padded[3]}"

    terrain_url = os.environ.get("MAPTERHORN_URL", MAPTERHORN_URL)

    cmd = [
        pmtiles_cli,
        "extract",
        terrain_url,
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
        return False

    if result.stdout:
        console.info(f"{result.stdout.strip()}")
    if result.stderr:
        for line in result.stderr.strip().split("\n"):
            if line.strip():
                console.info(f"{line.strip()}")

    return os.path.exists(output_path)


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
    terrain_rgb_path = os.path.join(cache_dir, "terrain_rgb.tif")

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

    # Step 3: Encode as Terrain RGB
    console.info("Encoding as Terrain RGB...")
    result = subprocess.run(
        [
            "rio",
            "rgbify",
            "-b",
            "-10000",
            "-i",
            "0.1",
            dem_mercator_path,
            terrain_rgb_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.error(f"rio rgbify failed: {result.stderr}")
        return False

    # Step 4: Package as PMTiles
    console.info("Packaging as PMTiles...")
    result = subprocess.run(
        [
            "rio",
            "pmtiles",
            terrain_rgb_path,
            output_path,
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
    if result.returncode != 0:
        console.error(f"rio pmtiles failed: {result.stderr}")
        return False

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
