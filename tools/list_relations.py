#!/usr/bin/env python3
"""List the OSM relations (routes) a map is built from.

Diagnostic: given a slug (or a config path), print every OpenStreetMap
relation that generates the map, one per line as ``<id>\\t<name>``.
Super-relations are expanded to their child routes -- the leaves that
actually produce geometry -- exactly as the build does, and clipped
relations are marked ``[clipped]``.

Operates purely from the local cache (``<repo>/cache``) by default: it
reconstructs the same Overpass query ``fetch_trails`` runs, hashes it the
same way (``md5(query)[:12]``), and reads ``cache/overpass_<hash>.json``.
It never touches the network unless ``--fetch`` is given. Maps that read a
local ``osm_file:`` are handled by parsing that file, no cache needed.

Usage:
    python tools/list_relations.py poto
    python tools/list_relations.py configs/poto/poto.yaml
    python tools/list_relations.py poto --ways      # member ways instead
    python tools/list_relations.py poto --fetch      # allow live Overpass on miss
"""

import argparse
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import config_io  # noqa: E402
import fetch_trails  # noqa: E402
import osm_parser  # noqa: E402


class CacheMiss(Exception):
    """A required Overpass response is not in the local cache."""

    def __init__(self, cache_path, label):
        self.cache_path = cache_path
        self.label = label
        super().__init__(cache_path)


def _cache_only_query(query_str, cache_dir=None, label="", require_elements=False, refresh=False):
    """Drop-in for overpass.query that reads the cache and never fetches.

    Reproduces overpass.query's key scheme (md5 of the exact query string,
    first 12 hex chars) so it resolves the same file the build wrote.
    Raises CacheMiss when the entry is absent instead of hitting the API.
    """
    if not cache_dir:
        raise CacheMiss(None, label)
    h = hashlib.md5(query_str.encode()).hexdigest()[:12]
    cache_path = os.path.join(cache_dir, f"overpass_{h}.json")
    if not os.path.exists(cache_path):
        raise CacheMiss(cache_path, label)
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_config_path(slug_or_path):
    """Accept either a config YAML path or a bare slug."""
    if slug_or_path.endswith((".yaml", ".yml")) or os.path.sep in slug_or_path:
        if not os.path.exists(slug_or_path):
            sys.exit(f"ERROR: config not found: {slug_or_path}")
        return slug_or_path
    candidate = os.path.join(_PROJECT_ROOT, "configs", slug_or_path, f"{slug_or_path}.yaml")
    if not os.path.exists(candidate):
        sys.exit(
            f"ERROR: no config for slug '{slug_or_path}' "
            f"(looked for {os.path.relpath(candidate, _PROJECT_ROOT)})"
        )
    return candidate


def _gather_relation_ids(config):
    """Union the config's route-relation lists exactly as fetch_trails does.

    Returns (relation_ids, clipped_ids). The winter/summer/emergency lists
    all mean 'pull this relation' as far as fetching goes, so they fold into
    the main set; clipped relations stay separate so we can mark them.
    """
    source_ids = list(config.get("relations") or [])
    winter = set(config.get("winter_relations") or [])
    summer = set(config.get("summer_relations") or [])
    emergency = set(config.get("emergency_access_relations") or [])
    relation_ids = list({*source_ids, *winter, *summer, *emergency})
    clipped_ids = list(config.get("clipped_relations") or [])
    return relation_ids, clipped_ids


def _load_from_osm_file(config, relation_ids, clipped_ids):
    """Local-.osm path: parse the file and extract relations, mirroring
    fetch_trails' local branch. Returns (members, clipped)."""
    parsed = osm_parser.parse_osm_file(config["osm_file"])
    members, _ = osm_parser.extract_source_relations(parsed, relation_ids)
    clipped = {}
    if clipped_ids:
        clipped, _ = osm_parser.extract_source_relations(parsed, clipped_ids)
        # A relation in both lists is a source relation, not clipped.
        clipped = {rid: info for rid, info in clipped.items() if rid not in members}
    return members, clipped, parsed


