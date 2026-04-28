#!/usr/bin/env python3
"""Fetch trail data from OpenStreetMap via Overpass API.

Queries the Overpass API for a super-relation and its member relations,
fetches all member ways with geometry, merges consecutive ways that share
the same set of relations, and outputs a GeoJSON file.
"""

import json
import os
import sys
from collections import defaultdict

import yaml

from overpass import query as overpass_query


def load_config(config_path):
    """Load YAML config, resolving any ``osm_file`` path relative to the
    config file's directory (so ``osm_file: osm.osm`` picks up the file
    sitting next to the YAML). Kept narrower than build.py's load_config
    because fetch_trails only touches ``osm_file``; full path resolution
    happens once in build.py for the full pipeline."""
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    osm_file = config.get("osm_file")
    if osm_file and isinstance(osm_file, str) and not os.path.isabs(osm_file):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        config["osm_file"] = os.path.join(config_dir, osm_file)
    return config


def _parse_relations(data):
    """Parse relation elements from an Overpass response into a dict."""
    relations = {}
    for element in data.get("elements", []):
        if element["type"] == "relation":
            tags = element.get("tags", {})
            relations[element["id"]] = {
                "id": element["id"],
                "name": tags.get("name", f"Route {element['id']}"),
                "colour": tags.get("colour", "#808080"),
                "ref": tags.get("ref", ""),
                "route": tags.get("route", ""),
                "seasonal": tags.get("seasonal", ""),
            }
    return relations


def fetch_all_relations(root_relation_id, extra_ids=None, clipped_ids=None, cache_dir=None):
    """Fetch relation metadata for the root relation + extras in a single query.

    The root relation may be either a super-relation (with child route
    relations as members) or a single route relation. In the super-relation
    case, the root itself is dropped from the results (it has no ways);
    in the single-relation case, the root is returned as the sole member.

    Returns (members, extras, clipped) — three dicts of relation info.
    """
    extra_ids = extra_ids or []
    clipped_ids = clipped_ids or []
    additional_ids = list(extra_ids) + list(clipped_ids)

    # Build a single query that fetches the root + any child relations + extras
    parts = [
        f"relation({root_relation_id});rel(r);",
    ]
    if additional_ids:
        parts.append(
            ";".join(f"relation({rid})" for rid in additional_ids) + ";"
        )

    query = f"""
[out:json][timeout:120];
({" ".join(parts)});
out tags;
"""
    data = overpass_query(query, cache_dir, label="relations", require_elements=True)
    all_rels = _parse_relations(data)

    # Split results into the three groups
    extra_set = set(extra_ids)
    clipped_set = set(clipped_ids)

    members = {}
    extras = {}
    clipped = {}
    root_info = None
    for rel_id, info in all_rels.items():
        if rel_id == root_relation_id:
            root_info = info  # remember it; may use as sole member below
            continue
        elif rel_id in clipped_set:
            clipped[rel_id] = info
        elif rel_id in extra_set:
            extras[rel_id] = info
        else:
            members[rel_id] = info

    # Single-relation mode: no children found, so the root IS the route.
    if not members and root_info is not None:
        members[root_relation_id] = root_info

    return members, extras, clipped


def fetch_all_ways_bulk(relation_ids, cache_dir=None):
    """Fetch ways for all relations in a single Overpass query using foreach.

    Uses Overpass's foreach to iterate over relations server-side, emitting
    a sentinel relation element before each group of ways so we can split
    the flat response back into per-relation buckets.

    Returns dict of {relation_id: {way_id: way_dict, ...}, ...}
    """
    if not relation_ids:
        return {}

    id_union = ";".join(f"relation({rid})" for rid in relation_ids)
    query = f"""
[out:json][timeout:300];
({id_union};);
foreach -> .rel(
  .rel out ids;
  way(r.rel);
  out geom;
);
"""
    data = overpass_query(query, cache_dir, label="ways", require_elements=True)

    # Parse the flat element list.  The foreach emits a relation element
    # (with just an id) followed by its member ways, then the next relation, etc.
    all_ways = {rid: {} for rid in relation_ids}
    current_rel_id = None

    for element in data.get("elements", []):
        if element["type"] == "relation":
            current_rel_id = element["id"]
            continue

        if element["type"] == "way" and "geometry" in element and current_rel_id is not None:
            coords = [
                [node["lon"], node["lat"]]
                for node in element["geometry"]
            ]
            if len(coords) >= 2:
                way = {
                    "id": element["id"],
                    "coords": coords,
                    "tags": element.get("tags", {}),
                }
                if current_rel_id in all_ways:
                    all_ways[current_rel_id][element["id"]] = way

    return all_ways


