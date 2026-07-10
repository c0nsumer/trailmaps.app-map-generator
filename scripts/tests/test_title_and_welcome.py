"""Tests for derived titles, the title_suffix brand tail, and the
Welcome-body default.

`title` is an optional override derived as "{name} Map"; `title_suffix`
is a site-wide brand tail that belongs to the <title> element alone; and
`welcome.body` defaults to `about.description` so the map is described
once rather than hand-copied into two yaml keys.

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
# title_suffix scoping (template_inject.copy_templates)
# ---------------------------------------------------------------------------


def test_title_suffix_lands_on_title_element_only(tmp_path):
    config = {**BASE, "title_suffix": " | example.org"}
    copy_templates(config, str(tmp_path), dict(TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "<title>My Trails Map | example.org</title>" in html
    # og:site_name already names the site; the brand tail must not be
    # repeated in the share-card titles.
    assert 'property="og:title" content="My Trails Map"' in html
    assert 'name="twitter:title" content="My Trails Map"' in html


def test_title_suffix_absent_by_default(tmp_path):
    """The engine ships unbranded for OSS consumers."""
    copy_templates(dict(BASE), str(tmp_path), dict(TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<title>My Trails Map</title>" in html


def test_title_containing_a_backslash_escape_survives_substitution(tmp_path):
    """A plain re.sub replacement string would read `\\1` as a group ref."""
    copy_templates({**BASE, "title": r"Back\1slash Map"}, str(tmp_path), dict(TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert r"<title>Back\1slash Map</title>" in html


# ---------------------------------------------------------------------------
# Welcome body defaulting (template_inject.inject_config_into_template)
# ---------------------------------------------------------------------------

ABOUT = {"description": "An unofficial map of the trails."}


def test_welcome_body_defaults_to_about_description():
    """The common case: no `welcome` key at all, so the map's one
    description reaches the Welcome modal without being hand-copied."""
    welcome = _config_obj({**BASE, "about": ABOUT})["welcome"]
    assert welcome == {"body": "An unofficial map of the trails."}


def test_welcome_false_stays_suppressed():
    """`false` must not be collapsed into the "use defaults" None."""
    assert _config_obj({**BASE, "about": ABOUT, "welcome": False})["welcome"] is False


def test_explicit_welcome_body_wins():
    config = {**BASE, "about": ABOUT, "welcome": {"body": "Course opens at 9am."}}
    assert _config_obj(config)["welcome"]["body"] == "Course opens at 9am."


def test_welcome_dict_without_body_keeps_its_other_keys():
    config = {**BASE, "about": ABOUT, "welcome": {"show_controls_hint": False}}
    welcome = _config_obj(config)["welcome"]
    assert welcome["body"] == "An unofficial map of the trails."
    assert welcome["show_controls_hint"] is False


def test_welcome_stays_none_without_about_description():
    """Nothing to say, so the runtime takes the framework default rather
    than an object that says nothing."""
    assert _config_obj(dict(BASE))["welcome"] is None
    assert _config_obj({**BASE, "about": {"description": "   "}})["welcome"] is None


def test_welcome_defaulting_does_not_mutate_the_caller_config():
    config = {**BASE, "about": ABOUT, "welcome": {"show_controls_hint": False}}
    _config_obj(config)
    assert config["welcome"] == {"show_controls_hint": False}