def _member_ways(config, relations, cache_dir, parsed=None):
    """Return {way_id: name} for every member way of the given relations.

    Deduped across relations. Uses the local parse when available (osm_file
    maps), else the cached Stage-B bulk-ways response.
    """
    rel_ids = list(relations.keys())
    if parsed is not None:
        all_ways = osm_parser.extract_ways(parsed, rel_ids)
    else:
        all_ways = fetch_trails.fetch_all_ways_bulk(rel_ids, cache_dir)
    ways = {}
    for per_rel in all_ways.values():
        for way_id, way in per_rel.items():
            ways[way_id] = way.get("tags", {}).get("name", "")
    return ways


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="List the OSM relations (or member ways) a map is built from, from cache."
    )
    parser.add_argument("slug", help="Map slug (e.g. poto) or a config YAML path")
    parser.add_argument(
        "--ways",
        action="store_true",
        help="List member ways (way ID + name) instead of relations",
    )
    parser.add_argument(
        "--override-template",
        action="store_true",
        help='Format each line as `<id>: "XX" # <name>` (a config override block '
        "with placeholder values to fill in)",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.path.join(_PROJECT_ROOT, "cache"),
        help="Overpass cache directory (default: <repo>/cache)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Allow a live Overpass query on a cache miss (default: cache-only)",
    )
    args = parser.parse_args(argv)

    config_path = _resolve_config_path(args.slug)
    config = config_io.load_config_for_fetch(config_path)
    slug = config.get("slug", args.slug)
    relation_ids, clipped_ids = _gather_relation_ids(config)

    if not relation_ids and not clipped_ids:
        print(
            f"{slug}: no OSM relations in config "
            f"(route-only map via custom_routes / event_mode.routes).",
            file=sys.stderr,
        )
        return 0

    # Force strictly-offline behavior unless --fetch was passed. Patch the
    # name fetch_trails bound at import (`from overpass import query as
    # overpass_query`) so both the relations and ways queries route through
    # the cache-only reader.
    parsed = None
    if not args.fetch:
        fetch_trails.overpass_query = _cache_only_query

    try:
        osm_file = config.get("osm_file")
        if osm_file:
            members, clipped, parsed = _load_from_osm_file(config, relation_ids, clipped_ids)
        else:
            members, clipped, _ = fetch_trails.fetch_all_relations(
                relation_ids, clipped_ids, args.cache_dir
            )
    except CacheMiss as miss:
        where = miss.cache_path or "(no cache dir)"
        print(
            f"ERROR: no cached Overpass response for {slug}'s {miss.label} query.\n"
            f"       Expected: {where}\n"
            f"       Build the map first, or re-run with --fetch to query Overpass.",
            file=sys.stderr,
        )
        return 1

    relations = {**members, **clipped}
    if not relations:
        print(f"{slug}: relations resolved to no usable data.", file=sys.stderr)
        return 1

    if args.ways:
        try:
            ways = _member_ways(config, relations, args.cache_dir, parsed)
        except CacheMiss as miss:
            where = miss.cache_path or "(no cache dir)"
            print(
                f"ERROR: no cached Overpass response for {slug}'s {miss.label} query.\n"
                f"       Expected: {where}\n"
                f"       Build the map first, or re-run with --fetch to query Overpass.",
                file=sys.stderr,
            )
            return 1
        for way_id, name in sorted(ways.items(), key=lambda kv: (kv[1].lower(), kv[0])):
            if args.override_template:
                print(f'{way_id}: "XX" # {name}')
            else:
                print(f"{way_id}\t{name}")
        print(f"# {len(ways)} member ways across {len(relations)} relations", file=sys.stderr)
        return 0

    clipped_ids_set = set(clipped.keys())
    for rel_id, info in sorted(relations.items(), key=lambda kv: kv[1]["name"].lower()):
        if args.override_template:
            print(f'{rel_id}: "XX" # {info["name"]}')
            continue
        suffix = "\t[clipped]" if rel_id in clipped_ids_set else ""
        print(f"{rel_id}\t{info['name']}{suffix}")
    print(
        f"# {len(members)} relation(s)"
        + (f" + {len(clipped)} clipped" if clipped else "")
        + f" for {slug}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
