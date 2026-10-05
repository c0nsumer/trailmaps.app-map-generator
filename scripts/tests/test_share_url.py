"""Share-link round trip, run against the real template functions in Node.

templates/app.js has no module system, but its top-level functions
start with `function name(` at column 0 and end with `}` at column 0.
The harness slices the share functions out by that shape and runs them
in a vm context with stub globals, so the test exercises the shipped
code rather than a copy. A renamed or reshaped function fails the
slice assertion instead of silently testing nothing.

Skips when Node is absent, like test_eslint.py: Node is optional dev
tooling, not a build prerequisite.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_JS = REPO_ROOT / "templates" / "app.js"

FUNCTIONS = [
    "isViewInRange",
    "consumeShareHash",
    "buildShareUrl",
    "shareBaseUrl",
    "sharedTrailName",
]

# Each case sets the stub state, then either builds a link and consumes
# it, or consumes a raw hash. `stripped` records whether the hash left
# the address bar.
HARNESS = r"""
const vm = require("vm");
const src = JSON.parse(require("fs").readFileSync(0, "utf8"));
const results = [];
for (const c of src.cases) {
    const ctx = {
        URL,
        document: { querySelector: () => null },
        isRouteMode: () => true,
        map: { getCenter: () => ({ lng: c.lng, lat: c.lat }), getZoom: () => c.zoom },
        highlight: c.highlight || null,
        _poiHighlightRef: null,
        _trailPopup: c.trail ? {} : null,
        tapLiftTrail: c.trail || null,
    };
    ctx.window = {
        location: { href: "https://example.test/map/", hash: "" },
        history: { replaceState: (s, t, u) => { ctx.window.location.hash = new URL(u).hash; } },
    };
    vm.createContext(ctx);
    vm.runInContext(src.code, ctx);
    let hash = c.hash;
    if (hash === undefined) {
        const url = vm.runInContext("buildShareUrl()", ctx);
        hash = new URL(url).hash;
    }
    ctx.window.location.href = "https://example.test/map/" + hash;
    ctx.window.location.hash = hash;
    const view = vm.runInContext("consumeShareHash()", ctx);
    results.push({ view, stripped: ctx.window.location.hash === "" });
}
process.stdout.write(JSON.stringify(results));
"""


def _slice(source, name):
    m = re.search(rf"^function {name}\(.*?^\}}$", source, re.M | re.S)
    assert m, f"function {name} not found at column 0 in templates/app.js"
    return m.group(0)


def _run(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed; share-link test is optional dev tooling")
    source = APP_JS.read_text(encoding="utf-8")
    code = "\n".join(_slice(source, n) for n in FUNCTIONS)
    result = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps({"code": code, "cases": cases}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_valid_links_round_trip():
    base = {"lng": -87.12345, "lat": 45.67891, "zoom": 14.5}
    cases = [
        {**base, "trail": "Bob's / Loop"},
        {**base, "trail": "Sentier de l'Écureuil"},
        {**base, "highlight": {"kind": "route", "key": "12345"}},
        {**base, "highlight": {"kind": "rating", "key": ""}},
    ]
    expected = [
        {"kind": "trail", "key": "Bob's / Loop"},
        {"kind": "trail", "key": "Sentier de l'Écureuil"},
        {"kind": "route", "key": "12345"},
        {"kind": "rating", "key": ""},
    ]
    for res, want in zip(_run(cases), expected, strict=True):
        assert res["view"] == {
            "center": [-87.12345, 45.67891], "zoom": 14.5, "highlight": want,
        }
        assert res["stripped"]


@pytest.mark.parametrize("hash_", [
    "#share=14/95/-87",
    "#share=14/45/-200",
    "#share=99/45/-87",
    "#share=14/45",
    "#share=abc/45/-87",
])
def test_unusable_links_open_the_default_view(hash_):
    [res] = _run([{"hash": hash_, "lng": 0, "lat": 0, "zoom": 0}])
    assert res["view"] is None
    assert res["stripped"]
