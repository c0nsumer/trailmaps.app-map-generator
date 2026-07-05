"""Tests for tools/clean_config.py — the template re-alignment tool.

Focused on the one behaviour that can silently corrupt a config: what
happens to LIVE (uncommented) template keys. Set keys must adopt the
production value; unset keys must be commented out, never inherited
with the template's placeholder value (the bug that gave two
custom-route-only event maps the template's example `relations:` ID).
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from clean_config import clean_config  # noqa: E402


TEMPLATE = """\
# --- Identity ---
name: My Trails
slug: my-trails

# --- Data sources ---
relations:
  - 12345678
# osm_file: osm.osm
"""


def _run(tmp_path, production_text):
    tmpl = tmp_path / "template.yaml"
    prod = tmp_path / "prod.yaml"
    out = tmp_path / "prod-cleaned.yaml"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    prod.write_text(production_text, encoding="utf-8")
    result = clean_config(str(tmpl), str(prod), str(out))
    return result, out.read_text(encoding="utf-8")


def test_set_keys_adopt_production_values(tmp_path):
    (seen, extra, commented), text = _run(
        tmp_path,
        "name: Real Map\nslug: real\nrelations: [999]\n",
    )
    parsed = yaml.safe_load(text)
    assert parsed == {"name": "Real Map", "slug": "real", "relations": [999]}
    assert seen == {"name", "slug", "relations"}
    assert extra == [] and commented == set()


def test_unset_live_key_is_commented_out_not_inherited(tmp_path):
    # A custom-route-only map legitimately omits `relations:`. The
    # cleaned output must not inherit the template's placeholder ID.
    (seen, extra, commented), text = _run(
        tmp_path,
        "name: Eventy\nslug: eventy\n",
    )
    parsed = yaml.safe_load(text)
    assert "relations" not in parsed, "template placeholder leaked into cleaned output"
    assert commented == {"relations"}
    # The block survives as comments, in the template's commented-key
    # style, so the key stays discoverable.
    assert "# relations:" in text
    assert "#   - 12345678" in text
