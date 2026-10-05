"""Tests for the 2026-07 QA review's config-handling hardening:
digit-string relation keys coerced at load, and event_mode's
forced_visible interaction.

Run from repo root:
    python -m pytest scripts/tests/test_config_hardening.py -v
"""



from event_mode import _apply_event_mode_to_custom_routes, _apply_event_mode_to_relations


def test_load_config_coerces_digit_string_relation_keys(tmp_path):
    """The validator blesses quoted keys ('1234567') for the
    per-relation override dicts, but the injector looks up by INT -
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


def test_event_mode_background_dash_keeps_curator_relation_color():
    """A non-featured relation the curator colored keeps that color: the
    synthesized dash entry must not carry the background gray in `colors`,
    which the runtime reads before the relation color."""
    config = {
        "event_mode": {"routes": []},
        "relation_colors": {111: "#ff0000"},
    }
    trails = {"metadata": {"routes": {"111": {}, "222": {}}}}
    _apply_event_mode_to_relations(config, trails)
    assert config["relation_colors"][111] == "#ff0000"
    assert "colors" not in config["dashed_relations"][111]
    gray = config["relation_colors"][222]
    assert config["dashed_relations"][222]["colors"] == [gray]


def test_event_pois_survive_all_show_flags_off():
    """Event POIs are always on: build_pois_geojson emits them even when
    every show_* flag is false."""
    from fetch_pois import POI_SHOW_FLAGS, build_pois_geojson

    config = {k: False for k in POI_SHOW_FLAGS}
    event = [{"name": "Start", "coordinates": [-88.0, 46.0]}]
    gj = build_pois_geojson({"elements": []}, [], [], config_event_pois=event, config=config)
    assert [f["properties"]["poi_type"] for f in gj["features"]] == ["event"]


def test_read_config_yaml_exits_cleanly_on_missing_file_and_bad_yaml(tmp_path, capsys):
    import pytest
    from config_io import read_config_yaml

    with pytest.raises(SystemExit) as exc:
        read_config_yaml(str(tmp_path / "nope.yaml"))
    assert exc.value.code == 1

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unclosed\nslug: x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        read_config_yaml(str(bad))
    assert exc.value.code == 1
    out = capsys.readouterr()
    assert "bad.yaml" in out.out + out.err
