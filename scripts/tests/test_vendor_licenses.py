"""Every bundled library ships with its license text."""

import os

from vendor_libs import EXTRA_LICENSES, VENDOR_LIBS, VENDOR_LICENSES, VENDOR_LICENSES_FILE

import build

LICENSES_DIR = os.path.join(os.path.dirname(build.SCRIPTS_DIR), "assets", "licenses")


def test_every_vendor_lib_names_a_license_text():
    # A library added to VENDOR_LIBS without a notice must fail here, not
    # ship to every map without one.
    assert set(VENDOR_LICENSES) == set(VENDOR_LIBS)
    for license_file in set(VENDOR_LICENSES.values()):
        path = os.path.join(LICENSES_DIR, license_file)
        assert os.path.getsize(path) > 500, license_file


def test_no_unused_license_text():
    used = {*VENDOR_LICENSES.values(), *EXTRA_LICENSES.values()}
    assert set(os.listdir(LICENSES_DIR)) == used


def test_licenses_file_holds_every_text(tmp_path):
    build._write_vendor_licenses(str(tmp_path))
    joined = (tmp_path / VENDOR_LICENSES_FILE).read_text(encoding="utf-8")
    for filename, license_file in {**VENDOR_LICENSES, **EXTRA_LICENSES}.items():
        assert filename in joined
        with open(os.path.join(LICENSES_DIR, license_file), encoding="utf-8") as f:
            assert f.read().strip("\n") in joined
