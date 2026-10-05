#!/usr/bin/env python3
"""Parse local .osm XML files into the same data structures as Overpass queries.

This allows generating maps from non-public OSM data maintained locally
in JOSM or similar editors, without querying the Overpass API.

The parser produces identical data structures to fetch_trails.py and
fetch_pois.py so all downstream processing (merging, GeoJSON building,
clipping, etc.) works unchanged.

Usage as standalone:
    python scripts/osm_parser.py data/mytrails.osm 12345
"""

import argparse
import sys
import xml.etree.ElementTree as ET

import console

# The POI categories both fetch paths collect: (tags that must all match,
# whether a way counts too). Mappers often trace an amenity's building
# rather than placing a node, so the amenities match as ways; both paths
# accept open ways as well as closed ones.
POI_TAG_FILTERS = (
    ((("tourism", "information"), ("information", "guidepost")), False),
    ((("highway", "emergency_access_point"),), False),
    ((("tourism", "attraction"),), False),
    ((("amenity", "toilets"),), True),
    ((("amenity", "drinking_water"),), True),
    ((("amenity", "bicycle_repair_station"),), True),
)


def _poi_match(tags, ways_only=False):
    """True when ``tags`` matches a POI_TAG_FILTERS entry (only the
    entries that count as ways when ``ways_only``)."""
    return any(
        all(tags.get(k) == v for k, v in pairs)
        for pairs, as_way in POI_TAG_FILTERS
        if as_way or not ways_only
    )


def parse_osm_file(osm_path):
    """Parse a .osm XML file into nodes, ways, and relations.

    Returns:
        nodes: dict[int, (lon, lat, tags)]
        ways: dict[int, {"id", "nd_refs", "tags"}]
        relations: dict[int, {"id", "members", "tags"}]
    """
    tree = ET.parse(osm_path)
    root = tree.getroot()

    nodes = {}
    ways = {}
    relations = {}

    for elem in root:
        # JOSM saves an object deleted locally but not yet uploaded with
        # action='delete'; it is no longer part of the curator's data.
        if elem.attrib.get("action") == "delete":
            continue
        if elem.tag == "node":
            node_id = int(elem.attrib["id"])
            lon = float(elem.attrib["lon"])
            lat = float(elem.attrib["lat"])
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in elem.findall("tag")}
            nodes[node_id] = (lon, lat, tags)

        elif elem.tag == "way":
            way_id = int(elem.attrib["id"])
            nd_refs = [int(nd.attrib["ref"]) for nd in elem.findall("nd")]
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in elem.findall("tag")}
            ways[way_id] = {
                "id": way_id,
                "nd_refs": nd_refs,
                "tags": tags,
            }

        elif elem.tag == "relation":
            rel_id = int(elem.attrib["id"])
            members = []
            for member in elem.findall("member"):
                members.append(
                    {
                        "type": member.attrib["type"],
                        "ref": int(member.attrib["ref"]),
                        "role": member.attrib.get("role", ""),
                    }
                )
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in elem.findall("tag")}
            relations[rel_id] = {
                "id": rel_id,
                "members": members,
                "tags": tags,
            }

    return nodes, ways, relations


def relation_info(rel_id, tags):
    """The standard five-field relation info dict every downstream stage
    consumes (merging, GeoJSON building, enrichment).

    Single source of truth for BOTH fetch paths: this module's
    local-.osm extraction and fetch_trails.py's Overpass parsing build
    their entries through here, so the shape can't drift between them
    (the missing-relation warning drifted exactly this way once).
    """
    return {
        "id": rel_id,
        "name": tags.get("name") or f"Route {rel_id}",
        # None when OSM has no colour tag - runtime layered fallback:
        # relation_colors → default_trail_color → #808080 build-time default.
        "colour": tags.get("colour"),
        "ref": tags.get("ref", ""),
        "seasonal": tags.get("seasonal", ""),
    }


def detect_super_expansions(input_ids, relations):
    """Find which input IDs are super-relations, one level deep.

    A super-relation has at least one type=relation member that is
    itself present in ``relations`` (the available set - parsed .osm
    file or Overpass response). Members pointing at relations we don't
    have fall through to the leaf path: we can't render what we don't
    have. A super-relation containing another super treats the inner
    one as a leaf.

    Returns {parent_id: [child_id, ...]} for the inputs that expanded;
    empty when every input is a leaf. Shared by both fetch paths so
    the expansion rule can't drift.
    """
    expansions = {}
    for rel_id in input_ids:
        rel = relations.get(rel_id)
        if not rel:
            continue
        child_ids = [
            m["ref"]
            for m in rel.get("members", [])
            if m["type"] == "relation" and m["ref"] in relations
        ]
        if child_ids:
            expansions[rel_id] = child_ids
    return expansions


