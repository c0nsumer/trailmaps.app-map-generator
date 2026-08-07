"""CONFIG.hasTerrain emission.

build.py stashes config["_has_terrain"] = os.path.exists(terrain.pmtiles)
after the fetch step; the injector emits it as CONFIG.hasTerrain so the
runtime can skip its terrain HEAD probe. The false/absent case must stay
false (the runtime then falls back to probing), so a caller that bypasses
build.py degrades to the old behavior instead of claiming terrain exists.

Run from repo root:
    python -m pytest scripts/tests/test_has_terrain.py -v
"""

import json
import os
import re
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_inject import inject_config_into_template  # noqa: E402

TRAILS = {"metadata": {"routes": {}}, "features": []}

BASE = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
    "center": [0, 0],
}


def _config_obj(config):
    out = inject_config_into_template("/*__CONFIG__*/", config, dict(TRAILS))
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def test_has_terrain_true_when_stash_set():
    assert _config_obj({**BASE, "_has_terrain": True})["hasTerrain"] is True


def test_has_terrain_false_when_stash_absent():
    # No stash at all (injector called outside build.py's flow).
    assert _config_obj(dict(BASE))["hasTerrain"] is False


def test_has_terrain_false_when_stash_false():
    assert _config_obj({**BASE, "_has_terrain": False})["hasTerrain"] is False
