#!/usr/bin/env python3
"""Fetch trail data from OpenStreetMap via Overpass API.

Queries the Overpass API for a super-relation and its member relations,
fetches all member ways with geometry, merges consecutive ways that share
the same set of relations, and outputs a GeoJSON file.

Internal build sub-stage: build.py imports and calls fetch_trails()
directly; the ``__main__`` CLI exists only for standalone debugging and
ad-hoc data refreshes.
"""

import json
import math
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime

import cli
import console

# Shared narrow-resolution loader (handles ``osm_file:`` only - the
# full path-resolution path lives in build.py for the standard
# pipeline).
from config_io import load_config_for_fetch
from osm_parser import (
    detect_super_expansions,
    extract_source_relations,
    extract_ways,
    parse_osm_file,
    relation_info,
)
from overpass import query as overpass_query


def _expand_through_supers(relation_ids, expansions):
    """Replace any super-relation IDs in `relation_ids` with their
    children, leaving leaf IDs alone. Returns a set."""
    out = set()
    for rid in relation_ids:
        if rid in expansions:
            out.update(expansions[rid])
        else:
            out.add(rid)
    return out


def _parse_relations(data):
    """Parse relation elements from an Overpass response into a dict.

    Entries are the shared five-field info dict (osm_parser.relation_info
    - same shape as the local-.osm path) plus `members`, preserved so
    super-relation expansion can identify type=relation member
    references. The original Overpass `out tags;` directive omits
    members; the caller must use `out body;` (or equivalent) to
    include them.
    """
    relations = {}
    for element in data.get("elements", []):
        if element["type"] == "relation":
            info = relation_info(element["id"], element.get("tags", {}))
            info["members"] = element.get("members", [])
            relations[element["id"]] = info
    return relations


def fetch_all_relations(relation_ids, clipped_ids=None, cache_dir=None, refresh=False):
    """Fetch relation metadata for every entry in `relation_ids` and
    `clipped_ids` in a single Overpass query.

    Each input ID may be a leaf route relation OR a super-relation
    (whose child relations become routes). Super-relations are
    auto-expanded one level deep: the parent itself is dropped from
    the result, and its children take its slot. Leaf relations pass
    through unchanged. A super-relation whose member is itself a
    super treats the inner one as a leaf (still one level deep).

    A relation ID with no resolvable children AND no own ways is
    treated as a leaf and returned directly (the "single-relation map"
    fallback: the relation IS the route).

    Returns (members, clipped, expansions, osm_base):
        members:    {rel_id: info} for every leaf route resolved from
                    `relation_ids` (and the children of any super-
                    relations therein).
        clipped:    {rel_id: info} same shape, but resolved from
                    `clipped_ids` (these get clipped to the core trail
                    bbox downstream).
        expansions: {parent_id: [child_id, ...]} for any input IDs
                    (from either list) that expanded as super-relations.
                    Empty when every input was a leaf. Used by the
                    caller to propagate per-list semantics (winter /
                    summer / emergency tagging) from parent slot to
                    children.
        osm_base:   the response's osm3s.timestamp_osm_base ("...Z" UTC
                    string), "" when absent. The OSM snapshot the data
                    came from - durable across rebuilds because it
                    lives inside the (cached) response body, unlike a
                    file mtime.
    """
    relation_ids = list(relation_ids or [])
    clipped_ids = list(clipped_ids or [])
    all_input_ids = relation_ids + clipped_ids

    if not all_input_ids:
        return {}, {}, {}, ""

    # Build a single Overpass query covering every input ID. Each entry
    # gets `(relation(R);rel(r);)` so super-relations expand to their
    # children inline. Wrapping each in `()` makes them independent
    # union members of the outer `({...})`.
    union = ";".join(f"(relation({rid});rel(r);)" for rid in all_input_ids) + ";"

    # `out body;` instead of `out tags;` so member lists come back too.
    # Super-relation detection below needs to see type=relation members.
    # The size delta is small (members are 3 fields each) and we already
    # cache the response.
    query = f"""
[out:json][timeout:120];
({union});
out body;
"""
    data = overpass_query(query, cache_dir, label="relations", require_elements=True, refresh=refresh)
    osm_base = data.get("osm3s", {}).get("timestamp_osm_base") or ""
    all_rels = _parse_relations(data)

    # Surface input IDs the response didn't contain. Without this, a
    # route relation deleted upstream in OSM (or a typo'd ID) silently
    # vanished from the map on the next refetch - the only signal was
    # the aggregate "Found N relation(s)" count. The local-file path
    # warns per missing ID (osm_parser.py); keep the two in lockstep.
    for rid in all_input_ids:
        if rid not in all_rels:
            console.warn(
                f"Relation {rid} not returned by Overpass - deleted "
                f"upstream, or a typo in the config? Its route will be "
                f"missing from the map."
            )

    # Detect super-relations among the inputs (shared rule with the
    # local-.osm path). A super-relation has type=relation members that
    # we ALSO fetched (via rel(r)). If a parent has no fetched relation
    # children, it's treated as a leaf.
    expansions = detect_super_expansions(all_input_ids, all_rels)

    # Resolve each input list: replace super-parents with their
    # children, leave leaves alone. A relation that appears in BOTH
    # lists ends up in the source set (clipped is "in addition to,
    # but clip me at bbox"), but in practice the lists shouldn't
    # overlap: clipped_relations exists for routes you DON'T want in
    # the core trails geometry.
    def _resolve(ids):
        out = []
        seen = set()
        for rid in ids:
            for resolved in expansions.get(rid) or [rid]:
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
        return out

    relation_set = set(_resolve(relation_ids))
    clipped_set = set(_resolve(clipped_ids)) - relation_set
    expanded_parents = set(expansions.keys())

    members = {}
    clipped = {}
    for rel_id, info in all_rels.items():
        if rel_id in expanded_parents:
            # Super-relation parent - drop. Its children carry the
            # bucket assignment via the expansions map.
            continue
        if rel_id in clipped_set:
            clipped[rel_id] = info
        elif rel_id in relation_set:
            members[rel_id] = info
        # Anything else can't happen in normal operation: every
        # fetched leaf came in via either an explicit input ID or a
        # super-relation expansion, and the case above handles both.
        # If it ever does happen we silently drop the orphan rather
        # than emitting a route the curator didn't ask for.

    return members, clipped, expansions, osm_base


