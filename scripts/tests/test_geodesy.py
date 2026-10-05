"""Tests for the shared geodesy / sort-key primitives."""



from geodesy import natural_key


def test_natural_key_numeric_aware():
    """Numeric runs sort numerically, not lexically."""
    keys = ["10", "2", "100", "1"]
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
    # Within all-numeric IDs the numeric ordering still holds:
    numeric_only = [k for k in out if k.isdigit()]
    assert numeric_only == ["5", "12345678", "98765432"]
