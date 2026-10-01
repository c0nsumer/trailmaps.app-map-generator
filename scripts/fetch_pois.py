#!/usr/bin/env python3
"""Fetch points of interest from OpenStreetMap.

Queries the Overpass API for six POI categories within the configured
bounding box (guideposts, emergency access points, attractions, toilets,
drinking water, bicycle repair stations - see fetch_pois_from_osm),
merges in the config-specified POIs (parking, trailheads, hubs,
event-mode POIs), and outputs a GeoJSON file.

Parking areas are defined entirely in the YAML config, not fetched from OSM.

Internal build sub-stage: build.py imports and calls fetch_pois() directly;
the ``__main__`` CLI exists only for standalone debugging and ad-hoc data
refreshes.
"""

import json
import os

import cli
import console
from config_io import load_config_for_fetch
from geodesy import haversine_m
from osm_parser import POI_TAG_FILTERS
from overpass import query as overpass_query

# Every show_* flag that gates a POI category in build_pois_geojson.
# build.py iterates this same tuple for its skip-the-fetch gate and the
# dry-run summary, so a POI type added here is reflected there.
POI_SHOW_FLAGS = (
    "show_markers",
    "show_features",
    "show_parking",
    "show_trailheads",
    "show_hubs",
    "show_toilets",
    "show_drinking_water",
    "show_bicycle_repair_stations",
)


def fetch_pois_from_osm(bbox, cache_dir=None, refresh=False):
    """Fetch trail-relevant POI nodes from Overpass API.

    Categories collected:
      - guideposts (tourism=information + information=guidepost)
      - emergency access points (highway=emergency_access_point)
      - tourism attractions (tourism=attraction)
      - public toilets (amenity=toilets)
      - drinking water (amenity=drinking_water)
      - bicycle repair stations (amenity=bicycle_repair_station)

    Each maps to a distinct ``poi_type`` in the output GeoJSON; runtime
    toggle visibility per category.

    The amenity categories are queried as both nodes AND ways - OSM
    mappers commonly tag the amenity's building polygon (a closed
    way) rather than placing a node. ``out center;`` asks Overpass
    to compute the centroid of any non-node geometry so the rest of
    the pipeline can treat them as point POIs.
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    # The query text is the cache key: keep the line order stable.
    statements = []
    for pairs, as_way in POI_TAG_FILTERS:
        tag_filter = "".join(f'["{k}"="{v}"]' for k, v in pairs)
        for element_type in ("node", "way") if as_way else ("node",):
            statements.append(f"  {element_type}{tag_filter}({south},{west},{north},{east});\n")
    q = f"""
