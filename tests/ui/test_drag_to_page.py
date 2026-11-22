"""Edit PDF (user 2026-10-01): one or several objects can be dragged to another page:
straight onto it in the view, or over its thumbnail in the Pages panel (the view then
shows that page, to drop onto it), or dropped on the thumbnail itself. One undo step."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.page_view import LayoutMode


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1400, 950)
    w.show()
    w.open_file(pdf_copy("text.pdf"))  # 3 pages, the same lines on each
    w.tool_panes.open("edit")
    w.side_panel.show_panel("pages")
    QCoreApplication.processEvents()
    yield w
    w.edit.finish_editing()
    w.undo_stack.setClean()
    w.close()


def _vp_pos(win, x, y, page):
    item = win.view._items[page]
    return win.view.mapFromScene(item.mapToScene(QPointF(x, y)))


def _ids(win, *words, page=0):
    return [next(k for k in win.view._markup_items if k.startswith(f"txt:{page}:") and word in win.edit._line(k).text)
            for word in words]


def _count(win, text, page):
    blocks = win.session.doc[page].get_text("dict")["blocks"]
    return sum(1 for b in blocks for l in b.get("lines", []) for s in l["spans"] if s["text"] == text)


def _thumb_pos_in_view(win, page):
    """A point on page's thumbnail, in the page view's viewport coordinates."""
    thumbs = win.thumbnails
    center = thumbs.visualItemRect(thumbs.item(page)).center()
    return win.view.viewport().mapFromGlobal(thumbs.viewport().mapToGlobal(center))


def _press_on(win, item_id, mods=Qt.KeyboardModifier.NoModifier):
    rect = win.view._markup_items[item_id].rect()
    page = win.view._markup_items[item_id].parentItem().index
    pos = _vp_pos(win, rect.center().x(), rect.center().y(), page)
    QTest.mousePress(win.view.viewport(), Qt.MouseButton.LeftButton, mods, pos)
    return pos


def _move(win, a: QPoint, b: QPoint, steps=6):
    for n in range(1, steps + 1):
        QTest.mouseMove(win.view.viewport(), a + (b - a) * (n / steps))


def test_group_dropped_on_another_page_in_the_view(win):
    win.view.set_layout(LayoutMode.TWO_UP)
    win.view.fit_page()
    QCoreApplication.processEvents()
    ids = _ids(win, "Name", "SSN")
    texts = [win.edit._line(i).text for i in ids]
    before = [_count(win, t, 1) for t in texts]
    win.view.set_group(ids)
    start = _press_on(win, ids[0])
    end = _vp_pos(win, 200, 400, 1)  # onto the right-hand page
    _move(win, start, end)
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert [_count(win, t, 0) for t in texts] == [0, 0]
    assert [_count(win, t, 1) for t in texts] == [b + 1 for b in before]
    assert win.undo_stack.undoText() == "Move 2 Objects to Page 2"
    assert len(win.view.selected_group) == 2  # still selected, on their new page
    win.undo_stack.undo()
    assert [_count(win, t, 0) for t in texts] == [1, 1]


@pytest.mark.parametrize("several", [False, True])
def test_hover_a_thumbnail_shows_that_page_then_drop_on_it(win, several):
    ids = _ids(win, "Email", "Account") if several else _ids(win, "Email")
    texts = [win.edit._line(i).text for i in ids]
    before = [_count(win, t, 2) for t in texts]
    if several:
        win.view.set_group(ids)
    else:
        win.view.markupClicked.emit(ids[0])
    start = _press_on(win, ids[0])
    over = _thumb_pos_in_view(win, 2)
    _move(win, start, over)
    assert win.view.current_page == 2  # the view switched to the hovered page
    assert win.thumbnails.currentRow() == 2
    back = _vp_pos(win, 150, 500, 2)  # back into the view, onto page 3
    _move(win, over, back)
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, back)
    assert [_count(win, t, 2) for t in texts] == [b + 1 for b in before]
    assert all(_count(win, t, 0) == 0 for t in texts)


@pytest.mark.parametrize("several", [False, True])
def test_drop_on_a_thumbnail_keeps_the_place_on_the_page(win, several):
    ids = _ids(win, "Phone", "Customer") if several else _ids(win, "Phone")
    lines = [win.edit._line(i) for i in ids]
    if several:
        win.view.set_group(ids)
    else:
        win.view.markupClicked.emit(ids[0])
    start = _press_on(win, ids[0])
    over = _thumb_pos_in_view(win, 1)
    _move(win, start, over)
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, over)
    blocks = win.session.doc[1].get_text("dict")["blocks"]
    spans = [s for b in blocks for l in b.get("lines", []) for s in l["spans"]]
    for line in lines:
        same = [s for s in spans if s["text"] == line.text]
        assert len(same) == 2  # page 2's own copy + the dropped one, on top of each other
        assert all(abs(s["origin"][1] - line.origin[1]) < 1 for s in same)
        assert _count(win, line.text, 0) == 0


def test_outlines_stay_visible_over_the_page_they_are_dragged_onto(win):
    """User request 2026-10-01: while dragging, the source page is drawn above the
    others, so its objects' outlines show over the target page too."""
    win.view.set_layout(LayoutMode.TWO_UP)
    win.view.fit_page()
    QCoreApplication.processEvents()
    ids = _ids(win, "Name", "SSN")
    win.view.set_group(ids)
    left, right = win.view._items[0], win.view._items[1]
    start = _press_on(win, ids[0])
    _move(win, start, _vp_pos(win, 200, 300, 1))
    assert left.zValue() > right.zValue()  # (later pages would paint over it otherwise)
    outline = win.view._markup_items[ids[0]]
    assert right.mapRectToScene(right.boundingRect()).intersects(outline.mapRectToScene(outline.rect()))
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _vp_pos(win, 200, 300, 1))
    assert all(page.zValue() == 0 for page in win.view._items)  # (the view rebuilt its pages)