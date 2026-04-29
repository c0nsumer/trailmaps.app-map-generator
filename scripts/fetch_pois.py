#!/usr/bin/env python3
"""Fetch points of interest from OpenStreetMap.

Queries the Overpass API for guideposts within the configured bounding
box, combines with config-specified parking locations (which include
driving direction URLs), and outputs a GeoJSON file.

Parking areas are defined entirely in the YAML config, not fetched from OSM.
"""

import json
import os
import sys

import yaml

from overpass import query as overpass_query


def load_config(config_path):
    """Load YAML config, resolving any ``osm_file`` path relative to the
    config file's directory. Mirrors fetch_trails.load_config so running
    this script standalone matches the full build pipeline."""
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    osm_file = config.get("osm_file")
    if osm_file and isinstance(osm_file, str) and not os.path.isabs(osm_file):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        config["osm_file"] = os.path.join(config_dir, osm_file)
    return config


def fetch_pois_from_osm(bbox, cache_dir=None):
    """Fetch trail-relevant POI nodes from Overpass API.

    Categories collected:
      - guideposts (tourism=information + information=guidepost)
      - emergency access points (highway=emergency_access_point)
      - tourism attractions (tourism=attraction)
      - public toilets (amenity=toilets)
      - drinking water (amenity=drinking_water)

    Each maps to a distinct ``poi_type`` in the output GeoJSON; runtime
    toggle visibility per category.
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    q = f"""
[out:json][timeout:60];
(
  node["tourism"="information"]["information"="guidepost"]({south},{west},{north},{east});
  node["highway"="emergency_access_point"]({south},{west},{north},{east});
  node["tourism"="attraction"]({south},{west},{north},{east});
  node["amenity"="toilets"]({south},{west},{north},{east});
  node["amenity"="drinking_water"]({south},{west},{north},{east});
);
out center;
"""
    return overpass_query(q, cache_dir, label="POIs")


def build_pois_geojson(osm_data, config_parking, config_trailheads):
    """Build GeoJSON from OSM guideposts, emergency access points, and config-defined parking.

    Guideposts and emergency access points are merged into a single
    ``poi_type: "trail_marker"`` category. Nodes tagged as both are
    emitted as a single feature (first tag encountered wins the label
    since the rendering is uniform anyway)."""
    features = []

    # Trail markers (guideposts + emergency access points) from OSM
    for element in osm_data.get("elements", []):
        tags = element.get("tags", {})
        if element["type"] == "node":
            lon, lat = element["lon"], element["lat"]
        elif "center" in element:
            lon, lat = element["center"]["lon"], element["center"]["lat"]
        else:
            continue

        is_emergency = tags.get("highway") == "emergency_access_point"
        is_guidepost = (
            tags.get("tourism") == "information"
            and tags.get("information") == "guidepost"
        )

        if is_emergency or is_guidepost:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "poi_type": "trail_marker",
                    "name": tags.get("name", ""),
                    "ref": tags.get("ref", ""),
                    "ele": tags.get("ele", ""),
                },
            })
        elif tags.get("tourism") == "attraction":
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "poi_type": "feature",
                    "name": tags.get("name", ""),
                    "description": tags.get("description", ""),
                },
            })
        elif tags.get("amenity") == "toilets":
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "poi_type": "toilet",
                    "name": tags.get("name", ""),
                    # OSM `access` tag (yes/no/permissive/private) helps
                    # riders know whether they can actually use it.
                    "access": tags.get("access", ""),
                    # OSM `fee` tag (yes/no) — same reason.
                    "fee": tags.get("fee", ""),
                },
            })
        elif tags.get("amenity") == "drinking_water":
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "poi_type": "drinking_water",
                    "name": tags.get("name", ""),
                    # OSM `seasonal` tag (yes/no/summer/winter) tells
                    # riders whether the fountain is reliably running.
                    "seasonal": tags.get("seasonal", ""),
                },
            })

    # Parking from YAML config
    for parking in config_parking:
        plon, plat = parking["coordinates"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [plon, plat]},
            "properties": {
                "poi_type": "parking",
                "name": parking.get("name", "Parking"),
                "directions_url": parking.get("directions_url", ""),
            },
        })

    # Trailheads from YAML config
    for trailhead in config_trailheads:
        tlon, tlat = trailhead["coordinates"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [tlon, tlat]},
            "properties": {
                "poi_type": "trailhead",
                "name": trailhead.get("name", "Trailhead"),
                "directions_url": trailhead.get("directions_url", ""),
            },
        })

    return {"type": "FeatureCollection", "features": features}


def fetch_pois(config_or_path, output_path, cache_dir="cache"):
    """Main entry point: fetch POIs and write GeoJSON."""
    config = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
    bbox = config["bbox"]
    config_parking = config.get("parking", [])
    config_trailheads = config.get("trailheads", [])

    osm_file = config.get("osm_file")

    if osm_file:
        print(f"Loading POIs for {config['name']} from {osm_file}...")
    else:
        print(f"Fetching POIs for {config['name']}...")
    print(f"  Bbox: {bbox}")

    if osm_file:
        from osm_parser import parse_osm_file, extract_guideposts
        if not os.path.isabs(osm_file):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            osm_file = os.path.join(project_root, osm_file)
        parsed = parse_osm_file(osm_file)
        osm_data = extract_guideposts(parsed, bbox)
    else:
        osm_data = fetch_pois_from_osm(bbox, cache_dir)

    # Count by type — guideposts + emergency access points are merged
    # into a single "markers" bucket.
    marker_count = sum(
        1 for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("highway") == "emergency_access_point"
        or (
            e.get("tags", {}).get("tourism") == "information"
            and e.get("tags", {}).get("information") == "guidepost"
        )
    )
    feature_count = sum(
        1 for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("tourism") == "attraction"
        and e.get("tags", {}).get("highway") != "emergency_access_point"
        and e.get("tags", {}).get("information") != "guidepost"
    )
    toilet_count = sum(
        1 for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("amenity") == "toilets"
    )
    water_count = sum(
        1 for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("amenity") == "drinking_water"
    )
    print(f"  Found {marker_count} trail markers (guideposts + emergency access points) in OSM")
    print(f"  Found {feature_count} features (tourism=attraction) in OSM")
    print(f"  Found {toilet_count} toilets (amenity=toilets) in OSM")
    print(f"  Found {water_count} drinking-water sources (amenity=drinking_water) in OSM")
    print(f"  Config defines {len(config_parking)} parking areas")
    print(f"  Config defines {len(config_trailheads)} trailheads")

    # Warn when show_* is enabled but no data exists for that POI type
    if config.get("show_markers", True) and marker_count == 0:
        print(f"  NOTE: show_markers is enabled but no guideposts or emergency access points found in data")
    if config.get("show_features", True) and feature_count == 0:
        print(f"  NOTE: show_features is enabled but no tourism=attraction nodes found in data")
    if config.get("show_toilets", True) and toilet_count == 0:
        print(f"  NOTE: show_toilets is enabled but no amenity=toilets nodes found in data")
    if config.get("show_drinking_water", True) and water_count == 0:
        print(f"  NOTE: show_drinking_water is enabled but no amenity=drinking_water nodes found in data")
    if config.get("show_parking", True) and len(config_parking) == 0:
        print(f"  NOTE: show_parking is enabled but no parking areas defined in config")
    if config.get("show_trailheads", True) and len(config_trailheads) == 0:
        print(f"  NOTE: show_trailheads is enabled but no trailheads defined in config")

    geojson = build_pois_geojson(osm_data, config_parking, config_trailheads)
    print(f"  Generated {len(geojson['features'])} POI features")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(geojson, f, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    print(f"  Wrote {output_path} ({size_kb:.1f} KB)")
    return geojson


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <config.yaml> [output.geojson] [cache_dir]")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)
    output = sys.argv[2] if len(sys.argv) > 2 else os.path.join("build", config["slug"], "pois.geojson")
    cache = sys.argv[3] if len(sys.argv) > 3 else "cache"

    fetch_pois(config_path, output, cache)
