"""Smoke test guarding the build.py module split.

Imports the whole build package (a circular import or a symbol left behind by
the split would raise) and dry-runs the bundled example config end to end -
no network, no file writes - so the orchestration path stays wired together.

Run from repo root:
    python -m pytest scripts/tests/test_build_smoke.py -v
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_all_split_modules_import():
    # build imports every sibling module, so a circular import or a missing
    # symbol anywhere in the engine raises here.
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
    # a usable icon source (square, >=512px, Pillow-readable).
    from PIL import Image

    asset = os.path.join(REPO_ROOT, "assets", "placeholder-logo.png")
    assert os.path.isfile(asset), f"missing bundled placeholder: {asset}"
    im = Image.open(asset)
    assert im.width == im.height and im.width >= 512


def test_vendor_scripts_lose_their_source_map_pointer(tmp_path):
    """A build ships no .map files, so the pointer is a 404 per library
    in any open inspector. The source copy must stay verbatim."""
    from build import _copy_vendor_script

    src = tmp_path / "lib.js.abc123"  # how a cached download is named
    body = b"var a=1;\n//# sourceMappingURL=lib.js.map\n"
    src.write_bytes(body)
    dst = tmp_path / "lib.js"
    _copy_vendor_script(str(src), str(dst))
    assert dst.read_bytes() == b"var a=1;\n"
    assert src.read_bytes() == body

    # mid-file mentions and non-scripts are left alone
    inner = b'var s="//# sourceMappingURL=x.map";\nvar b=2;\n'
    src.write_bytes(inner)
    _copy_vendor_script(str(src), str(dst))
    assert dst.read_bytes() == inner
    css = tmp_path / "lib.css"
    src.write_bytes(b"a{}\n/*# sourceMappingURL=lib.css.map */\n")
    _copy_vendor_script(str(src), str(css))
    assert css.read_bytes() == src.read_bytes()


def test_unreachable_protomaps_server_is_named_as_such(monkeypatch, capsys):
    # Offline, every HEAD fails before any HTTP answer: the message must
    # blame the network, not claim that no build exists.
    import fetch_basemap
    import requests

    def fail(*_a, **_k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fetch_basemap.requests, "head", fail)
    assert fetch_basemap.find_latest_protomaps_build() is None
    out = capsys.readouterr().out
    assert "Could not reach" in out and "No Protomaps build found" not in out
