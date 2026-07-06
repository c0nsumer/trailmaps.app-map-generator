"""Tests for the 2026-07 QA review's config-handling hardening:
digit-string relation keys coerced at load, and event_mode's
forced_visible interaction.

Run from repo root:
    python -m pytest scripts/tests/test_config_hardening.py -v
Or as a script:
    python scripts/tests/test_config_hardening.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from event_mode import _apply_event_mode_to_custom_routes  # noqa: E402


def test_load_config_coerces_digit_string_relation_keys(tmp_path):
    """The validator blesses quoted keys ('1234567') for the
    per-relation override dicts, but the injector looks up by INT —
    a quoted key used to produce a clean build with the override
    silently dropped. load_config coerces once, up front."""
    from build import load_config

    cfg_path = tmp_path / "t.yaml"
    cfg_path.write_text(
        "name: T\n"
        "slug: t\n"
        "title: T Map\n"
        "relations: [123]\n"
        'relation_colors: {"1234567": "#ff0000", 89: "#00ff00"}\n'
        'dashed_relations: {"555": [4, 2]}\n'
        'relation_names: {"777": "Renamed"}\n',
        encoding="utf-8",
    )
    config = load_config(str(cfg_path))
    assert config["relation_colors"] == {1234567: "#ff0000", 89: "#00ff00"}
    assert config["dashed_relations"] == {555: [4, 2]}
    assert config["relation_names"] == {777: "Renamed"}


def test_event_mode_arrows_preserve_forced_visible_all():
    """event_mode.direction_arrows + forced_visible: "all" used to
    explode the string into ['a','l','l','direction_arrows'], after
    which the injector's == "all" check missed and every genuinely
    forced layer silently un-forced."""
    config = {
        "forced_visible": "all",
        "event_mode": {"direction_arrows": True},
    }
    _apply_event_mode_to_custom_routes(config)
    assert config["forced_visible"] == "all"


def test_event_mode_arrows_append_to_forced_visible_list():
    config = {
        "forced_visible": ["toilets"],
        "event_mode": {"direction_arrows": True},
    }
    _apply_event_mode_to_custom_routes(config)
    assert config["forced_visible"] == ["toilets", "direction_arrows"]


if __name__ == "__main__":
    sys.exit(os.system(f"{sys.executable} -m pytest {os.path.abspath(__file__)} -v") >> 8)
