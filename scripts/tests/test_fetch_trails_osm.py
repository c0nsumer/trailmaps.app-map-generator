"""An OSM-file map whose config names relations the file does not hold
stops at the parse with a message about the ids, instead of writing an
empty trails file and failing later on the bounding box. The usual
cause is a JOSM save renumbering its negative ids."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fetch_trails import fetch_trails

TINY_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='JOSM'>
  <node id='-1' lat='46.5' lon='-87.6' />
  <node id='-2' lat='46.51' lon='-87.61' />
  <way id='-3'>
    <nd ref='-1' />
    <nd ref='-2' />
    <tag k='highway' v='path' />
  </way>
  <relation id='-190'>
    <member type='way' ref='-3' role='' />
    <tag k='type' v='route' />
    <tag k='route' v='mtb' />
    <tag k='name' v='Massive Fallout 2026' />
  </relation>
</osm>
"""


def test_missing_relation_ids_stop_at_the_parse(tmp_path, capsys):
    osm = tmp_path / "mfo.osm"
    osm.write_text(TINY_OSM)
    config = {"name": "MFO", "slug": "mfo", "relations": [-70], "osm_file": str(osm)}
    with pytest.raises(SystemExit):
        fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    out = capsys.readouterr()
    assert "None of the relations [-70]" in out.out + out.err
    assert not (tmp_path / "trails.geojson").exists()


def test_present_relation_id_resolves(tmp_path):
    osm = tmp_path / "mfo.osm"
    osm.write_text(TINY_OSM)
    config = {"name": "MFO", "slug": "mfo", "relations": [-190], "osm_file": str(osm)}
    fetch_trails(config, str(tmp_path / "trails.geojson"), cache_dir=str(tmp_path / "c"))
    assert (tmp_path / "trails.geojson").exists()