def fetch_all_ways_bulk(relation_ids, cache_dir=None, refresh=False):
    """Fetch ways for all relations in a single Overpass query using foreach.

    Uses Overpass's foreach to iterate over relations server-side, emitting
    a sentinel relation element before each group of ways so we can split
    the flat response back into per-relation buckets.

    Returns (all_ways, osm_base):
        all_ways: dict of {relation_id: {way_id: way_dict, ...}, ...}
        osm_base: the response's osm3s.timestamp_osm_base ("...Z" UTC
                  string), "" when absent - see fetch_all_relations.
    """
    if not relation_ids:
        return {}, ""

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
    data = overpass_query(query, cache_dir, label="ways", require_elements=True, refresh=refresh)
    osm_base = data.get("osm3s", {}).get("timestamp_osm_base") or ""

    # Parse the flat element list.  The foreach emits a relation element
    # (with just an id) followed by its member ways, then the next relation, etc.
    all_ways = {rid: {} for rid in relation_ids}
    current_rel_id = None

    for element in data.get("elements", []):
        if element["type"] == "relation":
            current_rel_id = element["id"]
            continue

        if element["type"] == "way" and "geometry" in element and current_rel_id is not None:
            coords = [[node["lon"], node["lat"]] for node in element["geometry"]]
            if len(coords) >= 2:
                way = {
                    "id": element["id"],
                    "coords": coords,
                    "tags": element.get("tags", {}),
                }
                if current_rel_id in all_ways:
                    all_ways[current_rel_id][element["id"]] = way

    return all_ways, osm_base


def build_way_to_relations_map(all_ways):
    """Build a mapping of way_id -> set of relation_ids that use it."""
    way_relations = defaultdict(set)
    for rel_id, ways in all_ways.items():
        for way_id in ways:
            way_relations[way_id].add(rel_id)
    return way_relations