def build_way_to_relations_map(relations, all_ways):
    """Build a mapping of way_id -> set of relation_ids that use it."""
    way_relations = defaultdict(set)
    for rel_id, ways in all_ways.items():
        for way_id in ways:
            way_relations[way_id].add(rel_id)
    return way_relations


def _resolve_oneway(tags):
    """Return the effective oneway value for direction-arrow purposes.

    OSM uses two relevant tags:
      * ``oneway``         — generic restriction, traditionally for vehicles
      * ``oneway:bicycle`` — bicycle-specific override

    For an MTB map, ``oneway:bicycle`` is the more authoritative signal:
    it lets curators mark MTB-only direction without affecting hikers
    (e.g., a flow trail that's hiked uphill but ridden downhill), or
    suppress a generic ``oneway`` that doesn't apply to bikes.

    Resolution: ``oneway:bicycle`` wins when set (any non-empty value,
    including ``"no"``); otherwise fall back to ``oneway``. Both tags
    share the same value vocabulary: ``""``, ``"yes"``, ``"no"``,
    ``"-1"``, ``"reversible"``. Returns ``""`` when neither is set.
    """
    bike = tags.get("oneway:bicycle", "")
    return bike if bike else tags.get("oneway", "")


def merge_consecutive_ways(ways_dict, relation_ids_set):
    """Merge consecutive ways that share the same set of relations, name, and difficulty.

    Given a dict of ways belonging to one relation and a function to look up
    which relations each way belongs to, merge consecutive ways where the
    relation membership, way name, AND IMBA difficulty are identical.

    Returns a list of merged segments, each being a dict with:
    - coords: merged coordinate list
    - shared_routes: sorted list of route (relation) IDs sharing this segment
    - trail_name: name of the trail segment (from the way's name tag)
    - imba_difficulty: IMBA difficulty rating (0-4) or empty string
    """
    if not ways_dict:
        return []

    # Build adjacency: node -> list of (way_id, is_start)
    # A way's first node is its "start", last node is its "end"
    node_to_ways = defaultdict(list)
    way_endpoints = {}

    for way_id, way in ways_dict.items():
        coords = way["coords"]
        start_node = tuple(coords[0])
        end_node = tuple(coords[-1])
        way_endpoints[way_id] = (start_node, end_node)
        node_to_ways[start_node].append(way_id)
        node_to_ways[end_node].append(way_id)

    # Group ways by their relation-membership signature + way name + IMBA
    # difficulty + oneway tag. Including `oneway` in the signature ensures
    # one-way and two-way ways never merge together, and that ways with
    # opposing oneway values (yes / -1 / reversible / no) stay separate
    # features so each retains its own digitisation order.
    way_signatures = {}
    way_names = {}
    for way_id, way in ways_dict.items():
        sig = tuple(sorted(relation_ids_set.get(way_id, set())))
        name = way.get("tags", {}).get("name", "")
        imba = way.get("tags", {}).get("mtb:scale:imba", "")
        # Resolve effective oneway state. Bicycle-specific tag takes
        # precedence so curators can mark MTB-only direction without
        # affecting hikers (or restrict bikes from a way that's
        # generically two-way). Both tags follow the same value
        # vocabulary: "" | "yes" | "no" | "-1" | "reversible".
        oneway = _resolve_oneway(way.get("tags", {}))
        way_signatures[way_id] = (sig, name, imba, oneway)
        way_names[way_id] = name

    # Merge consecutive ways with the same signature (relations + name + difficulty + oneway)
    visited = set()
    merged_segments = []

    for start_way_id in ways_dict:
        if start_way_id in visited:
            continue
        visited.add(start_way_id)

        full_sig = way_signatures[start_way_id]
        sig, name, imba, oneway = full_sig
        # `reversible` ways have a direction-of-travel (just one that flips by
        # schedule), so they must not be glued to a neighbour in reversed
        # orientation any more than `yes`/`-1` ways can.
        is_oneway = oneway in ("yes", "-1", "reversible")
        coords = list(ways_dict[start_way_id]["coords"])
        # Track every source way fused into this segment so downstream
        # validation (e.g. unscheduled oneway=reversible detection in
        # build.py) can point users at specific OSM ways even when working
        # from cached trails.geojson.
        member_way_ids = [start_way_id]

        # Extend forward from the end of the current chain
        while True:
            end_node = tuple(coords[-1])
            candidates = [
                wid for wid in node_to_ways[end_node]
                if wid not in visited and way_signatures.get(wid) == full_sig
            ]
            if not candidates:
                break
            next_way_id = candidates[0]
            next_coords = ways_dict[next_way_id]["coords"]
            # Check orientation: if the next way starts at our end, append as-is
            if tuple(next_coords[0]) == end_node:
                visited.add(next_way_id)
                coords.extend(next_coords[1:])  # skip duplicate junction node
                member_way_ids.append(next_way_id)
            elif tuple(next_coords[-1]) == end_node:
                # Need to reverse the next way to glue on; for one-way ways
                # this would invert their tagged direction, so leave them
                # as a separate feature instead.
                if is_oneway:
                    break
                visited.add(next_way_id)
                coords.extend(list(reversed(next_coords))[1:])
                member_way_ids.append(next_way_id)
            else:
                break

        # Extend backward from the start of the current chain
        while True:
            start_node = tuple(coords[0])
            candidates = [
                wid for wid in node_to_ways[start_node]
                if wid not in visited and way_signatures.get(wid) == full_sig
            ]
            if not candidates:
                break
            prev_way_id = candidates[0]
            prev_coords = ways_dict[prev_way_id]["coords"]
            # Check orientation: if the previous way ends at our start, prepend
            if tuple(prev_coords[-1]) == start_node:
                visited.add(prev_way_id)
                coords = prev_coords[:-1] + coords
                member_way_ids.insert(0, prev_way_id)
            elif tuple(prev_coords[0]) == start_node:
                # Reversal would invert direction — keep one-way ways separate.
                if is_oneway:
                    break
                visited.add(prev_way_id)
                coords = list(reversed(prev_coords))[:-1] + coords
                member_way_ids.insert(0, prev_way_id)
            else:
                break

        merged_segments.append({
            "coords": coords,
            "shared_routes": sorted(sig),
            "trail_name": name,
            "imba_difficulty": imba,
            "oneway": oneway,
            "way_ids": member_way_ids,
        })

    return merged_segments


