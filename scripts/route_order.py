"""Side-stable route ordering for subway-style parallel rendering.

Solves the Metro-Line Node Crossing Minimization (MLNCM) problem as
formalized by Bast, Brosi & Storandt (LOOM, ACM TSAS 2019). Given a
set of routes and the corridor adjacencies they share, computes a
global routeOrder array that minimizes the number of sign-flips —
routes crossing the corridor centerline between adjacent corridors.

Secondary objective: minimize partner separations (LOOM's MLNCM-S
variant) as a strict tiebreaker — partner routes that travel
together stay visually adjacent in the rendered offset stack.

Mode awareness
==============
The same map can be displayed in different "visible modes" (summer /
winter / emergency on/off). Each mode produces a different visible-
route subset and therefore a different effective-corridor graph.
``enumerate_modes`` derives the distinct visible-route subsets a map
can produce; ``compute_route_orders`` returns one routeOrder per mode.

This matters: on some maps (e.g. RAMBA), most OSM ways are shared
between summer and winter relations. Optimizing once against the
full shared_routes union would leave most corridors mis-rendered in
each individual mode.

Algorithm
=========
Local search (pairwise-swap hill climbing) with multi-restart. The
first restart seeds from the previous-build's routeOrder when
available, else from natural-sort; subsequent restarts shuffle a
natural-sorted base. On score ties the previous order wins, so an
unchanged-topology rebuild is a true no-op; otherwise the lex-smallest
ordering wins. Output is a deterministic function of the input
geometry + seed (default 42), reproducible across processes regardless
of PYTHONHASHSEED. Empirically converges to within 1 sign-flip of
optimum on every map in our network.

ILP-based optimal solutions (LOOM Section 3.2) are not implemented
here — heuristic is sufficient for our scale (≤15 routes per map).

References
==========
* Bast, Brosi, Storandt (2019). "Efficient Generation of
  Geographically Accurate Transit Maps." ACM Trans. Spatial Algo
  Syst. 5(4) Article 25.
  https://doi.org/10.1145/3337790
* Bekos, Kaufmann, Potika, Symvonis (2013). "Metro-Line Crossing
  Minimization: Hardness, Approximations, and Tractable Cases."
  arXiv:1306.2079.
* LOOM source: https://github.com/ad-freiburg/loom
"""

import random
from collections import defaultdict

import console
from geodesy import coord_key as _coord_key
from geodesy import natural_key as _natural_key


def build_corridor_adjacencies(features, visible_routes=None):
    """Build the corridor adjacency graph from trails.geojson features.

    Two corridor signatures are adjacent when they meet at a junction
    node AND share at least one route. The signature is the sorted
    tuple of route IDs in the corridor (matches MapLibre/runtime
    rendering exactly).

    Stubs (features with ``isStub: true``) are skipped — they're
    output of a previous subway-style pass, not input.

    Parameters
    ----------
    features : list of GeoJSON Feature dicts.
    visible_routes : iterable of str or None.
        When provided, each feature's ``shared_routes`` is FILTERED
        to this subset before signature construction. The adjacency
        graph reflects effective corridors as the user would see
        them in the corresponding mode.

    Returns
    -------
    list of (sig_a, sig_b, frozenset_of_shared_routes) tuples.
        Each pair appears once (deduped via sorted-tuple key).
    """
    visible = None if visible_routes is None else {str(r) for r in visible_routes}
    node_to_sigs = defaultdict(set)
    sig_routes = {}

    for feat in features:
        props = feat.get("properties") or {}
        if props.get("isStub"):
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if not coords:
            continue
        shared = props.get("shared_routes") or [props.get("route_id")]
        shared_strs = [str(x) for x in shared if x]
        if visible is not None:
            shared_strs = [r for r in shared_strs if r in visible]
        if not shared_strs:
            continue
        sig = tuple(sorted(shared_strs))
        sig_routes[sig] = set(sig)
        for pt in (coords[0], coords[-1]):
            node_to_sigs[_coord_key(pt)].add(sig)

    adjacencies = []
    seen = set()
    for sigs in node_to_sigs.values():
        sigs_list = list(sigs)
        for i in range(len(sigs_list)):
            for j in range(i + 1, len(sigs_list)):
                s1, s2 = sigs_list[i], sigs_list[j]
                shared = sig_routes[s1] & sig_routes[s2]
                if not shared:
                    continue
                key = (s1, s2) if s1 < s2 else (s2, s1)
                if key in seen:
                    continue
                seen.add(key)
                adjacencies.append((s1, s2, frozenset(shared)))
    return adjacencies


