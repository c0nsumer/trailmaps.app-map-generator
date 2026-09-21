"""Tests for the shared geodesy / sort-key primitives."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from geodesy import natural_key  # noqa: E402


def test_natural_key_numeric_aware():
    """Numeric runs sort numerically, not lexically."""
    keys = ["1", "2", "10", "100"]
    assert sorted(keys, key=natural_key) == ["1", "2", "10", "100"]


def test_natural_key_mixed_numeric_and_string_ids():
    """Regression: route lists with BOTH numeric OSM relation ids AND
    custom (non-numeric) event-mode ids must sort without raising
    ``TypeError: '<' not supported between instances of 'str' and 'int'``.

    Triggered in production on sheldensbigbang where event_mode emits
    inline custom-id routes alongside OSM relation ids.
    """
    keys = ["12345678", "event_stage_1", "98765432", "intro", "5"]
    # Must not raise; order is implementation-defined but stable
    out = sorted(keys, key=natural_key)
    assert set(out) == set(keys)
    # Within all-numeric IDs the numeric ordering still holds:
    numeric_only = [k for k in out if k.isdigit()]
    assert numeric_only == ["5", "12345678", "98765432"]
