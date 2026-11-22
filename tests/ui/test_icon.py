from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from planetpdf.core.resources import app_icon_path
from planetpdf.ui.icons import app_icon
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_icon_bundled_with_all_sizes(qapp):
    assert app_icon_path().is_file()
    icon = app_icon()
    assert not icon.isNull()
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 32, 48, 256} <= sizes  # taskbar, title bar, Alt+Tab, Explorer


def test_main_window_uses_it(qapp, tmp_path):
    win = MainWindow(tmp_path / "profile")
    assert not win.windowIcon().isNull()
    assert {s.width() for s in win.windowIcon().availableSizes()} >= {16, 256}
    win.close()