def clip_line_to_bbox(coords, bbox):
    """Clip a LineString's coordinates to a bounding box.

    Uses Liang-Barsky line-segment clipping. Returns a list of
    (coords, start_clipped, end_clipped) triples — one LineString may produce
    multiple segments when it exits and re-enters the bbox. The boolean
    flags indicate whether the start/end of each output segment was created
    by the clip (i.e. that endpoint coincides with the bbox boundary because
    the original line continued past it). Used downstream to render
    "continues off-map" arrowheads only at clip-created endpoints.

    bbox is [west, south, east, north].
    """
    west, south, east, north = bbox

    def intersect_segment(x1, y1, x2, y2):
        """Clip one segment to the bbox.

        Returns (cx1, cy1, cx2, cy2, start_clipped, end_clipped) on success,
        or None when the segment lies entirely outside. The flags are True
        when the corresponding endpoint was moved by the clip (i.e. the
        original endpoint sat outside the bbox).
        """
        # Liang-Barsky algorithm
        dx = x2 - x1
        dy = y2 - y1
        t0, t1 = 0.0, 1.0

        for p, q in [(-dx, x1 - west), (dx, east - x1),
                      (-dy, y1 - south), (dy, north - y1)]:
            if p == 0:
                if q < 0:
                    return None  # parallel and outside
            else:
                t = q / p
                if p < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 > t1:
                    return None

        cx1 = x1 + t0 * dx
        cy1 = y1 + t0 * dy
        cx2 = x1 + t1 * dx
        cy2 = y1 + t1 * dy
        return (cx1, cy1, cx2, cy2, t0 > 0.0, t1 < 1.0)

    # Walk through the coordinate pairs and build clipped segments. Each
    # entry in `segments` is (coords, start_clipped, end_clipped).
    segments = []
    current = []                   # accumulator coords for in-progress segment
    current_start_clipped = False  # was current[0] clip-created?
    last_end_clipped = False       # was current[-1] clip-created? (tracked
                                   # so the loop-exit flush knows what to do)

    def flush(end_clipped):
        if len(current) >= 2:
            segments.append((list(current), current_start_clipped, end_clipped))

    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]

        result = intersect_segment(x1, y1, x2, y2)
        if result is None:
            # Segment entirely outside — flush current. The accumulator's
            # last coord must be on the bbox (since the next original vertex
            # was outside), so its end is clip-created.
            flush(end_clipped=True)
            current = []
            current_start_clipped = False
            last_end_clipped = False
            continue

        cx1, cy1, cx2, cy2, this_start_clipped, this_end_clipped = result
        if not current:
            current.append([cx1, cy1])
            current_start_clipped = this_start_clipped
        elif abs(current[-1][0] - cx1) > 1e-9 or abs(current[-1][1] - cy1) > 1e-9:
            # Discontinuity — flush and start a new segment. The flushed
            # tail's end and the new segment's start are both clip-created
            # (the gap traversed the bbox boundary).
            flush(end_clipped=True)
            current = [[cx1, cy1]]
            current_start_clipped = this_start_clipped

        current.append([cx2, cy2])
        last_end_clipped = this_end_clipped

    flush(end_clipped=last_end_clipped)
    return segments


