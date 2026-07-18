"""Stable-lane corridor baselines for parallel-route rendering.

The runtime renders each route in a shared corridor at

    offset = position + baseline(corridor)

where ``position`` is the route's 0-based index among the corridor's
routes sorted by the mode's global route order, and ``baseline`` is a
per-corridor constant computed here.

This replaces the old per-corridor *centered* offset
(``position - (n-1)/2``), which re-centers every corridor independently
and therefore shifts every route sideways ("breathing") whenever a
route joins or leaves a corridor. On dense shared networks (RAMBA),
~100% of the residual lateral movement was this re-centering rather than
real route crossings — the dominant visual defect (casings flipping
sides, jerking when a route branches off). LOOM ordering
(``route_order.py``) already drives real crossings to zero; this module
removes the breathing the LOOM flip metric never modeled.

Model
=====
For adjacent corridors c, c' sharing route r,

    offset_c(r) - offset_c'(r) = (pos_c(r) - pos_c'(r)) + (b_c - b_c').

The position deltas are fixed by the ordering; we choose the baselines
b_c to cancel them for shared routes (minimize lateral movement), with a
weak pull toward each corridor's *centered* position (drift control) and
a hard clamp so no bundle drifts too far off the trail centerline:

    minimize   sum over adjacent (c,c'), shared r of |(b_c - b_c') + d_r|   (movement)
             + LAMBDA * sum over c of |b_c + (n_c - 1)/2|                    (drift)
    s.t.       |b_c + (n_c - 1)/2| <= MAX_DRIFT                              (clamp)

with d_r = pos_c(r) - pos_c'(r). This is a convex piecewise-linear (L1)
problem, solved by clamped coordinate descent with weighted-median
updates, multi-seeded for robustness and deterministic across processes.
Scale is tiny (<=15 routes, tens of corridors).

LAMBDA is intentionally small: movement dominates (stable lanes); drift
only breaks ties between movement-optimal solutions and, with the clamp,
bounds how far a bundle sits off-center. LAMBDA -> infinity recovers the
old centered formula exactly (each b_c -> -(n_c - 1)/2) — the back-compat
anchor pinned by the tests.

Mode awareness mirrors route_order: one baseline map per visible mode.
"""

from collections import defaultdict

from geodesy import natural_key as _natural_key
from route_order import build_corridor_adjacencies, enumerate_modes

# Drift weight (lambda). Small so movement dominates; only a tiebreaker
# plus (with the clamp) an anchor on each connected component's absolute
# position. See module docstring.
_DRIFT_WEIGHT = 0.001

# Hard clamp on how far a corridor's bundle center may sit from the trail
# centerline, in lanes (one lane ~ 5-8 px at z14-z18).
#
# 0.5 is the "parity quantum": the gap between an odd-count bundle
# (centered on a lane) and an even-count bundle (centered between lanes).
# Allowing exactly this much drift lets a bundle absorb a count-parity
# change WITHOUT shifting any route — which is the breathing we're killing.
# Empirically on RAMBA this is the knee: at 0.5, lateral movement is
# already fully minimal (only the irreducible packing moves from LOOM's
# separations remain), while the bundle stays ~3 px off the trail. Larger
# values add static drift for zero movement benefit; smaller values
# reintroduce breathing. Tunable; per-map override possible later.
_MAX_DRIFT = 0.5

# Coordinate-descent iteration cap. Converges in well under this at our
# scale; the cap is only a runaway backstop.
_MAX_ITER = 100


def _rank_sorted(routes, rank):
    """Route ids sorted by the mode's global rank (natural-sort tiebreak
    for any id missing from rank). Mirrors the runtime's ``visibleShared``
    sort in computeOffsetsAndFilter."""
    n = len(rank)
    return sorted(
        (str(r) for r in routes),
        key=lambda r: (rank.get(r, n), _natural_key(r)),
    )


def corridor_key(routes, rank):
    """Canonical corridor signature: rank-sorted ids joined by '|'.

    Must match the runtime key built from ``visibleShared`` so build-time
    baked offsets and runtime corridor offsets agree.
    """
    return "|".join(_rank_sorted(routes, rank))


def _positions(routes, rank):
    """{route_id: 0-based index} within the rank-sorted corridor."""
    return {r: i for i, r in enumerate(_rank_sorted(routes, rank))}


