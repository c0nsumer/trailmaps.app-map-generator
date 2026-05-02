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

To prevent the corridor B's main rendering from doubling-up with the
transition zone, the host corridor's first vertex is replaced with
the transition zone's endpoint. The micro-features REPLACE that
~10 m section of B's geometry instead of overlaying it. The original
first vertex is stashed in `_subwayOriginalCoord0` so re-runs
(idempotency) and toggle-off restoration both work cleanly — see
the restore step in build.py's _enrich_trails_geojson.

Caveats:
- We do NOT walk past corridor B's first vertex. The transition zone
  is capped to 80% of the first segment's length so we never have
  to traverse multiple OSM ways. Trails with very short first
  segments at junctions get correspondingly shorter transition zones,
  but the structural simplicity is worth it.
- See plan: ~/.claude/plans/smooth-stitching-houghton.md
"""

import math


# Number of micro-features per transition zone. Higher = smoother
# fade AND tighter tangent matching at the endpoints (which is what
# eliminates the "spike" artifact at corner junctions — see notes
# inside apply_subway_style). At N=16, the first/last micro-feature
# segment direction is within ~4° of corridor A / B's tangent for a
# 90° corner; at N=8 it's ~9°. Worth the doubled feature count.
_TRANSITION_SAMPLES = 16

# Target along-trail length of the transition zone, in meters. We
# cap to a fraction of corridor B's first-segment length so we never
# have to walk past coords[1].
_TRANSITION_LENGTH_M = 10.0
_TRANSITION_FIRST_SEGMENT_FRACTION = 0.8

# ---- Sharp-corner smoothing (Pass 1 of apply_subway_style) -----------
#
# OSM way geometry routinely has sharp internal vertices (45°-90°+
# bends) where two ways meet at a bend node. MapLibre's `line-offset`
# paint expression offsets each rendered segment INDEPENDENTLY by
# `perp_local * offset_index`, so at a sharp internal vertex the
# adjacent offset segments don't meet up — visible "tab" / "kink"
# artifact for any route with non-zero offset_index. The artifact is
# especially obvious in multi-route corridors where 4+ parallel
# routes all show the same kink at the same vertex.
#
# Fix: replace each sharp internal vertex with a smooth cubic-bezier
# arc that tucks back ~3m on each side. The corridor's centerline
# now curves smoothly through the corner; line-offset rendering on
# the smooth curve produces clean parallel routes with no tabs.
#
# Parameters chosen for idempotency: each smoothed arc has bend
# angles per pair of adjacent vertices below the threshold, so a
# second smoothing pass is a no-op. Re-running build doesn't compound.

# Bends sharper than this (in degrees) get smoothed. Below this,
# we leave the OSM geometry alone (its slight kinks are invisible
# at typical zoom + line widths).
_SHARP_BEND_THRESHOLD_DEG = 25.0

# How far back from the sharp vertex (in meters) we tuck the
# smoothing arc on each side. Larger = smoother visual but larger
# trail-shape distortion. 3m is a good balance — at z16 that's
# ~12px, easily visible as smoothing but small relative to corridor
# length. Capped per-vertex to 45% of each adjacent segment length
# so adjacent smoothed vertices never overlap.
_SMOOTH_TUCK_M = 3.0

# Number of intermediate vertices in each smoothed arc. With 6
# samples through a 90° bend, each pair of adjacent arc vertices
# has a bend angle of 90/5 = 18° — well below the threshold, so
# re-smoothing is a no-op (idempotent).
_SMOOTH_ARC_SAMPLES = 6


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


def _meters_unit_to_degree_delta(unit_meters, magnitude_meters, latitude):
    """Convert a unit vector in METER space + a meter-magnitude into
    a (dlon, dlat) delta in degree space at the given latitude. Used
    to convert tangent vectors (unit meters) into geographic offsets
    we can add to a [lon, lat] coordinate."""
    cos_lat = math.cos(math.radians(latitude))
    deg_per_m_lon = 1.0 / (cos_lat * METERS_PER_DEGREE_LAT) if cos_lat else 0.0
    deg_per_m_lat = 1.0 / METERS_PER_DEGREE_LAT
    return (
        unit_meters[0] * magnitude_meters * deg_per_m_lon,
        unit_meters[1] * magnitude_meters * deg_per_m_lat,
    )


def _sample_cubic_bezier(p0, p1, p2, p3, n_samples):
    """Sample n_samples evenly-spaced points along a cubic bezier from
    p0 to p3 with control points p1 and p2. All points are [lon, lat]
    pairs (or any consistent 2D coordinate system). t spans [0, 1]
    inclusive."""
    samples = []
    for i in range(n_samples):
        t = (i / (n_samples - 1)) if n_samples > 1 else 0.0
        omt = 1 - t
        omt2 = omt * omt
        omt3 = omt2 * omt
        t2 = t * t
        t3 = t2 * t
        x = (omt3 * p0[0]
             + 3 * omt2 * t * p1[0]
             + 3 * omt * t2 * p2[0]
             + t3 * p3[0])
        y = (omt3 * p0[1]
             + 3 * omt2 * t * p1[1]
             + 3 * omt * t2 * p2[1]
             + t3 * p3[1])
        samples.append([x, y])
    return samples


# Cubic-bezier handle length as a fraction of the transition span.
# Larger values = the curve hugs the tangent direction longer at
# each end before turning toward the other side. 0.55 is the
# canonical value for approximating a quarter-circle with a bezier;
# we use 0.5 — slightly tighter — to keep the curve from overshooting
# on shallow bends while still visibly arcing on sharp corners.
_BEZIER_HANDLE_FRACTION = 0.5


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


def _smooth_sharp_corners(coords):
    """Replace sharp internal vertices with smooth cubic-bezier arcs.

    For each internal vertex with a bend angle exceeding
    _SHARP_BEND_THRESHOLD_DEG, replace the single vertex with N+1
    points sampled along a cubic bezier whose endpoints sit
    _SMOOTH_TUCK_M back from the sharp vertex on each adjacent
    segment. The bezier's tangent at each endpoint matches the
    adjacent segment direction, so the smoothed corridor flows
    continuously through where the sharp vertex used to be.

    Tuck distance is capped per-vertex to 45% of each adjacent
    segment length so smoothing at adjacent sharp vertices never
    overlaps (worst case: tuck_a + tuck_b = 0.9 * seg_len, leaving
    a 10% gap of original geometry between two smoothed corners).

    Returns a new coords list (does not mutate input). Endpoint
    coordinates (coords[0], coords[-1]) are always preserved
    unchanged so corridor-set-changing junction nodes still align
    across features for downstream subway-transition logic.
    """
    n = len(coords)
    if n < 3:
        return [list(c) for c in coords]

    # Pre-compute per-segment lengths (in meters) so we can both
    # detect degenerate zero-length segments and cap tuck distances.
    seg_lens = []
    for i in range(n - 1):
        seg_lens.append(_euclidean_meters(coords[i], coords[i + 1]))

    # info[i] is None for non-sharp vertices, or a list of replacement
    # arc coordinates (length = _SMOOTH_ARC_SAMPLES + 1) for sharp ones.
    arcs = [None] * n

    for i in range(1, n - 1):
        l_in = seg_lens[i - 1]
        l_out = seg_lens[i]
        if l_in == 0 or l_out == 0:
            continue

        v_prev = coords[i - 1]
        v_curr = coords[i]
        v_next = coords[i + 1]

        # All vector math in METER space at the local latitude.
        lat_for_scale = v_curr[1]
        cos_lat = math.cos(math.radians(lat_for_scale))
        dx_in = (v_curr[0] - v_prev[0]) * cos_lat * METERS_PER_DEGREE_LAT
        dy_in = (v_curr[1] - v_prev[1]) * METERS_PER_DEGREE_LAT
        dx_out = (v_next[0] - v_curr[0]) * cos_lat * METERS_PER_DEGREE_LAT
        dy_out = (v_next[1] - v_curr[1]) * METERS_PER_DEGREE_LAT

        # Bend angle: dot of unit-tangent-into-vertex with
        # unit-tangent-out-of-vertex. dot=+1 means straight (no bend),
        # dot=0 means 90° bend, dot=-1 means 180° U-turn.
        dot = (dx_in * dx_out + dy_in * dy_out) / (l_in * l_out)
        dot = max(-1.0, min(1.0, dot))
        bend_deg = math.degrees(math.acos(dot))

        if bend_deg <= _SHARP_BEND_THRESHOLD_DEG:
            continue

        # Tuck distance: capped at 45% of each adjacent segment so
        # smoothing arcs at adjacent sharp corners never overlap.
        tuck_m = min(_SMOOTH_TUCK_M, l_in * 0.45, l_out * 0.45)
        if tuck_m < 0.3:
            # Sub-30cm tuck wouldn't read as smoothing anyway —
            # leave the vertex alone.
            continue

        # Unit tangents in meters.
        unit_in_m = (dx_in / l_in, dy_in / l_in)
        unit_out_m = (dx_out / l_out, dy_out / l_out)
        deg_per_m_lon = 1.0 / (cos_lat * METERS_PER_DEGREE_LAT)
        deg_per_m_lat = 1.0 / METERS_PER_DEGREE_LAT

        # Tuck endpoints — on the line from v_prev to v_curr (resp.
        # v_curr to v_next), tuck_m back from v_curr.
        p_in = [v_curr[0] - unit_in_m[0] * tuck_m * deg_per_m_lon,
                v_curr[1] - unit_in_m[1] * tuck_m * deg_per_m_lat]
        p_out = [v_curr[0] + unit_out_m[0] * tuck_m * deg_per_m_lon,
                 v_curr[1] + unit_out_m[1] * tuck_m * deg_per_m_lat]

        # Bezier control points. P0 = p_in tangent unit_in_m, P3 =
        # p_out tangent unit_out_m. Handle length 0.55 * tuck_m is
        # the canonical quarter-circle approximation; for our use
        # case (smoothing arbitrary bend angles) it gives the curve
        # enough room to round the corner without overshooting.
        handle_m = tuck_m * 0.55
        bp0 = p_in
        bp1 = [p_in[0] + unit_in_m[0] * handle_m * deg_per_m_lon,
               p_in[1] + unit_in_m[1] * handle_m * deg_per_m_lat]
        bp2 = [p_out[0] - unit_out_m[0] * handle_m * deg_per_m_lon,
               p_out[1] - unit_out_m[1] * handle_m * deg_per_m_lat]
        bp3 = p_out

        arcs[i] = _sample_cubic_bezier(
            bp0, bp1, bp2, bp3, _SMOOTH_ARC_SAMPLES + 1)

    # Build the new coords list. Preserve endpoints exactly; replace
    # each sharp internal vertex with its arc samples; keep other
    # internal vertices as-is.
    new_coords = [list(coords[0])]
    for i in range(1, n - 1):
        if arcs[i] is None:
            new_coords.append(list(coords[i]))
        else:
            for pt in arcs[i]:
                new_coords.append(list(pt))
    new_coords.append(list(coords[-1]))
    return new_coords


def apply_subway_style(trails_geojson):
    """Two-pass corridor smoother that runs in place on
    trails_geojson.

    Pass 1 — sharp-corner smoothing: every LineString feature gets
    its sharp internal vertices replaced with smooth bezier arcs
    (see _smooth_sharp_corners). This addresses the line-offset
    artifact at sharp internal corners that's INDEPENDENT of
    corridor-set changes — i.e. a multi-route corridor with a sharp
    bend in its centerline used to render visible "tabs" on each
    parallel route, even when no route was joining or branching at
    the bend.

    Pass 2 — junction transition zones: at each corridor-set-changing
    junction (where a route's `shared_routes` set changes between
    adjacent features), inject N=_TRANSITION_SAMPLES micro-features
    forming a cubic bezier that smoothly fades the route's
    `offset_index` from the previous corridor's value to the new
    one's. The bezier's tangents at the endpoints match the incoming
    and outgoing corridor directions so the offset shift renders
    without spikes.

    Idempotent: caller must strip prior `isStub: true` features
    before calling for re-runs. Smoothing is also idempotent: each
    smoothed arc's per-vertex bends are below the smoothing
    threshold, so a second smoothing pass is a no-op.

    Mutates `trails_geojson["features"]` and returns the number of
    transition micro-features added (not the number of smoothed
    corners or the number of transitions).

    Pre-condition: features have already been per-route-stitched by
    `merge_consecutive_ways` in fetch_trails.py — each feature
    represents one corridor of constant `shared_routes` set within
    its route.
    """
    features = trails_geojson.get("features") or []

    # ---- Pass 1: sharp-corner smoothing ---------------------------
    # Every LineString feature gets its sharp internal vertices
    # replaced with smooth bezier arcs. This is BEFORE the junction
    # transition logic so that:
    #   - tangent_in / tangent_host_out for transitions are computed
    #     from the smoothed-corridor first/last segments (which
    #     happen to match the original direction since smoothing
    #     only touches internal vertices, not endpoints), and
    #   - the host's truncation lands on a smooth corridor — no
    #     downstream kink past the truncation point.
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 3:
            continue
        smoothed = _smooth_sharp_corners(coords)
        if len(smoothed) != len(coords):
            geom["coordinates"] = smoothed

    # ---- Pass 2: junction transition zones -----------------------
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
    # (route_id, feature_index) → endpoint to truncate the host's
    # first vertex to. Filled during the per-junction walk below;
    # applied after the loop so each host is truncated at most once.
    truncations = {}

    # Bezier params captured per junction node so Pass 3 (fade-in
    # transitions for joining routes) can re-use them. Without this,
    # a route that JOINS at a junction (has only a START endpoint
    # there, no incoming corridor) would have its host truncated to
    # the bezier endpoint AS IF it had a transition — but since no
    # transition is generated for it, the rider sees a 9.57m gap
    # where the joining route is invisible. The fade-in pass uses
    # the captured bezier geometry to render the joining route
    # along the same curve, with offset_index interpolating from
    # an "edge" position (overlapping with an existing route at
    # the junction) to the route's actual host_offset_value.
    node_to_bezier = {}

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

            # Collect ALL valid transition candidates for this
            # (rid, junction) pair, then keep only the SINGLE most
            # aligned one. Without this dedup, a 3-corridor junction
            # would generate three pair-wise transitions for routes
            # in all three corridors — they'd visually overlap at the
            # junction, multiplying the bezier-bulge artifact and
            # producing the visible "spike" at sharp Y-junctions.
            # The most-aligned pair represents the route's natural
            # continuation through the junction; other pairs are
            # branches whose offset shift is already covered by the
            # natural-pair transition.
            candidates = []
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

                    # Continuation-pair sharpness: how aligned are
                    # the two corridors? dot of into-junction
                    # tangents — a true straight continuation gives
                    # dot ≈ -1 (vectors point opposite). Branching
                    # corridors give dot near 0. The MOST aligned
                    # pair (most-negative dot) is the route's natural
                    # path through the junction.
                    t_a = _tangent_meters_into_junction(coords_a, end_a)
                    t_b = _tangent_meters_into_junction(coords_b, end_b)
                    sharpness_dot = t_a[0] * t_b[0] + t_a[1] * t_b[1]
                    candidates.append((
                        sharpness_dot, i, j, f_idx_a, end_a,
                        f_idx_b, end_b, feat_a, feat_b,
                        sig_a, sig_b, coords_a, coords_b,
                        offset_a, offset_b))

            if not candidates:
                continue

            # Most-aligned pair = smallest sharpness_dot (most
            # negative). For ties (multiple equally-aligned pairs),
            # break with i,j to be deterministic.
            candidates.sort(key=lambda c: (c[0], c[1], c[2]))
            (_, _, _, f_idx_a, end_a, f_idx_b, end_b,
             feat_a, feat_b, sig_a, sig_b,
             coords_a, coords_b, offset_a, offset_b) = candidates[0]

            # Pick the "host" side for the transition zone.
            # Prefer to host on whichever side has a "start"
            # endpoint (so we ride along its outgoing
            # geometry). If both ends are "end" we skip — no
            # natural outgoing direction to ride along.
            if end_b == "start":
                host_coords = coords_b
                host_offset_value = offset_b
                incoming_coords = coords_a
                incoming_end = end_a
                opposite_offset_value = offset_a
                host_idx_for_truncation = f_idx_b
                # coords_b[0] is the junction, coords_b[1] is
                # the next vertex along host B.
                p_junction = coords_b[0]
                p_next = coords_b[1]
            elif end_a == "start":
                host_coords = coords_a
                host_offset_value = offset_a
                incoming_coords = coords_b
                incoming_end = end_b
                opposite_offset_value = offset_b
                host_idx_for_truncation = f_idx_a
                p_junction = coords_a[0]
                p_next = coords_a[1]
            else:
                # Both are "end" — neither corridor heads
                # away from the junction.
                continue

            # Determine the transition zone span. The bezier's
            # endpoint lands at p_junction + tangent_host_out *
            # span_m, which lies ON the host's straight first
            # segment (between p_junction and p_next). Cap the
            # span to a fraction of that segment's length so
            # we never overshoot p_next.
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

            # Bezier-curve transition zone — replaces the
            # earlier straight-line implementation, which
            # produced visible spikes/kinks at corner
            # junctions because the transition's first
            # micro-feature took its perpendicular from the
            # host's local direction (perp_B) while the
            # corridor on the OTHER side of the junction
            # painted its last pixel using its own local
            # perpendicular (perp_A). At a corner perp_A ≠
            # perp_B, so the same junction node rendered at
            # two different offset positions — the spike.
            #
            # Fix: trace the transition along a cubic bezier
            # whose tangent matches the incoming corridor at
            # the junction and the host corridor at the far
            # end. Each micro-feature's local direction
            # smoothly rotates from corridor A's tangent to
            # corridor B's tangent, so its rendered
            # perpendicular sweeps from perp_A to perp_B and
            # the offset positions track both corridors at
            # both ends.
            tangent_in = _tangent_meters_into_junction(
                incoming_coords, incoming_end)
            tangent_host_into = _tangent_meters_into_junction(
                host_coords, "start")
            tangent_host_out = (
                -tangent_host_into[0], -tangent_host_into[1])
            if (tangent_in == (0.0, 0.0)
                    or tangent_host_out == (0.0, 0.0)):
                # Degenerate corridor — no usable tangent.
                continue

            # Adaptive bezier shape based on corner sharpness.
            # The bezier from p0 → p3 with tangent constraints
            # at both endpoints MUST bulge for sharp corners,
            # because the control polygon has to detour to
            # change tangent direction. The visible-spike
            # artifact in the user's screenshots is this
            # bulge, not the original endpoint mismatch (which
            # the bezier already handles via its tangent
            # match at t=0 and t=1).
            #
            # Strategy: aggressively shorten BOTH the transition
            # span and the bezier handle for sharper corners.
            # Bulge scales ~linearly with span AND with handle,
            # so for a 90° bend (sharpness=1) the combined
            # shrink (span 10→1.5m, handle 0.5→0.15) takes the
            # bulge from 2.2m down to ~0.05m — well under one
            # pixel even at z21. Trade-off: tighter handle
            # gives slightly worse tangent matching at first/
            # last segment, but with N=16 samples the first
            # segment only spans 1/16 of the curve, so the
            # local tangent there is still close to tangent_in.
            dot_inout = (tangent_in[0] * tangent_host_out[0]
                         + tangent_in[1] * tangent_host_out[1])
            dot_inout = max(-1.0, min(1.0, dot_inout))
            # Sharpness in [0, 1]: 0 = straight, 1 = 90° bend.
            # tangent_in and tangent_host_out are both
            # "outward from junction" directions, so dot=+1
            # means the corridor continues straight, dot=0
            # means a 90° bend, dot<0 would mean a >90° bend
            # (rare; clamped at 1).
            sharpness = max(0.0, 1.0 - dot_inout)
            sharpness = min(sharpness, 1.0)
            # Two-stage shrink: cubed-falloff scaled span, AND an
            # absolute-meter cap that kicks in for ANYTHING beyond
            # near-straight. Why both:
            #   - The cubed factor handles the gentle taper from
            #     "smooth straight continuation = full 10m bezier"
            #     down to "noticeable bend = much shorter".
            #   - The absolute cap (1.5m max for sharpness > 0.05)
            #     protects against the case where a long host first
            #     segment + only-slightly-curving tangents still
            #     produces a multi-meter bezier whose bulge is
            #     visible. We'd rather have a tight 1m transition
            #     than a smooth-looking 5m one because the tighter
            #     one occupies less screen real estate and the
            #     visible curve is contained to a smaller area.
            shrink = max(0.05, (1.0 - sharpness) ** 3)
            span_eff = span_m * shrink
            handle_frac = _BEZIER_HANDLE_FRACTION * shrink
            # Hard cap at 1.5m UNIVERSALLY. Even straight-continuation
            # transitions used to span the full 10m (because shrink ≈ 1
            # for sharpness ≈ 0), which produced a visible "lateral
            # tilt" of the corridor over a long stretch any time the
            # corridor's offset structure changed (route joining or
            # leaving). The user's screenshots showed this as a
            # noticeable "jog" — even though the bezier had no bulge,
            # the offset shift distributed over 10m made the corridor
            # visibly drift sideways. Capping at 1.5m localizes the
            # drift to a tight transition zone that reads as a clean
            # junction marker rather than a corridor that's slowly
            # tilting. The corresponding bezier bulge for sharper
            # corners (where the cap matters most for hiding bulge)
            # is unchanged from before.
            span_eff = min(span_eff, 1.5)
            if span_eff <= 0.3:
                # If sharpness pushed span below the visible-noise
                # floor, skip the transition altogether — the offset
                # shift will happen abruptly at the junction node,
                # which at this scale is invisible against the host
                # corridor's normal rendering.
                continue

            # Bezier control points. P0 at the junction; P3
            # at span_eff along the host's outgoing direction
            # (lands ON the host's straight first segment).
            # P1 extends from P0 along the incoming tangent,
            # so the curve initially aligns with corridor A's
            # direction past the junction. P2 retracts from
            # P3 along the host's outgoing direction, so the
            # curve aligns with corridor B at its end.
            p0 = [p_junction[0], p_junction[1]]
            p3_dlon, p3_dlat = _meters_unit_to_degree_delta(
                tangent_host_out, span_eff, p_junction[1])
            p3 = [p_junction[0] + p3_dlon,
                  p_junction[1] + p3_dlat]
            handle_m = span_eff * handle_frac
            p1_dlon, p1_dlat = _meters_unit_to_degree_delta(
                tangent_in, handle_m, p_junction[1])
            p1 = [p0[0] + p1_dlon, p0[1] + p1_dlat]
            p2_dlon, p2_dlat = _meters_unit_to_degree_delta(
                tangent_host_out, -handle_m, p_junction[1])
            p2 = [p3[0] + p2_dlon, p3[1] + p2_dlat]

            # The "fade" goes from opposite_offset_value (at
            # the junction, matching the corridor on the
            # OTHER side of the junction) to host_offset_value
            # (at the end of the transition zone, matching
            # the host corridor's main offset). When the host
            # side is end_b == "start", the rider going along
            # the route from A to B sees A's offset position
            # at the junction, then a smooth fade to B's
            # offset over the next ~10 m.

            # Sample N+1 points along the bezier. Each
            # adjacent pair becomes one micro-feature with an
            # interpolated offset_index (midpoint
            # interpolation of the segment's t range).
            # NB: use `k` not `i` in the inner loops below —
            # the outer pair-iteration uses `i` and a regular
            # `for i` would shadow it.
            points = _sample_cubic_bezier(
                p0, p1, p2, p3, _TRANSITION_SAMPLES + 1)

            # Capture bezier geometry for Pass 3 (joining-route
            # fade-in). Multiple routes at the same junction share
            # the same picked pair (because dedup picks the most-
            # aligned pair, which is the same for all routes
            # iterating through the same corridors); first writer
            # wins, deterministic via the route iteration order.
            if node_key not in node_to_bezier:
                node_to_bezier[node_key] = {
                    "points": points,
                    "host_sig": sig_b if end_b == "start" else sig_a,
                    "incoming_sig": sig_a if end_b == "start" else sig_b,
                    "host_idx_for_truncation": host_idx_for_truncation,
                    "feat_a": feat_a,  # for inheriting route_name etc.
                }
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

            # Mark the host's first vertex for truncation —
            # the transition zone REPLACES the host's first
            # span_m of geometry instead of overlaying it.
            # Without this, corridor B's main rendering would
            # double-up with the transition zone for that
            # first ~10 m, creating visible "wavy thickening"
            # at the junction (the v1 artefact this fix
            # addresses).
            #
            # If the same host has multiple transitions on
            # the same start (rare Y-junction case), we keep
            # the FIRST recorded endpoint — they all sample
            # against the same first segment with the same
            # span_m, so the endpoint is identical anyway.
            truncation_key = (rid, host_idx_for_truncation)
            if truncation_key not in truncations:
                truncations[truncation_key] = points[-1]

    # ---- Pass 3: fade-in for joining routes ----------------------
    # A route that JOINS at a junction (its only feature there is a
    # START — no END/incoming corridor) currently gets no transition
    # because there's no continuation pair. But the OTHER routes at
    # the junction DO get transitions, which truncates the host
    # corridor's coords[0] forward by span_eff meters. The joining
    # route's host shares this same corridor (same OSM way), so its
    # coords[0] is ALSO at the original junction node — but no
    # truncation has been applied to it. Result: the joining route
    # renders along its full host geometry from coords[0] (junction)
    # onward, but the OTHER routes only render from bezier_endpoint
    # (junction + span_eff) onward. The joining route's segment
    # between junction and bezier_endpoint is rendered at the new
    # corridor's offset, which puts it visually OUT OF PLACE next to
    # the bezier transitions of the other routes.
    #
    # Fix: emit fade-in stubs for the joining route along the same
    # bezier geometry. The joining route's offset_index interpolates
    # from a "neighbor's offset" (overlapping with an existing route
    # in the OLD corridor at the junction) to the joining route's
    # own offset in the NEW corridor at the bezier endpoint. Visual:
    # the new route emerges smoothly from one of the existing
    # routes' positions, peeling off as it slides into its final
    # corridor slot. Then truncate the joining route's host like
    # everyone else's.
    for rid, route_features in by_route.items():
        # Re-index endpoints (cheap; same as Pass 2)
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
            if node_key not in node_to_bezier:
                continue
            starts = [e for e in endpoints if e[1] == "start"]
            ends = [e for e in endpoints if e[1] == "end"]
            if not starts:
                # No outgoing feature for this route at the junction.
                # Pure leaving route — skip (could be future "fade
                # out" extension; for now leave the abrupt
                # disappearance, which mirrors the abrupt-appearance
                # case we used to leave at junctions before this
                # pass existed).
                continue
            if ends:
                # Has both START and END — already handled by Pass 2's
                # candidate-picking + transition emission.
                continue

            # Joining route: only START at this junction.
            bezier = node_to_bezier[node_key]
            host_sig = bezier["host_sig"]
            incoming_sig = bezier["incoming_sig"]

            # Compute joining route's offset in the host corridor
            # (where it actually lives, downstream of the junction).
            host_offset_value = _offset_index_for_route(rid, host_sig)
            if host_offset_value == 0:
                # Centered route in odd-count corridor — no visible
                # offset shift even if it appeared abruptly. Skip.
                continue

            # Compute the "neighbor's offset" — the offset of the
            # closest existing route in the INCOMING (smaller)
            # corridor on the same side as the joining route. If the
            # joining route's offset is +2.5 and the incoming
            # corridor's max offset is +2 (for a 5-wide → 6-wide
            # transition where the new route appended at the natural-
            # sort outer edge), use +2 as the start. Visually the
            # joining route emerges from the existing outermost
            # route's position and peels outward.
            incoming_count = len(incoming_sig)
            if incoming_count == 0:
                # Can't compute neighbor offset — skip.
                continue
            incoming_max_abs = (incoming_count - 1) / 2.0
            sign = 1.0 if host_offset_value > 0 else -1.0
            neighbor_offset = sign * min(
                abs(host_offset_value) - 0.5, incoming_max_abs)

            # Find this route's host feature at the junction (the
            # one with START here) so we can mark it for truncation.
            host_idx = starts[0][0]
            host_feat = route_features[host_idx]
            template_feat = bezier.get("feat_a", host_feat)

            # Emit stubs along the bezier path with offset_index
            # interpolating from neighbor_offset → host_offset_value.
            points = bezier["points"]
            for k in range(_TRANSITION_SAMPLES):
                t_mid = (k + 0.5) / _TRANSITION_SAMPLES
                offset_idx = (
                    neighbor_offset
                    + (host_offset_value - neighbor_offset) * t_mid
                )
                micro = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [points[k], points[k + 1]],
                    },
                    "properties": {
                        "route_id": rid,
                        "route_name": template_feat["properties"].get(
                            "route_name", ""),
                        "route_colour": host_feat["properties"].get(
                            "route_colour", ""),
                        "route_ref": host_feat["properties"].get(
                            "route_ref", ""),
                        "shared_routes": [rid],
                        "trail_name": "",
                        "imba_difficulty": "",
                        "oneway": "",
                        "offset_index": offset_idx,
                        "isStub": True,
                    },
                }
                new_micro_features.append(micro)

            # Mark this route's host for truncation too — same
            # endpoint as the other routes at this junction (the
            # bezier ends at points[-1]).
            truncation_key = (rid, host_idx)
            if truncation_key not in truncations:
                truncations[truncation_key] = points[-1]

    # Apply truncations. Stash the original first vertex in
    # _subwayOriginalCoord0 so build.py's _enrich_trails_geojson can
    # restore it on re-runs (and the truncation doesn't compound).
    for (truncate_rid, host_idx), endpoint in truncations.items():
        host_feat = by_route[truncate_rid][host_idx]
        coords = host_feat["geometry"]["coordinates"]
        props = host_feat.setdefault("properties", {})
        if "_subwayOriginalCoord0" not in props:
            props["_subwayOriginalCoord0"] = list(coords[0])
        coords[0] = list(endpoint)

    if new_micro_features:
        features.extend(new_micro_features)
        trails_geojson["features"] = features
    return len(new_micro_features)
