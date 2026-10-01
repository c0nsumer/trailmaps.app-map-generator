"""CONFIG.hasTerrain emission.

build.py stashes config["_has_terrain"] = os.path.exists(terrain.pmtiles)
after the fetch step; the injector emits it as CONFIG.hasTerrain so the
runtime can skip its terrain HEAD probe. The false/absent case must stay
false (the runtime then falls back to probing), so a caller that bypasses
build.py degrades to the old behavior instead of claiming terrain exists.

Run from repo root:
    python -m pytest scripts/tests/test_has_terrain.py -v
"""

from conftest import MINIMAL_CONFIG, inject_config


def test_has_terrain_true_when_stash_set():
    assert inject_config({**MINIMAL_CONFIG, "_has_terrain": True})["hasTerrain"] is True


def test_has_terrain_false_when_stash_absent():
    # No stash at all (injector called outside build.py's flow).
    assert inject_config(dict(MINIMAL_CONFIG))["hasTerrain"] is False


def test_has_terrain_false_when_stash_false():
    assert inject_config({**MINIMAL_CONFIG, "_has_terrain": False})["hasTerrain"] is False
