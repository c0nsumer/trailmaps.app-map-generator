"""Subway-style parallel-route smoothing.

Eliminates the visible "junction step" artifact that shows up where
routes that share a corridor diverge — without that, route A's line
literally jumps sideways at the junction node when its `shared_routes`
set changes between adjacent segments.

The fix: at each junction where a route's `shared_routes` set changes,
emit a short "transition zone" of N small line features riding ALONG
the trail direction past the junction. Each micro-feature carries its
own constant ``offset_index`` property that linearly interpolates from
the previous corridor's offset position to the new corridor's offset
position. The micro-features render via the same paint-time
``line-offset`` expression the corridors use, so they track the
corridor positions correctly at every zoom level.

This replaces an earlier draft that baked the perpendicular offset
into the geometry of a single bezier-curve stub at z14's pixel scale.
That approach worked at z14 but rendered the stub 5–20× too far from
the corridor at z16–z18 (because corridor offset is paint-time and
scales with zoom, while baked geometry doesn't), producing the
"floating squiggles disconnected from the corridor" artefact most
visible on RAMBA at typical close-in viewing zooms.

Why it has to be N micro-features and not a single stub:
- MapLibre's `line-offset` paint property cannot be data-driven
  per-vertex via line-progress (only line-color and line-pattern
  support that). So the offset for any single feature is a single
  scalar — to interpolate the offset along a trail we need separate
  features each with their own constant offset.

Caveats:
- The transition zone overlays corridor B's first ~10m. Both render
  in that area: the micro-features fade from offset_a to offset_b
  while corridor B's main rendering is at offset_b throughout.
  Visually the eye sees corridor A's line "extending" into the
  junction zone and gradually merging with corridor B. Intentional —
  this IS the smooth merge.
- We do NOT walk past corridor B's first vertex. The transition zone
  is capped to 80% of the first segment's length so we never have
  to traverse multiple OSM ways. Trails with very short first
  segments at junctions get correspondingly shorter transition zones,
  but the structural simplicity is worth it.
- See plan: ~/.claude/plans/smooth-stitching-houghton.md
"""

import math


# Number of micro-features per transition zone. Higher = smoother
# fade but more features in the geojson. 8 is a reasonable balance —
# at z14 each step is ~0.6 px in offset terms, well below the eye's
# resolution.
_TRANSITION_SAMPLES = 8

# Target along-trail length of the transition zone, in meters. We
# cap to a fraction of corridor B's first-segment length so we never
# have to walk past coords[1].
_TRANSITION_LENGTH_M = 10.0
_TRANSITION_FIRST_SEGMENT_FRACTION = 0.8


def _shared_set_signature(shared_routes):
    """Stable hashable signature for a `shared_routes` list."""
    if shared_routes is None:
        return ()
    return tuple(sorted(str(x) for x in shared_routes))


def _natural_key(s):
    """Numeric-aware natural-sort key matching app.js's
    ROUTE_ID_COMPARE: split into runs of digits / non-digits, convert
    digit runs to ints so "1", "2", "10" sort numerically."""
    s = str(s)
    parts = []
    cur = ""
    cur_is_digit = None
    for ch in s:
        if ch.isdigit():
            if cur_is_digit is False:
                parts.append(cur)
                cur = ""
            cur_is_digit = True
        else:
            if cur_is_digit is True:
                parts.append(int(cur))
                cur = ""
            cur_is_digit = False
        cur += ch
    if cur:
        parts.append(int(cur) if cur_is_digit else cur)
    return tuple(parts)


def _offset_index_for_route(route_id, shared_routes):
    """Mirror app.js's `computeOffsetsAndFilter()` math at build time.

    Sorts the shared-routes list with numeric-aware natural ordering,
    finds this route's position, returns the centered offset index.
    """
    if not shared_routes:
        return 0
    sorted_ids = sorted((str(x) for x in shared_routes), key=_natural_key)
    visible_count = len(sorted_ids)
    if visible_count <= 1:
        return 0
    rid = str(route_id)
    if rid not in sorted_ids:
        return 0
    position = sorted_ids.index(rid)
    return position - (visible_count - 1) / 2


METERS_PER_DEGREE_LAT = 111320.0


def _euclidean_meters(p_a, p_b):
    """Distance in meters between two [lon, lat] points."""
    lat_for_scale = (p_a[1] + p_b[1]) / 2
    cos_lat = math.cos(math.radians(lat_for_scale))
    dx_m = (p_b[0] - p_a[0]) * cos_lat * METERS_PER_DEGREE_LAT
    dy_m = (p_b[1] - p_a[1]) * METERS_PER_DEGREE_LAT
    return math.sqrt(dx_m * dx_m + dy_m * dy_m)