# Weight applied to separation count in the lexicographic objective.
# The full flip count is multiplied by 1000 so it always dominates —
# the optimizer will never trade away a sign flip to reduce
# separations. Separations only break ties between flip-count-equal
# orderings.
_SEPARATION_WEIGHT = 1
_FLIP_WEIGHT = 1000


def score(order, adjacencies):
    """Lexicographic objective: full sign flips × 1000 + separations.

    "Full sign flip": a shared route changes from a positive offset in
    one corridor to a negative offset in the adjacent corridor (or
    vice versa). Center-transitions (offset 0 → non-zero, or
    non-zero → 0) are NOT counted — they're not visually "switching
    sides" and counting them dilutes the optimization signal.

    "Separation": two routes that are adjacent in one corridor's
    offset stack (positions differ by exactly 1) become non-adjacent
    in the next corridor (positions differ by >1, meaning some other
    route slid between them). LOOM's MLNCM-S secondary objective.

    The 1000:1 weight ratio guarantees ``flips`` dominates — the
    optimizer prefers any ordering with one fewer flip over an
    ordering with up to 1000 fewer separations.
    """
    idx = {r: i for i, r in enumerate(order)}
    flips = 0
    separations = 0
    for sig_a, sig_b, shared in adjacencies:
        s1 = sorted(sig_a, key=lambda r: idx.get(r, len(order)))
        s2 = sorted(sig_b, key=lambda r: idx.get(r, len(order)))
        pos1 = {r: i for i, r in enumerate(s1)}
        pos2 = {r: i for i, r in enumerate(s2)}
        # --- Sign flips ---
        for r in shared:
            o1 = pos1[r] - (len(sig_a) - 1) / 2
            o2 = pos2[r] - (len(sig_b) - 1) / 2
            if (o1 > 0 and o2 < 0) or (o1 < 0 and o2 > 0):
                flips += 1
        # --- Separations: for each pair (A, B) in `shared`, check
        # whether they are adjacent in s1 AND s2, or in neither.
        # Asymmetry (adjacent in one but not the other) → separation.
        shared_list = list(shared)
        for i in range(len(shared_list)):
            for j in range(i + 1, len(shared_list)):
                ra, rb = shared_list[i], shared_list[j]
                d1 = abs(pos1[ra] - pos1[rb])
                d2 = abs(pos2[ra] - pos2[rb])
                if (d1 == 1) != (d2 == 1):
                    separations += 1
    return flips * _FLIP_WEIGHT + separations * _SEPARATION_WEIGHT


def local_search(initial_order, adjacencies, max_iter=500):
    """Pairwise-swap hill climbing.

    Try every pair (i, j) of routes; commit the FIRST improving swap
    and restart the inner loop from the new order. Converges when no
    swap improves the score. Strict ``<`` improvement ensures
    termination.

    For our scale (≤15 routes, ≤60 adjacencies), convergence is
    typically <20 iterations.

    Returns
    -------
    (order, score) where order is the optimized list and score is its
    objective value.
    """
    order = list(initial_order)
    best = score(order, adjacencies)
    iters = 0
    while iters < max_iter:
        iters += 1
        improved = False
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                cand = order[:]
                cand[i], cand[j] = cand[j], cand[i]
                s = score(cand, adjacencies)
                if s < best:
                    order = cand
                    best = s
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    return order, best


