"""Tests for inject_clip_arrow.py: the sprite atlas gets one SDF icon, once."""

import json

from inject_clip_arrow import SPRITE_NAME, inject_clip_arrow
from PIL import Image


def _atlas(sprites, name, size, mode="RGBA"):
    Image.new(mode, size, 0).save(sprites / f"{name}.png")
    (sprites / f"{name}.json").write_text(json.dumps({"pin": {"x": 0, "y": 0}}), encoding="utf-8")


def _setup(tmp_path):
    sprites = tmp_path / "sprites"
    sprites.mkdir()
    _atlas(sprites, "light", (32, 20))
    _atlas(sprites, "light@2x", (64, 40), mode="P")
    Image.new("L", (8, 6), 200).save(tmp_path / "a.png")
    Image.new("L", (16, 12), 200).save(tmp_path / "a2.png")
    return sprites, str(tmp_path / "a.png"), str(tmp_path / "a2.png")


def test_each_atlas_gets_the_icon_in_a_strip_below_it(tmp_path):
    sprites, sdf1, sdf2 = _setup(tmp_path)
    inject_clip_arrow(str(sprites), sdf1, sdf2)

    meta = json.loads((sprites / "light.json").read_text(encoding="utf-8"))
    assert meta[SPRITE_NAME] == {
        "x": 0, "y": 20, "width": 8, "height": 6, "pixelRatio": 1, "sdf": True,
    }
    assert meta["pin"] == {"x": 0, "y": 0}
    assert Image.open(sprites / "light.png").size == (32, 26)

    meta2 = json.loads((sprites / "light@2x.json").read_text(encoding="utf-8"))
    assert meta2[SPRITE_NAME]["pixelRatio"] == 2
    assert meta2[SPRITE_NAME]["y"] == 40
    # A palette-mode atlas is converted so the tile can carry partial alpha.
    assert Image.open(sprites / "light@2x.png").mode == "RGBA"


def test_the_sdf_bytes_land_in_the_alpha_channel(tmp_path):
    sprites, sdf1, sdf2 = _setup(tmp_path)
    inject_clip_arrow(str(sprites), sdf1, sdf2)
    assert Image.open(sprites / "light.png").getpixel((0, 20))[3] == 200


def test_a_second_run_changes_nothing(tmp_path):
    sprites, sdf1, sdf2 = _setup(tmp_path)
    inject_clip_arrow(str(sprites), sdf1, sdf2)
    before = (sprites / "light.png").read_bytes(), (sprites / "light.json").read_bytes()
    inject_clip_arrow(str(sprites), sdf1, sdf2)
    assert ((sprites / "light.png").read_bytes(), (sprites / "light.json").read_bytes()) == before


def test_a_json_without_its_png_is_ignored(tmp_path):
    sprites, sdf1, sdf2 = _setup(tmp_path)
    (sprites / "orphan.json").write_text("{}", encoding="utf-8")
    inject_clip_arrow(str(sprites), sdf1, sdf2)
    assert (sprites / "orphan.json").read_text(encoding="utf-8") == "{}"


def test_a_missing_sprites_directory_is_a_no_op(tmp_path):
    inject_clip_arrow(str(tmp_path / "nope"), "unused", "unused")