def _interp_along_segment(p_a, p_b, fraction):
    """Linear interpolation between two [lon, lat] points. fraction
    in [0, 1]: 0 returns p_a, 1 returns p_b."""
    return [
        p_a[0] + (p_b[0] - p_a[0]) * fraction,
        p_a[1] + (p_b[1] - p_a[1]) * fraction,
    ]


def _coord_key(coord, precision=7):
    """Hashable key for a [lon, lat] coordinate. Round to ~1 cm to
    absorb float-equality wobble at junction nodes."""
    return (round(coord[0], precision), round(coord[1], precision))


def _tangent_meters_into_junction(coords, end_kind):
    """Unit tangent at a feature's junction-touching end, expressed
    in meters and pointing TOWARD the junction. Used by the
    continuation-pair filter."""
    if len(coords) < 2:
        return (0.0, 0.0)
    if end_kind == "end":
        p_from, p_to = coords[-2], coords[-1]
    else:
        # For a "start" endpoint, reverse so the vector points INTO
        # the junction.
        p_from, p_to = coords[1], coords[0]
    lat_for_scale = (p_from[1] + p_to[1]) / 2
    cos_lat = math.cos(math.radians(lat_for_scale))
    dx_m = (p_to[0] - p_from[0]) * cos_lat * METERS_PER_DEGREE_LAT
    dy_m = (p_to[1] - p_from[1]) * METERS_PER_DEGREE_LAT
    length = math.sqrt(dx_m * dx_m + dy_m * dy_m)
    if length == 0:
        return (0.0, 0.0)
    return (dx_m / length, dy_m / length)


def _is_continuation_pair(coords_a, end_a, coords_b, end_b):
    """True iff the two endpoints meet at a junction with a small
    enough bend that it plausibly represents the same route
    continuing through the junction (rather than a perpendicular
    branch / X-crossing).

    Both tangents point INTO the junction, so a true continuation
    has them pointing in OPPOSING directions — dot product near -1.
    A perpendicular branch has dot ~0; a U-turn has dot ~+1.
    Threshold: dot < 0 catches anything bent up to 90°."""
    t_a = _tangent_meters_into_junction(coords_a, end_a)
    t_b = _tangent_meters_into_junction(coords_b, end_b)
    if t_a == (0, 0) or t_b == (0, 0):
        return False
    dot = t_a[0] * t_b[0] + t_a[1] * t_b[1]
    return dot < 0


