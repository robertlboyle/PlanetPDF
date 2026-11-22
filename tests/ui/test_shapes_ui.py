"""Edit PDF (user request 2026-10-01): link underlines and links move with their text;
any shape can be selected, moved and deleted; the free-form eraser."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core import drawings as DR
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "daryl.schmoldt@gmail.com", fontname="helv", fontsize=11)
    page.draw_rect(pymupdf.Rect(72, 102, 210, 102.7), color=None, fill=(0, 0, 1))  # its underline
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 210, 103), "uri": "mailto:d@example.com"})
    page.insert_text((72, 300), "Erase me please", fontname="helv", fontsize=11)
    page.insert_text((72, 400), "Keep me", fontname="helv", fontsize=11)
    page.draw_line((72, 500), (540, 500), color=(0.4, 0.4, 0.4), width=1)  # a section rule
    doc.save(path)
    return path


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 950)
    w.show()
    w.open_file(_make_pdf(tmp_path / "shapes.pdf"))
    w.tool_panes.open("edit")
    QCoreApplication.processEvents()
    yield w
    w.edit.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _vp(win, x, y, page=0):
    item = win.view._items[page]
    return win.view.mapFromScene(item.mapToScene(QPointF(x, y)))


def _shapes(win):
    return [tuple(round(v) for v in d.rect) for d in DR.page_drawings(win.session, 0)]


def _id_of(win, prefix, test):
    return next(k for k in win.view._markup_items if k.startswith(prefix) and test(k))


def test_shapes_are_objects_but_text_wins_the_click(win):
    rule = _id_of(win, "vec:0:", lambda k: win.edit._drawing(k).rect[1] > 490)
    assert win.view._markup_items[rule].rect().height() >= 4  # a thin line still has something to click
    line = _id_of(win, "txt:0:", lambda k: "gmail" in win.edit._line(k).text)
    center = win.view._markup_items[line].rect().center()
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _vp(win, center.x(), center.y()))
    assert win.view.selected_markup == line


def test_dragging_a_linked_line_takes_its_underline_and_link(win):
    line = _id_of(win, "txt:0:", lambda k: "gmail" in win.edit._line(k).text)
    shown = win.view._markup_items[line].rect()
    win.view.markupMoved.emit(line, shown.translated(40, 150))
    assert (112, 252, 250, 253) in _shapes(win) and (72, 102, 210, 103) not in _shapes(win)
    [link] = win.session.doc[0].get_links()
    assert tuple(round(v) for v in link["from"]) == (112, 240, 250, 253)
    assert win.undo_stack.undoText() == "Move Text"
    win.undo_stack.undo()
    assert (72, 102, 210, 103) in _shapes(win)
    assert tuple(round(v) for v in win.session.doc[0].get_links()[0]["from"]) == (72, 90, 210, 103)


def test_select_a_shape_then_move_or_delete_it(win):
    rule = _id_of(win, "vec:0:", lambda k: win.edit._drawing(k).rect[1] > 490)
    win.view.markupClicked.emit(rule)
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)  # nudge 10 pt
    assert (72, 510, 540, 510) in _shapes(win) and win.undo_stack.undoText() == "Move Shape"
    win.edit.delete()  # (still selected after the move)
    assert all(r[1] < 490 for r in _shapes(win))
    assert win.undo_stack.undoText() == "Delete Shape"
    win.undo_stack.undo()
    assert (72, 510, 540, 510) in _shapes(win)


def test_deleting_a_linked_line_takes_its_underline_and_link(win):
    line = _id_of(win, "txt:0:", lambda k: "gmail" in win.edit._line(k).text)
    win.view.markupClicked.emit(line)
    win.edit.delete()
    assert "gmail" not in win.session.doc[0].get_text()
    assert (72, 102, 210, 103) not in _shapes(win) and win.session.doc[0].get_links() == []


def test_the_eraser(win):
    win.edit_ui.act_erase.trigger()
    assert win.edit.tool == "erase" and win.view.draw_tool == "ink" and win.edit_ui.act_erase.isChecked()
    small = next(a for a in win.edit_ui.eraser_menu.actions() if a.text().startswith("Large"))
    small.trigger()
    assert win.edit.eraser_size == 24 and win.edit.tool == "erase"
    win.view.shapeDrawn.emit("ink", 0, [QPointF(70, 296), QPointF(200, 296)])
    text = win.session.doc[0].get_text()
    assert "Erase me" not in text and "Keep me" in text and "gmail" in text
    assert win.undo_stack.undoText() == "Eraser" and win.edit.tool == "erase"  # still erasing
    win.undo_stack.undo()
    assert "Erase me please" in win.session.doc[0].get_text()
    win.edit_ui.act_erase.trigger()  # off again
    assert win.edit.tool is None


def test_a_shape_in_a_group_moves_exactly(win):
    rule = _id_of(win, "vec:0:", lambda k: win.edit._drawing(k).rect[1] > 490)
    line = _id_of(win, "txt:0:", lambda k: "Keep me" in win.edit._line(k).text)
    win.view.set_group([rule, line])
    assert win.edit.move_group([rule, line], 0, 10)
    assert (72, 510, 540, 510) in _shapes(win)

def test_eraser_stroke_preview_starts_at_the_click(win):
    """User bug 2026-10-02: the preview's first segment came from the page's corner."""
    win.edit_ui.act_erase.trigger()
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(win, 200, 300))
    item = win.view._drag["item"]
    for x in (220, 240):
        QTest.mouseMove(vp, _vp(win, x, 300))
    path = item.path()
    first = path.elementAt(0)
    assert (round(first.x), round(first.y)) == (200, 300)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(win, 240, 300))


def test_eraser_cursor_is_a_circle_the_size_of_the_brush(win):
    win.edit_ui.act_erase.trigger()
    view = win.view
    zoom = abs(view.mapFromScene(QPointF(100, 0)).x() - view.mapFromScene(QPointF(0, 0)).x()) / 100
    width = lambda: view.viewport().cursor().pixmap().deviceIndependentSize().width()
    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
    assert width() == pytest.approx(12 * zoom + 4, abs=2)  # the 12 pt brush, as displayed
    next(a for a in win.edit_ui.eraser_menu.actions() if a.text().startswith("Huge")).trigger()
    assert width() == pytest.approx(48 * zoom + 4, abs=2)
    before = width()
    win.act_zoom_in.trigger()  # the circle follows the zoom
    assert width() > before + 5
    win.edit_ui.act_erase.trigger()  # off: the usual cursor again
    assert view.viewport().cursor().shape() != Qt.CursorShape.BitmapCursor

def test_plus_and_minus_change_the_eraser_size(win):
    """User 2026-10-02: + bigger, - smaller (and the tool is called Eraser)."""
    assert win.edit_ui.act_erase.text() == "Eraser"
    win.edit_ui.act_erase.trigger()
    win.view.setFocus()
    assert win.edit.eraser_size == 12
    QTest.keyClick(win.view, Qt.Key.Key_Plus)
    assert win.edit.eraser_size == 16
    QTest.keyClick(win.view, Qt.Key.Key_Equal)  # (the same key without Shift)
    assert win.edit.eraser_size == 24
    ticked = [a.data() for a in win.edit_ui.eraser_menu.actions() if a.isChecked()]
    assert ticked == [24]  # the size menu follows
    for _ in range(9):
        QTest.keyClick(win.view, Qt.Key.Key_Minus)
    assert win.edit.eraser_size == 2  # never smaller than the smallest
    win.edit_ui.act_erase.trigger()
    QTest.keyClick(win.view, Qt.Key.Key_Plus)  # the Eraser is off: + does nothing to it
    assert win.edit.eraser_size == 2

def test_eraser_circle_stays_over_text_and_shapes(win):
    """User 2026-10-02: the cursor showed something else (a line's own cursor took over)."""
    win.edit_ui.act_erase.trigger()
    line = _id_of(win, "txt:0:", lambda k: "Erase me" in win.edit._line(k).text)
    center = win.view._markup_items[line].rect().center()
    QTest.mouseMove(win.view.viewport(), _vp(win, center.x(), center.y()))
    QCoreApplication.processEvents()
    assert win.view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor

def test_the_eraser_erases_even_over_the_selected_line(win):
    """User 2026-10-18: "The eraser tool on edit seems to be broken": with a line selected, dragging
    the eraser over it moved the line instead of erasing it."""
    line = _id_of(win, "txt:0:", lambda k: "Erase me" in win.edit._line(k).text)
    win.view.set_group([line])
    win.edit_ui.act_erase.trigger()
    assert win.view.selected_group == [] and win.view.selected_markup is None  # (nothing stays selected)
    win.view.set_group([line])  # (and even if something is)
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 75, 296))
    for x in range(80, 210, 10):
        QTest.mouseMove(vp, _vp(win, x, 296))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=_vp(win, 200, 296))
    assert "Erase me" not in win.session.doc[0].get_text()
    assert win.undo_stack.undoText() == "Eraser"


def test_the_eraser_width_box_shows_while_the_eraser_is_on(win):
    """"There should be a width property that comes up when it is selected"."""
    win.tool_panes.open("edit")
    QCoreApplication.processEvents()
    box = win.edit_ui.eraser_width
    assert not box.isVisible()
    win.edit_ui.act_erase.trigger()
    assert box.isVisible()
    spin = win.tool_panes.bars["edit"].widgetForAction(box)
    spin.setValue(30)
    assert win.edit.eraser_size == 30
    QTest.keyClick(win.view, Qt.Key.Key_Plus)  # (the + key: the next size, and the box follows)
    assert spin.value() == int(win.edit.eraser_size) and win.edit.eraser_size > 30
    win.edit_ui.act_erase.trigger()  # off
    assert not box.isVisible()
