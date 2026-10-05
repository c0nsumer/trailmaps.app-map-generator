"""The shipped example and reference configs must stay valid, and
reference.yaml must keep documenting the schema validate_config enforces."""

import os
import re

import pytest
from validate_config import _RETIRED_KEYS, KNOWN_KEYS, validate_config

from build import load_config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCE = os.path.join(REPO_ROOT, "configs", "reference", "reference.yaml")


@pytest.mark.parametrize("rel", [
    "configs/example/example.yaml",
    "configs/reference/reference-minimal.yaml",
    "configs/reference/reference.yaml",
])
def test_shipped_configs_validate_cleanly(rel):
    path = os.path.join(REPO_ROOT, rel)
    errors, _warnings = validate_config(load_config(path), config_path=path)
    assert errors == [], errors


def _reference_text():
    with open(REFERENCE, encoding="utf-8") as f:
        return f.read()


def _documented(key, text):
    """True when `key:` opens a live or commented line in the reference."""
    return re.search(rf"^\s*#?\s*{re.escape(key)}:", text, re.M) is not None


def test_reference_documents_every_known_key():
    text = _reference_text()
    missing = [k for k in KNOWN_KEYS if not _documented(k, text)]
    assert missing == []


def test_reference_does_not_present_a_retired_key_as_live():
    text = _reference_text()
    assert [k for k in _RETIRED_KEYS if _documented(k, text)] == []