def _compass_bearing(p1, p2):
    """Compass bearing (degrees clockwise from north) FROM p1 TO p2.

    Inputs are [lon, lat] pairs. Uses a simple equirectangular approximation
    (Δlon · cos(lat)) — at trail scale this is within fractions of a degree
    of the true great-circle bearing, plenty for orienting an arrowhead.
    Returns a value in [0, 360).
    """
    import math
    lon1, lat1 = p1
    lon2, lat2 = p2
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = (lon2 - lon1) * math.cos(mean_lat)
    dy = lat2 - lat1
    # atan2(east, north) → 0 = north, 90 = east, …
    bearing = math.degrees(math.atan2(dx, dy))
    if bearing < 0:
        bearing += 360.0
    return bearing


def build_geojson(relations, all_ways, way_relations):
    """Build GeoJSON FeatureCollection from merged trail segments."""
    features = []

    for rel_id, rel_info in sorted(relations.items(), key=lambda x: x[1]["name"]):
        ways = all_ways.get(rel_id, {})
        if not ways:
            print(f"  Warning: No ways found for relation {rel_id} ({rel_info['name']})")
            continue

        # Build per-way relation membership lookup for this relation's ways
        way_rel_lookup = {
            way_id: way_relations.get(way_id, {rel_id})
            for way_id in ways
        }

        merged = merge_consecutive_ways(ways, way_rel_lookup)

        for i, segment in enumerate(merged):
            # Normalise oneway=-1 to oneway=yes with reversed coordinates so
            # the runtime only ever has to handle a single canonical case for
            # static one-ways. `reversible` is passed through unchanged: its
            # default arrow direction is the OSM digitisation order, and the
            # day-of-week schedule (direction_schedules) flips it.
            oneway = segment.get("oneway", "")
            coords = segment["coords"]
            if oneway == "-1":
                coords = list(reversed(coords))
                oneway = "yes"

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords,
                },
                "properties": {
                    "route_id": rel_id,
                    "route_name": rel_info["name"],
                    "route_colour": rel_info["colour"],
                    "route_ref": rel_info["ref"],
                    "trail_name": segment.get("trail_name", ""),
                    "shared_routes": segment["shared_routes"],
                    "imba_difficulty": segment.get("imba_difficulty", ""),
                    "oneway": oneway,
                    "segment_index": i,
                    # Source OSM way IDs fused into this segment. Used by
                    # build.py's reversible-without-schedule validator so
                    # cached-GeoJSON rebuilds still produce actionable errors
                    # pointing at specific OSM ways.
                    "way_ids": segment.get("way_ids", []),
                },
            }
            features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def compute_bbox_from_features(features, buffer=0.005):
    """Compute [west, south, east, north] bbox from GeoJSON features."""
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")
    for f in features:
        for lon, lat in f.get("geometry", {}).get("coordinates", []):
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)
    return [min_lon - buffer, min_lat - buffer, max_lon + buffer, max_lat + buffer]


