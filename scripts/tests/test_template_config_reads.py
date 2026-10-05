"""app.js reads CONFIG.<name> properties that only the build-time injector
creates. ESLint cannot see a misspelled or renamed property, which would
read as undefined in the browser."""

import os
import re

from conftest import MINIMAL_CONFIG, inject_config

APP_JS = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "app.js")


def test_every_config_property_app_js_reads_is_emitted_by_the_injector():
    with open(APP_JS, encoding="utf-8") as f:
        used = set(re.findall(r"\bCONFIG\.([A-Za-z_]\w*)", f.read()))
    emitted = set(inject_config(dict(MINIMAL_CONFIG)))
    assert used, "no CONFIG reads found: the pattern no longer matches app.js"
    assert sorted(used - emitted) == []
