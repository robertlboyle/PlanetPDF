from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures import generate

# Qt must never open real windows during tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PLANETPDF_UPDATE_URL"] = ""  # (never ask the real update server from a test)


def _lasting_test_folder() -> Path:
    """Where pytest makes its temporary folders: not the system's temporary folder, where PlanetPDF
    rightly won't save a document (core/temp_folders.py). So every test runs with that rule on,
    as PlanetPDF does (security review 2026-11-25: a switch in PlanetPDF that turned it off for
    every test hid where it was missing)."""
    import sys

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PlanetPDF-tests"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "PlanetPDF-tests"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "planetpdf-tests"
    base.mkdir(parents=True, exist_ok=True)
    return base


os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(_lasting_test_folder()))


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Pristine generated fixtures, built once per test run. Don't modify these."""
    directory = tmp_path_factory.mktemp("fixtures")
    for name, make in generate.ALL_FIXTURES.items():
        make(directory / name)
    return directory


@pytest.fixture(scope="session")
def test_certs(tmp_path_factory: pytest.TempPathFactory):
    from tests.fixtures.certs import make_test_certs

    return make_test_certs(tmp_path_factory.mktemp("certs"))


@pytest.fixture
def pdf_copy(fixture_dir: Path, tmp_path: Path):
    """Return a factory that copies a named fixture into this test's own temp dir."""

    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copyfile(fixture_dir / name, target)
        return target

    return copy
