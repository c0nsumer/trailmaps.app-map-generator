"""Shared argparse helpers for the standalone script entry points.

The fetch_* stages share a ``config [output]`` command-line surface;
centralizing it keeps their ``--help`` and error handling identical instead
of each hand-rolling ``sys.argv`` length checks. Stage-specific arguments
(a planet-URL positional, a ``--cache-dir`` option) are added by the
caller before ``parse_args``.
"""

import argparse
import os

# Defaults resolve here, as build.py's do, so a debug run from any working
# directory reads the same cache and writes the same build dir as a build.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_path(*parts):
    """Join ``parts`` onto the repo root."""
    return os.path.join(PROJECT_ROOT, *parts)


def config_output_parser(
    description, output_help="Output path (default: <repo>/build/<slug>/…)"
):
    """Return an ArgumentParser with a required ``config`` and optional ``output``.

    Callers add any further arguments (positionals or options) before
    calling ``parse_args`` so the optional ``output`` still comes first.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("config", help="Path to the YAML config file")
    parser.add_argument("output", nargs="?", help=output_help)
    return parser


def add_cache_dir_arg(parser):
    """The ``--cache-dir`` option the Overpass-backed stages share."""
    parser.add_argument(
        "--cache-dir",
        default=repo_path("cache"),
        help="Cache directory (default: <repo>/cache)",
    )
