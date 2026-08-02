"""Tests for cache_manifest.py - per-map cache manifests and pruning.

The safety property under test throughout: pruning can never delete
another map's cache entries or anything outside the cache dir. The
shared cache/ dir has no ownership records, so these fences (own old
manifest only, sibling-claim protection, filename allowlist) are the
whole guarantee.

Run from repo root:
    python -m pytest scripts/tests/test_cache_manifest.py -v
"""

import hashlib
import json
import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cache_manifest  # noqa: E402
import overpass  # noqa: E402
import pytest  # noqa: E402

# Valid-shaped relative entry names (the allowlist is strict about them).
OP_A = "overpass_" + "a" * 12 + ".json"
OP_B = "overpass_" + "b" * 12 + ".json"
RS_A = "route_stats/elev_100_" + "c" * 16 + ".json"
DA_A = "derive_accent/" + "d" * 16 + ".json"


def _plant(cache_dir, rel, content="{}"):
    path = os.path.join(cache_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _cats(cache_dir, trails=(), pois=(), stats=(), accent=()):
    """Build an absolute-path categories dict as build.py assembles it."""
    j = lambda rels: [os.path.join(cache_dir, r) for r in rels]  # noqa: E731
    return {
        "overpass_trails": j(trails),
        "overpass_pois": j(pois),
        "route_stats": j(stats),
        "derive_accent": j(accent),
    }


@pytest.fixture(autouse=True)
def _clean_collector():
    # The collector is module-global; a recording left by another test
    # (or an aborted one) must not leak across tests.
    cache_manifest.drain()
    yield
    cache_manifest.drain()


def test_record_drain_dedups_and_clears():
    cache_manifest.record("/c/a.json")
    cache_manifest.record("/c/b.json")
    cache_manifest.record("/c/a.json")
    assert cache_manifest.drain() == ["/c/a.json", "/c/b.json"]
    assert cache_manifest.drain() == []


def test_save_load_roundtrip(tmp_path):
    cache_dir = str(tmp_path)
    rel = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A], stats=[RS_A]))
    assert rel == {
        "overpass_trails": [OP_A],
        "overpass_pois": [],
        "route_stats": [RS_A],
        "derive_accent": [],
    }
    assert cache_manifest.load(cache_dir, "mymap") == rel
    manifests = os.listdir(os.path.join(cache_dir, "manifests"))
    assert manifests == ["mymap.json"]  # no .tmp sibling left behind


def test_save_drops_paths_outside_cache_dir(tmp_path):
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir)
    outside = str(tmp_path / "elsewhere" / OP_A)
    cats = _cats(cache_dir, trails=[OP_A])
    cats["overpass_trails"].append(outside)
    rel = cache_manifest.save(cache_dir, "mymap", cats)
    assert rel["overpass_trails"] == [OP_A]


def test_save_refuses_unsafe_slug(tmp_path):
    assert cache_manifest.save(str(tmp_path), "../evil", _cats(str(tmp_path))) is None
    assert not os.path.exists(os.path.join(str(tmp_path), "manifests"))


@pytest.mark.parametrize(
    "content",
    [
        None,  # missing file
        "{truncated",  # corrupt JSON
        json.dumps({"version": 99, "categories": {}}),  # wrong version
        json.dumps({"version": 1, "categories": {"overpass_trails": "notalist"}}),
    ],
)
def test_load_returns_none_on_missing_corrupt_or_wrong_shape(tmp_path, content):
    if content is not None:
        _plant(str(tmp_path), "manifests/mymap.json", content)
    assert cache_manifest.load(str(tmp_path), "mymap") is None


def test_prune_removes_only_stale_candidates(tmp_path):
    cache_dir = str(tmp_path)
    path_a = _plant(cache_dir, OP_A, content='{"a": 1}')
    path_b = _plant(cache_dir, OP_B)
    size_a = os.path.getsize(path_a)
    old = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A, OP_B]))
    new = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_B]))
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    assert (removed, freed) == (1, size_a)
    assert not os.path.exists(path_a)
    assert os.path.exists(path_b)


def test_prune_protects_other_maps_claims(tmp_path):
    cache_dir = str(tmp_path)
    path_a = _plant(cache_dir, OP_A)
    cache_manifest.save(cache_dir, "othermap", _cats(cache_dir, pois=[OP_A]))
    old = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A]))
    new = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir))
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    assert (removed, freed) == (0, 0)
    assert os.path.exists(path_a)


