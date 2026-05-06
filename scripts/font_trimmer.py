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

import gzip
import json
import mmap
import os
import shutil


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
        (0x0900, 0x097F),   # Devanagari
        (0xA8E0, 0xA8FF),   # Devanagari Extended
        (0x1CD0, 0x1CFF),   # Vedic Extensions
    ],
    # Add more script-specific faces here as they are added to assets/fonts/
}


def collect_text_from_geojson(path):
    """Extract all string property values from a GeoJSON file."""
    chars = set()
    try:
        with open(path) as f:
            data = json.load(f)
        for feat in data.get("features", []):
            for v in feat.get("properties", {}).values():
                if isinstance(v, str):
                    chars.update(v)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return chars


def collect_text_from_pmtiles(path):
    """Extract all string property values from a PMTiles vector tile archive."""
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
                    return mm[offset:offset + length]

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
                            for v in feat.get("properties", {}).values():
                                if isinstance(v, str):
                                    chars.update(v)
                    count += 1
                print(f"  Scanned {count} basemap tiles")
            finally:
                mm.close()
    except (FileNotFoundError, OSError) as e:
        print(f"  warn: Could not read basemap for font scan: {e}")
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
            print(f"  warn: Unknown font face '{face}' — skipping "
                  f"(add to SCRIPT_FONT_BLOCKS in font_trimmer.py if needed)")

    return needed


def copy_trimmed_fonts(output_dir, fonts_src):
    """Copy only the needed font face directories and PBF range files.

    Scans basemap PMTiles and GeoJSON data to determine which Unicode
    ranges are actually used, then copies only matching PBF files.
    """
    if not os.path.exists(fonts_src) or not os.listdir(fonts_src):
        print(f"  warn: Fonts not found at {fonts_src}")
        print(f"  Download from: https://github.com/protomaps/basemaps-assets/releases")
        return

    fonts_dst = os.path.join(output_dir, "fonts")
    if os.path.exists(fonts_dst):
        shutil.rmtree(fonts_dst)

    # Collect all text from map data
    print("  Scanning map data for font trimming...")
    all_chars = set()

    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    basemap_chars = collect_text_from_pmtiles(basemap_path)
    if basemap_chars is None:
        # PMTiles libraries not available — fall back to full copy
        print("  warn: pmtiles/mapbox-vector-tile not installed — copying all fonts")
        shutil.copytree(fonts_src, fonts_dst)
        return
    all_chars.update(basemap_chars)

    for geojson_name in ["trails.geojson", "pois.geojson"]:
        all_chars.update(collect_text_from_geojson(
            os.path.join(output_dir, geojson_name)
        ))

    if not all_chars:
        # No text found (unlikely) — basemap was probably skipped
        if not os.path.exists(basemap_path):
            print("  warn: No basemap found — copying all fonts")
            shutil.copytree(fonts_src, fonts_dst)
            return

    # Compute needed ranges and faces
    needed_ranges = compute_needed_ranges(all_chars)

    available_faces = [
        d for d in os.listdir(fonts_src)
        if os.path.isdir(os.path.join(fonts_src, d))
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

        print(f"  Font: {face} — {copied}/{len(all_pbfs)} ranges")

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
                    total_original += os.path.getsize(
                        os.path.join(face_src, pbf)
                    )

    skipped_faces = set(available_faces) - needed_faces
    saved_mb = (total_original - total_copied) / (1024 * 1024)
    print(f"  Fonts: {total_copied / (1024*1024):.1f} MB "
          f"(trimmed {saved_mb:.1f} MB, "
          f"{len(needed_faces)}/{len(available_faces)} faces, "
          f"{len(needed_ranges)}/256 ranges)")
    if skipped_faces:
        print(f"  Skipped faces: {', '.join(sorted(skipped_faces))}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_dir> [fonts_src]")
        print(f"  Dry-run: shows which fonts/ranges would be kept")
        sys.exit(1)

    output_dir = sys.argv[1]
    fonts_src = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts"
    )

    print(f"Scanning: {output_dir}")
    print(f"Fonts source: {fonts_src}")
    print()

    # Collect text
    all_chars = set()
    basemap_path = os.path.join(output_dir, "basemap.pmtiles")
    if os.path.exists(basemap_path):
        basemap_chars = collect_text_from_pmtiles(basemap_path)
        if basemap_chars:
            all_chars.update(basemap_chars)
    for name in ["trails.geojson", "pois.geojson"]:
        all_chars.update(collect_text_from_geojson(os.path.join(output_dir, name)))

    print(f"\nUnique characters: {len(all_chars)}")

    needed_ranges = compute_needed_ranges(all_chars)
    print(f"PBF ranges needed: {len(needed_ranges)}/256")

    available_faces = [
        d for d in os.listdir(fonts_src)
        if os.path.isdir(os.path.join(fonts_src, d))
    ]
    needed_faces = determine_needed_faces(all_chars, available_faces)
    print(f"Font faces needed: {len(needed_faces)}/{len(available_faces)}")
    print(f"  Included: {', '.join(sorted(needed_faces))}")
    skipped = set(available_faces) - needed_faces
    if skipped:
        print(f"  Skipped: {', '.join(sorted(skipped))}")

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

    print(f"\nOriginal: {total_original / (1024*1024):.1f} MB")
    print(f"Trimmed:  {total_kept / (1024*1024):.1f} MB")
    print(f"Savings:  {(total_original - total_kept) / (1024*1024):.1f} MB "
          f"({100 * (1 - total_kept / total_original):.0f}%)")
