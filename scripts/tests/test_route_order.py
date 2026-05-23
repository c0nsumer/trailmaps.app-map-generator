"""Tests for route_order.py — side-stable parallel route ordering.

Run from repo root:
    python -m pytest scripts/tests/test_route_order.py -v

Or as a script:
    python scripts/tests/test_route_order.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json  # noqa: E402

from route_order import (  # noqa: E402
    _coord_key,
    _natural_key,
    build_corridor_adjacencies,
    compute_route_order,
    compute_route_orders,
    enumerate_modes,
    local_search,
    score,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adj(sig_a, sig_b, *shared):
    """Build a single adjacency tuple."""
    return (tuple(sig_a), tuple(sig_b), frozenset(shared))


def _count_flips_raw(order, adjacencies):
    """Count true sign flips (no weighting) for assertions."""
    idx = {r: i for i, r in enumerate(order)}
    n = 0
    for sig1, sig2, shared in adjacencies:
        s1 = sorted(sig1, key=lambda r: idx.get(r, len(order)))
        s2 = sorted(sig2, key=lambda r: idx.get(r, len(order)))
        pos1 = {r: i for i, r in enumerate(s1)}
        pos2 = {r: i for i, r in enumerate(s2)}
        for r in shared:
            o1 = pos1[r] - (len(sig1) - 1) / 2
            o2 = pos2[r] - (len(sig2) - 1) / 2
            if (o1 > 0 and o2 < 0) or (o1 < 0 and o2 > 0):
                n += 1
    return n


def _make_feature(coords, shared, route_id=None):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "shared_routes": list(shared),
            "route_id": route_id or list(shared)[0],
        },
    }


# ---------------------------------------------------------------------------
# Sanity / helper tests
# ---------------------------------------------------------------------------


def test_natural_key_numeric_aware():
    """Numeric runs sort numerically, not lexically."""
    keys = ["1", "2", "10", "100"]
    assert sorted(keys, key=_natural_key) == ["1", "2", "10", "100"]


def test_natural_key_mixed_numeric_and_string_ids():
    """Regression: route lists with BOTH numeric OSM relation ids AND
    custom (non-numeric) event-mode ids must sort without raising
    ``TypeError: '<' not supported between instances of 'str' and 'int'``.

    Triggered in production on sheldensbigbang where event_mode emits
    inline custom-id routes alongside OSM relation ids.
    """
    keys = ["12345678", "event_stage_1", "98765432", "intro", "5"]
    # Must not raise; order is implementation-defined but stable
    out = sorted(keys, key=_natural_key)
    assert set(out) == set(keys)
    # Within all-numeric IDs the numeric ordering still holds:
    numeric_only = [k for k in out if k.isdigit()]
    assert numeric_only == ["5", "12345678", "98765432"]


def test_natural_key_mixed_does_not_raise_in_compute_route_order():
    """End-to-end: compute_route_order with mixed-type ids returns
    cleanly (no TypeError from internal sorts or shuffles)."""
    routes = ["12345678", "event_stage_1", "98765432", "intro"]
    adjs = [
        _adj(
            ["12345678", "event_stage_1"],
            ["12345678", "event_stage_1", "intro"],
            "12345678",
            "event_stage_1",
        )
    ]
    order, flips, _ = compute_route_order(routes, adjs)
    assert set(order) == set(routes)
    assert flips >= 0


def test_coord_key_rounds_float_wobble():
    """Same logical node despite sub-precision floating-point wobble."""
    # Precision 7 → rounds at the 7th decimal place. Sub-7th-decimal
    # differences absorb.
    assert _coord_key([1.00000001, 2.00000001]) == _coord_key([1.00000002, 2.00000002])
    # Differences at the 7th decimal place do NOT absorb.
    assert _coord_key([1.0000001, 2.0000001]) != _coord_key([1.0000003, 2.0000003])


# ---------------------------------------------------------------------------
# score() correctness
# ---------------------------------------------------------------------------


def test_score_zero_for_no_flips():
    """Identical signatures → no flips → score 0."""
    # [A,B] -> [A,B,C]: A doesn't flip, B doesn't flip.
    # Natural-sort order [A,B,C].
    adjs = [_adj(["A", "B"], ["A", "B", "C"], "A", "B")]
    assert score(["A", "B", "C"], adjs) == 0


def test_score_counts_full_flip():
    """One sign flip → score = FLIP_WEIGHT (1000)."""
    # Order [A,B,C,D]; in [A,B] B is at offset +0.5, in [A,B,C,D]
    # B is at offset -0.5. Full flip.
    adjs = [_adj(["A", "B"], ["A", "B", "C", "D"], "A", "B")]
    # B flips (+0.5 → -0.5); A doesn't (-0.5 → -1.5, both negative).
    # No separations: A-B adjacent in both (pos 0,1 vs 0,1).
    s = score(["A", "B", "C", "D"], adjs)
    assert s == 1000


def test_score_center_transition_not_a_flip():
    """Offset 0 → ±n is NOT counted as a flip."""
    # In [A,B] B is at +0.5; in [A,B,C] (3 routes) B is at 0 (center).
    # Center-transition, no full flip.
    adjs = [_adj(["A", "B"], ["A", "B", "C"], "A", "B")]
    # A doesn't flip (-0.5 → -1, both negative).
    # B: +0.5 → 0 (center). Not a flip per our definition.
    # No separations (B is adjacent to A both times).
    assert score(["A", "B", "C"], adjs) == 0


def test_score_counts_separation():
    """A-B adjacent in one, separated in the other → 1 separation."""
    # In [A, B] (only 2 routes), A and B are adjacent (positions 0,1).
    # In [A, X, B] with order [A, X, B], A at 0, X at 1, B at 2.
    # A-B distance = 2 (not adjacent). One separation.
    # A and B don't flip (both stay on their sides).
    order = ["A", "X", "B"]
    adjs = [_adj(["A", "B"], ["A", "X", "B"], "A", "B")]
    # A: -0.5 → -1, no flip. B: +0.5 → +1, no flip.
    # Separation: A-B were adjacent (d=1) in [A,B]; in [A,X,B] sorted
    # by order, A at 0, X at 1, B at 2, |0-2|=2 != 1, so separated.
    s = score(order, adjs)
    assert s == 1  # 0 flips, 1 separation


def test_score_flips_dominate_separations():
    """1 flip is worse than 999 separations — tiebreaker is strict."""
    # Construct an order that has 1 flip but 0 separations vs.
    # an order with 0 flips but some separations: the first wins on
    # the primary objective, so its score is HIGHER.
    adjs = [_adj(["A", "B"], ["A", "B", "C", "D"], "A", "B")]
    flip_order = ["A", "B", "C", "D"]  # 1 flip, 0 separations
    no_flip_order = ["C", "B", "A", "D"]  # 0 flips
    assert score(flip_order, adjs) > score(no_flip_order, adjs)
    assert score(flip_order, adjs) >= 1000


# ---------------------------------------------------------------------------
# local_search() correctness
# ---------------------------------------------------------------------------


def test_local_search_improves_from_bad_start():
    """Starting from a known-bad order, local search reaches a better
    local minimum."""
    adjs = [_adj(["A", "B"], ["A", "B", "C", "D"], "A", "B")]
    # Natural sort has 1 flip. A better order exists with 0 flips.
    bad_order = ["A", "B", "C", "D"]
    bad_score = score(bad_order, adjs)
    opt_order, opt_score = local_search(bad_order, adjs)
    assert opt_score < bad_score


# ---------------------------------------------------------------------------
# compute_route_order() — top-level driver
# ---------------------------------------------------------------------------


def test_linear_chain_zero_flips():
    """[A] → [A,B] → [A,B,C] chain: no flips possible."""
    routes = ["A", "B", "C"]
    adjs = [
        _adj(["A"], ["A", "B"], "A"),
        _adj(["A", "B"], ["A", "B", "C"], "A", "B"),
    ]
    order, flips, _ = compute_route_order(routes, adjs)
    assert flips == 0
    assert _count_flips_raw(order, adjs) == 0


def test_parity_inversion_solvable():
    """[A,B] adjacent to [A,B,C,D]: zero flips ACHIEVABLE via clever
    sort order (e.g. [C,B,A,D] keeps A right and B left in both)."""
    routes = ["A", "B", "C", "D"]
    adjs = [_adj(["A", "B"], ["A", "B", "C", "D"], "A", "B")]
    order, flips, _ = compute_route_order(routes, adjs)
    assert flips == 0


def test_disconnected_components_zero_flips():
    """Two unrelated clusters can each be solved independently."""
    routes = ["A", "B", "C", "D", "E", "F"]
    adjs = [
        _adj(["A", "B"], ["A", "B", "C"], "A", "B"),
        _adj(["D", "E"], ["D", "E", "F"], "D", "E"),
    ]
    order, flips, _ = compute_route_order(routes, adjs)
    assert flips == 0


def test_determinism_same_seed():
    """Same input + seed → bit-identical output."""
    routes = ["1", "2", "3", "4", "5"]
    adjs = [
        _adj(["1", "2"], ["1", "2", "3", "4"], "1", "2"),
        _adj(["1", "2", "3", "4"], ["3", "4", "5"], "3", "4"),
    ]
    o1, _, _ = compute_route_order(routes, adjs, seed=42)
    o2, _, _ = compute_route_order(routes, adjs, seed=42)
    assert o1 == o2


def test_previous_order_seeding_stable():
    """If topology is unchanged, seeding from previous_order returns it."""
    routes = ["A", "B", "C", "D"]
    adjs = [_adj(["A", "B"], ["A", "B", "C", "D"], "A", "B")]
    # First-build order
    order_v1, _, _ = compute_route_order(routes, adjs)
    # Second-build, identical inputs, seeded from v1
    order_v2, _, _ = compute_route_order(routes, adjs, previous_order=order_v1)
    assert order_v1 == order_v2


def test_previous_order_handles_new_routes():
    """A route added since the previous build is appended deterministically."""
    routes = ["A", "B", "C", "D", "E"]  # E is new
    adjs = [
        _adj(["A", "B"], ["A", "B", "C"], "A", "B"),
        _adj(["A", "B", "C"], ["A", "B", "C", "D", "E"], "A", "B", "C"),
    ]
    previous = ["A", "B", "C", "D"]  # E was not in the previous build
    order, _, _ = compute_route_order(routes, adjs, previous_order=previous)
    assert set(order) == {"A", "B", "C", "D", "E"}


def test_separation_tiebreaker_engages():
    """When multiple orderings have equal flip count, prefer fewer
    separations.

    Setup: ``[X, Y]`` adjacent to ``[X, Y, Z]``. With order
    ``[X, Z, Y]`` the corridor ``[X, Y, Z]`` renders as ``[X, Z, Y]`` —
    Z is between X and Y, breaking their partnership. With order
    ``[X, Y, Z]`` both corridors keep X and Y adjacent. Both orderings
    have 0 flips, so the tiebreaker must pick the second.
    """
    routes = ["X", "Y", "Z"]
    adjs = [_adj(["X", "Y"], ["X", "Y", "Z"], "X", "Y")]
    order, flips, seps = compute_route_order(routes, adjs)
    assert flips == 0
    assert seps == 0  # X and Y end up adjacent in both corridors


def test_single_route_no_op():
    """Edge case: a single-route map has no ordering to optimize."""
    order, flips, seps = compute_route_order(["A"], [])
    assert order == ["A"]
    assert flips == 0 and seps == 0


def test_empty_no_op():
    """Edge case: no routes at all."""
    order, flips, seps = compute_route_order([], [])
    assert order == []
    assert flips == 0 and seps == 0


# ---------------------------------------------------------------------------
# enumerate_modes()
# ---------------------------------------------------------------------------


def test_enumerate_modes_summer_only():
    routes_meta = {
        "A": {"summer": True, "winter": False, "emergency": False},
        "B": {"summer": True, "winter": False, "emergency": False},
    }
    modes = enumerate_modes(routes_meta)
    assert set(modes.keys()) == {"summer"}
    assert modes["summer"] == frozenset({"A", "B"})


def test_enumerate_modes_mixed_seasons_with_emergency():
    routes_meta = {
        "A": {"summer": True, "winter": False, "emergency": False},
        "B": {"summer": False, "winter": True, "emergency": False},
        "C": {"summer": False, "winter": False, "emergency": True},
    }
    modes = enumerate_modes(routes_meta)
    assert set(modes.keys()) == {"summer", "summer_emergency", "winter", "winter_emergency"}
    assert modes["summer"] == frozenset({"A"})
    assert modes["summer_emergency"] == frozenset({"A", "C"})
    assert modes["winter"] == frozenset({"B"})
    assert modes["winter_emergency"] == frozenset({"B", "C"})


def test_enumerate_modes_summer_winter_overlap():
    """A route flagged both summer AND winter appears in both modes."""
    routes_meta = {
        "Y": {"summer": True, "winter": True, "emergency": False},
        "S": {"summer": True, "winter": False, "emergency": False},
        "W": {"summer": False, "winter": True, "emergency": False},
    }
    modes = enumerate_modes(routes_meta)
    assert modes["summer"] == frozenset({"Y", "S"})
    assert modes["winter"] == frozenset({"Y", "W"})


# ---------------------------------------------------------------------------
# build_corridor_adjacencies() — mode filtering
# ---------------------------------------------------------------------------


def test_adjacency_filters_invisible_routes():
    """Routes filtered out of `visible_routes` don't appear in the
    effective signatures, so corridor adjacencies collapse."""
    features = [
        _make_feature([[0, 0], [1, 1]], ["A", "B", "X"]),
        _make_feature([[1, 1], [2, 2]], ["A", "B"]),
    ]
    # All routes visible: corridor signatures {A,B,X} and {A,B} differ
    # at the junction, so they're adjacent.
    full = build_corridor_adjacencies(features)
    assert len(full) == 1

    # Hide X: both signatures collapse to {A, B} — same signature on
    # both sides of the junction → no adjacency.
    filtered = build_corridor_adjacencies(features, visible_routes={"A", "B"})
    assert len(filtered) == 0


def test_adjacency_handles_empty_visible_set():
    """If visible_routes is empty, no adjacencies."""
    features = [_make_feature([[0, 0], [1, 1]], ["A", "B"])]
    adjs = build_corridor_adjacencies(features, visible_routes=set())
    assert adjs == []


# ---------------------------------------------------------------------------
# compute_route_orders() — per-mode driver
# ---------------------------------------------------------------------------


def test_compute_route_orders_single_mode():
    """All-summer map: just one mode, one order."""
    routes_meta = {
        "A": {"summer": True, "winter": False, "emergency": False},
        "B": {"summer": True, "winter": False, "emergency": False},
    }
    features = [
        _make_feature([[0, 0], [1, 1]], ["A", "B"]),
        _make_feature([[1, 1], [2, 2]], ["A"]),
    ]
    orders, stats = compute_route_orders(routes_meta, features)
    assert set(orders.keys()) == {"summer"}
    assert set(orders["summer"]) == {"A", "B"}


def test_compute_route_orders_dedups_identical_subsets():
    """If two mode keys produce the same visible subset, share the order."""
    routes_meta = {
        # No emergency routes — summer == summer_emergency, but only
        # summer is enumerated since emergency is empty.
        "A": {"summer": True, "winter": True, "emergency": False},
        "B": {"summer": True, "winter": True, "emergency": False},
    }
    features = [_make_feature([[0, 0], [1, 1]], ["A", "B"])]
    orders, _ = compute_route_orders(routes_meta, features)
    # Summer and winter both contain {A, B} → both modes enumerated
    # → both orders identical
    assert orders["summer"] is orders["winter"] or orders["summer"] == orders["winter"]


# ---------------------------------------------------------------------------
# Real-world regression: RAMBA
# ---------------------------------------------------------------------------


def test_ramba_regression():
    """RAMBA optimization stays at ≤2 sign flips across the union
    adjacency (defensive ceiling — current empirical is 1)."""
    p = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "build",
        "ramba",
        "trails.geojson",
    )
    p = os.path.abspath(p)
    if not os.path.exists(p):
        # Build artifacts not present — skip in CI; useful only when
        # running locally with a built map.
        import pytest

        pytest.skip(f"RAMBA build artifacts not at {p}")
    with open(p) as f:
        g = json.load(f)
    features = g["features"]
    routes_meta = g["metadata"]["routes"]
    all_routes = list(routes_meta.keys())
    adjs = build_corridor_adjacencies(features)
    order, flips, seps = compute_route_order(all_routes, adjs)
    assert flips <= 2, f"Regression: RAMBA flip count {flips} > 2"
    assert order  # not empty


# ---------------------------------------------------------------------------
# Self-runner fallback (no pytest required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