def offset_index(route_id, shared_routes, rank, baselines):
    """Stable-lane offset for one route in one corridor.

    ``offset = position + baseline(corridor)``. Falls back to the legacy
    centered offset (``position - (n-1)/2``) when the corridor has no
    baseline — e.g. a custom route added after baselines were computed.
    This is the single source of truth the runtime mirrors; keep in sync
    with computeOffsetsAndFilter in app.js.
    """
    rid = str(route_id)
    routes = [str(r) for r in shared_routes]
    if len(routes) <= 1 or rid not in routes:
        return 0.0
    pos = _positions(routes, rank)
    if baselines is not None:
        key = corridor_key(routes, rank)
        if key in baselines:
            return pos[rid] + baselines[key]
    return pos[rid] - (len(routes) - 1) / 2


def _weighted_median(points):
    """Value minimizing ``sum(w * |x - v|)`` over (v, w) in ``points``.

    On an exact split (cumulative weight reaches half at a breakpoint),
    the optimum is an interval; we return its lower endpoint for
    determinism. This also yields the "majority stays put" behavior at a
    mid-bundle insertion: the displaced minority moves, the rest hold.
    """
    if not points:
        return 0.0
    pts = sorted(points)
    half = sum(w for _, w in pts) / 2.0
    cum = 0.0
    for v, w in pts:
        cum += w
        if cum >= half:
            return v
    return pts[-1][0]


def _solve(keys, anchors, incident, seed):
    """Clamped coordinate descent from ``seed``. Returns {key: baseline}.

    ``incident[k]`` is a list of (other_key, d) — one entry per shared
    route on each adjacency incident to k — contributing the term
    ``|b_k - (b_other - d)|``. ``anchors[k] = -(n_k - 1)/2`` is the
    centered baseline (the drift target and clamp center).
    """
    b = {k: seed.get(k, anchors[k]) for k in keys}
    for _ in range(_MAX_ITER):
        max_delta = 0.0
        for k in keys:
            pts = [(b[other] - d, 1.0) for (other, d) in incident[k]]
            pts.append((anchors[k], _DRIFT_WEIGHT))
            nb = _weighted_median(pts)
            lo = anchors[k] - _MAX_DRIFT
            hi = anchors[k] + _MAX_DRIFT
            nb = lo if nb < lo else hi if nb > hi else nb
            delta = abs(nb - b[k])
            if delta > max_delta:
                max_delta = delta
            b[k] = nb
        if max_delta < 1e-9:
            break
    return b


def _objective(b, edge_terms, anchors):
    movement = sum(abs((b[a] - b[c]) + d) for (a, c, d) in edge_terms)
    drift = _DRIFT_WEIGHT * sum(abs(b[k] - anchors[k]) for k in b)
    return movement + drift


def _visible_corridors(features, visible):
    """Collect multi-route corridor signatures from ``features``.

    A corridor is the frozenset of visible route ids sharing a
    LineString segment; stubs and subway host variants are skipped.
    Only corridors with 2+ visible routes matter for offsets.
    ``visible`` must already be a set of string ids.
    """
    corridors = set()
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("isStub") or props.get("_subwayHostVariant"):
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        shared = props.get("shared_routes") or [props.get("route_id")]
        ss = frozenset(str(x) for x in shared if x and str(x) in visible)
        if len(ss) >= 2:
            corridors.add(ss)
    return corridors


