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


class ConfigReadError(Exception):
    """A config file that cannot be read as a mapping; str() is the
    one-line, curator-facing reason."""


def parse_config_yaml(config_path):
    """Parse a config YAML and return its top-level mapping, raising
    ConfigReadError instead of exiting.

    An empty file reads as ``{}``. A file whose top level is a scalar or
    a list, a missing or unreadable file, and a YAML syntax error each
    raise. The standalone validator reports the reason in its own format
    and moves on to the next file, so it cannot use the exiting form.
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except OSError as e:
        raise ConfigReadError(f"cannot read config {config_path}: {e.strerror or e}") from e
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(e, "problem", None) or "invalid YAML"
        raise ConfigReadError(f"{config_path}: {problem}{where}") from e
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ConfigReadError(
            f"{config_path}: the top level must be a mapping of config keys, "
            f"not a {type(config).__name__}"
        )
    return config


def read_config_yaml(config_path):
    """parse_config_yaml, exiting with a one-line message on failure,
    because every build-side caller reads the result with ``config.get``
    and would otherwise die with a traceback."""
    try:
        return parse_config_yaml(config_path)
    except ConfigReadError as e:
        console.error(str(e))
        sys.exit(1)


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
