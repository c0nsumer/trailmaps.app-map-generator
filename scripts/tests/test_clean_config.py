"""Tests for tools/clean_config.py — the template re-alignment tool.

Two invariants matter most:
- The cleaned output parses to exactly the same data as the original.
  The tool enforces this itself (equality gate) — a wrong splice
  aborts and deletes the output instead of handing back a config that
  behaves differently.
- Curator comments survive: set blocks are spliced verbatim (inline
  comments, list-item annotations, interior comment lines), stashed
  alternatives keep their template position, and anything the
  placement heuristics can't attach lands in a carry-over section
  rather than being dropped.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from clean_config import _assert_same_data, clean_config  # noqa: E402

TEMPLATE = """\
# --- Identity ---
name: My Trails
slug: my-trails

# --- Data sources ---
relations:
  - 12345678
# osm_file: osm.osm

# --- Per-route style overrides ---
# dashed_relations: {}

# --- Display ---
# forced_labels: trails

# --- Marker and accent colours ---
# accent_color: "#1D6FA5"

# --- User-supplied points ---
# trailheads: []
"""


def _run(tmp_path, production_text):
    tmpl = tmp_path / "template.yaml"
    prod = tmp_path / "prod.yaml"
    out = tmp_path / "prod-cleaned.yaml"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    prod.write_text(production_text, encoding="utf-8")
    summary = clean_config(str(tmpl), str(prod), str(out))
    return summary, out.read_text(encoding="utf-8")


def test_set_keys_adopt_production_values(tmp_path):
    summary, text = _run(
        tmp_path,
        "name: Real Map\nslug: real\nrelations: [999]\n",
    )
    parsed = yaml.safe_load(text)
    assert parsed == {"name": "Real Map", "slug": "real", "relations": [999]}
    assert summary["set"] == ["name", "relations", "slug"]
    assert summary["extra"] == [] and summary["commented_out"] == []


def test_unset_live_key_is_commented_out_not_inherited(tmp_path):
    # A custom-route-only map legitimately omits `relations:`. The
    # cleaned output must not inherit the template's placeholder ID.
    summary, text = _run(
        tmp_path,
        "name: Eventy\nslug: eventy\n",
    )
    parsed = yaml.safe_load(text)
    assert "relations" not in parsed, "template placeholder leaked into cleaned output"
    assert summary["commented_out"] == ["relations"]
    # The block survives as comments, in the template's commented-key
    # style, so the key stays discoverable.
    assert "# relations:" in text
    assert "#   - 12345678" in text


def test_inline_comment_on_set_scalar_survives(tmp_path):
    _, text = _run(
        tmp_path,
        'name: X\nslug: x\nrelations: [1]\naccent_color: "#79af13" # RAMBA Green\n',
    )
    assert 'accent_color: "#79af13" # RAMBA Green' in text


def test_list_item_comments_survive(tmp_path):
    _, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations:\n"
        "  - 123 # Some Trail\n"
        "  - 456 # Another Trail\n",
    )
    assert "  - 123 # Some Trail" in text
    assert "  - 456 # Another Trail" in text


def test_comment_line_inside_set_mapping_block_survives(tmp_path):
    # The brighton pattern: a commented-out entry stashed between real
    # entries inside a set mapping block.
    _, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n"
        "dashed_relations:\n"
        "  110: [4, 4]\n"
        "  # 111: [4, 4] # Murray Lake — stashed until signage is in\n"
        "  222: [1, 1]\n",
    )
    assert "  # 111: [4, 4] # Murray Lake — stashed until signage is in" in text
    parsed = yaml.safe_load(text)
    assert parsed["dashed_relations"] == {110: [4, 4], 222: [1, 1]}


def test_set_block_with_interior_blank_line_copied_whole(tmp_path):
    # Exercises find_block_end_prod: a blank line between trailhead
    # entries must not truncate the block (the equality gate would
    # abort the run if it did).
    _, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n"
        "trailheads:\n"
        "  - name: North Lot\n"
        "    coordinates: [-87.6, 46.5]\n"
        "\n"
        "  - name: South Lot\n"
        "    coordinates: [-87.7, 46.4]\n",
    )
    parsed = yaml.safe_load(text)
    assert [t["name"] for t in parsed["trailheads"]] == ["North Lot", "South Lot"]


def test_stash_replaces_template_default_line(tmp_path):
    summary, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n# forced_labels: routes\n",
    )
    assert summary["stashed"] == ["forced_labels"]
    lines = text.splitlines()
    # The stash sits at the template's position for the key, and the
    # template's own generic default line is gone.
    assert lines.index("# forced_labels: routes") > lines.index("# --- Display ---")
    assert "# forced_labels: trails" not in text


def test_no_space_stash_is_carried(tmp_path):
    _, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n#osm_file: custom.osm\n",
    )
    assert "#osm_file: custom.osm" in text
    assert "# osm_file: osm.osm" not in text


def test_boilerplate_is_not_duplicated(tmp_path):
    # Production carries the template's own commented default around
    # verbatim — the output must contain it exactly once (the
    # template's copy).
    _, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n# osm_file: osm.osm\n",
    )
    assert text.count("# osm_file: osm.osm") == 1


def test_leading_comment_carried_but_divider_is_not(tmp_path):
    summary, text = _run(
        tmp_path,
        "name: X\nslug: x\nrelations: [1]\n"
        "\n"
        "# --- Marker and accent colours ---\n"
        "# Matches the club logo green.\n"
        'accent_color: "#79af13"\n',
    )
    lines = text.splitlines()
    idx = lines.index('accent_color: "#79af13"')
    assert lines[idx - 1] == "# Matches the club logo green."
    assert text.count("# --- Marker and accent colours ---") == 1


def test_unplaced_comment_lands_in_carry_over_section(tmp_path):
    # A lone comment separated from every block by blank lines
    # attaches to nothing — the safety net keeps it at the end.
    summary, text = _run(
        tmp_path,
        "name: X\nslug: x\n\n# TODO check this later\n\nrelations: [1]\n",
    )
    assert summary["unplaced_comments"] == 1
    lines = text.splitlines()
    header = (
        "# --- Unplaced comments carried from the previous file "
        "(review/relocate) ---"
    )
    assert lines.index("# TODO check this later") > lines.index(header)


def test_assert_same_data_removes_output_and_exits(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("name: one\n", encoding="utf-8")
    b.write_text("name: two\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _assert_same_data(str(a), str(b))
    assert not b.exists()


def test_cleaning_is_idempotent(tmp_path):
    # Cleaning a file, then cleaning the RESULT, must be byte-identical
    # — otherwise every re-run churns the diff.
    prod_text = (
        "# Curator note about the map\n"
        "name: X\n"
        "slug: x\n"
        "relations:\n"
        "  - 123 # Some Trail\n"
        "\n"
        "# forced_labels: routes\n"
        "#osm_file: custom.osm\n"
        "\n"
        "# TODO check this later\n"
        "\n"
        'accent_color: "#79af13" # RAMBA Green\n'
        "unknown_key: 5\n"
    )
    tmpl = tmp_path / "template.yaml"
    prod = tmp_path / "prod.yaml"
    out1 = tmp_path / "pass1.yaml"
    out2 = tmp_path / "pass2.yaml"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    prod.write_text(prod_text, encoding="utf-8")
    clean_config(str(tmpl), str(prod), str(out1))
    clean_config(str(tmpl), str(out1), str(out2))
    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


def test_gate_deletes_output_when_reparse_fails(tmp_path):
    """The equality gate must also hold when the cleaned output doesn't
    even PARSE (e.g. block reordering moved a YAML alias above its
    anchor). This used to escape as an unhandled ComposerError traceback
    with the broken *-cleaned.yaml left on disk."""
    prod = tmp_path / "prod.yaml"
    prod.write_text("a: 1\n", encoding="utf-8")
    out = tmp_path / "out.yaml"
    out.write_text("bbox: *b\npan_bbox: &b [1, 2]\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _assert_same_data(str(prod), str(out))
    assert "not valid YAML" in str(exc.value)
    assert not out.exists()
