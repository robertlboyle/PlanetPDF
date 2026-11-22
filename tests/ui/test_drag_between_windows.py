"""Drag pages from one document's window into another's (user 2026-10-02): a copy goes
in where you drop (its Pages panel / Organize grid at the line, or its page view), one
undo step there; the source document is unchanged. Plus the menu bar's close X."""

from __future__ import annotations

import time

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pdf(path, label, pages):
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 720), f"{label} {n + 1}", fontname="helv", fontsize=14)
    doc.save(path)
    return path


@pytest.fixture
def two(qapp, tmp_path):
    a = MainWindow(tmp_path / "profile")
    a.show()
    a.open_paths([_pdf(tmp_path / "a.pdf", "A", 3), _pdf(tmp_path / "b.pdf", "B", 2)])  # (two tabs)
    b = a.move_document(a.find_document(tmp_path / "b.pdf")[1])  # popped out: side by side
    assert b is not a and a.window_for(tmp_path / "b.pdf") is b
    a.setGeometry(0, 0, 1150, 900)  # side by side (Window ▸ Tile)
    b.setGeometry(1200, 0, 1150, 900)
    for w in (a, b):
        w.side_panel.show_panel("pages")
    settle()
    yield a, b
    for w in MainWindow.open_windows():
        w.undo_stack.setClean()
        w.close()


def settle(seconds=0.3):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QCoreApplication.processEvents()


def _labels(w):
    return [p.get_text().strip().splitlines()[-1] for p in w.session.doc]


def _drag_out(source, row, target_widget, target_local: QPoint):
    """Press on ``row`` of ``source`` and let go over ``target_widget`` (another window)."""
    vp = source.viewport()
    start = source.visualItemRect(source.item(row)).center()
    end = vp.mapFromGlobal(target_widget.mapToGlobal(target_local))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for n in range(1, 9):
        QTest.mouseMove(vp, start + (end - start) * (n / 8))
    seen = (target_widget.parentWidget()._drop_row if hasattr(target_widget.parentWidget(), "_drop_row") else None)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    settle()
    return seen


def test_drag_a_page_into_the_other_windows_pages_panel(two):
    a, b = two
    target = b.thumbnails
    rect = target.visualItemRect(target.item(1))
    line = _drag_out(a.thumbnails, 2, target.viewport(), QPoint(rect.center().x(), rect.top() + 3))
    assert line == 1  # the drop line showed in the other window while dragging
    assert _labels(b) == ["B 1", "A 3", "B 2"]
    assert _labels(a) == ["A 1", "A 2", "A 3"]  # the source keeps its page
    assert b.undo_stack.undoText() == "Copy 1 Page from a.pdf" and not a.is_dirty()
    b.undo_stack.undo()
    assert _labels(b) == ["B 1", "B 2"]
    assert target._drop_row is None


def test_drop_on_the_other_windows_page_view(two):
    a, b = two
    view = b.view
    page = view._items[0]
    lower_half = view.mapFromScene(page.mapToScene(QPointF(300, 480)))  # page 1 of b, lower half
    _drag_out(a.thumbnails, 0, view.viewport(), lower_half)
    assert _labels(b) == ["B 1", "A 1", "B 2"]  # after the page dropped on


def test_several_pages_from_the_organize_grid(two):
    a, b = two
    a.tool_panes.open("organize")
    settle()
    a.organizer.select_pages([0, 1])
    target = b.thumbnails
    rect = target.visualItemRect(target.item(1))
    _drag_out(a.organizer, 1, target.viewport(), QPoint(rect.center().x(), rect.bottom() - 3))
    assert _labels(b) == ["B 1", "B 2", "A 1", "A 2"]
    assert b.undo_stack.undoText() == "Copy 2 Pages from a.pdf"


def test_the_close_x_closes_the_document(two):
    a, b = two
    x = a.close_doc_button
    assert a.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is x and x.isEnabled()
    assert not x.icon().isNull() and x.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly  # an X, not "Close File"
    x.click()
    settle()
    assert a not in MainWindow.open_windows()  # (another window is open: this one closes)
    b.close_doc_button.click()
    assert b.session is None and not b.close_doc_button.isEnabled()  # the last window stays, empty