def _resolve_oneway(tags):
    """Return the effective oneway value for direction-arrow purposes.

    OSM uses two relevant tags:
      * ``oneway``         - generic restriction, traditionally for vehicles
      * ``oneway:bicycle`` - bicycle-specific override

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


def merge_consecutive_ways(ways_dict, way_relation_ids):
    """Merge consecutive ways that share the same set of relations, name, and difficulty.

    Given a dict of ways belonging to one relation and a mapping of
    ``{way_id: set(relation_ids)}``, merge consecutive ways where the
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
    # features so each retains its own digitization order.
    way_signatures = {}
    for way_id, way in ways_dict.items():
        sig = tuple(sorted(way_relation_ids.get(way_id, set())))
        name = way.get("tags", {}).get("name", "")
        imba = way.get("tags", {}).get("mtb:scale:imba", "")
        # Resolve effective oneway state. Bicycle-specific tag takes
        # precedence so curators can mark MTB-only direction without
        # affecting hikers (or restrict bikes from a way that's
        # generically two-way). Both tags follow the same value
        # vocabulary: "" | "yes" | "no" | "-1" | "reversible".
        oneway = _resolve_oneway(way.get("tags", {}))
        way_signatures[way_id] = (sig, name, imba, oneway)

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
        # schedule), so they must not be glued to a neighbor in reversed
        # orientation any more than `yes`/`-1` ways can.
        is_oneway = oneway in ("yes", "-1", "reversible")
        coords = list(ways_dict[start_way_id]["coords"])
        # Track every source way fused into this segment so the
        # unscheduled oneway=reversible check in template_inject can point
        # users at specific OSM ways on a rebuild from the cached base.
        member_way_ids = [start_way_id]

        # Extend forward from the end of the current chain
        while True:
            end_node = tuple(coords[-1])
            candidates = [
                wid
                for wid in node_to_ways[end_node]
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
                wid
                for wid in node_to_ways[start_node]
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
                # Reversal would invert direction - keep one-way ways separate.
                if is_oneway:
                    break
                visited.add(prev_way_id)
                coords = list(reversed(prev_coords))[:-1] + coords
                member_way_ids.insert(0, prev_way_id)
            else:
                break

        merged_segments.append(
            {
                "coords": coords,
                "shared_routes": sorted(sig),
                "trail_name": name,
                "imba_difficulty": imba,
                "oneway": oneway,
                "way_ids": member_way_ids,
            }
        )

    return merged_segments


def clip_line_to_bbox(coords, bbox):
    """Clip a LineString's coordinates to a bounding box.

    Uses Liang-Barsky line-segment clipping. Returns a list of
    (coords, start_clipped, end_clipped) triples - one LineString may produce
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

        for p, q in [(-dx, x1 - west), (dx, east - x1), (-dy, y1 - south), (dy, north - y1)]:
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
    current = []  # accumulator coords for in-progress segment
    current_start_clipped = False  # was current[0] clip-created?
    last_end_clipped = False  # was current[-1] clip-created? (tracked
    # so the loop-exit flush knows what to do)

    def flush(end_clipped):
        if len(current) >= 2:
            segments.append((list(current), current_start_clipped, end_clipped))

    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]

        result = intersect_segment(x1, y1, x2, y2)
        if result is None:
            # Segment entirely outside - flush current. The accumulator's
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
            # Discontinuity - flush and start a new segment. The flushed
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
    (Δlon · cos(lat)) - at trail scale this is within fractions of a degree
    of the true great-circle bearing, plenty for orienting an arrowhead.
    Returns a value in [0, 360).
    """
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
            console.warn(f"No ways found for relation {rel_id} ({rel_info['name']})")
            continue

        # Build per-way relation membership lookup for this relation's ways
        way_rel_lookup = {way_id: way_relations.get(way_id, {rel_id}) for way_id in ways}

        merged = merge_consecutive_ways(ways, way_rel_lookup)

        for segment in merged:
            # Normalize oneway=-1 to oneway=yes with reversed coordinates so
            # the runtime only ever has to handle a single canonical case for
            # static one-ways. `reversible` is passed through unchanged: its
            # default arrow direction is the OSM digitization order, and the
            # day-of-week schedule (direction_schedule) flips it.
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
                    "trail_name": segment.get("trail_name", ""),
                    "shared_routes": segment["shared_routes"],
                    "imba_difficulty": segment.get("imba_difficulty", ""),
                    "oneway": oneway,
                    # Source OSM way IDs fused into this segment. Used by
                    # template_inject's reversible-without-schedule check so
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


def _log_relation_list(relations, *, clipped=False):
    """Print one indented line per relation (name, id, colour), with an
    optional ``[clipped]`` marker. Shared by the local-.osm and Overpass
    fetch paths so the two stay in lockstep."""
    suffix = " [clipped]" if clipped else ""
    for rel_id, info in sorted(relations.items(), key=lambda x: x[1]["name"]):
        colour = info["colour"] or "(no tag)"
        console.info(f"  {info['name']} ({rel_id}) colour={colour}{suffix}")


def _log_way_counts(relations, all_ways):
    """Print each relation's extracted way count, warning on any relation
    that resolved to zero ways (a likely bad relation ID or an
    over-aggressive clip). Shared by both fetch paths."""
    for rel_id, info in sorted(relations.items(), key=lambda x: x[1]["name"]):
        way_count = len(all_ways.get(rel_id, {}))
        console.info(f"{info['name']}: {way_count} ways")
        if way_count == 0:
            console.warn(f"No ways found for {info['name']} ({rel_id})")


def gather_relation_ids(config):
    """Return (relation_ids, clipped_ids): the relations the fetch queries.

    The winter / summer / emergency lists carry bucket semantics on top,
    but for fetching they all mean "pull this relation", so they fold
    into the main set; any overlap is harmless. The order is set
    iteration order, and the Overpass queries (so their cache keys) are
    built from it: changing the construction refetches every map.
    tools/list_relations.py calls this to find the same cache entries.
    """
    relation_ids = list(
        {
            *(config.get("relations") or []),
            *set(config.get("winter_relations") or []),
            *set(config.get("summer_relations") or []),
            *set(config.get("emergency_access_relations") or []),
        }
    )
    clipped_ids = list(config.get("clipped_relations") or [])
    return relation_ids, clipped_ids


def _has_custom_geometry(config):
    """True when the config supplies route geometry without OSM relations:
    top-level `custom_routes` or inline `event_mode.routes`. Lets a
    race/event map render a GeoJSON route with no `relations:` at all."""
    if config.get("custom_routes"):
        return True
    em = config.get("event_mode")
    return bool(isinstance(em, dict) and em.get("routes"))


def _write_empty_trails(output_path, map_name):
    """Write + return an empty-but-well-formed trails.geojson skeleton.

    Used for relation-free maps: enrichment later folds the custom /
    event-route geometry into this structure, so the `metadata` skeleton
    must match the normal fetch path (`routes`, `super_relation_expansions`)
    for downstream readers (build.py, enrichment.py, event_mode.py).
    """
    console.step(f"No relations for {map_name}: writing empty trail base (route-only map)")
    geojson = {
        "type": "FeatureCollection",
        "features": [],
        "metadata": {"routes": {}, "super_relation_expansions": {}, "data_timestamp": ""},
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))
    _write_clip_endpoints(output_path, [])
    return geojson


