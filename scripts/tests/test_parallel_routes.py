"""Tests for parallel_routes.canonicalize_shared_corridors.

Run from repo root:
    python -m pytest scripts/tests/test_parallel_routes.py -v
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parallel_routes import canonicalize_shared_corridors  # noqa: E402

# ---- helpers ---------------------------------------------------------

# A corridor with an interior loop: A -> B -> loop -> C -> D. Traversing
# the loop clockwise vs counterclockwise yields the same vertex SET with
# a different vertex ORDER - the bdb pond-loop shape that mirrored the
# 2-Mile's lane.
_A = [-83.3380, 43.0460]
_B = [-83.3375, 43.0462]
_L1 = [-83.3370, 43.0465]
_L2 = [-83.3368, 43.0461]
_C = [-83.3366, 43.0463]
_D = [-83.3360, 43.0464]

FORWARD = [_A, _B, _L1, _L2, _C, _D]
LOOP_FLIPPED = [_A, _B, _L2, _L1, _C, _D]
REVERSED = list(reversed(FORWARD))


def feat(route_id, shared, coords, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]},
        "properties": {"route_id": route_id, "shared_routes": list(shared), **props},
    }


def coords(f):
    return f["geometry"]["coordinates"]


# ---- tests -----------------------------------------------------------


def test_reversed_copy_rewritten_to_canon():
    shared = ["-238", "-267"]
    canon = feat("-267", shared, FORWARD)
    flipped = feat("-238", shared, REVERSED)
    rewritten, skipped = canonicalize_shared_corridors([canon, flipped])
    assert rewritten == 1
    assert skipped == 0
    # Natural-key order puts -238 before -267, so -238's copy is the
    # canon and -267's copy is the one rewritten - both must end up
    # identical either way.
    assert coords(canon) == coords(flipped)


def test_loop_traversal_mismatch_rewritten():
    # Same endpoints, same vertex set, different interior order (the
    # bdb symptom shape).
    shared = ["-238", "-267", "-314"]
    a = feat("-238", shared, FORWARD)
    b = feat("-267", shared, LOOP_FLIPPED)
    c = feat("-314", shared, FORWARD)
    rewritten, skipped = canonicalize_shared_corridors([a, b, c])
    assert rewritten == 1
    assert skipped == 0
    assert coords(a) == coords(b) == coords(c)


def test_aligned_copies_are_noop():
    shared = ["-238", "-267"]
    a = feat("-238", shared, FORWARD)
    b = feat("-267", shared, FORWARD)
    rewritten, skipped = canonicalize_shared_corridors([a, b])
    assert rewritten == 0
    assert skipped == 0


def test_idempotent():
    shared = ["-238", "-267"]
    a = feat("-238", shared, FORWARD)
    b = feat("-267", shared, REVERSED)
    canonicalize_shared_corridors([a, b])
    rewritten, _ = canonicalize_shared_corridors([a, b])
    assert rewritten == 0


def test_canon_is_natural_key_lowest_route():
    shared = ["-238", "-267"]
    low = feat("-238", shared, FORWARD)
    high = feat("-267", shared, REVERSED)
    canonicalize_shared_corridors([low, high])
    # -238 sorts before -267, so its vertex order wins.
    assert coords(low) == FORWARD
    assert coords(high) == FORWARD


def test_out_and_back_group_left_alone():
    # One route rides the corridor twice (out and back): its copies are
    # inherently anti-parallel, so the whole group must be skipped.
    shared = ["-238", "-267"]
    out = feat("-238", shared, FORWARD)
    back = feat("-238", shared, REVERSED)
    other = feat("-267", shared, REVERSED)
    rewritten, skipped = canonicalize_shared_corridors([out, back, other])
    assert rewritten == 0
    assert skipped == 0
    assert coords(other) == REVERSED


def test_oneway_group_skipped():
    shared = ["-238", "-267"]
    a = feat("-238", shared, FORWARD, oneway="yes")
    b = feat("-267", shared, REVERSED)
    rewritten, skipped = canonicalize_shared_corridors([a, b])
    assert rewritten == 0
    assert skipped == 1
    assert coords(b) == REVERSED


def test_distinct_corridors_same_sig_not_mixed():
    # Two separate trail sections shared by the same route pair must
    # not be treated as copies of each other (their vertex sets
    # differ).
    shared = ["-238", "-267"]
    elsewhere = [[-83.3400, 43.0400], [-83.3401, 43.0402], [-83.3403, 43.0401]]
    a = feat("-238", shared, FORWARD)
    b = feat("-267", shared, elsewhere)
    rewritten, skipped = canonicalize_shared_corridors([a, b])
    assert rewritten == 0
    assert skipped == 0
    assert coords(b) == elsewhere


def test_stubs_and_solo_features_ignored():
    shared = ["-238", "-267"]
    stub = feat("-238", ["-238"], FORWARD, isStub=True, offset_index=0.5)
    variant = feat("-238", shared, REVERSED, _subwayHostVariant=True)
    solo = feat("-267", ["-267"], REVERSED)
    rewritten, skipped = canonicalize_shared_corridors([stub, variant, solo])
    assert rewritten == 0
    assert skipped == 0
    assert coords(variant) == REVERSED
