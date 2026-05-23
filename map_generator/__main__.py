"""Public CLI entry point for the Map Generator.

Usage:
    python -m map_generator build CONFIG_PATH [--output-dir DIR] [--cache-dir DIR] [build-flag ...]

Thin shim: dispatches to ``scripts/build.py`` without duplicating its
argparse surface. Any flag the implementation gains is automatically
forwarded; the only thing this module knows about is the subcommand
name. The legacy ``python scripts/build.py CONFIG_PATH ...`` invocation
keeps working in parallel.
"""

import os
import sys

SUBCOMMANDS = ("build",)


def _print_usage(stream=sys.stderr):
    print(
        "usage: python -m map_generator <subcommand> [args...]\n"
        f"  subcommands: {', '.join(SUBCOMMANDS)}\n"
        "  run `python -m map_generator <subcommand> --help` for flags",
        file=stream,
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        _print_usage(sys.stdout if argv else sys.stderr)
        return 0 if argv else 2

    cmd, rest = argv[0], argv[1:]
    if cmd not in SUBCOMMANDS:
        print(f"map_generator: unknown subcommand {cmd!r}", file=sys.stderr)
        _print_usage()
        return 2

    # Bootstrap sys.path so `scripts.build` and its sibling imports
    # (fetch_trails, fetch_pois, …) resolve regardless of cwd. `python -m`
    # adds the package's parent dir to sys.path[0], which is the repo root
    # here — but only when invoked that way. Adding it explicitly keeps
    # the shim importable from arbitrary entry points too.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if cmd == "build":
        from scripts.build import main as build_main

        return build_main(rest)


if __name__ == "__main__":
    sys.exit(main() or 0)
