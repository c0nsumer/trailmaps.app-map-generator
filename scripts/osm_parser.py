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

import xml.etree.ElementTree as ET


def parse_osm_file(osm_path):
    """Parse a .osm XML file into nodes, ways, and relations.

    Returns:
        nodes: dict[int, (lon, lat)]
        ways: dict[int, {"id", "nd_refs", "tags"}]
        relations: dict[int, {"id", "members", "tags"}]
    """
    tree = ET.parse(osm_path)
    root = tree.getroot()

    nodes = {}
    ways = {}
    relations = {}

    for elem in root:
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
                members.append({
                    "type": member.attrib["type"],
                    "ref": int(member.attrib["ref"]),
                    "role": member.attrib.get("role", ""),
                })
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in elem.findall("tag")}
            relations[rel_id] = {
                "id": rel_id,
                "members": members,
                "tags": tags,
            }

    return nodes, ways, relations


def _relation_info(rel):
    """Extract the standard relation info dict from a parsed relation."""
    tags = rel["tags"]
    return {
        "id": rel["id"],
        "name": tags.get("name", f"Route {rel['id']}"),
        "colour": tags.get("colour", "#808080"),
        "ref": tags.get("ref", ""),
        "route": tags.get("route", ""),
        "seasonal": tags.get("seasonal", ""),
    }


def extract_relations(parsed, root_relation_id):
    """Extract route relations starting from a root relation.

    The root may be a super-relation (whose child relations become routes)
    or a single route relation (used directly as the only route when it has
    no child relations as members).

    Returns the same dict format as fetch_member_relations():
        {rel_id: {"id", "name", "colour", "ref", "route", "seasonal"}, ...}
    """
    _nodes, _ways, relations = parsed

    root_rel = relations.get(root_relation_id)
    if not root_rel:
        raise ValueError(
            f"Relation {root_relation_id} not found in .osm file. "
            f"Available relations: {sorted(relations.keys())}"
        )

    result = {}
    for member in root_rel["members"]:
        if member["type"] == "relation":
            child_id = member["ref"]
            child = relations.get(child_id)
            if child:
                result[child_id] = _relation_info(child)

    # Single-relation mode: no child relations found, so the root IS the route.
    if not result:
        result[root_relation_id] = _relation_info(root_rel)

    return result


def extract_extra_relations(parsed, relation_ids):
    """Extract metadata for specific relation IDs.

    Returns the same dict format as fetch_extra_relations():
        {rel_id: {"id", "name", "colour", "ref", "route", "seasonal"}, ...}
    """
    _nodes, _ways, relations = parsed

    result = {}
    for rel_id in relation_ids:
        rel = relations.get(rel_id)
        if rel:
            result[rel_id] = _relation_info(rel)
        else:
            print(f"  WARNING: Relation {rel_id} not found in .osm file")

    return result


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


def extract_guideposts(parsed, bbox):
    """Extract guidepost, emergency access point, and tourism=attraction nodes
    within a bounding box.

    Returns the same format as fetch_pois_from_osm():
        {"elements": [{"type": "node", "id", "lon", "lat", "tags": {}}, ...]}

    bbox is [west, south, east, north].
    """
    nodes, _ways, _relations = parsed
    west, south, east, north = bbox

    elements = []
    for node_id, (lon, lat, tags) in nodes.items():
        if not (west <= lon <= east and south <= lat <= north):
            continue

        is_guidepost = (tags.get("tourism") == "information"
                        and tags.get("information") == "guidepost")
        is_emergency = tags.get("highway") == "emergency_access_point"
        is_feature = tags.get("tourism") == "attraction"

        if is_guidepost or is_emergency or is_feature:
            elements.append({
                "type": "node",
                "id": node_id,
                "lon": lon,
                "lat": lat,
                "tags": tags,
            })

    return {"elements": elements}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <file.osm> <root_relation_id>")
        print(f"  Dry-run: shows what would be extracted from the .osm file")
        sys.exit(1)

    osm_path = sys.argv[1]
    root_id = int(sys.argv[2])

    print(f"Parsing: {osm_path}")
    parsed = parse_osm_file(osm_path)
    nodes, ways, relations = parsed
    print(f"  Nodes: {len(nodes)}")
    print(f"  Ways: {len(ways)}")
    print(f"  Relations: {len(relations)}")

    print(f"\nExtracting relations from root {root_id}:")
    try:
        rels = extract_relations(parsed, root_id)
        for rel_id, info in sorted(rels.items(), key=lambda x: x[1]["name"]):
            print(f"  {info['name']} ({rel_id}) colour={info['colour']}")

        print(f"\nExtracting ways for {len(rels)} relations:")
        all_ways = extract_ways(parsed, list(rels.keys()))
        for rel_id, info in sorted(rels.items(), key=lambda x: x[1]["name"]):
            way_count = len(all_ways.get(rel_id, {}))
            print(f"  {info['name']}: {way_count} ways")
    except ValueError as e:
        print(f"  ERROR: {e}")
