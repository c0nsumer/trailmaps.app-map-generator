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

import cli
import console

# Shared narrow-resolution loader (handles ``osm_file:`` only — the
# full path-resolution path lives in build.py for the standard
# pipeline). Imported under the historical name so call sites stay
# unchanged.
from config_io import load_config_for_fetch as load_config
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

    Entries are the shared six-field info dict (osm_parser.relation_info
    — same shape as the local-.osm path) plus `members`, preserved so
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

    Returns (members, clipped, expansions):
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
    """
    relation_ids = list(relation_ids or [])
    clipped_ids = list(clipped_ids or [])
    all_input_ids = relation_ids + clipped_ids

    if not all_input_ids:
        return {}, {}, {}

    # Build a single Overpass query covering every input ID. Each entry
    # gets `(relation(R);rel(r);)` so super-relations expand to their
    # children inline. Wrapping each in `()` makes them independent
    # union members of the outer `({...})`.
    parts = [";".join(f"(relation({rid});rel(r);)" for rid in all_input_ids) + ";"]

    # `out body;` instead of `out tags;` so member lists come back too.
    # Super-relation detection below needs to see type=relation members.
    # The size delta is small (members are 3 fields each) and we already
    # cache the response.
    query = f"""
[out:json][timeout:120];
({" ".join(parts)});
out body;
"""
    data = overpass_query(query, cache_dir, label="relations", require_elements=True, refresh=refresh)
    all_rels = _parse_relations(data)

    # Surface input IDs the response didn't contain. Without this, a
    # route relation deleted upstream in OSM (or a typo'd ID) silently
    # vanished from the map on the next refetch — the only signal was
    # the aggregate "Found N relation(s)" count. The local-file path
    # warns per missing ID (osm_parser.py); keep the two in lockstep.
    for rid in all_input_ids:
        if rid not in all_rels:
            console.warn(
                f"Relation {rid} not returned by Overpass — deleted "
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
            # Super-relation parent — drop. Its children carry the
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

    return members, clipped, expansions


def fetch_all_ways_bulk(relation_ids, cache_dir=None, refresh=False):
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
    data = overpass_query(query, cache_dir, label="ways", require_elements=True, refresh=refresh)

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

    return all_ways


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
        # Track every source way fused into this segment so downstream
        # validation (e.g. unscheduled oneway=reversible detection in
        # build.py) can point users at specific OSM ways even when working
        # from cached trails.geojson.
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
                # Reversal would invert direction — keep one-way ways separate.
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

        for i, segment in enumerate(merged):
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
        "metadata": {"routes": {}, "super_relation_expansions": {}},
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))
    # Mirror the main path's stale-sibling cleanup so a prior relation-based
    # build's clip_endpoints.geojson doesn't linger and draw phantom
    # continuation arrowheads on the route-only render.
    endpoints_path = os.path.join(os.path.dirname(output_path) or ".", "clip_endpoints.geojson")
    if os.path.exists(endpoints_path):
        os.remove(endpoints_path)
        console.info(f"Removed stale {endpoints_path}")
    return geojson


def fetch_trails(config_or_path, output_path, cache_dir="cache", refresh=False):
    """Main entry point: fetch trails and write GeoJSON.

    ``refresh=True`` (build.py --refresh / --refresh-trails) bypasses cached Overpass
    responses for this map's queries without touching the shared
    cache directory's other entries.
    """
    config = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
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
    # winter / summer / emergency lists carry bucket semantics on top,
    # but from fetch_trails.py's perspective they all mean "pull this
    # relation." Fold them into the unified source set so a relation
    # listed only as (e.g.) winter_relations still gets fetched. Any
    # overlap is harmless: the downstream dedup handles it.
    winter_relation_ids = set(config.get("winter_relations") or [])
    summer_relation_ids = set(config.get("summer_relations") or [])
    emergency_relation_ids = set(config.get("emergency_access_relations") or [])
    relation_ids = list(
        {
            *source_ids,
            *winter_relation_ids,
            *summer_relation_ids,
            *emergency_relation_ids,
        }
    )
    clipped_relation_ids = config.get("clipped_relations") or []

    osm_file = config.get("osm_file")
    if osm_file:
        console.step(f"Loading trails for {config['name']} from {osm_file}...")
    else:
        console.step(f"Fetching trails for {config['name']} (relations {sorted(source_ids)})...")

    # Tracks any super-relation IDs that get expanded during fetch.
    # Both code paths populate this; it's persisted to trails.geojson
    # metadata so build.py can apply the same expansion when computing
    # winter / summer / emergency bucket flags from the config.
    super_relation_expansions = {}

    def _log_expansions(label, expansions):
        for parent_id, child_ids in sorted(expansions.items()):
            console.info(f"  {label}: super-relation {parent_id} → {len(child_ids)} child route(s)")

    if osm_file:
        if not os.path.isabs(osm_file):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            osm_file = os.path.join(project_root, osm_file)

        console.step("Stage A: Parsing .osm file...")
        parsed = parse_osm_file(osm_file)
        console.info(
            f"Parsed {len(parsed[0])} nodes, {len(parsed[1])} ways, {len(parsed[2])} relations"
        )

        relations, source_expansions = extract_source_relations(parsed, relation_ids)
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
        members, clipped_relations, super_relation_expansions = fetch_all_relations(
            relation_ids, clipped_relation_ids, cache_dir, refresh=refresh
        )
        _log_expansions("expanded", super_relation_expansions)

        if not members and not clipped_relations:
            console.step(f"\n  ERROR: Relations {sorted(source_ids)} returned no usable data.")
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
        all_ways = fetch_all_ways_bulk(list(relations.keys()), cache_dir, refresh=refresh)
        _log_way_counts(relations, all_ways)

    # Apply winter_relations config override (marks relations as winter
    # even if they don't have seasonal=winter in OSM). Expand through
    # the fetch-time super-relation map BEFORE tagging, so a curator
    # listing one super-relation in `winter_relations` propagates
    # seasonal=winter to every child route — the parent itself is gone
    # (replaced by children in `relations`). Same logic applies in
    # build.py for summer/emergency bucket flags via the persisted
    # expansion mapping. Shared by both fetch paths — this block used
    # to be maintained twice.
    winter_relation_ids = _expand_through_supers(winter_relation_ids, super_relation_expansions)
    for rel_id in winter_relation_ids:
        if rel_id in relations:
            relations[rel_id]["seasonal"] = "winter"

    # Build way-to-relations mapping
    way_relations = build_way_to_relations_map(all_ways)
    shared_count = sum(1 for wids in way_relations.values() if len(wids) > 1)
    console.info(f"{shared_count} ways are shared by multiple relations")

    # Validate oneway=reversible ways. These are trails that change direction
    # by schedule (the canonical OSM tag for that case) and have no inherent
    # forward direction — they are only meaningful with a direction schedule
    # on one of their parent relations (or a system-wide default schedule).
    # Fail the build with a precise list of offending ways otherwise; the
    # framework would otherwise silently render them as static one-ways in
    # OSM-digitization order, which is wrong half the time.
    #
    # Resolution mirrors template_inject.py's CONFIG emission:
    #   - direction_schedule.reverse_days (non-empty) covers every
    #     relation by default.
    #   - direction_schedule.per_route[<rel>] is a per-relation override.
    #     An entry with non-empty reverse_days schedules that relation;
    #     an explicit empty reverse_days opts that relation out of the
    #     system-wide default.
    #   - A super-relation key fans out to every child route, except
    #     where a child has its own explicit entry (which always wins).
    #     Mirrors the two-pass logic in template_inject.py — leaves
    #     first, then supers fill in unset children.
    sched_block = config.get("direction_schedule") or {}
    sched_raw = sched_block.get("per_route") or {}
    default_active = bool(sched_block.get("reverse_days"))

    # Build per-child resolved entries through the expansion table. The
    # resolved set drives both validation here and CONFIG.directionSchedules
    # in template_inject.py (re-derived there from the persisted metadata
    # so the cached-build path stays consistent).
    overrides_active = set()  # relations explicitly scheduled
    overrides_optout = set()  # relations explicitly opted out (empty list)
    deferred_supers = []  # super entries to fan out after leaves

    def _classify(target_rid, days):
        (overrides_active if days else overrides_optout).add(target_rid)

    # Pass 1: leaves.
    for k, v in sched_raw.items():
        rid_int = int(k)
        if rid_int in super_relation_expansions:
            deferred_supers.append((rid_int, v))
            continue
        days = (v or {}).get("reverse_days") or []
        _classify(rid_int, days)

    # Pass 2: supers fan out to children that aren't already classified.
    already_set = overrides_active | overrides_optout
    for super_rid, v in deferred_supers:
        days = (v or {}).get("reverse_days") or []
        for child_id in super_relation_expansions[super_rid]:
            if child_id in already_set:
                continue
            _classify(child_id, days)
            already_set.add(child_id)

    def _relation_is_scheduled(rid):
        if rid in overrides_active:
            return True
        if rid in overrides_optout:
            return False
        return default_active

    unscheduled_reversible = []
    seen_way_ids = set()
    for ways in all_ways.values():
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
            "       Either set a system-wide schedule that covers every route:",
            "",
            "         direction_schedule:",
            "           reverse_days: [tuesday, thursday, saturday]",
            "",
            "       …or schedule the specific parent relation:",
            "",
            "         direction_schedule:",
            "           per_route:",
            "             <relation_id>:",
            "               reverse_days: [tuesday, thursday, saturday]",
            "",
            "       Offending ways (way_id → parent relation IDs):",
        ]
        for way_id, parents in unscheduled_reversible[:20]:
            lines.append(f"         https://www.openstreetmap.org/way/{way_id}  →  {parents}")
        if len(unscheduled_reversible) > 20:
            lines.append(f"         ... and {len(unscheduled_reversible) - 20} more")
        sys.exit("\n".join(lines))

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
        # route — the renderer's filter uses `in` against the array so the
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

        console.info(
            f"{len(clip_features)} features clipped to {len(clipped_features)} segments "
            f"({len(clip_endpoints)} continuation arrowheads)"
        )
        geojson["features"] = core_features + clipped_features

    console.info(f"Generated {len(geojson['features'])} features")

    # Warn when show_difficulty is enabled but no way carries an
    # mtb:scale:imba tag — same posture as the POI fetch's
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
    # super-relation expansion mapping so build.py can apply the
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
    }

    # Write output
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    console.info(f"Wrote {output_path} ({size_kb:.1f} KB)")

    # Write clip_endpoints.geojson sibling — the renderer reads it (when
    # present) to draw continuation arrowheads at clip-created endpoints.
    # Stale files are removed when the current build has none, so a config
    # that drops `clipped_relations` doesn't leave orphan endpoints behind.
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

    return geojson


if __name__ == "__main__":
    parser = cli.config_output_parser("Fetch trail data from OpenStreetMap via Overpass.")
    parser.add_argument(
        "--cache-dir", default="cache", help="Cache directory (default: cache)"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    output = args.output or os.path.join("build", config["slug"], "trails.geojson")
    fetch_trails(args.config, output, args.cache_dir)