def resolve_relations(relation_ids, clipped_ids, available):
    """Split the relations a config names into source and clipped routes.

    Single resolver for BOTH fetch paths, so a trail loaded from a local
    .osm file and the same trail fetched from Overpass give the same
    routes. ``available`` maps rel_id -> an entry with a ``members``
    list (a parsed .osm relation or an Overpass relation); the results
    hold those same entries, in ``available``'s order.

    Each input may be a leaf route or a super-relation, expanded one
    level deep (detect_super_expansions). Every expanded parent is
    dropped, including a super listed next to the super that holds it,
    and a relation cycle expands both sides away. A relation in BOTH
    lists stays a source route: clipped_relations exists for routes kept
    out of the core trail geometry.

    Returns (members, clipped, expansions):
        members:    {rel_id: entry} resolved from ``relation_ids``.
        clipped:    {rel_id: entry} resolved from ``clipped_ids``.
        expansions: {parent_id: [child_id, ...]} for the inputs (from
                    either list) that expanded as super-relations.
    """
    relation_ids = list(relation_ids)
    clipped_ids = list(clipped_ids)
    expansions = detect_super_expansions(relation_ids + clipped_ids, available)

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

    members = {}
    clipped = {}
    for rel_id, entry in available.items():
        if rel_id in expansions:
            # Super-relation parent: its children carry the bucket
            # assignment via the expansions map.
            continue
        if rel_id in clipped_set:
            clipped[rel_id] = entry
        elif rel_id in relation_set:
            members[rel_id] = entry
        # Anything else is a relation the curator didn't ask for.
    return members, clipped, expansions


def extract_relations(parsed, relation_ids, clipped_ids=()):
    """Resolve a config's relations from a parsed .osm file.

    Warns per input ID missing from the file and skips it; the build
    continues with whatever else resolved. Resolution follows
    resolve_relations, so it matches the Overpass path.

    Returns (members, clipped, expansions) as resolve_relations does,
    with each entry the standard relation_info dict, in the order the
    config lists them (a super-relation's children in its member order).
    That order is the order of the map's key, and a file's own order
    changes whenever the editor rewrites it.
    """
    _nodes, _ways, relations = parsed
    for rel_id in list(relation_ids) + list(clipped_ids):
        if rel_id not in relations:
            console.warn(f"Relation {rel_id} not found in .osm file")
    members, clipped, expansions = resolve_relations(relation_ids, clipped_ids, relations)

    def _info(entries, ids):
        ordered = [r for rid in ids for r in (expansions.get(rid) or [rid])]
        return {rid: relation_info(rid, entries[rid]["tags"])
                for rid in dict.fromkeys(ordered) if rid in entries}

    return _info(members, relation_ids), _info(clipped, clipped_ids), expansions


def extract_source_relations(parsed, relation_ids):
    """Extract route relations for every entry in `relation_ids`.

    Each ID may be a leaf route relation OR a super-relation (a
    relation whose members are themselves type=relation entries - the
    OSM convention for grouping multiple route relations under a
    single umbrella). Super-relations are expanded into their child
    routes; the parent itself is dropped from the result (a super-
    relation has no ways of its own to render). Leaf relations pass
    through unchanged. One level deep only: a super-relation containing
    another super-relation treats the inner one as a leaf.

    A relation ID that isn't present in the parsed file is reported as
    a warning and skipped; the build continues with whatever else
    resolved. If NO inputs resolve, the caller's downstream code
    (fetch_trails) errors on the empty result.

    Returns (resolved, expansions) where:
        resolved: {rel_id: {"id", "name", "colour", "ref", "seasonal"},
                   ...} - leaf-route entries
                  only (parents replaced by children)
        expansions: {parent_id: [child_id, ...]} for any input IDs
                    that were expanded as super-relations. Empty when
                    every input was a leaf. The caller uses this to
                    propagate seasonal / emergency tagging from the
                    parent's config-keyed bucket to its children.
    """
    resolved, _clipped, expansions = extract_relations(parsed, relation_ids)
    return resolved, expansions


def extract_ways(parsed, relation_ids):
    """Extract ways for the given relations, resolving node refs to coordinates.

    Returns the same format as fetch_all_ways_bulk():
        {rel_id: {way_id: {"id", "coords": [[lon, lat], ...], "tags": {}}, ...}, ...}
    """
    nodes, ways, relations = parsed

    all_ways = {rid: {} for rid in relation_ids}

    for rel_id in relation_ids:
        rel = relations.get(rel_id)
        if not rel:
            continue

        for member in rel["members"]:
            if member["type"] != "way":
                continue
            way_id = member["ref"]
            way = ways.get(way_id)
            if not way:
                continue

            # Resolve node refs to coordinates
            coords = []
            for nd_ref in way["nd_refs"]:
                node = nodes.get(nd_ref)
                if node:
                    coords.append([node[0], node[1]])  # [lon, lat]

            if len(coords) >= 2:
                all_ways[rel_id][way_id] = {
                    "id": way_id,
                    "coords": coords,
                    "tags": way["tags"],
                }

    return all_ways


