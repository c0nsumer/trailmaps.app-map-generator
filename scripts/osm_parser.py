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


def _relation_info(rel):
    """Extract the standard relation info dict from a parsed relation."""
    tags = rel["tags"]
    return {
        "id": rel["id"],
        "name": tags.get("name", f"Route {rel['id']}"),
        # None when OSM has no colour tag — runtime layered fallback:
        # relation_colors → default_trail_color → #808080 build-time default.
        "colour": tags.get("colour"),
        "ref": tags.get("ref", ""),
        "route": tags.get("route", ""),
        "seasonal": tags.get("seasonal", ""),
    }


def extract_source_relations(parsed, relation_ids):
    """Extract route relations for every entry in `relation_ids`.

    Each ID may be a leaf route relation OR a super-relation (a
    relation whose members are themselves type=relation entries — the
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
        resolved: {rel_id: {"id", "name", "colour", "ref", "route",
                            "seasonal"}, ...} — leaf-route entries
                  only (parents replaced by children)
        expansions: {parent_id: [child_id, ...]} for any input IDs
                    that were expanded as super-relations. Empty when
                    every input was a leaf. The caller uses this to
                    propagate seasonal / emergency tagging from the
                    parent's config-keyed bucket to its children.
    """
    _nodes, _ways, relations = parsed

    result = {}
    expansions = {}
    for rel_id in relation_ids:
        rel = relations.get(rel_id)
        if not rel:
            console.warn(f"Relation {rel_id} not found in .osm file")
            continue
        # A super-relation has at least one type=relation member that
        # also exists in the parsed set. (Members pointing at relations
        # outside the .osm file fall through to the leaf path — we
        # can't render what we don't have.)
        child_ids = [
            m["ref"] for m in rel["members"] if m["type"] == "relation" and m["ref"] in relations
        ]
        if child_ids:
            expansions[rel_id] = child_ids
            for cid in child_ids:
                result[cid] = _relation_info(relations[cid])
        else:
            result[rel_id] = _relation_info(rel)

    return result, expansions


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

    Used to give building-shaped POIs (closed ways tagged
    amenity=toilets / drinking_water) a single point location for the
    map. Arithmetic mean is exact for axis-aligned rectangles and a
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


def extract_guideposts(parsed, bbox):
    """Extract trail-relevant POI nodes within a bounding box.

    Despite the legacy name, this also yields tourism=attraction,
    amenity=toilets, and amenity=drinking_water — both the node form
    AND closed-way (building polygon) form for the two amenity tags,
    matching the Overpass query in fetch_pois_from_osm(). Building
    polygons are reduced to a single (lon, lat) via _way_centroid()
    and emitted as ``type: way`` elements with a ``center`` field so
    the output shape matches what Overpass returns with ``out center;``.

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

        is_guidepost = (
            tags.get("tourism") == "information" and tags.get("information") == "guidepost"
        )
        is_emergency = tags.get("highway") == "emergency_access_point"
        is_feature = tags.get("tourism") == "attraction"
        is_toilet = tags.get("amenity") == "toilets"
        is_water = tags.get("amenity") == "drinking_water"

        if is_guidepost or is_emergency or is_feature or is_toilet or is_water:
            elements.append(
                {
                    "type": "node",
                    "id": node_id,
                    "lon": lon,
                    "lat": lat,
                    "tags": tags,
                }
            )

    # Building-polygon toilets / drinking water — common enough in OSM
    # (mappers trace the building rather than placing a node) that
    # ignoring them leaves obvious gaps. Centroid → point, bbox-filter
    # on the centroid (matches Overpass's bbox-on-center semantics).
    for way_id, way in ways.items():
        tags = way["tags"]
        if not (tags.get("amenity") == "toilets" or tags.get("amenity") == "drinking_water"):
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
