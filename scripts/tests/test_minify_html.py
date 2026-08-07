"""Tests for build._minify_html - the comment-stripping HTML pass.

The properties under test: ordinary comments are stripped, but the two
comment classes that are contracts rather than commentary survive:
downlevel-hidden conditionals, and the `<!-- BEGIN OG -->` /
`<!-- END OG -->` pair that the trailmaps.app orchestrator's
inject-og-meta.py matches in the built page.

Run from repo root:
    python -m pytest scripts/tests/test_minify_html.py -v
"""

import os
import sys

# Make `scripts/` importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from build import _minify_html  # noqa: E402

PAGE = """<html><head>
    <!-- design rationale that no reader of the built page needs -->
    <!-- BEGIN OG -->
    <!-- explains the default OG block -->
    <meta property="og:image" content="icons/android-chrome-512x512.png">
    <!-- END OG -->
    <!--[if lt IE 9]><script src="shim.js"></script><![endif]-->
    <style>
        /* a comment */
        body { color: red; }
    </style>
</head></html>
"""


def test_og_markers_survive():
    out = _minify_html(PAGE)
    assert "<!-- BEGIN OG -->" in out
    assert "<!-- END OG -->" in out
    # The block stays well-formed: BEGIN before the tag, END after it.
    begin = out.index("<!-- BEGIN OG -->")
    tag = out.index('property="og:image"')
    end = out.index("<!-- END OG -->")
    assert begin < tag < end


def test_ordinary_comments_stripped():
    out = _minify_html(PAGE)
    assert "design rationale" not in out
    assert "explains the default OG block" not in out


def test_conditional_comment_survives():
    assert "<!--[if lt IE 9]>" in _minify_html(PAGE)


def test_inline_style_minified():
    out = _minify_html(PAGE)
    assert "/* a comment */" not in out
    assert "color:red" in out
