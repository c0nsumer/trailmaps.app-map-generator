"""Consistent console output for the build pipeline.

A deliberately small wrapper over ``print()`` — not the stdlib ``logging``
module, which carries more machinery (loggers, handlers, propagation) than a
single-process build CLI needs. The point is one prefix/indent vocabulary and
a single verbosity dial, so every script's output reads the same.

Vocabulary::

    step    a top-level pipeline phase           (no indent)
    info    ordinary progress under a step       (indented)
    note    an advisory worth noticing           "  note: ..."
    warn    a non-fatal problem                  "  warn: ..."
    error   a serious problem                    "  error: ..."

Verbosity is set once from the CLI via :func:`set_verbosity`:

    quiet    only note / warn / error
    normal   + step / info / blank                (default)

Everything goes to stdout today; routing diagnostics elsewhere would be a
one-line change here rather than a sweep across every caller — which is the
whole reason this indirection exists.
"""

import sys

# Trail/POI names carry accents and typographic dashes/arrows (—, →). Under a
# non-UTF-8 locale (bare debian:11 images, cron, LANG=C) stdout defaults to
# ASCII and printing those raises UnicodeEncodeError. Force UTF-8 stdio once,
# at first import — idempotent, and a no-op when stdout is already UTF-8 or has
# been replaced by something without .reconfigure (e.g. pytest capture).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

QUIET = 0
NORMAL = 1

_verbosity = NORMAL


def set_verbosity(*, quiet=False):
    """Set global output verbosity. Call once at CLI startup."""
    global _verbosity
    _verbosity = QUIET if quiet else NORMAL


def step(msg=""):
    """A top-level pipeline phase. No indent."""
    if _verbosity >= NORMAL:
        print(msg)


def info(msg):
    """Ordinary progress under a step. Indented one level."""
    if _verbosity >= NORMAL:
        print(f"  {msg}")


def note(msg):
    """An advisory the user should notice but that isn't a problem."""
    if _verbosity >= NORMAL:
        print(f"  note: {msg}")


def warn(msg):
    """A non-fatal problem. Always shown."""
    print(f"  warn: {msg}")


def error(msg):
    """A serious problem. Always shown."""
    print(f"  error: {msg}")


def blank():
    """A blank separator line (suppressed when quiet)."""
    if _verbosity >= NORMAL:
        print()
