"""Shared YAML config loading for build.py and the standalone fetch stages.

The fetch_* scripts can run standalone, outside build.py, and need a
relative ``osm_file:`` to resolve against the config's directory.
build.py keeps its richer load_config, which resolves every per-map
asset path.
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

    The minimal version, for the standalone fetch entry points.
    """
    config = read_config_yaml(config_path)
    osm_file = config.get("osm_file")
    if osm_file and isinstance(osm_file, str) and not os.path.isabs(osm_file):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        config["osm_file"] = os.path.join(config_dir, osm_file)
    return config
