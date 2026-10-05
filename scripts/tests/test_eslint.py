"""Static no-undef lint over the runtime templates, via ESLint.

The templates ship as plain JS with no build step, so a runtime
ReferenceError (e.g. a refactor removing a helper that another code
path still calls) parses cleanly and only fails in the browser, where
it can kill the whole boot chain. ESLint's no-undef rule catches that
class statically; eslint.config.mjs at the repo root enables only
that rule.

Skips (rather than fails) when Node or the installed ESLint is
absent, so the suite stays green for Python-only contributors: the
lint harness is optional dev tooling, not a build prerequisite. It
runs offline once installed, keeping the tests-offline contract.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import rjsmin

REPO_ROOT = Path(__file__).resolve().parents[2]
ESLINT_JS = REPO_ROOT / "node_modules" / "eslint" / "bin" / "eslint.js"
TEMPLATES = ["templates/app.js", "templates/sw.js"]


def test_templates_have_no_undefined_identifiers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed; template lint is optional dev tooling")
    if not ESLINT_JS.exists():
        pytest.skip("ESLint not installed (run `pnpm install` or `npm install`)")

    result = subprocess.run(
        [node, str(ESLINT_JS), *TEMPLATES],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "ESLint no-undef found problems in the runtime templates:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _node_or_skip():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed; template lint is optional dev tooling")
    return node


def test_each_template_is_linted_against_its_own_scope_globals(tmp_path):
    """app.js runs on the page and sw.js in a worker: `document` in sw.js
    and `skipWaiting` in app.js are runtime ReferenceErrors, so lint must
    reject them. Runs on scratch copies, never the real templates."""
    node = _node_or_skip()
    if not ESLINT_JS.exists():
        pytest.skip("ESLint not installed (run `pnpm install` or `npm install`)")
    (tmp_path / "templates").mkdir()
    (tmp_path / "node_modules").symlink_to(REPO_ROOT / "node_modules")
    shutil.copy(REPO_ROOT / "eslint.config.mjs", tmp_path)
    for name, stray in (("app.js", "skipWaiting();\n"), ("sw.js", "document.title;\n")):
        src = (REPO_ROOT / "templates" / name).read_text(encoding="utf-8")
        (tmp_path / "templates" / name).write_text(src + stray, encoding="utf-8")
    result = subprocess.run(
        [node, str(ESLINT_JS), *TEMPLATES],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0
    assert "'skipWaiting' is not defined" in result.stdout
    assert "'document' is not defined" in result.stdout


@pytest.mark.parametrize("name", ["app.js", "sw.js"])
def test_minified_templates_still_parse(name, tmp_path):
    """Every map ships the rjsmin output. A construct rjsmin mangles (a
    regex literal after `)`, a `//` inside a template literal) parses in
    the template and breaks only in production."""
    node = _node_or_skip()
    src = (REPO_ROOT / "templates" / name).read_text(encoding="utf-8")
    # The build substitutes sw.js's config before minifying it.
    src = src.replace("/*__SW_CONFIG__*/", 'const SW_CONFIG = {"CACHE_SCOPE": "t"};')
    out = tmp_path / name
    out.write_text(rjsmin.jsmin(src), encoding="utf-8")
    result = subprocess.run([node, "--check", str(out)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
