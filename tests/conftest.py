from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures import generate

# Qt must never open real windows during tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PLANETPDF_UPDATE_URL"] = ""  # (never ask the real update server from a test)


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