def compute_baselines_for_mode(features, visible, rank, *, previous=None):
    """Compute {corridor_key: baseline} for one visible mode.

    Only multi-route corridors (>=2 visible routes) get baselines; solo
    segments render at offset 0 via the runtime's ``visibleCount <= 1``
    path, and a route's solo->corridor entry is a genuine join handled by
    a smooth transition rather than a baseline constraint.

    ``rank`` is {route_id: index} from this mode's route order.
    ``previous`` (optional) seeds the search for rebuild stability.
    """
    visible = {str(r) for r in visible}
    corridors = _visible_corridors(features, visible)
    if not corridors:
        return {}

    keymap = {ss: corridor_key(ss, rank) for ss in corridors}
    pos_cache = {ss: _positions(ss, rank) for ss in corridors}
    anchors = {keymap[ss]: -(len(ss) - 1) / 2 for ss in corridors}

    incident = defaultdict(list)
    edge_terms = []
    for sig_a, sig_b, shared in build_corridor_adjacencies(features, visible_routes=visible):
        fa, fb = frozenset(sig_a), frozenset(sig_b)
        # Skip adjacencies touching a solo segment (size-1 sig) — solos
        # are not optimized nodes.
        if fa not in keymap or fb not in keymap:
            continue
        ka, kb = keymap[fa], keymap[fb]
        if ka == kb:
            continue
        pa, pb = pos_cache[fa], pos_cache[fb]
        for r in shared:
            r = str(r)
            if r not in pa or r not in pb:
                continue
            d = pa[r] - pb[r]
            incident[ka].append((kb, d))
            incident[kb].append((ka, -d))
            edge_terms.append((ka, kb, d))

    keys = sorted(anchors.keys())
    for k in keys:
        incident.setdefault(k, [])

    # Seeds, in preference order (earlier wins on objective ties):
    # previous build (stability), centered (low drift), zeros.
    seeds = []
    if previous:
        seeds.append({k: previous[k] for k in keys if k in previous})
    seeds.append({k: anchors[k] for k in keys})
    seeds.append({k: 0.0 for k in keys})

    best_b = None
    best_obj = None
    for seed in seeds:
        b = _solve(keys, anchors, incident, seed)
        obj = _objective(b, edge_terms, anchors)
        if best_obj is None or obj < best_obj - 1e-12:
            best_obj = obj
            best_b = b

    return best_b


def _movement_and_drift(baselines, features, visible, rank):
    """Diagnostic stats for one mode: (residual_movement, max_drift,
    transitions) where ``transitions`` counts adjacencies with non-zero
    residual movement. Used for build logging / verification."""
    visible = {str(r) for r in visible}
    corridors = _visible_corridors(features, visible)
    keymap = {ss: corridor_key(ss, rank) for ss in corridors}
    pos_cache = {ss: _positions(ss, rank) for ss in corridors}

    def off(ss, r):
        return pos_cache[ss][r] + baselines.get(keymap[ss], -(len(ss) - 1) / 2)

    movement = 0.0
    transitions = 0
    for sig_a, sig_b, shared in build_corridor_adjacencies(features, visible_routes=visible):
        fa, fb = frozenset(sig_a), frozenset(sig_b)
        if fa not in keymap or fb not in keymap:
            continue
        edge_move = sum(abs(off(fa, str(r)) - off(fb, str(r))) for r in shared)
        movement += edge_move
        if edge_move > 1e-6:
            transitions += 1
    max_drift = 0.0
    for ss in corridors:
        center = baselines.get(keymap[ss], -(len(ss) - 1) / 2) + (len(ss) - 1) / 2
        max_drift = max(max_drift, abs(center))
    return movement, max_drift, transitions


def compute_corridor_baselines(
    routes_metadata, features, route_orders, *, previous_baselines=None, verbose=False
):
    """Compute {mode_key: {corridor_key: baseline}} for every visible mode.

    Parallels route_order.compute_route_orders: one baseline map per
    active mode, keyed identically so the runtime can look up by mode +
    corridor signature.

    Parameters
    ----------
    routes_metadata : dict {route_id: info} — drives mode enumeration.
    features : list of GeoJSON features (post-enrichment, pre-stub).
    route_orders : dict {mode_key: [route_id, ...]} — the LOOM orderings.
    previous_baselines : dict {mode_key: {corridor_key: baseline}} or None
        — from a prior build's metadata, for rebuild stability.

    Returns
    -------
    (baselines, stats) where
      baselines : {mode_key: {corridor_key: baseline}}
      stats     : {mode_key: (residual_movement, max_drift, transitions)}
    """
    if not route_orders:
        return {}, {}
    previous_baselines = previous_baselines or {}
    modes = enumerate_modes(routes_metadata)

    baselines = {}
    stats = {}
    for mode_key, order in route_orders.items():
        visible = modes.get(mode_key) or frozenset(str(r) for r in order)
        rank = {str(r): i for i, r in enumerate(order)}
        prev = previous_baselines.get(mode_key)
        bl = compute_baselines_for_mode(features, visible, rank, previous=prev)
        baselines[mode_key] = bl
        stats[mode_key] = _movement_and_drift(bl, features, visible, rank)
        if verbose:
            mv, dr, tr = stats[mode_key]
            print(
                f"corridorBaselines[{mode_key}]: movement={mv:.1f} lanes, "
                f"max_drift={dr:.2f}, {tr} real transition(s)"
            )

    return baselines, stats
