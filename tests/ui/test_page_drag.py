"""Organize Pages / the Pages panel (user 2026-10-02): drag pages with the mouse to
rearrange them, Shift held when letting go makes a copy there; the drop line runs across
between pages when they're shown one per row. Driven with real mouse events: the page
drag is PlanetPDF's own (Qt's system drag never delivered the drop on Windows)."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(pdf_copy("text.pdf"))  # 3 pages: "Page 1", "Page 2", "Page 3" at their feet
    w.tool_panes.open("organize")
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def settle(seconds=0.3):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QCoreApplication.processEvents()


def _labels(win):
    """Which original page each page is (by the 'Page N' line at its foot)."""
    return [next(l for l in page.get_text().splitlines() if l.startswith("Page ")) for page in win.session.doc]


def _before(view, row) -> QPoint:
    """A drop point just before page ``row`` (left of it, or above it in one column)."""
    rect = view.visualItemRect(view.item(row))
    if not view.grid or view._one_column():
        return QPoint(rect.center().x(), rect.top() + 3)
    return QPoint(rect.left() + 3, rect.center().y())


def _after(view, row) -> QPoint:
    rect = view.visualItemRect(view.item(row))
    if not view.grid or view._one_column():
        return QPoint(rect.center().x(), rect.bottom() - 3)
    return QPoint(rect.right() - 3, rect.center().y())


def _drag(view, row, to: QPoint, mods=Qt.KeyboardModifier.NoModifier, check=None):
    vp = view.viewport()
    start = view.visualItemRect(view.item(row)).center()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for n in range(1, 9):
        QTest.mouseMove(vp, start + (to - start) * (n / 8))
    if check is not None:
        check()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, mods, to)
    settle()


def test_drag_a_page_to_a_new_spot_in_the_grid(win):
    grid = win.organizer
    seen = {}
    _drag(grid, 2, _before(grid, 0), check=lambda: seen.update(line=grid._drop_row, cursor=QApplication.overrideCursor().shape()))
    assert seen == {"line": 0, "cursor": Qt.CursorShape.DragMoveCursor}  # the drop line shows where it goes
    assert _labels(win) == ["Page 3", "Page 1", "Page 2"]
    assert win.undo_stack.undoText().startswith("Move")
    assert grid._drop_row is None and QApplication.overrideCursor() is None  # (the drag cursor is gone)


def test_shift_when_letting_go_makes_a_copy(win):
    grid = win.organizer
    _drag(grid, 0, _after(grid, 1), mods=Qt.KeyboardModifier.ShiftModifier)
    assert _labels(win) == ["Page 1", "Page 2", "Page 1", "Page 3"]
    assert win.undo_stack.undoText() == "Copy 1 Page"
    win.undo_stack.undo()
    assert _labels(win) == ["Page 1", "Page 2", "Page 3"]


def test_drag_in_the_pages_panel_in_edit_mode(win):
    win.tool_panes.open("edit")  # the side panel works in every mode
    win.side_panel.show_panel("pages")
    thumbs = win.thumbnails
    _drag(thumbs, 0, _after(thumbs, 2))
    assert _labels(win) == ["Page 2", "Page 3", "Page 1"]
    _drag(thumbs, 2, _before(thumbs, 0), mods=Qt.KeyboardModifier.ShiftModifier)
    assert _labels(win) == ["Page 1", "Page 2", "Page 3", "Page 1"]


@pytest.mark.parametrize("where", ["panel", "grid"])
def test_no_selection_band_is_left_behind(win, where):
    """User bug 2026-10-02: a tall pink bar stayed on the thumbnails after a drag. The first
    pixels of the move went to Qt, which started its own drag-to-select band; our page drag
    then never let Qt finish it, so the band stayed painted."""
    from PySide6.QtWidgets import QAbstractItemView

    if where == "panel":
        win.side_panel.show_panel("pages")
        view = win.thumbnails
    else:
        view = win.organizer
    vp = view.viewport()
    start = view.visualItemRect(view.item(0)).center()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    assert not view.isSelectionRectVisible()  # (pressed on a page: no band from here)
    QTest.mouseMove(vp, start + QPoint(1, 2))  # a wobble, less than a drag
    assert view.state() is not QAbstractItemView.State.DragSelectingState
    QTest.mouseMove(vp, _after(view, 2))
    assert view.state() is not QAbstractItemView.State.DragSelectingState
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _after(view, 2))
    settle()
    assert _labels(win) == ["Page 2", "Page 3", "Page 1"]
    QTest.mouseMove(vp, start + QPoint(5, 40))  # (moving afterwards draws nothing either)
    assert view.state() is QAbstractItemView.State.NoState
    # a drag that starts on empty space still selects pages with a band
    empty = QPoint(2, vp.height() - 2)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty)
    assert view.isSelectionRectVisible()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty)


def test_several_selected_pages_move_together(win):
    grid = win.organizer
    grid.select_pages([0, 1])
    _drag(grid, 1, _after(grid, 2))  # pressing on one of them keeps both selected
    assert _labels(win) == ["Page 3", "Page 1", "Page 2"]


def test_a_click_on_one_of_several_selects_just_it(win):
    grid = win.organizer
    grid.select_pages([0, 1])
    pos = grid.visualItemRect(grid.item(1)).center()
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert grid.selected_pages() == [1]


def test_escape_cancels_a_drag(win):
    grid = win.organizer
    vp = grid.viewport()
    start = grid.visualItemRect(grid.item(0)).center()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(vp, _after(grid, 2))
    QTest.keyClick(grid, Qt.Key.Key_Escape)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _after(grid, 2))
    settle()
    assert _labels(win) == ["Page 1", "Page 2", "Page 3"]


def test_the_drop_line_runs_across_when_pages_are_one_per_row(win):
    grid, splitter = win.organizer, win.central_splitter
    win.side_panel.show_panel("pages")
    total = sum(splitter.sizes())
    splitter.setSizes([total - 260, 260])  # (panel on the left) the grid narrow: one page per row
    settle()
    assert grid._one_column()
    rect = grid.visualItemRect(grid.item(1))
    assert grid.drop_row_at(QPoint(rect.center().x(), rect.top() + 3)) == 1  # above page 2
    assert grid.drop_row_at(QPoint(rect.center().x(), rect.bottom() - 3)) == 2  # below it
    splitter.setSizes([200, total - 200])  # wide: side by side, the line is vertical between them
    settle()
    assert not grid._one_column()
