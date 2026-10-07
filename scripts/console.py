"""Consistent console output for the build pipeline.

A deliberately small wrapper over ``print()`` - not the stdlib ``logging``
module, which carries more machinery (loggers, handlers, propagation) than a
single-process build CLI needs. The point is one prefix/indent vocabulary and
a single verbosity dial, so every script's output reads the same.

Vocabulary::

    step     a top-level pipeline phase           (no indent)
    info     ordinary progress under a step       (indented)
    detail   info that only --verbose shows       (indented)
    summary  one line per stage outcome           (indented; default level only)
    note     an advisory worth noticing           "  note: ..."
    warn     a non-fatal problem                  "  warn: ..."
    error    a serious problem                    "  error: ..."

step, note and blank take ``detail=True`` to become verbose-only while keeping
their exact format, so --verbose reproduces the full log line for line. The
one exception is the pmtiles CLI's progress bar, which never prints on success.

Verbosity is set once from the CLI via :func:`set_verbosity`:

    quiet    only warn / error / raw
    normal   + step / info / note / summary / blank   (default)
    verbose  + detail, and summary is replaced by the lines it condenses

summary lines are the compact default view; at verbose level the detailed
lines they condense are already on screen, so they would only repeat them.

Everything goes to stdout today; routing diagnostics elsewhere would be a
one-line change here rather than a sweep across every caller - which is the
whole reason this indirection exists.

Color marks only what a human must read: the ``warn:`` / ``error:`` /
``note:`` prefixes and a step heading flagged ``attention=True`` (the OSM
data notes). Ordinary progress stays plain so the colored markers stay
rare enough to draw the eye. Escape codes go out only when stdout is a
terminal, ``NO_COLOR`` is unset (https://no-color.org) and ``FORCE_COLOR``
can turn them on for a log viewer that renders ANSI; the orchestrator and
any captured log see plain text.
"""

import os
import sys

# Trail/POI names carry accents and typographic dashes/arrows (-, →). Under a
# non-UTF-8 locale (bare debian:11 images, cron, LANG=C) stdout defaults to
# ASCII and printing those raises UnicodeEncodeError. Force UTF-8 stdio once,
# at first import - idempotent, and a no-op when stdout is already UTF-8 or has
# been replaced by something without .reconfigure (e.g. pytest capture).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

QUIET = 0
NORMAL = 1
VERBOSE = 2

_verbosity = NORMAL


def _color_enabled():
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


_COLOR = _color_enabled()

_RED = "31"
_YELLOW = "33"
_CYAN = "36"


def _paint(text, code):
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def set_verbosity(*, quiet=False, verbose=False):
    """Set global output verbosity. Call once at CLI startup."""
    global _verbosity
    _verbosity = QUIET if quiet else VERBOSE if verbose else NORMAL


def is_verbose():
    """True at --verbose, for callers that skip work only verbose output needs."""
    return _verbosity >= VERBOSE


def rel_path(path):
    """The path relative to the working directory when it lies inside it,
    else absolute: the short form is only worth it when it stays short."""
    absolute = os.path.abspath(path)
    cwd = os.getcwd()
    if absolute == cwd or absolute.startswith(cwd + os.sep):
        return os.path.relpath(absolute, cwd)
    return absolute


def step(msg="", *, detail=False, attention=False):
    """A top-level pipeline phase. No indent. ``detail=True``: verbose only.
    ``attention=True`` colors the heading like a note, for a block whose
    lines are a curator's to-do list rather than progress."""
    if _verbosity >= (VERBOSE if detail else NORMAL):
        print(_paint(msg, _CYAN) if attention else msg)


def info(msg):
    """Ordinary progress under a step. Indented one level."""
    if _verbosity >= NORMAL:
        print(f"  {msg}")


def detail(msg):
    """Progress that only --verbose shows. Indented like info."""
    if _verbosity >= VERBOSE:
        print(f"  {msg}")


def summary(msg):
    """A stage's one-line outcome, shown at the default level only."""
    if _verbosity == NORMAL:
        print(f"  {msg}")


def note(msg, *, detail=False):
    """An advisory the user should notice but that isn't a problem.

    ``detail=True`` is for advisories that fire on most maps and say nothing
    (a default-on layer with no data): verbose only.
    """
    if _verbosity >= (VERBOSE if detail else NORMAL):
        print(f"  {_paint('note:', _CYAN)} {msg}")


def warn(msg):
    """A non-fatal problem. Always shown."""
    print(f"  {_paint('warn:', _YELLOW)} {msg}")


def error(msg):
    """A serious problem. Always shown."""
    print(f"  {_paint('error:', _RED)} {msg}")


def raw(line):
    """A preformatted line that carries its own prefix (the validator's
    report). Always shown, like warn and error."""
    print(line)


def blank(*, detail=False):
    """A blank separator line (suppressed when quiet; ``detail=True``:
    verbose only, for the gaps between stages that print nothing by default)."""
    if _verbosity >= (VERBOSE if detail else NORMAL):
        print()