[out:json][timeout:60];
(
{"".join(statements)});
out center;
"""
    return overpass_query(q, cache_dir, label="POIs", refresh=refresh)


def _dedup_osm_pois(features):
    """Collapse OSM-derived POIs of the same type within ~10m of each
    other into a single feature.

    OSM often tags one amenity twice: a way (the building footprint,
    whose Overpass center we use) and a node at the entrance. Overpass
    returns both, so without dedup the search overlay reports a doubled
    count while only one marker is visible, the second stacking on the
    first.

    Two POIs are duplicates iff they share a poi_type, the type is one of
    the building-footprint amenities (toilet / drinking_water /
    bicycle_repair_station), and they lie within 10m. Other types never
    collapse: distinct guideposts stand <10m apart at junction clusters,
    and merging dropped their ref numbers. The survivor inherits the
    first non-empty value of each property, so a tagged-once name
    survives its untagged twin.

    10m catches the center-vs-node offset (typically 0-5m) without
    merging distinct facilities (two outhouses at a trailhead are
    usually 15m+ apart). The O(n²) cost is negligible at 5-50 POIs per
    type.

    Logs a one-line count of collapsed duplicates.
    """
    DEDUP_M = 10.0
    # Everything else (trail_marker, feature) can legitimately have
    # distinct instances <10m apart.
    DEDUP_TYPES = {"toilet", "drinking_water", "bicycle_repair_station"}
    out = []
    collapsed = 0
    for f in features:
        ptype = f["properties"].get("poi_type")
        lng, lat = f["geometry"]["coordinates"]
        merged_into = None
        if ptype not in DEDUP_TYPES:
            out.append(f)
            continue
        for existing in out:
            if existing["properties"].get("poi_type") != ptype:
                continue
            elng, elat = existing["geometry"]["coordinates"]
            if haversine_m(lng, lat, elng, elat) <= DEDUP_M:
                merged_into = existing
                break
        if merged_into is not None:
            for k, v in f["properties"].items():
                if v and not merged_into["properties"].get(k):
                    merged_into["properties"][k] = v
            collapsed += 1
        else:
            out.append(f)
    if collapsed:
        console.info(
            f"Collapsed {collapsed} duplicate OSM POI(s) within "
            f"{DEDUP_M:.0f}m (typical pattern: amenity tagged on both "
            f"a building way and an entrance node)."
        )
    return out


def build_pois_geojson(
    osm_data,
    config_parking,
    config_trailheads,
    config_hubs=None,
    config_event_pois=None,
    config=None,
):
    """Build GeoJSON from OSM guideposts, emergency access points, and config-defined parking.

    Guideposts and emergency access points are merged into a single
    ``poi_type: "trail_marker"`` category. Nodes tagged as both are
    emitted as a single feature (first tag encountered wins the label
    since the rendering is uniform anyway).

    config_hubs: optional list of curator-supplied trail-hub waypoints
    (named on-trail intersections riders use as wayfinding landmarks).
    Same shape as ``config_trailheads`` but rendered as a separate POI
    type - see runtime ``addHubMarkers`` for the visual treatment.

    config: optional reference to the full per-map config dict. When
    provided, show_* flags gate each POI type at the GeoJSON-emit step
    - a type with show_X: false is excluded from the output entirely,
    so it doesn't appear in counts, search index, or anywhere else
    downstream (not just suppressed from rendering). Backwards-
    compatible default of None means "show everything," matching the
    historical behavior for any caller not yet passing config."""
    cfg = config or {}
    show_markers = cfg.get("show_markers", True)
    show_features = cfg.get("show_features", True)
    show_toilets = cfg.get("show_toilets", True)
    show_drinking_water = cfg.get("show_drinking_water", True)
    show_bicycle_repair_stations = cfg.get("show_bicycle_repair_stations", True)
    show_parking = cfg.get("show_parking", True)
    show_trailheads = cfg.get("show_trailheads", True)
    show_hubs = cfg.get("show_hubs", True)

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
            tags.get("tourism") == "information" and tags.get("information") == "guidepost"
        )

        if is_emergency or is_guidepost:
            if not show_markers:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "poi_type": "trail_marker",
                        "name": tags.get("name", ""),
                        "ref": tags.get("ref", ""),
                    },
                }
            )
        elif tags.get("tourism") == "attraction":
            if not show_features:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "poi_type": "feature",
                        "name": tags.get("name", ""),
                        "description": tags.get("description", ""),
                    },
                }
            )
        elif tags.get("amenity") == "toilets":
            if not show_toilets:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "poi_type": "toilet",
                        "name": tags.get("name", ""),
                    },
                }
            )
        elif tags.get("amenity") == "drinking_water":
            if not show_drinking_water:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "poi_type": "drinking_water",
                        "name": tags.get("name", ""),
                    },
                }
            )
        elif tags.get("amenity") == "bicycle_repair_station":
            if not show_bicycle_repair_stations:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "poi_type": "bicycle_repair_station",
                        "name": tags.get("name", ""),
                    },
                }
            )

    # See _dedup_osm_pois. Curator-supplied POIs are added below this
    # pass; they're hand-entered and don't need it.
    features = _dedup_osm_pois(features)

    # Parking from YAML config
    if show_parking:
        for parking in config_parking:
            plon, plat = parking["coordinates"]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [plon, plat]},
                    "properties": {
                        "poi_type": "parking",
                        "name": parking.get("name", "Parking"),
                        "directions_url": parking.get("directions_url", ""),
                    },
                }
            )

    # Trailheads from YAML config
    if show_trailheads:
        for trailhead in config_trailheads:
            tlon, tlat = trailhead["coordinates"]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [tlon, tlat]},
                    "properties": {
                        "poi_type": "trailhead",
                        "name": trailhead.get("name", "Trailhead"),
                        "directions_url": trailhead.get("directions_url", ""),
                    },
                }
            )

    # Trail hubs from YAML config - named on-trail intersections riders
    # use as wayfinding landmarks ("meet me at Bottle Junction"). Distinct
    # POI type from Trailheads because riders can't drive to them: the
    # runtime renders them with the name inline (no popup, no directions
    # link) so the marker IS the signal at a glance.
    if show_hubs:
        for hub in config_hubs or []:
            hlon, hlat = hub["coordinates"]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [hlon, hlat]},
                    "properties": {
                        "poi_type": "hub",
                        "name": hub.get("name", "Hub"),
                    },
                }
            )

    # Event POIs from event_mode.pois (always-on at runtime; no toggle).
    # Used for race-day fixtures: start / finish, aid stations, support
    # vehicles, etc. Distinct from OSM POIs in that they never get
    # proximity-filtered (they're race fixtures, not bbox-incidental).
    for ep in config_event_pois or []:
        elon, elat = ep["coordinates"]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [elon, elat]},
                "properties": {
                    "poi_type": "event",
                    "name": ep.get("name", "Event"),
                    "description": ep.get("description", ""),
                    # Whether the popup offers "Get Directions". Off
                    # unless the curator asks: see validate_config.
                    "directions": ep.get("directions") is True,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def fetch_pois(config_or_path, output_path, cache_dir="cache", refresh=False):
    """Main entry point: fetch POIs and write GeoJSON.

    ``refresh=True`` (build.py --refresh / --refresh-pois) bypasses cached Overpass
    responses for this map's queries without touching the shared
    cache directory's other entries.
    """
    config = config_or_path if isinstance(config_or_path, dict) else load_config_for_fetch(config_or_path)
    bbox = config["bbox"]
    config_parking = config.get("parking", [])
    config_trailheads = config.get("trailheads", [])
    config_hubs = config.get("hubs", [])
    # event_mode.pois are always-on, not gated by show_*; if event mode
    # is absent the list is just empty and contributes nothing.
    config_event_pois = (config.get("event_mode") or {}).get("pois") or []

    osm_file = config.get("osm_file")

    if osm_file:
        console.step(f"Loading POIs for {config['name']} from {osm_file}...")
    else:
        console.step(f"Fetching POIs for {config['name']}...")
    console.info(f"Bbox: {bbox}")

    if osm_file:
        from osm_parser import extract_pois, parse_osm_file

        if not os.path.isabs(osm_file):
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            osm_file = os.path.join(project_root, osm_file)
        parsed = parse_osm_file(osm_file)
        osm_data = extract_pois(parsed, bbox)
    else:
        osm_data = fetch_pois_from_osm(bbox, cache_dir, refresh=refresh)

    # Count by type - guideposts + emergency access points are merged
    # into a single "markers" bucket.
    marker_count = sum(
        1
        for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("highway") == "emergency_access_point"
        or (
            e.get("tags", {}).get("tourism") == "information"
            and e.get("tags", {}).get("information") == "guidepost"
        )
    )
    feature_count = sum(
        1
        for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("tourism") == "attraction"
        and e.get("tags", {}).get("highway") != "emergency_access_point"
        and e.get("tags", {}).get("information") != "guidepost"
    )
    toilet_count = sum(
        1 for e in osm_data.get("elements", []) if e.get("tags", {}).get("amenity") == "toilets"
    )
    water_count = sum(
        1
        for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("amenity") == "drinking_water"
    )
    repair_count = sum(
        1
        for e in osm_data.get("elements", [])
        if e.get("tags", {}).get("amenity") == "bicycle_repair_station"
    )
    console.info(
        f"Found {marker_count} trail markers (guideposts + emergency access points) in OSM"
    )
    console.info(f"Found {feature_count} features (tourism=attraction) in OSM")
    console.info(f"Found {toilet_count} toilets (amenity=toilets) in OSM")
    console.info(f"Found {water_count} drinking-water sources (amenity=drinking_water) in OSM")
    console.info(
        f"Found {repair_count} bicycle repair stations (amenity=bicycle_repair_station) in OSM"
    )
    console.info(f"Config defines {len(config_parking)} parking areas")
    console.info(f"Config defines {len(config_trailheads)} trailheads")
    console.info(f"Config defines {len(config_hubs)} trail hubs")
    if config_event_pois:
        console.info(f"event_mode defines {len(config_event_pois)} event POI(s)")

    # Warn when show_* is enabled but no data exists for that POI type
    if config.get("show_markers", True) and marker_count == 0:
        console.note(
            "show_markers is enabled but no guideposts or emergency access points found in data"
        )
    if config.get("show_features", True) and feature_count == 0:
        console.note("show_features is enabled but no tourism=attraction nodes found in data")
    if config.get("show_toilets", True) and toilet_count == 0:
        console.note("show_toilets is enabled but no amenity=toilets nodes or ways found in data")
    if config.get("show_drinking_water", True) and water_count == 0:
        console.note(
            "show_drinking_water is enabled but no amenity=drinking_water nodes or ways found in data"
        )
    if config.get("show_bicycle_repair_stations", True) and repair_count == 0:
        console.note(
            "show_bicycle_repair_stations is enabled but no amenity=bicycle_repair_station "
            "nodes or ways found in data"
        )
    if config.get("show_parking", True) and len(config_parking) == 0:
        console.note("show_parking is enabled but no parking areas defined in config")
    if config.get("show_trailheads", True) and len(config_trailheads) == 0:
        console.note("show_trailheads is enabled but no trailheads defined in config")
    if config.get("show_hubs", True) and len(config_hubs) == 0:
        console.note("show_hubs is enabled but no hubs defined in config")

    geojson = build_pois_geojson(
        osm_data,
        config_parking,
        config_trailheads,
        config_hubs=config_hubs,
        config_event_pois=config_event_pois,
        config=config,
    )
    console.info(f"Generated {len(geojson['features'])} POI features")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) / 1024
    console.info(f"Wrote {output_path} ({size_kb:.1f} KB)")
    return geojson


if __name__ == "__main__":
    parser = cli.config_output_parser("Fetch points of interest from OpenStreetMap via Overpass.")
    parser.add_argument(
        "--cache-dir", default="cache", help="Cache directory (default: cache)"
    )
    args = parser.parse_args()

    config = load_config_for_fetch(args.config)
    output = args.output or os.path.join("build", config["slug"], "pois.geojson")
    fetch_pois(config, output, args.cache_dir)
