#!/usr/bin/env python3
"""Automatic font trimming for the trailmaps.app Map Generator.

Scans basemap PMTiles and GeoJSON data to determine which Unicode
character ranges are actually used, then copies only the needed
PBF glyph files. This is fully data-driven: a US trail map gets
only Latin ranges (~1 MB), while a map in Japan would automatically
include CJK ranges.

Usage as standalone dry-run:
    python scripts/font_trimmer.py build/ramba/
"""

import argparse
import gzip
import json
import mmap
import os
import shutil

import console

# Font faces always needed by the Protomaps basemap style.
BASEMAP_FACES = {
    "Noto Sans Regular",
    "Noto Sans Medium",
    "Noto Sans Italic",
}

# Script-specific font faces and the Unicode blocks they cover.
# A face is included only when characters from its blocks appear in the data.
SCRIPT_FONT_BLOCKS = {
    "Noto Sans Devanagari Regular v1": [
        (0x0900, 0x097F),  # Devanagari
        (0xA8E0, 0xA8FF),  # Devanagari Extended
        (0x1CD0, 0x1CFF),  # Vedic Extensions
    ],
    # Add more script-specific faces here as they are added to assets/fonts/
}

# Basemap label layers render only a small set of name fields (see the
# `o()` text-field builder in vendor/basemaps.js): the localized name for
# the configured language, the generic `name`, and the secondary
# `name2`/`name3` slots. The app pins lang="en" (app.js), so these are the
# only fields whose glyphs can ever be requested at runtime. Scanning ONLY
# these — instead of every tile property — stops untranslated `name:ko` /
# `name:ja` / `pgf:name:hi` fields (present in planet-extract basemaps for
# i18n but never rendered here) from dragging in whole CJK / Hangul /
# Devanagari glyph ranges, and the Devanagari face, that no client ever
# fetches. For a typical English map this is the single biggest deploy-size
# lever (~6.6 MB -> ~1.8 MB of glyphs). If the app's basemap `lang` ever
# changes from "en", add the matching `name:<lang>` entries here.
RENDERED_NAME_FIELDS = {
    "name",
    "name:en",
    "name2",
    "name2:en",
    "name3",
    "name3:en",
}


def collect_text_from_geojson(path):
    """Extract all string property values from a GeoJSON file."""
    chars = set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for feat in data.get("features", []):
            for v in feat.get("properties", {}).values():
                if isinstance(v, str):
                    chars.update(v)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return chars


def collect_text_from_pmtiles(path, rendered_fields=RENDERED_NAME_FIELDS):
    """Extract rendered-label text from a PMTiles vector tile archive.

    Only the name fields the style actually renders (``rendered_fields``)
    are scanned; other localized `name:*` / `pgf:*` tile properties exist
    for i18n but are never painted, so counting them would keep glyph
    ranges no client ever fetches. See RENDERED_NAME_FIELDS.
    """
    try:
        import mapbox_vector_tile
        import pmtiles.reader as pmreader
    except ImportError:
        return None  # Signal that scanning is unavailable

    chars = set()
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:

                def get_bytes(offset, length):
                    return mm[offset : offset + length]

                count = 0
                for _zxy, data in pmreader.all_tiles(get_bytes):
                    try:
                        raw = gzip.decompress(data)
                    except (gzip.BadGzipFile, OSError):
                        raw = data
                    try:
                        decoded = mapbox_vector_tile.decode(raw)
                    except Exception:
                        continue
                    for layer in decoded.values():
                        for feat in layer.get("features", []):
                            props = feat.get("properties", {})
                            for k in rendered_fields:
                                v = props.get(k)
                                if isinstance(v, str):
                                    chars.update(v)
                    count += 1
                console.info(f"Scanned {count} basemap tiles")
            finally:
                mm.close()
    except (FileNotFoundError, OSError) as e:
        console.warn(f"Could not read basemap for font scan: {e}")
    return chars


