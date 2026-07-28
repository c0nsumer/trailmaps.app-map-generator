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
