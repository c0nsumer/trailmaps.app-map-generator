"""Shared argparse helpers for the standalone script entry points.

The fetch_* stages share a ``config [output]`` command-line surface;
centralizing it keeps their ``--help`` and error handling identical instead
of each hand-rolling ``sys.argv`` length checks. Stage-specific positionals
(a cache dir, a planet URL) are added by the caller before ``parse_args``.
"""

import argparse


def config_output_parser(description, output_help="Output path (default: build/<slug>/…)"):
    """Return an ArgumentParser with a required ``config`` and optional ``output``.

    Callers add any further positionals (e.g. ``cache_dir``) before calling
    ``parse_args`` so the optional ``output`` still comes first.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("config", help="Path to the YAML config file")
    parser.add_argument("output", nargs="?", help=output_help)
    return parser
