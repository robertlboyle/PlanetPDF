"""The Bookmarks tool (user 2026-10-04): selected text + Enter / + / right-click = a
bookmark there; Add Bookmarks Automatically from the headings."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core import links as L
from planetpdf.core.text import page_words
from planetpdf.ui import bookmark_tool
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _handbook(path):
    doc = pymupdf.open()
    for chapter in range(1, 4):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Chapter {chapter}: Topic {chapter}", fontsize=18, fontname="hebo")
        for n in range(4):
            page.insert_text((72, 130 + n * 14), "Ordinary body text that runs along the line.", fontsize=10)
    doc.save(path)
    return path


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.resize(1400, 800)
    w.show()
    w.open_file(_handbook(tmp_path / "handbook.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _select(w, page, first, count):
    words = page_words(w.session, page)[first : first + count]
    w.text_tools.set_selection(page, words)


def test_tool_selects_text_and_enter_adds_a_bookmark(win):
    win.tool_panes.open("bookmark")
    assert win.tool_panes.bars["bookmark"].isVisible()
    assert win.text_tools.act_select.isChecked()
    assert win.view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor  # (not the crosshair)
    _select(win, 1, 0, 4)  # "Chapter 2: Topic 2"
    QApplication.sendEvent(win.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    marks = L.get_bookmarks(win.session)
    assert [(m.title, m.page) for m in marks] == [("Chapter 2: Topic 2", 1)]
    assert 70 < marks[0].y < 90
    assert win.text_tools.selection is None
    _select(win, 0, 0, 4)  # an earlier place goes first
    QApplication.sendEvent(win.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Plus, Qt.KeyboardModifier.NoModifier))
    assert [m.title for m in L.get_bookmarks(win.session)] == ["Chapter 1: Topic 1", "Chapter 2: Topic 2"]
    win.undo_stack.undo()
    assert [m.title for m in L.get_bookmarks(win.session)] == ["Chapter 2: Topic 2"]
    win.tool_panes.close()
    assert not win.text_tools.act_select.isChecked()


def test_add_bookmarks_automatically(win, monkeypatch):
    monkeypatch.setattr(bookmark_tool.SectionsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    win.bookmark_tool.add_automatically()
    marks = L.get_bookmarks(win.session)
    assert [(m.title, m.page) for m in marks] == [("Chapter 1: Topic 1", 0), ("Chapter 2: Topic 2", 1),
                                                  ("Chapter 3: Topic 3", 2)]
    win.undo_stack.undo()
    assert L.get_bookmarks(win.session) == []


def test_click_jumps_and_never_moves_a_bookmark(win):
    win.tool_panes.open("bookmark")
    _select(win, 1, 0, 4)
    win.bookmark_tool.add()
    before = [(m.title, m.page, m.y) for m in L.get_bookmarks(win.session)]
    panel, tree = win.bookmarks_panel, win.bookmarks_panel.tree
    assert tree.editTriggers() == tree.EditTrigger.EditKeyPressed  # (a click never starts renaming)
    win.view.go_to_page(2)
    _select(win, 2, 0, 4)  # (text selected elsewhere: a click must not use it)
    item = tree.topLevelItem(0)
    tree.itemClicked.emit(item, 0)
    QCoreApplication.processEvents()
    assert win.view.current_page == 1
    assert [(m.title, m.page, m.y) for m in L.get_bookmarks(win.session)] == before
    # rename keeps the place; Set Destination is the one way to move it
    item.setText(0, "Second chapter")
    assert [(m.title, m.page, m.y) for m in L.get_bookmarks(win.session)] == [("Second chapter",) + before[0][1:]]
    menu = panel.context_menu(tree.topLevelItem(0))  # (the list is rebuilt after each change)
    assert [a.text() for a in menu.actions() if a.text()] == ["Go to Bookmark", "Rename\tF2", "Set Destination",
                                                              "Delete", "Add Bookmark"]
    panel.set_destination(0)
    moved = L.get_bookmarks(win.session)[0]
    assert (moved.title, moved.page) == ("Second chapter", 2)
    win.undo_stack.undo()
    assert [(m.title, m.page, m.y) for m in L.get_bookmarks(win.session)] == [("Second chapter",) + before[0][1:]]


def test_bookmark_jumps_in_every_layout(win):
    """User bug 2026-10-04 (a bookmark low on page 2 "doesn't jump"): Single Page View
    didn't turn the page, and scrolling views showed the next page's number."""
    from PySide6.QtCore import QPointF

    from planetpdf.ui.viewer.page_view import LayoutMode

    L.set_bookmarks(win.session, [L.Bookmark(1, "Top", 0, 80), L.Bookmark(1, "Low", 1, 600)])
    win.bookmarks_panel.refresh()
    view, tree = win.view, win.bookmarks_panel.tree
    for layout in LayoutMode:
        for fit in (view.fit_width, view.fit_page):
            view.set_layout(layout)
            fit()
            QCoreApplication.processEvents()
            for row, (page, y) in ((0, (0, 80)), (1, (1, 600)), (0, (0, 80))):
                tree.itemClicked.emit(tree.topLevelItem(row), 0)
                QCoreApplication.processEvents()
                item = view._items[page]
                spot = view.mapFromScene(item.mapToScene(QPointF(10, y + 5)))
                assert view.current_page == page, (layout, fit.__name__, row)
                assert item.isVisible() and view.viewport().rect().contains(spot), (layout, fit.__name__, row)


def test_insert_in_order_keeps_children_together():
    marks = [L.Bookmark(1, "A", 0, 50), L.Bookmark(2, "A.1", 1, 50), L.Bookmark(1, "B", 3, 50)]
    new, at = bookmark_tool.insert_in_order(marks, L.Bookmark(1, "between", 2, 10))
    assert at == 2 and new[2] == L.Bookmark(2, "between", 2, 10)
    new, at = bookmark_tool.insert_in_order(marks, L.Bookmark(1, "first", 0, 10))
    assert at == 0 and new[0].level == 1
