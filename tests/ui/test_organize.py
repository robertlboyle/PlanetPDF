"""Organize Pages: the page grid replaces the page view; selection sets, hover rotate /
delete, drag reorder and double-click to open a page, all undoable."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1200, 800)
    w.show()
    w.open_file(pdf_copy("text.pdf"))  # 3 pages
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _texts(w):
    return [w.session.doc[i].get_text().split("\n")[-2] for i in range(w.session.page_count)]


def test_grid_replaces_the_page_view(win):
    win.tool_panes.open("organize")
    assert win.central.currentWidget() is win.organizer
    assert win.organizer.count() == 3
    win.tool_panes.close()
    assert win.central.currentWidget() is win.view


def test_page_sets_and_selection_drive_the_commands(win):
    win.tool_panes.open("organize")
    grid = win.organizer
    grid.select_set("Even Pages")
    assert win.selected_pages() == [1]
    grid.select_set("Odd Pages")
    assert win.selected_pages() == [0, 2]
    win.act_rotate_cw.trigger()
    assert [win.session.doc[i].rotation for i in range(3)] == [90, 0, 90]
    win.undo_stack.undo()
    assert [win.session.doc[i].rotation for i in range(3)] == [0, 0, 0]


def test_hover_buttons_rotate_and_delete(win):
    win.tool_panes.open("organize")
    grid = win.organizer
    grid.rotate(1, -90)
    assert win.session.doc[1].rotation == 270
    before = _texts(win)
    grid.delete(0)
    assert win.session.page_count == 2 and _texts(win) == before[1:]
    assert grid.count() == 2  # the grid follows the document
    win.undo_stack.undo()
    assert win.session.page_count == 3 and grid.count() == 3


def test_drag_reorder_and_double_click_open(win):
    win.tool_panes.open("organize")
    before = _texts(win)
    win.organizer.moveRequested.emit([2], 0)
    assert _texts(win) == [before[2], before[0], before[1]]
    win.organizer.open_page(1)
    assert win.central.currentWidget() is win.view and win.tool_panes.current is None
    assert win.view.current_page == 1
