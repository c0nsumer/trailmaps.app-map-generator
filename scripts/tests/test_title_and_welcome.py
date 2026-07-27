"""Tests for derived titles and Welcome-config pass-through.

`title` is an optional override derived as "{name} Map".
`welcome.body` is the map's one descriptive text (the retired
`about.description` folded into it), so injection is a plain
pass-through: dict stays a dict, false stays false, empty means None.

Run from repo root:
    python -m pytest scripts/tests/test_title_and_welcome.py -v
"""

import json
import os
import re
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_inject import copy_templates, inject_config_into_template  # noqa: E402

TRAILS = {"metadata": {"routes": {}}, "features": []}

# Smallest config inject_config_into_template accepts: every CONFIG_SPEC
# entry with a None default is a required read.
BASE = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
    "center": [0, 0],
}


def _config_obj(config):
    """Run the injector and parse the CONFIG object back out."""
    out = inject_config_into_template("/*__CONFIG__*/", config, dict(TRAILS))
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))


def _write_config(tmp_path, body):
    cfg_path = tmp_path / "my-trails.yaml"
    cfg_path.write_text(body, encoding="utf-8")
    return str(cfg_path)


# ---------------------------------------------------------------------------
# Title derivation (build.load_config)
# ---------------------------------------------------------------------------


def test_title_derived_from_name_when_absent(tmp_path):
    from build import load_config

    config = load_config(_write_config(tmp_path, "name: My Trails\nslug: my-trails\n"))
    assert config["title"] == "My Trails Map"


def test_explicit_title_is_not_overwritten(tmp_path):
    from build import load_config

    config = load_config(
        _write_config(
            tmp_path,
            'name: Custer\nslug: custer\ntitle: "Custer\'s Last Stand Route Map"\n',
        )
    )
    assert config["title"] == "Custer's Last Stand Route Map"


def test_derivation_does_not_dedupe_a_name_ending_in_map(tmp_path):
    """The engine does not second-guess the curator; the deploying
    orchestrator's pre-validate is what forbids such names."""
    from build import load_config

    config = load_config(_write_config(tmp_path, "name: Triple Trail Challenge Map\nslug: ttc\n"))
    assert config["title"] == "Triple Trail Challenge Map Map"


# ---------------------------------------------------------------------------
# <title> emission (template_inject.copy_templates)
# ---------------------------------------------------------------------------


def test_title_emitted_unbranded(tmp_path):
    """The engine ships unbranded output for every consumer. A deploying
    site that wants a brand tail on the <title> appends it in its own
    post-processing (trailmaps.app does this in inject-og-meta.py)."""
    copy_templates(dict(BASE), str(tmp_path), dict(TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<title>My Trails Map</title>" in html
    assert 'property="og:title" content="My Trails Map"' in html
    assert 'name="twitter:title" content="My Trails Map"' in html


def test_app_js_never_writes_document_title():
    """The <title> element's build-time value must be the only writer.

    app.js used to run `document.title = CONFIG.title` at init, which was
    a no-op while the element and CONFIG.title were always the same
    string. Once a deployer post-processes a brand tail onto the element
    (trailmaps.app appends " | trailmaps.app" in inject-og-meta.py), that
    runtime write silently strips it the moment the app boots: the tab
    briefly shows the branded title, then loses it. Field-hit 2026-07-10.
    If a runtime title write is ever genuinely needed, it must preserve
    the element's existing tail rather than overwrite from CONFIG."""
    app_js_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "templates", "app.js"
    )
    with open(app_js_path, encoding="utf-8") as f:
        app_js = f.read()
    writes = re.findall(r"^(?!\s*//).*document\.title\s*=", app_js, re.MULTILINE)
    assert writes == [], f"app.js writes document.title: {writes}"


def test_title_containing_a_backslash_escape_survives_substitution(tmp_path):
    """A plain re.sub replacement string would read `\\1` as a group ref."""
    copy_templates({**BASE, "title": r"Back\1slash Map"}, str(tmp_path), dict(TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert r"<title>Back\1slash Map</title>" in html


# ---------------------------------------------------------------------------
# Welcome config pass-through (template_inject.inject_config_into_template)
# ---------------------------------------------------------------------------

ABOUT = {"curator": {"name": "A Curator"}}


def test_welcome_dict_passes_through():
    """`welcome.body` is the one authored home of the map's description;
    injection must hand it to the runtime untouched."""
    config = {**BASE, "about": ABOUT, "welcome": {"body": "An unofficial map."}}
    assert _config_obj(config)["welcome"] == {"body": "An unofficial map."}


def test_welcome_false_stays_suppressed():
    """`false` must not be collapsed into the "use defaults" None."""
    assert _config_obj({**BASE, "about": ABOUT, "welcome": False})["welcome"] is False


def test_welcome_dict_without_body_keeps_its_other_keys():
    """No defaulting from `about` — the retired `about.description` must
    never leak back into the welcome body."""
    config = {
        **BASE,
        "about": {**ABOUT, "description": "legacy text"},
        "welcome": {"show_controls_hint": False},
    }
    welcome = _config_obj(config)["welcome"]
    assert welcome == {"show_controls_hint": False}


def test_welcome_stays_none_when_absent_or_empty():
    """Nothing configured, so the runtime takes the framework default
    rather than an object that says nothing."""
    assert _config_obj(dict(BASE))["welcome"] is None
    assert _config_obj({**BASE, "welcome": {}})["welcome"] is None