def _write_clip_endpoints(output_path, clip_endpoints):
    """Write the clip_endpoints.geojson sibling of ``output_path``.

    The renderer reads it, when present, to draw continuation arrowheads
    at clip-created endpoints. With no endpoints, a stale file from an
    earlier build is removed instead, so a config that drops
    `clipped_relations` (or a route-only map) draws no phantom arrows.
    """
    endpoints_path = os.path.join(os.path.dirname(output_path) or ".", "clip_endpoints.geojson")
    if clip_endpoints:
        endpoints_geojson = {
            "type": "FeatureCollection",
            "features": clip_endpoints,
        }
        with open(endpoints_path, "w", encoding="utf-8") as f:
            json.dump(endpoints_geojson, f, separators=(",", ":"))
        console.info(f"Wrote {endpoints_path} ({len(clip_endpoints)} points)")
    elif os.path.exists(endpoints_path):
        os.remove(endpoints_path)
        console.info(f"Removed stale {endpoints_path}")


def fetch_trails(config_or_path, output_path, cache_dir="cache", refresh=False):
    """Main entry point: fetch trails and write GeoJSON.

    ``refresh=True`` (build.py --refresh / --refresh-trails) bypasses cached Overpass
    responses for this map's queries without touching the shared
    cache directory's other entries.
    """
    config = (config_or_path if isinstance(config_or_path, dict)
              else load_config_for_fetch(config_or_path))
    source_ids = list(config.get("relations") or [])
    if not source_ids:
        # No OSM relations. A race/event or route-only map supplies its
        # geometry via custom_routes / event_mode.routes, which enrichment
        # folds into trails.geojson AFTER this fetch. Emit a well-formed
        # empty skeleton so the build proceeds; if there's no custom
        # geometry either, there's genuinely nothing to build.
        if _has_custom_geometry(config):
            return _write_empty_trails(output_path, config["name"])
        sys.exit(
            "ERROR: config must specify `relations:` (a non-empty list of "
            "OSM relation IDs) or supply `custom_routes` / "
            "`event_mode.routes` geometry."
        )
    winter_relation_ids = set(config.get("winter_relations") or [])
    relation_ids, clipped_relation_ids = gather_relation_ids(config)

    osm_file = config.get("osm_file")
    if osm_file:
        console.step(f"Loading trails for {config['name']} from {osm_file}...")
    else:
        console.step(f"Fetching trails for {config['name']} (relations {sorted(source_ids)})...")

    # Tracks any super-relation IDs that get expanded during fetch.
    # Both code paths populate this; it's persisted to trails.geojson
    # metadata so enrichment can apply the same expansion when computing
    # winter / summer / emergency bucket flags from the config.
    super_relation_expansions = {}

    # "...Z" UTC string recording where this data is from in time: the
    # Overpass osm3s snapshot timestamp (or the .osm file's mtime for
    # local-file maps). Persisted to metadata so build.py's dataDate
    # survives rebuilds that re-expand from cached responses.
    data_timestamp = ""

    def _log_expansions(label, expansions):
        for parent_id, child_ids in sorted(expansions.items()):
            console.info(f"  {label}: super-relation {parent_id} → {len(child_ids)} child route(s)")

    if osm_file:
        if not os.path.isabs(osm_file):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            osm_file = os.path.join(project_root, osm_file)

        # The .osm file IS the data source, so its mtime is the honest
        # "data as of" record (and, like buildDate's inputs, survives
        # rebuilds and machine moves that preserve mtimes).
        data_timestamp = datetime.fromtimestamp(os.path.getmtime(osm_file), tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        console.step("Stage A: Parsing .osm file...")
        parsed = parse_osm_file(osm_file)
        console.info(
            f"Parsed {len(parsed[0])} nodes, {len(parsed[1])} ways, {len(parsed[2])} relations"
        )

        relations, source_expansions = extract_source_relations(parsed, relation_ids)
        # Stop here, not at the bbox step downstream, whose message blames
        # Overpass. The usual cause is a JOSM save: it hands every new
        # object a fresh negative id, so a config written against the
        # previous save names relations the file no longer holds.
        if relation_ids and not relations:
            console.error(
                f"None of the relations {relation_ids} is in {osm_file}. A file saved "
                "from JOSM renumbers its negative ids on every save; read the current "
                "ids from the file and update the config."
            )
            sys.exit(1)
        super_relation_expansions.update(source_expansions)
        _log_expansions("expanded", source_expansions)
        console.info(f"Found {len(relations)} relation(s):")
        _log_relation_list(relations)

        clipped_relations = {}
        if clipped_relation_ids:
            console.info(f"Loading {len(clipped_relation_ids)} clipped relation(s)...")
            clipped_relations, clipped_expansions = extract_source_relations(
                parsed, clipped_relation_ids
            )
            super_relation_expansions.update(clipped_expansions)
            _log_expansions("clipped", clipped_expansions)
            _log_relation_list(clipped_relations, clipped=True)
            relations.update(clipped_relations)

        console.step(f"Stage B: Extracting ways for {len(relations)} relations...")
        all_ways = extract_ways(parsed, list(relations.keys()))
        _log_way_counts(relations, all_ways)
    else:
        # Stage A: Fetch all relation metadata in a single query
        console.step("Stage A: Fetching relation metadata...")
        members, clipped_relations, super_relation_expansions, relations_osm_base = (
            fetch_all_relations(relation_ids, clipped_relation_ids, cache_dir, refresh=refresh)
        )
        _log_expansions("expanded", super_relation_expansions)

        if not members and not clipped_relations:
            console.blank()
            console.error(f"Relations {sorted(source_ids)} returned no usable data.")
            console.info("Check that the relation IDs are correct and exist:")
            for rid in sorted(source_ids):
                console.info(f"  https://www.openstreetmap.org/relation/{rid}")
            console.blank()
            sys.exit(1)

        console.info(f"Found {len(members)} relation(s):")
        _log_relation_list(members)

        relations = dict(members)

        if clipped_relations:
            console.info(f"Found {len(clipped_relations)} clipped relation(s):")
            _log_relation_list(clipped_relations, clipped=True)
            relations.update(clipped_relations)

        # Stage B: Fetch ways for all relations in a single bulk query
        console.step(f"Stage B: Fetching ways for {len(relations)} relations (bulk query)...")
        all_ways, ways_osm_base = fetch_all_ways_bulk(
            list(relations.keys()), cache_dir, refresh=refresh
        )
        _log_way_counts(relations, all_ways)

        # The two responses' snapshots normally match to the minute (both
        # queries key off the same relation set, so they refresh together);
        # min() takes the conservative claim if they ever diverge. ISO "Z"
        # strings order lexicographically = chronologically.
        data_timestamp = min(
            [t for t in (relations_osm_base, ways_osm_base) if t], default=""
        )

    # Apply winter_relations config override (marks relations as winter
    # even if they don't have seasonal=winter in OSM). Expand through
    # the fetch-time super-relation map BEFORE tagging, so a curator
    # listing one super-relation in `winter_relations` propagates
    # seasonal=winter to every child route - the parent itself is gone
    # (replaced by children in `relations`). Enrichment applies the same
    # logic to summer/emergency bucket flags via the persisted expansion
    # mapping. Shared by both fetch paths.
    winter_relation_ids = _expand_through_supers(winter_relation_ids, super_relation_expansions)
    for rel_id in winter_relation_ids:
        if rel_id in relations:
            relations[rel_id]["seasonal"] = "winter"

    # Build way-to-relations mapping
    way_relations = build_way_to_relations_map(all_ways)
    shared_count = sum(1 for wids in way_relations.values() if len(wids) > 1)
    console.info(f"{shared_count} ways are shared by multiple relations")

    # oneway=reversible ways without a direction schedule are rejected by
    # template_inject.inject_config_into_template, which runs on every
    # build, so a config-only rebuild is checked as well.

    # Stage C: Merge ways and build GeoJSON
    console.step("Stage C: Merging ways and building GeoJSON...")
    geojson = build_geojson(relations, all_ways, way_relations)

    # Stage D: Clip features for clipped_relations to the core trail bbox.
    # Always initialize the endpoints accumulator so the writer below has a
    # well-defined value when the map has no clipped relations.
    clip_endpoints = []
    if clipped_relations:
        clipped_ids = set(clipped_relations.keys())
        core_features = [
            f for f in geojson["features"] if f["properties"]["route_id"] not in clipped_ids
        ]
        clip_features = [
            f for f in geojson["features"] if f["properties"]["route_id"] in clipped_ids
        ]

        bbox = compute_bbox_from_features(core_features)
        console.info(
            f"Clipping {len(clip_features)} features to bbox {[round(v, 4) for v in bbox]}..."
        )

        clipped_features = []
        for feature in clip_features:
            coords = feature["geometry"]["coordinates"]
            route_id = feature["properties"]["route_id"]
            segments = clip_line_to_bbox(coords, bbox)
            for seg_coords, start_clipped, end_clipped in segments:
                clipped_feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": seg_coords},
                    "properties": dict(feature["properties"]),
                }
                clipped_features.append(clipped_feature)

                # Record continuation arrowhead points for any clip-created
                # endpoints. Bearing points OUTWARD (the direction the
                # trail is heading as it leaves the map).
                if start_clipped and len(seg_coords) >= 2:
                    clip_endpoints.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": list(seg_coords[0])},
                            "properties": {
                                "route_id": route_id,
                                "bearing": _compass_bearing(seg_coords[1], seg_coords[0]),
                            },
                        }
                    )
                if end_clipped and len(seg_coords) >= 2:
                    clip_endpoints.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": list(seg_coords[-1])},
                            "properties": {
                                "route_id": route_id,
                                "bearing": _compass_bearing(seg_coords[-2], seg_coords[-1]),
                            },
                        }
                    )

        # Dedup endpoints that coincide (same coord + bearing). Two clipped
        # relations sharing a boundary-crossing way produce identical points
        # and would otherwise stack as overlapping arrows. We collapse them
        # into a single feature whose `route_ids` array lists every sharing
        # route - the renderer's filter uses `in` against the array so the
        # arrow stays visible as long as ANY of its routes is shown.
        groups = {}
        for ep in clip_endpoints:
            lon, lat = ep["geometry"]["coordinates"]
            key = (round(lon, 7), round(lat, 7), round(ep["properties"]["bearing"], 1))
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
                    # strings - every match fails, every shared
                    # endpoint reads as visible_count=0, and the
                    # multi-route "fill black" branch never fires.
                    "route_ids": [str(r) for r in g["route_ids"]],
                    "route_ids_str": "|" + "|".join(str(r) for r in g["route_ids"]) + "|",
                    "bearing": g["bearing"],
                },
            }
            for g in groups.values()
        ]

        console.info(
            f"{len(clip_features)} features clipped to {len(clipped_features)} segments "
            f"({len(clip_endpoints)} continuation arrowheads)"
        )
        geojson["features"] = core_features + clipped_features

    console.info(f"Generated {len(geojson['features'])} features")

    # Warn when show_difficulty is enabled but no way carries an
    # mtb:scale:imba tag - same posture as the POI fetch's
    # show_* warnings in fetch_pois.py. The runtime auto-hides
    # the Difficulty toggle anyway (CONFIG.hasDifficultyTrails),
    # but the build-time note tells the curator "the toggle you
    # might expect to see won't appear, and here's why."
    if config.get("show_difficulty", True):
        imba_tagged = sum(
            1 for f in geojson["features"] if (f.get("properties") or {}).get("imba_difficulty")
        )
        if imba_tagged == 0:
            console.note("show_difficulty is enabled but no mtb:scale:imba tags found in data")

    # Also embed route (relation) metadata for the viewer + the
    # super-relation expansion mapping so enrichment can apply the
    # same parent→children expansion to its summer/winter/emergency
    # config sets when computing per-route bucket flags.
    geojson["metadata"] = {
        "routes": {
            str(rel_id): {
                "name": info["name"],
                "colour": info["colour"],
                "ref": info["ref"],
                "seasonal": info.get("seasonal", ""),
            }
            for rel_id, info in relations.items()
        },
        "super_relation_expansions": {
            str(parent_id): [str(c) for c in child_ids]
            for parent_id, child_ids in super_relation_expansions.items()
        },
        # UTC "...Z" record of where the trail data is from in time (see
        # the assignment sites above). build.py derives dataDate from
        # this, NOT from file mtimes, so the About modal's "Trail data"
        # date stays put across rebuilds / checkouts / machine moves and
        # only advances on a genuine refetch.
        "data_timestamp": data_timestamp,
    }

    # Write output
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    console.info(f"Wrote {output_path} ({size_kb:.1f} KB)")

    _write_clip_endpoints(output_path, clip_endpoints)
    return geojson


if __name__ == "__main__":
    parser = cli.config_output_parser("Fetch trail data from OpenStreetMap via Overpass.")
    parser.add_argument(
        "--cache-dir", default="cache", help="Cache directory (default: cache)"
    )
    args = parser.parse_args()

    config = load_config_for_fetch(args.config)
    output = args.output or os.path.join("build", config["slug"], "trails.geojson")
    fetch_trails(config, output, args.cache_dir)
