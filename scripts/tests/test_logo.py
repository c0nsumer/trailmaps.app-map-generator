"""Tests for logo.process_logo and the shared logo-source resolver."""

import os

from logo import aspect_mismatch, process_logo, resolve_logo_dark_source
from PIL import Image, ImageDraw


def test_indexed_png_logo_is_resampled_smoothly(tmp_path):
    # Pillow resamples a mode "P" image with nearest-neighbor whatever
    # filter is asked for, so the downscale must come after the
    # conversion. A two-color disc then gains antialiased edge pixels.
    src = tmp_path / "logo.png"
    disc = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((100, 100, 1100, 1100), fill=(200, 30, 30, 255))
    disc.quantize(colors=4).save(src, transparency=0)
    assert Image.open(src).mode == "P"

    out = tmp_path / "logo.webp"
    process_logo(str(src), str(out))

    alpha = Image.open(out).convert("RGBA").getchannel("A")
    assert len(alpha.getcolors()) > 2


def test_corner_ratio_rounds_the_corners_and_keeps_the_middle(tmp_path):
    # The bundled placeholder is a full-bleed square; its on-page copy
    # gets rounded corners while the edges' midpoints stay opaque.
    src = tmp_path / "tile.png"
    Image.new("RGB", (512, 512), (58, 107, 62)).save(src)

    out = tmp_path / "logo.webp"
    w, h = process_logo(str(src), str(out), corner_ratio=7 / 26)

    alpha = Image.open(out).convert("RGBA").getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((w - 1, h - 1)) == 0
    assert alpha.getpixel((w // 2, 0)) == 255
    assert alpha.getpixel((w // 2, h // 2)) == 255
    assert len(alpha.getcolors()) > 2  # antialiased edge


def test_no_corner_ratio_leaves_a_square_logo_opaque(tmp_path):
    src = tmp_path / "tile.png"
    Image.new("RGB", (512, 512), (58, 107, 62)).save(src)

    out = tmp_path / "logo.webp"
    process_logo(str(src), str(out))

    assert Image.open(out).convert("RGBA").getpixel((0, 0))[3] == 255


def test_missing_logo_file_means_no_logo_at_every_site(tmp_path, monkeypatch):
    # A `logo:` whose file is gone resolves to no logo everywhere: no
    # logo file written, a null logoUrl, no brand <img>, and no accent
    # pick, even with a usable icon on disk (no silent icon swap at one
    # site while another points at a logo.webp that was never written).
    import template_inject
    from colors import FRAMEWORK_DEFAULT_ACCENT, resolve_accent_palette
    from conftest import MINIMAL_CONFIG, inject_config

    icon = tmp_path / "icon.png"
    Image.new("RGBA", (64, 64), (200, 30, 30, 255)).save(icon)
    config = dict(MINIMAL_CONFIG, logo=str(tmp_path / "gone.png"), icon=str(icon))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # Icon generation is not under test and needs potrace; stub it.
    monkeypatch.setattr(template_inject, "generate_icons", lambda *a, **k: [])
    template_inject.copy_assets(config, str(out_dir))
    assert not list(out_dir.glob("logo.*"))

    assert inject_config(config)["logoUrl"] is None

    index_path = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "index.html")
    with open(index_path, encoding="utf-8") as f:
        html = template_inject._process_index_html(f.read(), config)
    assert "Brand img start" not in html and 'src="logo.' not in html

    palette = resolve_accent_palette(config, str(tmp_path / "cache"))
    assert palette["light"] == FRAMEWORK_DEFAULT_ACCENT


def test_present_logo_wins_at_every_site(tmp_path, monkeypatch):
    # The positive control for the test above: with the logo on disk,
    # every site agrees on it.
    import template_inject
    from colors import FRAMEWORK_DEFAULT_ACCENT, resolve_accent_palette
    from conftest import MINIMAL_CONFIG, inject_config

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (200, 30, 30, 255)).save(logo)
    config = dict(MINIMAL_CONFIG, logo=str(logo))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(template_inject, "generate_icons", lambda *a, **k: [])
    template_inject.copy_assets(config, str(out_dir))
    assert (out_dir / "logo.webp").is_file()
    assert inject_config(config)["logoUrl"] == "logo.webp"
    palette = resolve_accent_palette(config, str(tmp_path / "cache"))
    assert palette["light"] != FRAMEWORK_DEFAULT_ACCENT


def test_logo_dark_resolves_only_beside_a_light_logo(tmp_path):
    # The dark file stands in for `logo:`; it never replaces an icon
    # shown as the logo, and a missing file resolves to nothing.
    light, dark = tmp_path / "l.png", tmp_path / "d.png"
    light.write_bytes(b"x")
    dark.write_bytes(b"x")
    assert resolve_logo_dark_source({"logo": str(light), "logo_dark": str(dark)}) == str(dark)
    assert resolve_logo_dark_source({"icon": str(light), "logo_dark": str(dark)}) == ""
    assert resolve_logo_dark_source({"logo": str(light), "logo_dark": str(tmp_path / "no")}) == ""
    assert resolve_logo_dark_source({"logo": str(light)}) == ""


def test_aspect_mismatch_needs_known_sizes_and_a_real_difference():
    assert aspect_mismatch((400, 96), (200, 96))
    assert not aspect_mismatch((400, 96), (398, 96))
    assert not aspect_mismatch((400, 96), (None, None))
