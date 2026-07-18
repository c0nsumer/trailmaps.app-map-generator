"""Tests for corridor_baselines.py — stable-lane offset baselines.

Run from repo root:
    python -m pytest scripts/tests/test_corridor_baselines.py -v
"""

import itertools
import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import corridor_baselines as CB  # noqa: E402
from corridor_baselines import (  # noqa: E402
    compute_baselines_for_mode,
    corridor_key,
    offset_index,
)

# ---- helpers ---------------------------------------------------------


def line(shared, p0, p1):
    """A LineString feature for a corridor segment with the given route
    set, from p0 to p1. One feature per corridor is enough to register
    its signature at both endpoints for adjacency building."""
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(p0), list(p1)]},
        "properties": {"route_id": shared[0], "shared_routes": list(shared)},
    }


def rank_of(order):
    return {str(r): i for i, r in enumerate(order)}


def offsets_for(route, features, order, baselines):
    """All distinct offsets ``route`` takes across the corridors it
    appears in (multi-route only), under ``baselines``."""
    rank = rank_of(order)
    visible = set(str(r) for r in order)
    seen = {}
    for f in features:
        shared = [str(x) for x in f["properties"]["shared_routes"] if str(x) in visible]
        if len(shared) < 2 or route not in shared:
            continue
        key = corridor_key(shared, rank)
        seen[key] = offset_index(route, shared, rank, baselines)
    return seen


# ---- key / fallback --------------------------------------------------


def test_corridor_key_is_rank_sorted():
    rank = rank_of(["C", "A", "B"])  # C=0, A=1, B=2
    assert corridor_key(["A", "B", "C"], rank) == "C|A|B"


def test_offset_index_centered_fallback_when_no_baseline():
    rank = rank_of(["A", "B", "C"])
    # No baselines -> legacy centered formula: positions 0,1,2 -> -1,0,1.
    assert offset_index("A", ["A", "B", "C"], rank, None) == -1.0
    assert offset_index("B", ["A", "B", "C"], rank, None) == 0.0
    assert offset_index("C", ["A", "B", "C"], rank, None) == 1.0
    # Solo / absent -> 0.
    assert offset_index("A", ["A"], rank, None) == 0.0
    assert offset_index("Z", ["A", "B"], rank, None) == 0.0


def test_offset_index_uses_baseline():
    rank = rank_of(["A", "B"])
    bl = {corridor_key(["A", "B"], rank): 0.0}  # baseline 0 instead of centered -0.5
    assert offset_index("A", ["A", "B"], rank, bl) == 0.0  # pos 0 + 0
    assert offset_index("B", ["A", "B"], rank, bl) == 1.0  # pos 1 + 0


# ---- core behavior ---------------------------------------------------


def test_chain_dropping_routes_has_zero_movement():
    """A trunk that sheds one route at a time: every staying route must
    hold a single, constant lane (no breathing)."""
    order = ["A", "B", "C", "D"]
    feats = [
        line(["A", "B", "C", "D"], (0, 0), (1, 0)),
        line(["A", "B", "C"], (1, 0), (2, 0)),
        line(["A", "B"], (2, 0), (3, 0)),
        line(["A"], (3, 0), (4, 0)),  # solo tail
    ]
    bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
    for route in ["A", "B", "C"]:
        offs = set(offsets_for(route, feats, order, bl).values())
        assert len(offs) == 1, f"{route} should hold one lane, got {offs}"
    # Bundle stays near the trail (drift within clamp).
    centers = []
    rank = rank_of(order)
    for shared in (["A", "B", "C", "D"], ["A", "B", "C"], ["A", "B"]):
        offs = [offset_index(r, shared, rank, bl) for r in shared]
        centers.append(abs(sum(offs) / len(offs)))
    assert max(centers) <= CB._MAX_DRIFT + 1e-9


def test_no_route_crosses_centerline_without_a_real_crossing():
    order = ["A", "B", "C", "D"]
    feats = [
        line(["A", "B", "C", "D"], (0, 0), (1, 0)),
        line(["A", "B", "C"], (1, 0), (2, 0)),
        line(["A", "B"], (2, 0), (3, 0)),
    ]
    bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
    for route in ["A", "B", "C", "D"]:
        offs = list(offsets_for(route, feats, order, bl).values())
        signs = {o > 0 for o in offs if o != 0}
        assert len(signs) <= 1, f"{route} changes sign via breathing: {offs}"


def test_mid_bundle_insertion_is_minimal_and_brackets_newcomer():
    """B inserts into the middle of {A,C}. Inserting one route mid-bundle
    forces the two flanks apart by exactly one lane total (irreducible);
    the newcomer must land between its neighbors. Whether that one lane is
    taken symmetrically (A,C each 0.5) or by a single route is a tie the
    drift term breaks — both are movement-optimal, so we pin the invariant
    (minimal total movement, correct bracketing), not the split."""
    order = ["A", "B", "C"]
    feats = [
        line(["A", "C"], (0, 0), (1, 0)),
        line(["A", "B", "C"], (1, 0), (2, 0)),
    ]
    bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
    rank = rank_of(order)
    moves = {
        r: abs(offset_index(r, ["A", "C"], rank, bl) - offset_index(r, ["A", "B", "C"], rank, bl))
        for r in ["A", "C"]
    }
    assert abs(sum(moves.values()) - 1.0) < 1e-9, moves  # irreducible total
    assert all(m <= 1.0 + 1e-9 for m in moves.values()), moves  # nobody overshoots
    oa = offset_index("A", ["A", "B", "C"], rank, bl)
    ob = offset_index("B", ["A", "B", "C"], rank, bl)
    oc = offset_index("C", ["A", "B", "C"], rank, bl)
    assert oa < ob < oc, (oa, ob, oc)  # newcomer bracketed by its neighbors


