#!/usr/bin/env python3
"""Regenerate the .coverage.json sidecar for each webfont in assets/webfonts/.

The sidecar records which Unicode codepoints a bundled UI webfont actually
contains, so the build's chrome-font coverage check (font_trimmer.py,
check_webfont_coverage) can warn about characters that would silently fall
back to platform system fonts - WITHOUT the build needing fonttools+brotli
to crack open the WOFF2 itself. Run this whenever a .woff2 in
assets/webfonts/ is added or replaced; the sidecar is committed alongside
the font and never ships in build output.

Requires fonttools + brotli, which are deliberately NOT build dependencies
(requirements.txt stays lean). Install ad hoc:

    .venv/bin/pip install fonttools brotli
    python tools/update_webfont_coverage.py
"""

import json
import os
import sys

try:
    from fontTools.ttLib import TTFont
except ImportError:
    sys.exit("fonttools not installed. Run: .venv/bin/pip install fonttools brotli")

WEBFONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "webfonts"
)


def codepoint_ranges(font_path):
    """Return the font's cmap as a sorted list of inclusive [start, end] ranges."""
    cmap = TTFont(font_path).getBestCmap()
    ranges = []
    for cp in sorted(cmap):
        if ranges and cp == ranges[-1][1] + 1:
            ranges[-1][1] = cp
        else:
            ranges.append([cp, cp])
    return ranges


def main():
    woff2s = sorted(
        f for f in os.listdir(WEBFONTS_DIR) if f.endswith((".woff2", ".woff"))
    )
    if not woff2s:
        sys.exit(f"No webfonts found in {WEBFONTS_DIR}")
    for fname in woff2s:
        ranges = codepoint_ranges(os.path.join(WEBFONTS_DIR, fname))
        sidecar = os.path.splitext(fname)[0] + ".coverage.json"
        out_path = os.path.join(WEBFONTS_DIR, sidecar)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"font": fname, "codepoint_ranges": ranges}, f, indent=1)
            f.write("\n")
        total = sum(e - s + 1 for s, e in ranges)
        print(f"{sidecar}: {total} codepoints in {len(ranges)} ranges")


if __name__ == "__main__":
    main()
