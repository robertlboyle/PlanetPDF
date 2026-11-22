"""Edit PDF: a line of text is an object (user 2026-10-01). Click selects it (move
cursor), drag or arrow keys move it, a click on the selected line edits it, and it can
be dragged onto another page. Same text, size and font afterwards; undoable."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
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
    w.open_file(pdf_copy("text.pdf"))
    w.tool_panes.open("edit")
    QCoreApplication.processEvents()
    yield w
    w.edit.finish_editing()
    w.undo_stack.setClean()
    w.close()


def _span(win, word, page=0):
    blocks = win.session.doc[page].get_text("dict")["blocks"]
    return next((s for b in blocks for l in b.get("lines", []) for s in l["spans"] if word in s["text"]), None)


def _line_id(win, word):
    return next(k for k in win.view._markup_items if k.startswith("txt:0:") and word in win.edit._line(k).text)


def _vp(win, page, x, y):
    item = win.view._items[page]
    return win.view.mapFromScene(item.mapToScene(QPointF(x, y)))


def test_click_selects_with_a_move_cursor_and_a_second_click_edits(win):
    line_id = _line_id(win, "SSN")
    overlay = win.view._markup_items[line_id]
    assert overlay.movable and not overlay.resizable
    assert overlay.cursor().shape() == Qt.CursorShape.SizeAllCursor  # the four-way arrows
    center = overlay.rect().center()
    vp = win.view.viewport()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 0, center.x(), center.y()))
    assert win.view.selected_markup == line_id and win.edit.editor is None
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 0, center.x(), center.y()))
    assert win.edit.editor is not None and "SSN" in win.edit.editor.toPlainText()


def test_drag_moves_the_line_and_undo_puts_it_back(win):
    before = _span(win, "SSN")
    overlay = win.view._markup_items[_line_id(win, "SSN")]
    start = overlay.rect().center()
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 0, start.x(), start.y()))
    for step in range(1, 6):
        QTest.mouseMove(vp, _vp(win, 0, start.x() + 20 * step, start.y() + 40 * step))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 0, start.x() + 100, start.y() + 200))
    after = _span(win, "SSN")
    assert abs(after["origin"][0] - before["origin"][0] - 100) < 2 and abs(after["origin"][1] - before["origin"][1] - 200) < 2
    assert after["text"] == before["text"] and after["size"] == pytest.approx(before["size"]) and after["font"] == before["font"]
    assert win.undo_stack.undoText() == "Move Text"
    assert win.edit._line(win.view.selected_markup).text == before["text"]  # still selected: keep moving it
    win.undo_stack.undo()
    assert _span(win, "SSN")["origin"] == pytest.approx(before["origin"], abs=0.5)


def test_arrow_keys_nudge_the_selected_line(win):
    before = _span(win, "Email")["origin"]
    win.view.markupClicked.emit(_line_id(win, "Email"))
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Right)
    QTest.keyClick(win.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    after = _span(win, "Email")["origin"]
    assert after[0] - before[0] == pytest.approx(1, abs=0.3) and after[1] - before[1] == pytest.approx(10, abs=0.3)


def test_drag_a_line_onto_another_page(win):
    line_id = _line_id(win, "Account")
    text = win.edit._line(line_id).text
    shown = win.view._markup_items[line_id].rect()
    on_page_2 = _count(win, text, 1)  # (the fixture's pages all have the same lines)
    win.view.markupMovedToPage.emit(line_id, 1, shown.translated(0, 300))
    assert _count(win, text, 0) == 0 and _count(win, text, 1) == on_page_2 + 1
    win.undo_stack.undo()
    assert _count(win, text, 0) == 1 and _count(win, text, 1) == on_page_2


def _count(win, text, page):
    blocks = win.session.doc[page].get_text("dict")["blocks"]
    return sum(1 for b in blocks for l in b.get("lines", []) for s in l["spans"] if s["text"] == text)