def compute_needed_ranges(chars):
    """Compute the set of PBF range tuples needed for the given characters.

    Each PBF file covers 256 Unicode codepoints (e.g. 0-255, 256-511).
    Always includes 0-255 (Basic Latin) as a baseline.
    """
    ranges = {(0, 255)}  # Always include Basic Latin
    for c in chars:
        cp = ord(c)
        start = (cp // 256) * 256
        ranges.add((start, start + 255))
    return ranges


def determine_needed_faces(chars, available_faces):
    """Determine which font face directories to include.

    Basemap faces are always included. Script-specific faces are included
    only when the data contains characters from their Unicode blocks.
    """
    needed = set()
    codepoints = {ord(c) for c in chars}

    for face in available_faces:
        # Always include basemap faces
        if face in BASEMAP_FACES:
            needed.add(face)
            continue

        # Check script-specific faces against their Unicode blocks
        blocks = SCRIPT_FONT_BLOCKS.get(face)
        if blocks:
            for block_start, block_end in blocks:
                if any(block_start <= cp <= block_end for cp in codepoints):
                    needed.add(face)
                    break
        else:
            # Unknown face not in basemap or script list — skip with warning
            console.warn(
                f"Unknown font face '{face}' — skipping "
                f"(add to SCRIPT_FONT_BLOCKS in font_trimmer.py if needed)"
            )

    return needed


def copy_trimmed_fonts(output_dir, fonts_src):
    """Copy only the needed font face directories and PBF range files.

    Scans basemap PMTiles and GeoJSON data to determine which Unicode
    ranges are actually used, then copies only matching PBF files.
    """
    if not os.path.exists(fonts_src) or not os.listdir(fonts_src):
        console.warn(f"Fonts not found at {fonts_src}")
        console.info("Download from: https://github.com/protomaps/basemaps-assets/releases")
        return

    fonts_dst = os.path.join(output_dir, "fonts")
    if os.path.exists(fonts_dst):
        shutil.rmtree(fonts_dst)

    # Collect all text from map data
    console.info("Scanning map data for font trimming...")
    all_chars = set()

    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    basemap_chars = collect_text_from_pmtiles(basemap_path)
    if basemap_chars is None:
        # PMTiles libraries not available — fall back to full copy
        console.warn("pmtiles/mapbox-vector-tile not installed — copying all fonts")
        shutil.copytree(fonts_src, fonts_dst)
        return
    all_chars.update(basemap_chars)

    for geojson_name in ["trails.geojson", "pois.geojson"]:
        all_chars.update(collect_text_from_geojson(os.path.join(output_dir, geojson_name)))

    if not all_chars:
        # No text found (unlikely) — basemap was probably skipped
        if not os.path.exists(basemap_path):
            console.warn("No basemap found — copying all fonts")
            shutil.copytree(fonts_src, fonts_dst)
            return

    # Compute needed ranges and faces
    needed_ranges = compute_needed_ranges(all_chars)

    available_faces = [
        d for d in os.listdir(fonts_src) if os.path.isdir(os.path.join(fonts_src, d))
    ]
    needed_faces = determine_needed_faces(all_chars, available_faces)

    # Copy only needed PBF files from needed faces
    total_original = 0
    total_copied = 0
    range_filenames = {f"{s}-{e}.pbf" for s, e in needed_ranges}

    for face in sorted(needed_faces):
        face_src = os.path.join(fonts_src, face)
        face_dst = os.path.join(fonts_dst, face)
        os.makedirs(face_dst, exist_ok=True)

        all_pbfs = [f for f in os.listdir(face_src) if f.endswith(".pbf")]
        copied = 0
        for pbf in all_pbfs:
            src_path = os.path.join(face_src, pbf)
            total_original += os.path.getsize(src_path)
            if pbf in range_filenames:
                shutil.copy2(src_path, os.path.join(face_dst, pbf))
                total_copied += os.path.getsize(src_path)
                copied += 1

        console.info(f"Font: {face} — {copied}/{len(all_pbfs)} ranges")

    # Also copy non-font files (like OFL.txt license)
    for item in os.listdir(fonts_src):
        src_path = os.path.join(fonts_src, item)
        if os.path.isfile(src_path):
            shutil.copy2(src_path, os.path.join(fonts_dst, item))

    # Count original total including skipped faces
    for face in available_faces:
        if face not in needed_faces:
            face_src = os.path.join(fonts_src, face)
            for pbf in os.listdir(face_src):
                if pbf.endswith(".pbf"):
                    total_original += os.path.getsize(os.path.join(face_src, pbf))

    skipped_faces = set(available_faces) - needed_faces
    saved_mb = (total_original - total_copied) / (1024 * 1024)
    console.info(
        f"Fonts: {total_copied / (1024 * 1024):.1f} MB "
        f"(trimmed {saved_mb:.1f} MB, "
        f"{len(needed_faces)}/{len(available_faces)} faces, "
        f"{len(needed_ranges)}/256 ranges)"
    )
    if skipped_faces:
        console.info(f"Skipped faces: {', '.join(sorted(skipped_faces))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dry-run: scan a build dir and report which fonts/ranges would be kept."
    )
    parser.add_argument("output_dir", help="Build output directory to scan")
    parser.add_argument(
        "fonts_src",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts"
        ),
        help="Fonts source directory (default: assets/fonts)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    fonts_src = args.fonts_src

    console.step(f"Scanning: {output_dir}")
    console.step(f"Fonts source: {fonts_src}")
    console.blank()

    # Collect text
    all_chars = set()
    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    if os.path.exists(basemap_path):
        basemap_chars = collect_text_from_pmtiles(basemap_path)
        if basemap_chars:
            all_chars.update(basemap_chars)
    for name in ["trails.geojson", "pois.geojson"]:
        all_chars.update(collect_text_from_geojson(os.path.join(output_dir, name)))

    console.step(f"\nUnique characters: {len(all_chars)}")

    needed_ranges = compute_needed_ranges(all_chars)
    console.step(f"PBF ranges needed: {len(needed_ranges)}/256")

    available_faces = [
        d for d in os.listdir(fonts_src) if os.path.isdir(os.path.join(fonts_src, d))
    ]
    needed_faces = determine_needed_faces(all_chars, available_faces)
    console.step(f"Font faces needed: {len(needed_faces)}/{len(available_faces)}")
    console.info(f"Included: {', '.join(sorted(needed_faces))}")
    skipped = set(available_faces) - needed_faces
    if skipped:
        console.info(f"Skipped: {', '.join(sorted(skipped))}")

    # Estimate size savings
    range_filenames = {f"{s}-{e}.pbf" for s, e in needed_ranges}
    total_original = 0
    total_kept = 0
    for face in available_faces:
        face_dir = os.path.join(fonts_src, face)
        for pbf in os.listdir(face_dir):
            if not pbf.endswith(".pbf"):
                continue
            size = os.path.getsize(os.path.join(face_dir, pbf))
            total_original += size
            if face in needed_faces and pbf in range_filenames:
                total_kept += size

    console.step(f"\nOriginal: {total_original / (1024 * 1024):.1f} MB")
    console.step(f"Trimmed:  {total_kept / (1024 * 1024):.1f} MB")
    console.step(
        f"Savings:  {(total_original - total_kept) / (1024 * 1024):.1f} MB "
        f"({100 * (1 - total_kept / total_original):.0f}%)"
    )
