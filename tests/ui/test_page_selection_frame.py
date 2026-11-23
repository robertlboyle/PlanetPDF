"""Selected pages get an obvious blue box (user 2026-11-22: "there is no visible indication
that something is selected"), in the Pages panel, the Organize Pages grid, and lists where
pages are ticked (Remove Blank Pages)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.thumbnails import SELECTED_FRAME, PageFrameDelegate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _blue_pixels(widget, rect) -> int:
    image = widget.viewport().grab(rect).toImage()
    want = QColor(SELECTED_FRAME)
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            if abs(c.red() - want.red()) < 20 and abs(c.green() - want.green()) < 20 and abs(c.blue() - want.blue()) < 20:
                count += 1
    return count


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.mark.parametrize("grid", [False, True])
def test_selected_pages_are_framed_in_blue(win, grid):
    if grid:
        win.tool_panes.open("organize")
        QCoreApplication.processEvents()
    lists = [l for l in win.page_lists() if l.grid == grid]
    assert lists
    pages = lists[0]
    pages.show()
    pages.select_pages([1])
    QCoreApplication.processEvents()
    chosen = pages.visualItemRect(pages.item(1))
    other = pages.visualItemRect(pages.item(0))
    assert _blue_pixels(pages, chosen) > 20
    assert _blue_pixels(pages, other) == 0


def test_ticked_pages_are_framed(qapp):
    view = QListWidget()
    view.setViewMode(QListWidget.ViewMode.IconMode)
    view.setItemDelegate(PageFrameDelegate(view, checked=True))
    for n in range(2):
        item = QListWidgetItem(f"Page {n + 1}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if n == 0 else Qt.CheckState.Unchecked)
        view.addItem(item)
    view.resize(400, 200)
    view.show()
    QCoreApplication.processEvents()
    assert _blue_pixels(view, view.visualItemRect(view.item(0))) > 10
    assert _blue_pixels(view, view.visualItemRect(view.item(1))) == 0
    view.close()
