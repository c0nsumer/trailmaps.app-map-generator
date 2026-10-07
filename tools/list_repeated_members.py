#!/usr/bin/env python3
"""List every relation that repeats a member way, across all audited maps.

Maintainer helper for fixing OSM by hand. Each build writes
``cache/osm_diff/<slug>/data-notes.md``; this reads the "Relations that list
a way more than once" section from every one and prints a worklist grouped
by map: the relation, its name, an iD edit link, a JOSM remote-control link,
and each repeated way with its multiplier.

The JOSM link needs JOSM running with remote control enabled (Preferences >
Remote Control). Opening it in a browser tells JOSM to download the relation
and its members.

The JSON sidecar only carries counts, so the markdown is the source of the
ids. Purely offline: it reads the cache and never builds or fetches.

Relations and ways with a negative id come from an unuploaded local OSM
file. They have no openstreetmap.org page, so they print as-is and are
marked ``[local]``.

Usage:
    python tools/list_repeated_members.py
    python tools/list_repeated_members.py bloomer drvg     # only these slugs
    python tools/list_repeated_members.py --json           # machine-readable
    python tools/list_repeated_members.py --cache-dir /path/to/cache
"""

import argparse
import glob
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HEADING = "## Relations that list a way more than once"
_WAY = r"(?:https://www\.openstreetmap\.org/way/\d+|way `-?\d+`)(?: \(\d+x\))?"
_ITEM = re.compile(
    r"^- (?P<rel>https://www\.openstreetmap\.org/relation/\d+|relation `-?\d+`) "
    r"(?P<name>.*): (?P<ways>" + _WAY + r"(?:, " + _WAY + r")*)$"
)
_WAY_PARTS = re.compile(r"(?:way/|way `)(-?\d+)`?(?: \((\d+)x\))?")
_ID = re.compile(r"(-?\d+)`?$")


def parse_report(text):
    """Return [{relation, name, ways: [{id, times}]}] from one data-notes.md."""
    found = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(_HEADING)
            continue
        if not in_section or not line.startswith("- "):
            continue
        m = _ITEM.match(line)
        if not m:
            print(f"warning: unparsed line: {line}", file=sys.stderr)
            continue
        rid = _ID.search(m["rel"]).group(1)
        ways = [{"id": w, "times": int(n) if n else 2}
                for w, n in _WAY_PARTS.findall(m["ways"])]
        found.append({"relation": rid, "name": m["name"], "ways": ways})
    return found


def collect(cache_dir, slugs):
    pattern = os.path.join(cache_dir, "osm_diff", "*", "data-notes.md")
    result = {}
    for path in sorted(glob.glob(pattern)):
        slug = os.path.basename(os.path.dirname(path))
        if slugs and slug not in slugs:
            continue
        with open(path, encoding="utf-8") as f:
            items = parse_report(f.read())
        if items:
            result[slug] = items
    return result


def _rel_url(rid):
    return f"https://www.openstreetmap.org/relation/{rid}"


def _way_url(wid):
    return f"https://www.openstreetmap.org/way/{wid}"


def _josm_url(rid):
    # JOSM's remote control listens on localhost:8111. relation_members=true
    # downloads the member ways too, so the repeats are visible on load.
    return f"http://127.0.0.1:8111/load_object?objects=r{rid}&relation_members=true"


def render(result):
    out = []
    for slug, items in result.items():
        out.append(f"{slug} ({len(items)})")
        for it in items:
            rid = it["relation"]
            local = rid.startswith("-")
            name = it["name"] or "(unnamed)"
            out.append(f"  {name}" + ("  [local]" if local else ""))
            out.append(f"    relation {rid}" if local else f"    {_rel_url(rid)}")
            if not local:
                out.append(f"    edit: https://www.openstreetmap.org/edit?relation={rid}")
                out.append(f"    josm: {_josm_url(rid)}")
            for w in it["ways"]:
                tag = f"  ({w['times']}x)" if w["times"] > 2 else ""
                wid = w["id"]
                out.append(f"      {'way ' + wid if wid.startswith('-') else _way_url(wid)}{tag}")
        out.append("")
    total = sum(len(v) for v in result.values())
    out.append(f"{total} relation(s) with repeated members across {len(result)} map(s)")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slugs", nargs="*", help="limit to these slugs (default: all)")
    ap.add_argument("--cache-dir", default=os.path.join(_PROJECT_ROOT, "cache"))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    result = collect(args.cache_dir, set(args.slugs))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