def compute_route_order(route_ids, adjacencies, *, restarts=30, seed=42, previous_order=None):
    """Multi-restart local search. Deterministic given inputs + seed.

    Restart 0 seeds from ``previous_order`` (if supplied; routes new
    to this build are appended by natural-sort) or natural-sort
    otherwise. Restarts 1..N-1 use random shuffles seeded by ``seed``.

    Tiebreaking: among equal-score orderings, prefer ``previous_order``
    (so an unchanged-topology rebuild is a true no-op); for the first
    build, fall back to the lex-smallest tuple. The shuffle base is
    natural-sorted, so output is reproducible across processes
    regardless of PYTHONHASHSEED.

    Parameters
    ----------
    route_ids : iterable of str.
        All route IDs to be ordered.
    adjacencies : list of (sig_a, sig_b, shared) tuples.
        From ``build_corridor_adjacencies``.
    restarts : int, default 30.
        Number of local-search restarts. 30 is empirically sufficient
        for our scale to find the best minimum from any of the maps
        we have.
    seed : int, default 42.
        Random seed for shuffle reproducibility.
    previous_order : list of str or None.
        If provided, seeds restart 0 from this order. Used by the
        build pipeline to maintain stability across rebuilds —
        topology unchanged → routeOrder unchanged.

    Returns
    -------
    (order, flip_count, separation_count)
    """
    # Natural-sort the route set up front so the random-restart shuffle
    # base order does not depend on the caller's iteration order.
    # Callers pass list(frozenset), whose order is hash-seed-dependent
    # across processes (PYTHONHASHSEED unset) — without this sort, a
    # fixed seed produces a different shuffle sequence each build, so the
    # ordering churns build-to-build even with identical input geometry.
    routes_str = sorted((str(r) for r in route_ids), key=_natural_key)
    if not routes_str:
        return [], 0, 0
    if len(routes_str) == 1:
        return list(routes_str), 0, 0

    # Reconstruct the previous-build order restricted to the current
    # route set: keep prior routes in their prior order, drop departed
    # ones, append newly-added routes by natural-sort. Seeds restart 0
    # AND is the preferred winner on score ties below.
    prev_seed = None
    if previous_order is not None:
        seen = set()
        prev_seed = []
        for r in previous_order:
            r = str(r)
            if r in routes_str and r not in seen:
                prev_seed.append(r)
                seen.add(r)
        prev_seed += sorted(set(routes_str) - seen, key=_natural_key)

    rng = random.Random(seed)
    best_order = None
    best_key = None

    for k in range(restarts):
        if k == 0:
            start = list(prev_seed) if prev_seed is not None else list(routes_str)
        else:
            start = list(routes_str)
            rng.shuffle(start)
        order, s = local_search(start, adjacencies)
        # Candidate ranking (lexicographic tuple, lower wins):
        #   1. score — primary flip objective + separation tiebreak.
        #   2. 0 if this order equals the previous build's order, else 1
        #      — an unchanged-topology rebuild reproduces the prior order
        #      byte-for-byte (a true no-op) rather than drifting to a
        #      different but equally-optimal ordering.
        #   3. the order tuple — lex tiebreak for the first build (no
        #      previous_order), reproducible across processes.
        key = (s, 0 if order == prev_seed else 1, tuple(order))
        if best_key is None or key < best_key:
            best_key = key
            best_order = order

    # Decompose the weighted score back into its components for
    # diagnostic reporting.
    best_s = best_key[0]
    flip_count = best_s // _FLIP_WEIGHT
    sep_count = best_s - flip_count * _FLIP_WEIGHT
    return best_order, int(flip_count), int(sep_count)


