"""Shared YAML config loader for fetch_trails and fetch_pois.

Both scripts can be run standalone (outside the full build.py
pipeline) and need the same minimal path-resolution behaviour for
``osm_file:`` so a relative path in the YAML resolves against the
config's directory. Previously copy-pasted across both scripts; now
lives here as the single source of truth.

The full build.py load_config is richer (resolves ``logo``,
``icon``, ``osm_file``, ``icons_dir``, and every
``custom_routes[].geometry``) — that path is the standard one when
running through ``build.py``. The trimmed version in this module is
the one fetch_trails and fetch_pois use when invoked directly from
the CLI for ad-hoc data refreshes.
"""

import os

import yaml


def load_config_for_fetch(config_path):
    """Parse a YAML config and resolve ``osm_file:`` relative to the
    config's directory.

    Equivalent to the narrow load_config previously duplicated in
    fetch_trails.py and fetch_pois.py. Renamed to make it obvious
    this is the *minimal* version (used by the standalone fetch
    entry-points) — build.py keeps its own richer load_config that
    resolves every per-map asset path.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    osm_file = config.get("osm_file")
    if osm_file and isinstance(osm_file, str) and not os.path.isabs(osm_file):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        config["osm_file"] = os.path.join(config_dir, osm_file)
    return config
