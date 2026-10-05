"""Tests for derived titles and Welcome-config pass-through.

`title` is an optional override derived as "{name} Map".
`welcome.body` is the map's one descriptive text (the retired
`about.description` folded into it), so injection is a plain
pass-through: dict stays a dict, false stays false, empty means None.

Run from repo root:
    python -m pytest scripts/tests/test_title_and_welcome.py -v
"""

import os
import re

from conftest import EMPTY_TRAILS, MINIMAL_CONFIG, inject_config
from template_inject import _process_index_html, copy_templates


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
    copy_templates(dict(MINIMAL_CONFIG), str(tmp_path), dict(EMPTY_TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<title>My Trails Map</title>" in html
    assert 'property="og:title" content="My Trails Map"' in html
    assert 'name="twitter:title" content="My Trails Map"' in html


def test_app_js_never_writes_document_title():
    """The <title> element's build-time value must be the only writer.

    A deployer may post-process a brand tail onto the element (the
    trailmaps.app orchestrator does). A runtime write from CONFIG.title
    would strip that tail the moment the app boots. A genuine runtime
    title write must preserve the element's existing tail."""
    app_js_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "templates", "app.js"
    )
    with open(app_js_path, encoding="utf-8") as f:
        app_js = f.read()
    writes = re.findall(r"^(?!\s*//).*document\.title\s*=", app_js, re.MULTILINE)
    assert writes == [], f"app.js writes document.title: {writes}"


def test_title_containing_a_backslash_escape_survives_substitution(tmp_path):
    """A plain re.sub replacement string would read `\\1` as a group ref."""
    copy_templates({**MINIMAL_CONFIG, "title": r"Back\1slash Map"}, str(tmp_path), dict(EMPTY_TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert r"<title>Back\1slash Map</title>" in html


def test_title_and_name_are_escaped_in_the_page(tmp_path):
    """Markup characters in the name or title reach <title> and og:title
    escaped, and never as a raw tag in index.html."""
    cfg = {**MINIMAL_CONFIG, "name": 'A "B" <i>&', "title": 'A "B" <i>& Map'}
    copy_templates(cfg, str(tmp_path), dict(EMPTY_TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<title>A \"B\" &lt;i&gt;&amp; Map</title>" in html
    assert 'property="og:title" content="A &quot;B&quot; &lt;i&gt;&amp; Map"' in html
    assert "<i>" not in html


# ---------------------------------------------------------------------------
# Welcome config pass-through (template_inject.inject_config_into_template)
# ---------------------------------------------------------------------------

ABOUT = {"curator": {"name": "A Curator"}}


def test_welcome_dict_passes_through():
    """`welcome.body` is the one authored home of the map's description;
    injection must hand it to the runtime untouched."""
    config = {**MINIMAL_CONFIG, "about": ABOUT, "welcome": {"body": "An unofficial map."}}
    assert inject_config(config)["welcome"] == {"body": "An unofficial map."}


def test_welcome_false_stays_suppressed():
    """`false` must not be collapsed into the "use defaults" None."""
    assert inject_config({**MINIMAL_CONFIG, "about": ABOUT, "welcome": False})["welcome"] is False


def test_welcome_dict_without_body_keeps_its_other_keys():
    """No defaulting from `about` - the retired `about.description` must
    never leak back into the welcome body."""
    config = {
        **MINIMAL_CONFIG,
        "about": {**ABOUT, "description": "legacy text"},
        "welcome": {"show_controls_hint": False},
    }
    welcome = inject_config(config)["welcome"]
    assert welcome == {"show_controls_hint": False}


def test_welcome_stays_none_when_absent_or_empty():
    """Nothing configured, so the runtime takes the framework default
    rather than an object that says nothing."""
    assert inject_config(dict(MINIMAL_CONFIG))["welcome"] is None
    assert inject_config({**MINIMAL_CONFIG, "welcome": {}})["welcome"] is None


# ---------------------------------------------------------------------------
# lane plugin script tag (templates/index.html)
# ---------------------------------------------------------------------------


def test_route_key_defaults_on_and_false_reaches_the_page():
    assert inject_config(dict(MINIMAL_CONFIG))["routeKey"] is True
    assert inject_config({**MINIMAL_CONFIG, "route_key": False})["routeKey"] is False


def test_every_map_loads_the_lane_plugin_and_carries_the_boot_note(tmp_path):
    # The plugin is the only thing that draws a route, so its script is
    # in every page, ahead of app.js (deferred scripts run in document
    # order, and app.js init refuses to start without the global).
    copy_templates(dict(MINIMAL_CONFIG), str(tmp_path), dict(EMPTY_TRAILS))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    lanes = html.index('<script src="vendor/maplibre-gl-lanes.js" defer></script>')
    assert lanes < html.index('<script src="app.js" defer></script>')
    # The static boot-failure note ships in the page and app.js takes it
    # down first thing; neither half is any use without the other.
    assert 'id="boot-fallback"' in html
    # The note comes down in the template's first lines, before any other
    # boot work (the built file starts with the injected CONFIG).
    template = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "app.js")
    with open(template, encoding="utf-8") as f:
        head = "".join(f.readlines()[:30])
    assert 'getElementById("boot-fallback")' in head


# ---------------------------------------------------------------------------
# Share section (templates/index.html)
# ---------------------------------------------------------------------------

def _index_html():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_index_always_ships_the_share_row_and_sheet():
    out = _process_index_html(_index_html(), dict(MINIMAL_CONFIG))
    for needle in ('id="share-btn"', 'id="qr-overlay"', 'id="qr-share-link"', "vendor/uqr.mjs"):
        assert needle in out


def test_additional_logos_do_not_invert_by_default():
    # Same default as invert_logo_dark on the primary (2026-10-03).
    config = dict(MINIMAL_CONFIG, _additional_logos_rendered=[
        {"url": "logo-2.webp"},
        {"url": "logo-3.webp", "invert_dark": True},
    ])
    out = _process_index_html(_index_html(), config)
    assert 'src="logo-2.webp" alt="" class="brand-logo-secondary"' in out
    assert 'src="logo-3.webp" alt="" class="brand-logo-secondary invert-dark"' in out


def test_pinned_tab_link_ships_only_with_the_svg():
    # Without potrace no safari-pinned-tab.svg is generated, so the
    # link would point at a missing (or a stale, unprecached) file.
    config = dict(MINIMAL_CONFIG, icon="icon.png")
    without = _process_index_html(_index_html(), config)
    assert 'rel="mask-icon"' not in without and 'rel="manifest"' in without
    with_svg = _process_index_html(_index_html(), dict(config, _has_pinned_tab=True))
    assert 'rel="mask-icon"' in with_svg