def enumerate_modes(routes_metadata):
    """Enumerate the distinct visible-mode subsets a map can produce.

    A route is rendered when:
      (seasonMode == "summer" and info.summer) OR
      (seasonMode == "winter" and info.winter) OR
      (emergencyOn and info.emergency)

    So the cross-product of (seasonMode ∈ {summer, winter}) ×
    (emergencyOn ∈ {False, True}) gives 4 possible visible-route
    subsets. Modes with no visible routes are omitted; modes that
    happen to produce identical subsets are still listed separately
    (the caller may dedupe).

    Returns
    -------
    dict {mode_key: frozenset(route_ids)} where mode_key is one of
    "summer", "summer_emergency", "winter", "winter_emergency".

    Mode_key naming is stable — runtime app.js builds the same keys
    from seasonMode + emergencyOn to look up the active routeOrder.
    """

    def _flag(info, key):
        return bool((info or {}).get(key))

    summer = frozenset(str(rid) for rid, info in routes_metadata.items() if _flag(info, "summer"))
    winter = frozenset(str(rid) for rid, info in routes_metadata.items() if _flag(info, "winter"))
    emergency = frozenset(
        str(rid) for rid, info in routes_metadata.items() if _flag(info, "emergency")
    )

    modes = {}
    if summer:
        modes["summer"] = summer
    if summer and emergency:
        modes["summer_emergency"] = summer | emergency
    if winter:
        modes["winter"] = winter
    if winter and emergency:
        modes["winter_emergency"] = winter | emergency
    return modes


def compute_route_orders(
    routes_metadata, features, *, previous_orders=None, restarts=30, seed=42, verbose=False
):
    """Compute one routeOrder per distinct visible mode.

    Modes with identical visible-route subsets share a single
    optimization run — the same ordering is reused. The output dict
    is keyed by every active mode_key, even when subsets are duped,
    so the runtime can do a simple dictionary lookup.

    Parameters
    ----------
    routes_metadata : dict {route_id: info_dict}.
        From trails_geojson["metadata"]["routes"]. Per-route flags
        ``summer`` / ``winter`` / ``emergency`` drive mode enumeration.
    features : list of GeoJSON Features.
        From trails_geojson["features"]. Used to build per-mode
        adjacency graphs by filtering each feature's shared_routes to
        the mode's visible subset.
    previous_orders : dict {mode_key: list of route_ids} or None.
        From a previous build's metadata.routeOrders. Used to seed
        local search per mode for stability across rebuilds.
    restarts, seed : forwarded to ``compute_route_order``.
    verbose : bool. If True, prints per-mode stats during computation.

    Returns
    -------
    (orders, stats) where
      orders : dict {mode_key: list of route_ids}
      stats : dict {mode_key: (flip_count, separation_count)}
    """
    modes = enumerate_modes(routes_metadata)
    if not modes:
        return {}, {}

    # Dedup by visible subset — modes with identical visible-route
    # sets share the optimization (one run, identical output).
    subset_to_keys = defaultdict(list)
    for mode_key, subset in modes.items():
        subset_to_keys[subset].append(mode_key)

    orders = {}
    stats = {}
    previous_orders = previous_orders or {}

    for subset, mode_keys in subset_to_keys.items():
        adjacencies = build_corridor_adjacencies(features, visible_routes=subset)

        # Seed from any of this subset's mode_keys that have a prior
        # order — they're all equivalent because the subset is the
        # same. First key wins for determinism.
        prev = None
        for k in mode_keys:
            if k in previous_orders:
                prev = previous_orders[k]
                break

        order, flips, seps = compute_route_order(
            list(subset),
            adjacencies,
            restarts=restarts,
            seed=seed,
            previous_order=prev,
        )

        for mode_key in mode_keys:
            orders[mode_key] = order
            stats[mode_key] = (flips, seps)

        if verbose:
            console.info(
                f"routeOrder[{'/'.join(sorted(mode_keys))}]: "
                f"{flips} flip(s), {seps} separation(s) "
                f"over {len(adjacencies)} adjacencies"
            )

    return orders, stats