def test_isolated_corridor_is_centered():
    order = ["A", "B", "C"]
    feats = [line(["A", "B", "C"], (0, 0), (1, 0))]  # standalone, no neighbors
    bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
    rank = rank_of(order)
    offs = [offset_index(r, ["A", "B", "C"], rank, bl) for r in ["A", "B", "C"]]
    assert offs == [-1.0, 0.0, 1.0]  # centered


def test_large_lambda_recovers_centered_formula():
    """LAMBDA -> infinity must reproduce the old centered offsets — the
    back-compat anchor."""
    saved_w, saved_d = CB._DRIFT_WEIGHT, CB._MAX_DRIFT
    CB._DRIFT_WEIGHT = 1e9
    CB._MAX_DRIFT = 99.0  # don't let the clamp interfere
    try:
        order = ["A", "B", "C", "D"]
        feats = [
            line(["A", "B", "C", "D"], (0, 0), (1, 0)),
            line(["A", "B", "C"], (1, 0), (2, 0)),
            line(["A", "B"], (2, 0), (3, 0)),
        ]
        bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
        rank = rank_of(order)
        for shared in (["A", "B", "C", "D"], ["A", "B", "C"], ["A", "B"]):
            n = len(shared)
            for i, r in enumerate(shared):
                assert abs(offset_index(r, shared, rank, bl) - (i - (n - 1) / 2)) < 1e-6
    finally:
        CB._DRIFT_WEIGHT, CB._MAX_DRIFT = saved_w, saved_d


def test_drift_clamp_respected():
    """A long shedding chain wants to drift far under pure stable lanes;
    the clamp must bound every bundle center."""
    order = ["A", "B", "C", "D", "E", "F"]
    feats = []
    routes = list(order)
    x = 0
    while len(routes) >= 2:
        feats.append(line(list(routes), (x, 0), (x + 1, 0)))
        x += 1
        routes.pop()  # drop the highest-rank route each step
    bl = compute_baselines_for_mode(feats, set(order), rank_of(order))
    rank = rank_of(order)
    routes = list(order)
    while len(routes) >= 2:
        offs = [offset_index(r, routes, rank, bl) for r in routes]
        assert abs(sum(offs) / len(offs)) <= CB._MAX_DRIFT + 1e-9
        routes.pop()


def test_deterministic():
    order = ["A", "B", "C", "D"]
    feats = [
        line(["A", "B", "C", "D"], (0, 0), (1, 0)),
        line(["A", "B", "C"], (1, 0), (2, 0)),
        line(["A", "B"], (2, 0), (3, 0)),
    ]
    a = compute_baselines_for_mode(feats, set(order), rank_of(order))
    b = compute_baselines_for_mode(feats, set(order), rank_of(order))
    assert a == b


def test_matches_brute_force_on_small_graph():
    """Coordinate descent should reach the true L1 optimum on a tiny
    graph (validated against a grid brute-force)."""
    order = ["A", "B", "C"]
    feats = [
        line(["A", "C"], (0, 0), (1, 0)),
        line(["A", "B", "C"], (1, 0), (2, 0)),
        line(["B", "C"], (2, 0), (3, 0)),
    ]
    rank = rank_of(order)
    bl = compute_baselines_for_mode(feats, set(order), rank)

    # Reconstruct the optimization terms to brute-force the objective.
    from corridor_baselines import _objective, _positions, build_corridor_adjacencies

    corridors = {frozenset(["A", "C"]), frozenset(["A", "B", "C"]), frozenset(["B", "C"])}
    keymap = {ss: corridor_key(ss, rank) for ss in corridors}
    posc = {ss: _positions(ss, rank) for ss in corridors}
    anchors = {keymap[ss]: -(len(ss) - 1) / 2 for ss in corridors}
    edge_terms = []
    for sig_a, sig_b, shared in build_corridor_adjacencies(feats, visible_routes=set(order)):
        fa, fb = frozenset(sig_a), frozenset(sig_b)
        if fa not in keymap or fb not in keymap:
            continue
        for r in shared:
            edge_terms.append((keymap[fa], keymap[fb], posc[fa][str(r)] - posc[fb][str(r)]))

    keys = sorted(anchors)
    grid = [v / 2.0 for v in range(-2 * 4, 2 * 4 + 1)]  # -4 .. 4 step 0.5
    best = None
    for combo in itertools.product(grid, repeat=len(keys)):
        cand = dict(zip(keys, combo))
        if any(abs(cand[k] - anchors[k]) > CB._MAX_DRIFT + 1e-9 for k in keys):
            continue
        o = _objective(cand, edge_terms, anchors)
        if best is None or o < best:
            best = o
    got = _objective(bl, edge_terms, anchors)
    assert got <= best + 1e-6, f"CD objective {got} worse than brute-force {best}"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
