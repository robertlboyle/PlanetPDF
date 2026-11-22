"""Edit PDF: select several objects (user 2026-10-01). Drag a box around them (a whole
paragraph), Shift+click adds one, Ctrl+Shift+click removes one; dragging any of them or
the arrow keys moves them all together (one undo step); Delete removes them all."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


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
    item = win.view._items[page]
    return win.view.mapFromScene(item.mapToScene(QPointF(x, y)))


def _ids(win, *words):
    out = []
    for word in words:
        out.append(next(k for k in win.view._markup_items if k.startswith("txt:0:") and word in win.edit._line(k).text))
    return out


def _origins(win, page=0):
    blocks = win.session.doc[page].get_text("dict")["blocks"]
    return {s["text"]: s["origin"] for b in blocks for l in b.get("lines", []) for s in l["spans"]}


def _drag(win, start, end, modifiers=Qt.KeyboardModifier.NoModifier, steps=5):
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, modifiers, _vp(win, *start))
    for n in range(1, steps + 1):
        QTest.mouseMove(vp, _vp(win, start[0] + (end[0] - start[0]) * n / steps, start[1] + (end[1] - start[1]) * n / steps))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, modifiers, _vp(win, *end))


def _box_around(win, ids, margin=6):
    rects = [win.view._markup_items[i].rect() for i in ids]
    left = min(r.left() for r in rects) - margin
    top = min(r.top() for r in rects) - margin
    right = max(r.right() for r in rects) + margin
    bottom = max(r.bottom() for r in rects) + margin
    return (left, top), (right, bottom)


def test_box_selects_a_whole_paragraph_and_dragging_moves_it_all(win):
    ids = _ids(win, "Customer", "Name", "SSN")
    start, end = _box_around(win, ids)
    _drag(win, (start[0] - 4, start[1]), end)  # (start a little left: on the empty page)
    assert set(ids) <= set(win.view.selected_group)
    selected = win.view.selected_group
    before = _origins(win)
    grab = win.view._markup_items[ids[1]].rect().center()
    _drag(win, (grab.x(), grab.y()), (grab.x() + 60, grab.y() + 150))
    after = _origins(win)
    for text in [win.edit._line(i).text for i in win.view.selected_group]:
        assert after[text][0] - before[text][0] == pytest.approx(60, abs=1.5)
        assert after[text][1] - before[text][1] == pytest.approx(150, abs=1.5)
    assert len(win.view.selected_group) == len(selected)  # still all selected
    assert win.undo_stack.undoText().startswith("Move ")
    win.undo_stack.undo()  # one step puts them all back
    restored = _origins(win)
    assert all(abs(restored[t][0] - before[t][0]) < 0.5 and abs(restored[t][1] - before[t][1]) < 0.5 for t in before)


def test_moving_a_paragraph_down_one_line_keeps_every_line(win):
    """Each line lands where the next one was: nothing may be erased on the way."""
    ids = _ids(win, "Name", "SSN", "Phone")
    texts = [win.edit._line(i).text for i in ids]
    spacing = win.edit._line(ids[1]).origin[1] - win.edit._line(ids[0]).origin[1]
    win.view.set_group(ids)
    assert win.edit.move_group(ids, 0, spacing)
    text = win.session.doc[0].get_text()
    for t in texts:
        assert t in text


def test_shift_click_adds_ctrl_shift_click_removes(win):
    a, b, c = _ids(win, "Name", "Phone", "Email")
    vp = win.view.viewport()
    click = lambda i, mods=Qt.KeyboardModifier.NoModifier: QTest.mouseClick(
        vp, Qt.MouseButton.LeftButton, mods, _vp(win, *win.view._markup_items[i].rect().center().toTuple()))
    click(a)
    click(b, Qt.KeyboardModifier.ShiftModifier)
    click(c, Qt.KeyboardModifier.ShiftModifier)
    assert win.view.selected_group == [a, b, c]
    click(b, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
    assert win.view.selected_group == [a, c]
    assert not win.view._markup_items[a].handles  # several selected: no resize handles


def test_arrow_keys_move_the_group_and_delete_removes_it(win):
    ids = _ids(win, "Email", "Account")
    texts = [win.edit._line(i).text for i in ids]
    win.view.set_group(ids)
    before = _origins(win)
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    after = _origins(win)
    for t in texts:
        assert after[t][1] - before[t][1] == pytest.approx(10, abs=0.5)
    assert len(win.view.selected_group) == 2
    win.edit.delete()
    remaining = win.session.doc[0].get_text()
    assert all(t not in remaining for t in texts)
    win.undo_stack.undo()
    assert all(t in win.session.doc[0].get_text() for t in texts)


def test_click_on_empty_page_deselects_and_plain_click_picks_one(win):
    a, b = _ids(win, "Name", "SSN")
    win.view.set_group([a, b])
    vp = win.view.viewport()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _vp(win, *win.view._markup_items[b].rect().center().toTuple()))
    assert win.view.selected_group == [b] and win.edit.editor is None  # one of them, not editing
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(win, 500, 700))
    assert win.view.selected_group == []
