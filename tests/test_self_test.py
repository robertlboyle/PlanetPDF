"""--self-test (packaging, 2026-10-04) passes from source, and the build recipe names
every module PlanetPDF loads by name."""

import sys
from pathlib import Path

import pytest

from planetpdf import self_test

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "win32", reason="checks the Windows modules too")
def test_self_test_passes_from_source(tmp_path):
    report = tmp_path / "report.txt"
    assert self_test.run(report) == 0, report.read_text(encoding="utf-8")
    text = report.read_text(encoding="utf-8")
    assert "RESULT: all passed" in text and "OK    Ghostscript" in text


def test_spec_names_the_windows_modules():
    spec = (ROOT / "packaging" / "planetpdf.spec").read_text(encoding="utf-8")
    for name in self_test.WINDOWS_MODULES:
        if name.startswith("planetpdf."):
            assert f'"{name}"' in spec, name