def fetch_trails(config_or_path, output_path, cache_dir="cache"):
    """Main entry point: fetch trails and write GeoJSON."""
    config = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
    root_relation_id = config.get("root_relation_id")
    if root_relation_id is None:
        sys.exit(
            "ERROR: config must specify `root_relation_id` "
            "(the OSM relation that anchors this map)."
        )
    # Every config list that declares an OSM relation belongs to this map
    # (beyond whatever the root relation's hierarchy already contains)
    # contributes to the fetch set. winter_relations / summer_relations /
    # emergency_access_relations each carry bucket semantics on top, but
    # from fetch_trails.py's perspective they all mean "pull this relation."
    # Any overlap with the root hierarchy is harmless — the downstream
    # dict update() calls dedupe by id.
    winter_relation_ids = set(config.get("winter_relations") or [])
    summer_relation_ids = set(config.get("summer_relations") or [])
    emergency_relation_ids = set(
        config.get("emergency_access_relations") or []
    )
    extra_relation_ids = list({
        *(config.get("extra_relations") or []),
        *winter_relation_ids,
        *summer_relation_ids,
        *emergency_relation_ids,
    })
    clipped_relation_ids = config.get("clipped_relations") or []

    osm_file = config.get("osm_file")
    if osm_file:
        print(f"Loading trails for {config['name']} from {osm_file}...")
    else:
        print(f"Fetching trails for {config['name']} (relation {root_relation_id})...")

    if osm_file:
        from osm_parser import parse_osm_file, extract_relations, extract_extra_relations, extract_ways
        if not os.path.isabs(osm_file):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            osm_file = os.path.join(project_root, osm_file)

        print("Stage A: Parsing .osm file...")
        parsed = parse_osm_file(osm_file)
        print(f"  Parsed {len(parsed[0])} nodes, {len(parsed[1])} ways, {len(parsed[2])} relations")

        relations = extract_relations(parsed, root_relation_id)
        print(f"  Found {len(relations)} relations from root:")
        for rel_id, info in sorted(relations.items(), key=lambda x: x[1]["name"]):
            print(f"    {info['name']} ({rel_id}) colour={info['colour']}")

        if extra_relation_ids:
            print(f"  Loading {len(extra_relation_ids)} extra relations...")
            extra = extract_extra_relations(parsed, extra_relation_ids)
            for rel_id, info in sorted(extra.items(), key=lambda x: x[1]["name"]):
                print(f"    {info['name']} ({rel_id}) colour={info['colour']}")
            relations.update(extra)

        clipped_relations = {}
        if clipped_relation_ids:
            print(f"  Loading {len(clipped_relation_ids)} clipped relations...")
            clipped_relations = extract_extra_relations(parsed, clipped_relation_ids)
            for rel_id, info in sorted(clipped_relations.items(), key=lambda x: x[1]["name"]):
                print(f"    {info['name']} ({rel_id}) colour={info['colour']} [clipped]")
            relations.update(clipped_relations)

        for rel_id in winter_relation_ids:
            if rel_id in relations:
                relations[rel_id]["seasonal"] = "winter"

        print(f"Stage B: Extracting ways for {len(relations)} relations...")
        all_ways = extract_ways(parsed, list(relations.keys()))
        for rel_id, info in sorted(relations.items(), key=lambda x: x[1]["name"]):
            way_count = len(all_ways.get(rel_id, {}))
            print(f"  {info['name']}: {way_count} ways")
            if way_count == 0:
                print(f"    Warning: No ways found for {info['name']} ({rel_id})")
    else:
        # Stage A: Fetch all relation metadata in a single query
        print("Stage A: Fetching relation metadata...")
        members, extra, clipped_relations = fetch_all_relations(
            root_relation_id, extra_relation_ids, clipped_relation_ids, cache_dir
        )

        print(f"  Found {len(members)} relations from root:")
        for rel_id, info in sorted(members.items(), key=lambda x: x[1]["name"]):
            print(f"    {info['name']} ({rel_id}) colour={info['colour']}")

        if not members and not extra and not clipped_relations:
            print(f"\n  ERROR: Relation {root_relation_id} returned no usable data.")
            print(f"  Check that the relation ID is correct and exists:")
            print(f"    https://www.openstreetmap.org/relation/{root_relation_id}\n")
            sys.exit(1)

        relations = dict(members)

        if extra:
            print(f"  Found {len(extra)} extra relations:")
            for rel_id, info in sorted(extra.items(), key=lambda x: x[1]["name"]):
                print(f"    {info['name']} ({rel_id}) colour={info['colour']}")
            relations.update(extra)

        if clipped_relations:
            print(f"  Found {len(clipped_relations)} clipped relations:")
            for rel_id, info in sorted(clipped_relations.items(), key=lambda x: x[1]["name"]):
                print(f"    {info['name']} ({rel_id}) colour={info['colour']} [clipped]")
            relations.update(clipped_relations)

        # Apply winter_relations config override (marks relations as winter
        # even if they don't have seasonal=winter in OSM)
        for rel_id in winter_relation_ids:
            if rel_id in relations:
                relations[rel_id]["seasonal"] = "winter"

        # Stage B: Fetch ways for all relations in a single bulk query
        print(f"Stage B: Fetching ways for {len(relations)} relations (bulk query)...")
        all_ways = fetch_all_ways_bulk(list(relations.keys()), cache_dir)
        for rel_id, info in sorted(relations.items(), key=lambda x: x[1]["name"]):
            way_count = len(all_ways.get(rel_id, {}))
            print(f"  {info['name']}: {way_count} ways")
            if way_count == 0:
                print(f"    Warning: No ways found for {info['name']} ({rel_id})")

    # Build way-to-relations mapping
    way_relations = build_way_to_relations_map(relations, all_ways)
    shared_count = sum(1 for wids in way_relations.values() if len(wids) > 1)
    print(f"  {shared_count} ways are shared by multiple relations")

    # Validate oneway=reversible ways. These are trails that change direction
    # by schedule (the canonical OSM tag for that case) and have no inherent
    # forward direction — they are only meaningful with a direction schedule
    # on one of their parent relations (or a system-wide default schedule).
    # Fail the build with a precise list of offending ways otherwise; the
    # framework would otherwise silently render them as static one-ways in
    # OSM-digitisation order, which is wrong half the time.
    #
    # Resolution mirrors build.py:
    #   - default_direction_schedule (with non-empty reverse_days) covers
    #     every relation by default.
    #   - direction_schedules[<rel>] is a per-relation override. An entry with
    #     non-empty reverse_days schedules that relation; an explicit empty
    #     reverse_days opts that relation out of the default.
    sched_raw = config.get("direction_schedules") or {}
    def_sched = config.get("default_direction_schedule") or {}
    default_active = bool((def_sched or {}).get("reverse_days"))

    overrides_active = set()      # relations explicitly scheduled
    overrides_optout = set()      # relations explicitly opted out (empty list)
    for k, v in sched_raw.items():
        days = (v or {}).get("reverse_days") or []
        (overrides_active if days else overrides_optout).add(int(k))

    def _relation_is_scheduled(rid):
        if rid in overrides_active:
            return True
        if rid in overrides_optout:
            return False
        return default_active

    unscheduled_reversible = []
    seen_way_ids = set()
    for rel_id, ways in all_ways.items():
        for way_id, way in ways.items():
            if way_id in seen_way_ids:
                continue
            seen_way_ids.add(way_id)
            # Use the same resolver as the merge step: oneway:bicycle
            # takes precedence so a bike-specific reversible declaration
            # is caught by this validation too.
            if _resolve_oneway(way.get("tags", {})) != "reversible":
                continue
            parent_rels = way_relations.get(way_id, set())
            if not any(_relation_is_scheduled(r) for r in parent_rels):
                unscheduled_reversible.append((way_id, sorted(parent_rels)))
    if unscheduled_reversible:
        lines = [
            "ERROR: Found oneway=reversible way(s) without a direction",
            "       schedule covering them. Reversible trails change direction",
            "       by schedule and cannot render correctly without one.",
            "",
            "       Either set a system-wide default that covers every route:",
            "",
            "         default_direction_schedule:",
            "           reverse_days: [tuesday, thursday, saturday]",
            "",
            "       …or schedule the specific parent relation:",
            "",
            "         direction_schedules:",
            "           <relation_id>:",
            "             reverse_days: [tuesday, thursday, saturday]",
            "",
            "       Offending ways (way_id → parent relation IDs):",
        ]
        for way_id, parents in unscheduled_reversible[:20]:
            lines.append(f"         https://www.openstreetmap.org/way/{way_id}"
                         f"  →  {parents}")
        if len(unscheduled_reversible) > 20:
            lines.append(f"         ... and {len(unscheduled_reversible) - 20} more")
        sys.exit("\n".join(lines))

    # Stage C: Merge ways and build GeoJSON
    print("Stage C: Merging ways and building GeoJSON...")
    geojson = build_geojson(relations, all_ways, way_relations)

    # Stage D: Clip features for clipped_relations to the core trail bbox.
    # Always initialise the endpoints accumulator so the writer below has a
    # well-defined value when the map has no clipped relations.
    clip_endpoints = []
    if clipped_relations:
        clipped_ids = set(clipped_relations.keys())
        core_features = [f for f in geojson["features"]
                         if f["properties"]["route_id"] not in clipped_ids]
        clip_features = [f for f in geojson["features"]
                         if f["properties"]["route_id"] in clipped_ids]

        bbox = compute_bbox_from_features(core_features)
        print(f"  Clipping {len(clip_features)} features to bbox {[round(v, 4) for v in bbox]}...")

        clipped_features = []
        for feature in clip_features:
            coords = feature["geometry"]["coordinates"]
            route_id = feature["properties"]["route_id"]
            segments = clip_line_to_bbox(coords, bbox)
            for j, (seg_coords, start_clipped, end_clipped) in enumerate(segments):
                clipped_feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": seg_coords},
                    "properties": dict(feature["properties"]),
                }
                clipped_feature["properties"]["segment_index"] = (
                    feature["properties"]["segment_index"] * 100 + j
                )
                clipped_features.append(clipped_feature)

                # Record continuation arrowhead points for any clip-created
                # endpoints. Bearing points OUTWARD (the direction the
                # trail is heading as it leaves the map).
                if start_clipped and len(seg_coords) >= 2:
                    clip_endpoints.append({
                        "type": "Feature",
                        "geometry": {"type": "Point",
                                     "coordinates": list(seg_coords[0])},
                        "properties": {
                            "route_id": route_id,
                            "bearing": _compass_bearing(seg_coords[1],
                                                       seg_coords[0]),
                        },
                    })
                if end_clipped and len(seg_coords) >= 2:
                    clip_endpoints.append({
                        "type": "Feature",
                        "geometry": {"type": "Point",
                                     "coordinates": list(seg_coords[-1])},
                        "properties": {
                            "route_id": route_id,
                            "bearing": _compass_bearing(seg_coords[-2],
                                                       seg_coords[-1]),
                        },
                    })

        # Dedup endpoints that coincide (same coord + bearing). Two clipped
        # relations sharing a boundary-crossing way produce identical points
        # and would otherwise stack as overlapping arrows. We collapse them
        # into a single feature whose `route_ids` array lists every sharing
        # route — the renderer's filter uses `in` against the array so the
        # arrow stays visible as long as ANY of its routes is shown.
        groups = {}
        for ep in clip_endpoints:
            lon, lat = ep["geometry"]["coordinates"]
            key = (round(lon, 7), round(lat, 7),
                   round(ep["properties"]["bearing"], 1))
            g = groups.get(key)
            if g is None:
                g = {
                    "coord": ep["geometry"]["coordinates"],
                    "bearing": ep["properties"]["bearing"],
                    "route_ids": [],
                }
                groups[key] = g
            rid = ep["properties"]["route_id"]
            if rid not in g["route_ids"]:
                g["route_ids"].append(rid)
        # Also emit route_ids as a pipe-delimited string. MapLibre's `in`
        # expression on an array property is fragile (it conflicts with the
        # legacy `["in", key, ...]` filter form); using `in` against a string
        # haystack is unambiguous, so the renderer filters on this field.
        clip_endpoints = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": g["coord"]},
                "properties": {
                    # Stringify route IDs to match the runtime
                    # convention (visibleRoutes uses strings, as does
                    # CONFIG.routes' keying). Without this, the
                    # runtime's visible_count loop in app.js sees
                    # ints here but compares against a Set of
                    # strings — every match fails, every shared
                    # endpoint reads as visible_count=0, and the
                    # multi-route "fill black" branch never fires.
                    "route_ids": [str(r) for r in g["route_ids"]],
                    "route_ids_str": "|" + "|".join(str(r) for r in g["route_ids"]) + "|",
                    "bearing": g["bearing"],
                },
            }
            for g in groups.values()
        ]

        print(f"  {len(clip_features)} features clipped to {len(clipped_features)} segments "
              f"({len(clip_endpoints)} continuation arrowheads)")
        geojson["features"] = core_features + clipped_features

    print(f"  Generated {len(geojson['features'])} features")

    # Also embed route (relation) metadata for the viewer
    geojson["metadata"] = {
        "routes": {
            str(rel_id): {
                "name": info["name"],
                "colour": info["colour"],
                "ref": info["ref"],
                "seasonal": info.get("seasonal", ""),
            }
            for rel_id, info in relations.items()
        }
    }

    # Write output
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(geojson, f, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    print(f"  Wrote {output_path} ({size_kb:.1f} KB)")

    # Write clip_endpoints.geojson sibling — the renderer reads it (when
    # present) to draw continuation arrowheads at clip-created endpoints.
    # Stale files are removed when the current build has none, so a config
    # that drops `clipped_relations` doesn't leave orphan endpoints behind.
    endpoints_path = os.path.join(
        os.path.dirname(output_path) or ".", "clip_endpoints.geojson"
    )
    if clip_endpoints:
        endpoints_geojson = {
            "type": "FeatureCollection",
            "features": clip_endpoints,
        }
        with open(endpoints_path, "w") as f:
            json.dump(endpoints_geojson, f, separators=(",", ":"))
        print(f"  Wrote {endpoints_path} ({len(clip_endpoints)} points)")
    elif os.path.exists(endpoints_path):
        os.remove(endpoints_path)
        print(f"  Removed stale {endpoints_path}")

    return geojson


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <config.yaml> [output.geojson] [cache_dir]")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)
    output = sys.argv[2] if len(sys.argv) > 2 else os.path.join("build", config["slug"], "trails.geojson")
    cache = sys.argv[3] if len(sys.argv) > 3 else "cache"

    fetch_trails(config_path, output, cache)
