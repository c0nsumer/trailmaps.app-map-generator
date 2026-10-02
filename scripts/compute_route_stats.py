"""Compute per-route distance at build time.

  show_distance: true   ->  walks the GeoJSON, sums haversine
                            segment lengths per route. No data
                            dependency and no network.

Output: writes ``distance_m`` (integer meters) into
trails_geojson["metadata"]["routes"][<id>]. The runtime reads it and
formats it in the viewer's chosen units. Distance is cheap and not
cached.

Elevation gain and loss are not computed. ``_chain_segments`` stays because tagging_report uses it to decide
whether a route's segments connect.
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


def compute_distances(trails_geojson):
    """Return ``{route_id: distance_m_int}`` for every route in metadata.

    Sums haversine distance along each segment, then sums segment totals
    per route. Cheap (~few thousand sqrt's per typical map). Always
    returns an int (rounded meters); routes with no geometry get 0.
    """
    metadata = trails_geojson.get("metadata") or {}
    routes = metadata.get("routes") or {}
    features = trails_geojson.get("features") or []
    out = {}
    for route_id in routes.keys():
        total = 0.0
        for line in _coords_for_route(features, route_id):
            for (lon1, lat1), (lon2, lat2) in zip(line, line[1:]):
                total += _haversine_m(lon1, lat1, lon2, lat2)
        out[str(route_id)] = round(total)
    return out


def _chain_segments(coord_lines):
    """Orient and join segments whose endpoints touch into continuous
    chains, minimizing the number of segment-break markers downstream.

    OSM relations don't order their member ways, so a closed loop
    arrives as many disconnected pieces in arbitrary order and
    direction.

    Greedy: seed a chain from the first unused segment, then repeatedly
    absorb any unused segment one of whose endpoints coincides with
    either end of the chain (reversing the segment as needed).
    Segments are only ever from ONE route, so a join is always a
    physically walkable continuation - at a junction shared by more
    than two of the route's segments, whichever continuation is
    absorbed first is still real terrain, and the leftover branch
    seeds its own chain. Deterministic: input order drives seeding
    and absorption.

    Endpoint matching is exact to 7 decimals (~1 cm) - segments from
    the same OSM way/node share coordinates bit-for-bit, so this is a
    node-identity check, not a proximity heuristic (two trails passing
    1 m apart must NOT join).

    Returns a new list of chains; input lines are not mutated.
    Degenerate (< 2 point) segments are dropped.
    """

    def _ckey(pt):
        return (round(pt[0], 7), round(pt[1], 7))

    segs = [list(line) for line in coord_lines if len(line) >= 2]
    used = [False] * len(segs)
    chains = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        chain = list(segs[i])
        extended = True
        while extended:
            extended = False
            for j in range(len(segs)):
                if used[j]:
                    continue
                s = segs[j]
                if _ckey(chain[-1]) == _ckey(s[0]):
                    chain.extend(s[1:])
                elif _ckey(chain[-1]) == _ckey(s[-1]):
                    chain.extend(reversed(s[:-1]))
                elif _ckey(chain[0]) == _ckey(s[-1]):
                    chain[:0] = s[:-1]
                elif _ckey(chain[0]) == _ckey(s[0]):
                    chain[:0] = reversed(s[1:])
                else:
                    continue
                used[j] = True
                extended = True
        chains.append(chain)
    return chains


def compute_and_attach(trails_geojson, config):
    """Attach ``distance_m`` to trails_geojson in place when ``show_distance`` is on.

    Returns True if anything changed (caller writes back to disk).

    MUST run on canonical geometry: one feature per route per run of
    way. Geometry that repeats a route's ways inflates its stats.
    """
    routes = trails_geojson.setdefault("metadata", {}).setdefault("routes", {})
    if not routes or not config.get("show_distance"):
        return False

    console.info("computing per-route distance...")
    changed = False
    for rid, dist in compute_distances(trails_geojson).items():
        if rid in routes and routes[rid].get("distance_m") != dist:
            routes[rid]["distance_m"] = dist
            changed = True
    return changed