def apply_subway_style(trails_geojson):
    """Inject transition-zone micro-features into trails.geojson in
    place. Each junction where a route's `shared_routes` set changes
    gets a fade of N=_TRANSITION_SAMPLES small features riding along
    the new corridor's first ~10 m, with `offset_index` interpolated
    from the previous corridor's value to the new one's. The features
    use the runtime's existing paint-time `line-offset` expression,
    so they track the corridor positions correctly at every zoom.

    Idempotent: caller must strip prior `isStub: true` features
    before calling for re-runs.

    Mutates `trails_geojson["features"]` and returns the number of
    micro-features added (not the number of transitions).

    Pre-condition: features have already been per-route-stitched by
    `merge_consecutive_ways` in fetch_trails.py — each feature
    represents one corridor of constant `shared_routes` set within
    its route.
    """
    features = trails_geojson.get("features") or []

    # Group features by route_id so junctions can be analysed
    # per-route (a junction "in route X" only cares about X's
    # adjacent features there, not other routes that pass through
    # the same node).
    by_route = {}
    for feat in features:
        if feat.get("geometry", {}).get("type") != "LineString":
            continue
        rid = str(feat.get("properties", {}).get("route_id", ""))
        if not rid:
            continue
        by_route.setdefault(rid, []).append(feat)

    new_micro_features = []

    for rid, route_features in by_route.items():
        # Index endpoints by node coordinate so we can find pairs
        # of features that meet at the same junction.
        node_to_endpoints = {}
        for idx, feat in enumerate(route_features):
            coords = feat["geometry"]["coordinates"]
            if len(coords) < 2:
                continue
            node_to_endpoints.setdefault(_coord_key(coords[0]), []).append(
                (idx, "start"))
            node_to_endpoints.setdefault(_coord_key(coords[-1]), []).append(
                (idx, "end"))

        for node_key, endpoints in node_to_endpoints.items():
            if len(endpoints) < 2:
                continue
            for i in range(len(endpoints)):
                for j in range(i + 1, len(endpoints)):
                    f_idx_a, end_a = endpoints[i]
                    f_idx_b, end_b = endpoints[j]
                    feat_a = route_features[f_idx_a]
                    feat_b = route_features[f_idx_b]

                    sig_a = _shared_set_signature(
                        feat_a["properties"].get("shared_routes"))
                    sig_b = _shared_set_signature(
                        feat_b["properties"].get("shared_routes"))
                    if sig_a == sig_b:
                        continue

                    coords_a = feat_a["geometry"]["coordinates"]
                    coords_b = feat_b["geometry"]["coordinates"]
                    if not _is_continuation_pair(
                            coords_a, end_a, coords_b, end_b):
                        continue

                    offset_a = _offset_index_for_route(rid, sig_a)
                    offset_b = _offset_index_for_route(rid, sig_b)
                    if offset_a == offset_b:
                        continue

                    # Pick the "host" side for the transition zone.
                    # Prefer to host on whichever side has a "start"
                    # endpoint (so we ride along its outgoing
                    # geometry). If both ends are "end" or both
                    # are "start", arbitrarily pick the first.
                    if end_b == "start":
                        host_coords = coords_b
                        host_offset_value = offset_b
                        opposite_offset_value = offset_a
                        # Forward direction = into corridor B from
                        # the junction. coords_b[0] is the junction,
                        # coords_b[1] is the next vertex along B.
                        p_junction = coords_b[0]
                        p_next = coords_b[1]
                    elif end_a == "start":
                        host_coords = coords_a
                        host_offset_value = offset_a
                        opposite_offset_value = offset_b
                        p_junction = coords_a[0]
                        p_next = coords_a[1]
                    else:
                        # Both are "end" — neither corridor heads
                        # away from the junction, so there's no
                        # natural "outgoing" direction to ride along.
                        # Skip.
                        continue

                    # Determine the transition zone span. We don't
                    # walk past p_next (the host's first vertex), so
                    # cap the span to a fraction of that segment's
                    # length.
                    seg_len_m = _euclidean_meters(p_junction, p_next)
                    span_m = min(
                        _TRANSITION_LENGTH_M,
                        seg_len_m * _TRANSITION_FIRST_SEGMENT_FRACTION,
                    )
                    if span_m <= 0.5:
                        # Host's first segment is too short to bother.
                        # Skipping is safer than emitting a sub-meter
                        # transition that won't read visually anyway.
                        continue

                    # The "fade" goes from opposite_offset_value (at
                    # the junction, matching the corridor on the
                    # OTHER side of the junction) to host_offset_value
                    # (at the end of the transition zone, matching
                    # the host corridor's main offset). When the host
                    # side is end_b == "start", the rider going along
                    # the route from A to B sees A's offset position
                    # at the junction, then a smooth fade to B's
                    # offset over the next ~10 m.

                    # Sample N+1 points along the transition zone.
                    # Each adjacent pair becomes one micro-feature
                    # with an interpolated offset_index (midpoint
                    # interpolation of the segment's t range).
                    fraction_max = span_m / seg_len_m
                    # NB: use `k` not `i` here — the outer pair-iteration
                    # uses `i` and a regular for-loop with `for i` would
                    # shadow it (list comprehensions have their own scope
                    # in py3 so the comprehension below is fine, but the
                    # following loop is NOT a comprehension).
                    points = [
                        _interp_along_segment(
                            p_junction, p_next,
                            (k / _TRANSITION_SAMPLES) * fraction_max)
                        for k in range(_TRANSITION_SAMPLES + 1)
                    ]
                    for k in range(_TRANSITION_SAMPLES):
                        # Segment k runs from points[k] to points[k+1].
                        # Its midpoint t (in [0, 1]) controls its
                        # interpolated offset.
                        t_mid = (k + 0.5) / _TRANSITION_SAMPLES
                        offset_idx = (
                            opposite_offset_value
                            + (host_offset_value - opposite_offset_value)
                            * t_mid
                        )
                        micro = {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [points[k], points[k + 1]],
                            },
                            "properties": {
                                "route_id": rid,
                                "route_name": feat_a["properties"].get(
                                    "route_name", ""),
                                "route_colour": feat_a["properties"].get(
                                    "route_colour", ""),
                                "route_ref": feat_a["properties"].get(
                                    "route_ref", ""),
                                # `shared_routes` set to a single-route
                                # list satisfies app.js's downstream
                                # consumers (rebuildFinderList etc.)
                                # without putting the micro-feature
                                # into anyone's "shared corridor" sort.
                                "shared_routes": [rid],
                                "trail_name": "",
                                "imba_difficulty": "",
                                "oneway": "",
                                # Direct offset_index — app.js's
                                # computeOffsetsAndFilter honours
                                # the property when isStub is true
                                # instead of recomputing from
                                # shared_routes.
                                "offset_index": offset_idx,
                                "isStub": True,
                            },
                        }
                        new_micro_features.append(micro)

    if new_micro_features:
        features.extend(new_micro_features)
        trails_geojson["features"] = features
    return len(new_micro_features)
