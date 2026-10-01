"""Shared YAML config loading for build.py and the standalone fetch stages.

The fetch_* scripts can be run standalone (outside the full build.py
pipeline) and need the same minimal path-resolution behavior for
``osm_file:`` so a relative path in the YAML resolves against the
config's directory. Previously copy-pasted across both scripts; now
lives here as the single source of truth.

The full build.py load_config is richer (resolves ``logo``,
``icon``, ``osm_file``, and every
``custom_routes[].geometry``) - that path is the standard one when
running through ``build.py``. The trimmed version in this module is
the one the fetch_* scripts use when invoked directly from
the CLI for ad-hoc data refreshes.
"""

import os
import sys

import console
import yaml


def read_config_yaml(config_path):
    """Parse a config YAML and return its top-level mapping.

    An empty file reads as ``{}``. A file whose top level is a scalar or
    a list exits with a message, because every caller reads it with
    ``config.get`` and would otherwise die with a traceback.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if config is None:
        return {}
    if not isinstance(config, dict):
        console.error(
            f"{config_path}: the top level must be a mapping of config keys, "
            f"not a {type(config).__name__}"
        )
        sys.exit(1)
    return config


def load_config_for_fetch(config_path):
    """Parse a YAML config and resolve ``osm_file:`` relative to the
    config's directory.

    Equivalent to the narrow load_config previously duplicated in
    fetch_trails.py and fetch_pois.py. Renamed to make it obvious
    this is the *minimal* version (used by the standalone fetch
    entry-points) - build.py keeps its own richer load_config that
    resolves every per-map asset path.
    """
    config = read_config_yaml(config_path)
    osm_file = config.get("osm_file")
    if osm_file and isinstance(osm_file, str) and not os.path.isabs(osm_file):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        config["osm_file"] = os.path.join(config_dir, osm_file)
    return config
