"""Shared fixtures and helpers for the offline test suite."""

import copy
import json
import re

from template_inject import inject_config_into_template

EMPTY_TRAILS = {"metadata": {"routes": {}}, "features": []}

# Smallest config inject_config_into_template accepts: every CONFIG_SPEC
# entry with a None default is a required read.
MINIMAL_CONFIG = {
    "name": "My Trails",
    "slug": "my-trails",
    "title": "My Trails Map",
    "bbox": [0, 0, 1, 1],
    "pan_bbox": [0, 0, 1, 1],
}


def inject_config(config, trails=None):
    """Run the injector and return the CONFIG object parsed back out."""
    if trails is None:
        trails = copy.deepcopy(EMPTY_TRAILS)
    out = inject_config_into_template("/*__CONFIG__*/", config, trails)
    return json.loads(re.match(r"const CONFIG = (.*);$", out, re.S).group(1))
