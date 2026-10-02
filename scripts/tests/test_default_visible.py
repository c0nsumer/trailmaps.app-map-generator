"""Tests for `default_visible` layer-toggle defaulting.

Unset (omitted or null) used to mean "everything off." The owner
decided unset should instead mean a sensible first-visit set (markers,
trailheads, hubs, parking, toilets, water, repair stations, direction
arrows), so a rider discovers those layers without opening Options.
`[]` stays the explicit bare-map opt-out; `"all"` and an explicit list
keep their existing meanings.

Run from repo root:
    python -m pytest scripts/tests/test_default_visible.py -v
"""

from conftest import MINIMAL_CONFIG, inject_config
from validate_config import DEFAULT_FIRST_VISIT_LAYERS, DEFAULT_VISIBLE_LAYERS


def test_default_first_visit_layers_is_subset_of_default_visible_layers():
    # Guards against DEFAULT_FIRST_VISIT_LAYERS drifting to name a
    # layer the runtime / validator doesn't otherwise recognize.
    assert DEFAULT_FIRST_VISIT_LAYERS <= DEFAULT_VISIBLE_LAYERS


def test_unset_default_visible_yields_first_visit_set():
    config = dict(MINIMAL_CONFIG)
    obj = inject_config(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_FIRST_VISIT_LAYERS)


def test_unset_display_defaults_match_the_fleet():
    # Defaults chosen 2026-10-02 because most live maps set these values.
    obj = inject_config(dict(MINIMAL_CONFIG))
    assert obj["forcedVisible"] == ["direction_arrows"]
    assert obj["suppressBasemapPois"] is True
    assert obj["invertLogoDark"] is False


def test_empty_forced_visible_forces_nothing():
    obj = inject_config(dict(MINIMAL_CONFIG, forced_visible=[]))
    assert obj["forcedVisible"] == []


def test_null_default_visible_yields_first_visit_set():
    config = dict(MINIMAL_CONFIG, default_visible=None)
    obj = inject_config(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_FIRST_VISIT_LAYERS)


def test_empty_list_default_visible_stays_bare_map():
    config = dict(MINIMAL_CONFIG, default_visible=[])
    obj = inject_config(config)
    assert obj["defaultVisible"] == []


def test_all_default_visible_expands_to_every_layer():
    config = dict(MINIMAL_CONFIG, default_visible="all")
    obj = inject_config(config)
    assert obj["defaultVisible"] == sorted(DEFAULT_VISIBLE_LAYERS)


def test_explicit_list_default_visible_passes_through():
    config = dict(MINIMAL_CONFIG, default_visible=["parking", "difficulty"])
    obj = inject_config(config)
    assert obj["defaultVisible"] == ["parking", "difficulty"]
