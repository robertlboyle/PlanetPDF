"""Dotted guides and snapping while an object is dragged (user 2026-11-24: "it should show dotted
guidelines when it aligns with something else close by … It should snap to being aligned if it is
within a few pixels")."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer import align_guides as AG


def test_a_box_a_little_off_snaps_into_line_with_its_neighbor():
    left_text = QRectF(72, 100, 120, 14)
    moving = QRectF(300, 102.5, 80, 14)  # (2.5 pt lower than the text to its left)
    found = AG.align(moving, [left_text], tolerance=4)
    assert found.dx == 0 and found.dy == pytest.approx(-2.5)
    assert found.lines  # (top, middle and bottom all line up: a guide for each)
    assert all(l.dy() == 0 for l in found.lines)  # rows: horizontal guides
    assert min(l.x1() for l in found.lines) == pytest.approx(72)  # from the text…
    assert max(l.x2() for l in found.lines) == pytest.approx(380)  # …to the moved box


def test_a_column_lines_up_by_left_center_or_right():
    above = QRectF(100, 50, 200, 14)
    found = AG.align(QRectF(103, 200, 60, 14), [above], tolerance=4)  # left edges 3 pt apart
    assert found.dx == pytest.approx(-3) and found.dy == 0
    assert any(l.dx() == 0 and l.x1() == pytest.approx(100) for l in found.lines)
    centered = AG.align(QRectF(171, 200, 60, 14), [above], tolerance=4)  # centers 1 pt apart
    assert centered.dx == pytest.approx(-1)


def test_nothing_close_means_no_snap_and_no_guides():
    found = AG.align(QRectF(300, 300, 50, 10), [QRectF(10, 10, 40, 10)], tolerance=4)
    assert (found.dx, found.dy, found.lines) == (0, 0, ())


def test_the_nearest_lined_up_box_wins():
    near = QRectF(100, 120, 40, 10)
    far = QRectF(98, 600, 40, 10)
    found = AG.align(QRectF(101, 140, 40, 10), [far, near], tolerance=4)
    assert found.dx == pytest.approx(-1)  # (to the near one's left edge, not the far one's)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 950)
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    w.tool_panes.open("edit")
    QCoreApplication.processEvents()
    yield w
    w.edit.finish_editing()
    w.undo_stack.setClean()
    w.close()


def _vp(win, x, y, page=0):
    return win.view.mapFromScene(win.view._items[page].mapToScene(QPointF(x, y)))


def _lines(win):
    return {k: o for k, o in win.view._markup_items.items() if k.startswith("txt:0:")}


def test_dragging_a_line_snaps_to_the_column_and_shows_guides(win):
    lines = _lines(win)
    keys = sorted(lines, key=lambda k: lines[k].rect().top())
    moving, other = lines[keys[0]], lines[keys[-1]]
    start = moving.rect()
    # drag so its left edge ends 1.5 pt right of the other line's left edge, well below everything
    target_left = other.rect().left() + 1.5
    grab = start.center()
    dx, dy = target_left - start.left(), other.rect().bottom() + 80 - start.top()
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(win, grab.x(), grab.y()))
    for n in range(1, 6):
        QTest.mouseMove(vp, _vp(win, grab.x() + dx * n / 5, grab.y() + dy * n / 5))
    assert moving.rect().left() == pytest.approx(other.rect().left(), abs=0.6)  # snapped into the column
    assert any(line.dx() == 0 for _page, line in win.view._align_lines)  # a dotted column guide shows
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _vp(win, grab.x() + dx, grab.y() + dy))
    assert not win.view._align_lines  # gone the moment it's dropped (user 2026-11-24: one stayed on
    # the screen looking like a real line), and the view is repainted so its picture goes too


def test_guides_go_when_the_drag_is_canceled_or_alt_is_held(win, monkeypatch):
    page = win.view._items[0]
    other = next(o for k, o in win.view._markup_items.items() if k.startswith("txt:0:")).rect()
    repaints = []
    monkeypatch.setattr(win.view.viewport(), "update", lambda *a: repaints.append(a))
    win.view._drag = {"mode": "group", "members": []}
    win.view._aligned(page, other.translated(1.5, 300), set())
    assert win.view._align_lines and repaints
    from PySide6.QtWidgets import QApplication as App

    monkeypatch.setattr(App, "keyboardModifiers", staticmethod(lambda: Qt.KeyboardModifier.AltModifier))
    repaints.clear()
    win.view._aligned(page, other.translated(1.5, 300), set())
    assert not win.view._align_lines and repaints  # (Alt: gone, and repainted)
    monkeypatch.setattr(App, "keyboardModifiers", staticmethod(lambda: Qt.KeyboardModifier.NoModifier))
    win.view._aligned(page, other.translated(1.5, 300), set())
    repaints.clear()
    win.view._cancel_drag()  # (Esc)
    assert not win.view._align_lines and repaints


def test_snap_to_objects_can_be_switched_off(win):
    win.act_snap_objects.setChecked(False)
    assert win.settings.snap_to_objects is False and win.view.snap_to_objects is False
    page = win.view._items[0]
    assert win.view._aligned(page, QRectF(72, 72, 10, 10), set()) == QPointF()
    win.act_snap_objects.setChecked(True)
    assert win.view.snap_to_objects