def test_prune_allowlist_blocks_unexpected_paths(tmp_path):
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir)
    # A hand-corrupted (or adversarial) manifest could claim anything;
    # only allowlist-shaped names are ever deletable.
    planted = [
        _plant(cache_dir, "vendor/pmtiles.js"),
        _plant(cache_dir, "osm_diff/x/trails.prev.geojson"),
        _plant(
            cache_dir,
            "manifests/othermap.json",
            json.dumps(
                {
                    "version": 1,
                    "slug": "othermap",
                    "categories": {
                        "overpass_trails": [],
                        "overpass_pois": [],
                        "route_stats": [],
                        "derive_accent": [],
                    },
                }
            ),
        ),
        _plant(str(tmp_path), "evil.json"),  # outside cache_dir
    ]
    old = {
        "overpass_trails": [
            "vendor/pmtiles.js",
            "osm_diff/x/trails.prev.geojson",
            "manifests/othermap.json",
            "../evil.json",
            os.path.join(str(tmp_path), "evil.json"),
        ],
        "overpass_pois": [],
        "route_stats": [],
        "derive_accent": [],
    }
    new = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir))
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    assert (removed, freed) == (0, 0)
    for p in planted:
        assert os.path.exists(p)


def test_prune_tolerates_missing_candidate_files(tmp_path):
    cache_dir = str(tmp_path)
    old = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A], accent=[DA_A]))
    new = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir))
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    assert (removed, freed) == (0, 0)


def test_prune_aborts_when_sibling_manifest_unreadable(tmp_path):
    cache_dir = str(tmp_path)
    path_a = _plant(cache_dir, OP_A)
    _plant(cache_dir, "manifests/othermap.json", "{truncated")
    old = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A]))
    new = cache_manifest.save(cache_dir, "mymap", _cats(cache_dir))
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    # The sibling's claims are unknowable, so nothing may be deleted.
    assert (removed, freed) == (0, 0)
    assert os.path.exists(path_a)


def test_reuse_carry_forward_protects_trail_entries(tmp_path):
    cache_dir = str(tmp_path)
    path_a = _plant(cache_dir, OP_A)
    # Build 1: real fetch recorded the trail entry.
    cache_manifest.save(cache_dir, "mymap", _cats(cache_dir, trails=[OP_A]))
    # Build 2: reuse build - nothing recorded for trails; build.py
    # carries the previous claims forward exactly like this.
    old = cache_manifest.load(cache_dir, "mymap")
    cats = _cats(cache_dir)
    cats["overpass_trails"] = [os.path.join(cache_dir, p) for p in old.get("overpass_trails", [])]
    new = cache_manifest.save(cache_dir, "mymap", cats)
    removed, freed = cache_manifest.prune(cache_dir, "mymap", old, new)
    assert (removed, freed) == (0, 0)
    assert os.path.exists(path_a)


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_overpass_query_records_path_on_hit_and_miss(tmp_path, monkeypatch):
    cache_dir = str(tmp_path)

    # Hit: pre-write the cache entry for a known query.
    hit_query = "[out:json];node(1);out;"
    h = hashlib.md5(hit_query.encode()).hexdigest()[:12]
    hit_path = _plant(cache_dir, f"overpass_{h}.json", '{"elements": [{"type": "node"}]}')

    def _boom(*a, **k):
        raise AssertionError("network must not be hit for a cached query")

    monkeypatch.setattr(overpass.requests, "post", _boom)
    overpass.query(hit_query, cache_dir=cache_dir)
    assert cache_manifest.drain() == [hit_path]

    # Miss: canned live response (no osm3s field, so the freshness
    # check no-ops), recorded and written through to the cache.
    miss_query = "[out:json];node(2);out;"
    resp = _FakeResp({"elements": [{"type": "node", "id": 2}]})
    monkeypatch.setattr(overpass.requests, "post", lambda *a, **k: resp)
    overpass.query(miss_query, cache_dir=cache_dir)
    h2 = hashlib.md5(miss_query.encode()).hexdigest()[:12]
    miss_path = os.path.join(cache_dir, f"overpass_{h2}.json")
    assert cache_manifest.drain() == [miss_path]
    assert os.path.exists(miss_path)
