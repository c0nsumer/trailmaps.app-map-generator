"""Compute per-route distance at build time.

  show_distance: true   ->  walks the GeoJSON, sums haversine
                            segment lengths per route. No data
                            dependency and no network.

Distance comes from two stages. Every build sums each route's ways
once, by haversine over the cached GeoJSON. The fetch adds
``repeat_m`` to the route's metadata: the length of every extra pass
the relation lists, such as a way ridden out and back. Both loaders
key ways by id, so a repeated member is gone from the GeoJSON, and
only the fetch still sees the member list (see repeat_meters).
``repeat_m`` arrives with the cached trails base, so a reuse build never
recomputes it; a ``TRAILS_BASE_FORMAT`` bump is what re-derives it
(offline, from the cached Overpass responses). An absent field counts
as zero.

Output: writes ``distance_m`` (integer meters) into
trails_geojson["metadata"]["routes"][<id>]. The runtime reads it and
formats it in the viewer's chosen units. Distance is cheap and not
cached.

Elevation gain and loss are not computed.
"""

import console
from geodesy import haversine_m as _haversine_m


def _coords_for_route(features, route_id):
    """Concatenate every segment's coords for one route, in feature order.

    Filters by ``route_id`` only, NOT by ``shared_routes``. When a way
    belongs to several relations, build_geojson emits one feature per
    parent relation, each carrying its own ``route_id``, so the
    ``route_id`` filter already finds every way of a route exactly once.
    Also matching ``shared_routes`` would double-count shared geometry:
    RAMBA's Ranger Loop reported 4.31 mi against 1.74 mi measured in
    JOSM.

    Segments come back in feature order, since a route has no canonical
    traversal through its junctions.

    A way the relation lists more than once still appears once here.
    Its extra passes reach the total through the fetch-time
    ``repeat_m``, not by re-walking features.
    """
    target = str(route_id)
    out = []
    for f in features:
        props = f.get("properties") or {}
        if str(props.get("route_id")) != target:
            continue
        geom = f.get("geometry") or {}
        if geom.get("type") == "LineString":
            out.append(geom.get("coordinates") or [])
    return out


def _line_m(coords):
    """Haversine length of one line. Positions are indexed, not unpacked,
    because a GPX-derived position can carry elevation as a third value."""
    return sum(
        _haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(coords, coords[1:])
    )


def repeat_meters(member_way_ids, ways):
    """Return the meters a relation rides again on ways it lists twice or more.

    ``member_way_ids`` is the relation's ordered way refs, repeats kept;
    ``ways`` is its ``{way_id: {"coords": [[lon, lat], ...]}}`` dict.
    Every appearance of a way beyond its first adds that way's
    haversine length. A ref missing from ``ways`` adds nothing.

    This runs at fetch time because only the fetch has per-way
    geometry: build_geojson fuses consecutive ways into one feature, so
    a single way's length cannot be recovered from the GeoJSON later.
    """
    seen = set()
    total = 0.0
    for wid in member_way_ids:
        if wid not in seen:
            seen.add(wid)
            continue
        coords = (ways.get(wid) or {}).get("coords") or []
        total += _line_m(coords)
    return total


def compute_distances(trails_geojson):
    """Return ``{route_id: distance_m_int}`` for every route in metadata.

    Sums haversine distance along each segment, then sums segment totals
    per route, then adds the route's fetch-time ``repeat_m`` for ways
    the relation rides more than once. Cheap (~few thousand sqrt's per
    typical map). Always returns an int (rounded meters); routes with
    no geometry get 0.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []
    out = {}
    for route_id, info in routes.items():
        total = float((info or {}).get("repeat_m") or 0)
        for line in _coords_for_route(features, route_id):
            total += _line_m(line)
        out[str(route_id)] = round(total)
    return out


def compute_and_attach(trails_geojson, config):
    """Attach ``distance_m`` to trails_geojson in place when ``show_distance`` is on.

    Returns True if anything changed (caller writes back to disk).

    With ``show_distance`` off, any ``distance_m`` already on a route is
    removed, so a rebuild over a base that once carried distances cannot
    ship numbers the map no longer asks for.

    MUST run on canonical geometry: one feature per route per run of
    way. Geometry that repeats a route's ways inflates its stats.
    A way the relation lists more than once enters only through the
    fetch-time ``repeat_m``, which compute_distances adds to the
    once-per-way sum.
    """
    routes = trails_geojson.setdefault("metadata", {}).setdefault("routes", {})
    if not routes:
        return False
    if not config.get("show_distance", True):
        changed = False
        for info in routes.values():
            if isinstance(info, dict) and info.pop("distance_m", None) is not None:
                changed = True
        return changed

    console.detail("computing per-route distance...")
    changed = False
    for rid, dist in compute_distances(trails_geojson).items():
        if rid in routes and routes[rid].get("distance_m") != dist:
            routes[rid]["distance_m"] = dist
            changed = True
    return changed
