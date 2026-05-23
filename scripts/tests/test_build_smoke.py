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


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    if failed:
        print(f"\n{failed}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