def _way_centroid(way, nodes):
    """Centroid of a way as the arithmetic mean of its node coords.

    Used to give building-shaped POIs (ways tagged amenity=toilets /
    drinking_water / bicycle_repair_station, usually closed) a single
    point location for the map. Arithmetic mean is exact for axis-aligned rectangles and a
    reasonable approximation for any small near-convex polygon, which
    covers the typical "toilet building" case. Returns (lon, lat) or
    None if no referenced nodes are present in the parsed file.
    """
    coords = [nodes[nid][:2] for nid in way["nd_refs"] if nid in nodes]
    if not coords:
        return None
    # Closed ways repeat the first node as the last; drop the dupe so
    # the centroid isn't biased toward that corner.
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    n = len(coords)
    return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)


def extract_pois(parsed, bbox):
    """Extract the POI_TAG_FILTERS categories within a bounding box.

    Yields the node form of every category and the way (building
    polygon) form of the amenity tags, the categories the Overpass
    query in fetch_pois_from_osm() collects. Each way is reduced to a
    single (lon, lat) via _way_centroid() and emitted as a ``type: way``
    element with a ``center`` field, the shape Overpass returns with
    ``out center;``. The point and the bbox test only approximate
    Overpass, which places a way at its bounding-box center and keeps
    any way with a segment in the bbox; the two differ by meters.

    Returns the same format as fetch_pois_from_osm():
        {"elements": [
            {"type": "node", "id", "lon", "lat", "tags": {}},
            {"type": "way",  "id", "center": {"lon", "lat"}, "tags": {}},
            ...
        ]}

    bbox is [west, south, east, north].
    """
    nodes, ways, _relations = parsed
    west, south, east, north = bbox

    elements = []
    for node_id, (lon, lat, tags) in nodes.items():
        if not (west <= lon <= east and south <= lat <= north):
            continue

        if _poi_match(tags):
            elements.append(
                {
                    "type": "node",
                    "id": node_id,
                    "lon": lon,
                    "lat": lat,
                    "tags": tags,
                }
            )

    # Building-polygon amenities (toilets / drinking water / bicycle
    # repair stations) - common enough in OSM (mappers trace the
    # building rather than placing a node) that ignoring them leaves
    # obvious gaps. Centroid → point, bbox-filter on the centroid (an
    # approximation of Overpass, see the docstring).
    for way_id, way in ways.items():
        tags = way["tags"]
        if not _poi_match(tags, ways_only=True):
            continue
        c = _way_centroid(way, nodes)
        if c is None:
            continue
        lon, lat = c
        if not (west <= lon <= east and south <= lat <= north):
            continue
        elements.append(
            {
                "type": "way",
                "id": way_id,
                "center": {"lon": lon, "lat": lat},
                "tags": tags,
            }
        )

    return {"elements": elements}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse a local .osm file and report what would be extracted (dry-run). "
        "Each relation_id may be a leaf route OR a super-relation, "
        "auto-expanded into its child routes one level deep."
    )
    parser.add_argument("osm_file", help="Path to the .osm XML file")
    parser.add_argument("relation_id", type=int, nargs="+", help="One or more relation IDs")
    args = parser.parse_args()

    osm_path = args.osm_file
    relation_ids = args.relation_id

    console.step(f"Parsing: {osm_path}")
    parsed = parse_osm_file(osm_path)
    nodes, ways, relations = parsed
    console.info(f"Nodes: {len(nodes)}")
    console.info(f"Ways: {len(ways)}")
    console.info(f"Relations: {len(relations)}")

    console.step(f"\nExtracting relations from {relation_ids}:")
    rels, expansions = extract_source_relations(parsed, relation_ids)
    if expansions:
        for parent_id, child_ids in sorted(expansions.items()):
            console.info(f"super-relation {parent_id} → {len(child_ids)} child route(s)")
    if not rels:
        console.error(f"No relations resolved from {relation_ids} in {osm_path}")
        sys.exit(1)
    for rel_id, info in sorted(rels.items(), key=lambda x: x[1]["name"]):
        console.info(f"{info['name']} ({rel_id}) colour={info['colour'] or '(no tag)'}")

    console.step(f"\nExtracting ways for {len(rels)} relations:")
    all_ways = extract_ways(parsed, list(rels.keys()))
    for rel_id, info in sorted(rels.items(), key=lambda x: x[1]["name"]):
        way_count = len(all_ways.get(rel_id, {}))
        console.info(f"{info['name']}: {way_count} ways")
