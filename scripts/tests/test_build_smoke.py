"""Smoke test guarding the build.py module split.

Imports the whole build package (a circular import or a symbol left behind by
the split would raise) and dry-runs the bundled example config end to end —
no network, no file writes — so the orchestration path stays wired together.

Run from repo root:
    python -m pytest scripts/tests/test_build_smoke.py -v
Or as a script:
    python scripts/tests/test_build_smoke.py
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_all_split_modules_import():
    # If the split introduced a circular import or dropped a symbol, the
    # import chain (build pulls in every sibling) raises here.
    import cache_signatures  # noqa: F401
    import colors  # noqa: F401
    import enrichment  # noqa: F401
    import event_mode  # noqa: F401
    import logo  # noqa: F401
    import template_inject  # noqa: F401

    import build  # noqa: F401


def test_example_config_dry_run():
    import build

    example = os.path.join(REPO_ROOT, "configs", "example", "example.yaml")
    assert os.path.exists(example), f"missing example config: {example}"
    # Dry-run validates the config and plans the build without fetching or
    # writing anything; returns falsy / 0 on success.
    rc = build.main([example, "--dry-run", "--quiet"])
    assert rc in (None, 0), f"dry-run returned {rc!r}"


def test_apply_default_brand_when_no_logo_or_icon():
    import build

    config = {}
    assert build.apply_default_brand(config, REPO_ROOT) is True
    assert config["icon"] == os.path.join(REPO_ROOT, "assets", "placeholder-logo.png")
    assert os.path.isfile(config["icon"])


def test_apply_default_brand_skipped_when_icon_set():
    import build

    config = {"icon": "/somewhere/custom-icon.png"}
    assert build.apply_default_brand(config, REPO_ROOT) is False
    assert config["icon"] == "/somewhere/custom-icon.png"


def test_apply_default_brand_skipped_when_logo_set():
    import build

    config = {"logo": "logo.webp"}
    assert build.apply_default_brand(config, REPO_ROOT) is False
    assert "icon" not in config


def test_bundled_placeholder_icon_ships_and_is_usable():
    # The default-brand fallback depends on this asset existing and being
    # a usable icon source (square, >=256px, Pillow-readable).
    from PIL import Image

    asset = os.path.join(REPO_ROOT, "assets", "placeholder-logo.png")
    assert os.path.isfile(asset), f"missing bundled placeholder: {asset}"
    im = Image.open(asset)
    assert im.width == im.height and im.width >= 256


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
