"""Comment ▸ Insert text at cursor, Callout, Polygon and Polyline (user request
2026-10-01), drawn with the real mouse / keyboard where it matters."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.document import PdfSession
from planetpdf.core.markup import Kind, list_markup
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
    w.tool_panes.open("comment")
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _pos(win, x, y, page=0):
    """Viewport position of page point (x, y) as displayed."""
    item = win.view._items[page]
    return win.view.mapFromScene(item.mapToScene(QPointF(x, y)))


def _items(win, kind):
    return [i for i in list_markup(win.session) if i.kind is kind]


def _type(win, text):
    editor = win.markup.editor
    assert editor is not None
    editor.setPlainText(text)
    editor.finish()


def test_tools_are_on_the_comment_bar_and_enabled(win):
    for kind in (Kind.CARET, Kind.CALLOUT, Kind.POLYGON, Kind.POLYLINE, Kind.REPLACE, Kind.CLOUD, Kind.ATTACH,
                 Kind.INK_ERASER):
        action = win.markup_ui.tool_actions[kind]
        assert action.isEnabled() and win.tool_panes.bars["comment"].widgetForAction(action) is not None
    assert win.markup_ui.not_yet_actions == []  # (nothing grayed out on the Comment bar any more)


def test_replace_text_crosses_out_and_asks_for_the_new_words(win):
    win.markup_ui.tool_actions[Kind.REPLACE].trigger()
    assert len(win.markup._page_words(0)) > 2
    win.view.shapeDrawn.emit("text", 0, (0, 1))  # (the first two words dragged across)
    _type(win, "new words")
    [item] = _items(win, Kind.REPLACE)
    assert item.spec.text == "new words"
    page = win.session.doc[0]
    assert sorted(a.type[1] for a in page.annots()) == ["Caret", "StrikeOut"]
    win.undo_stack.undo()
    page = win.session.doc[0]
    assert list(page.annots()) == []


def test_cloud_by_clicks(win):
    win.markup_ui.tool_actions[Kind.CLOUD].trigger()
    vp = win.view.viewport()
    for x, y in [(100, 500), (260, 470), (260, 600), (100, 500)]:
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, x, y))
    [item] = _items(win, Kind.CLOUD)
    assert "/BE" in win.session.doc.xref_object(item.xref)


def test_attach_file_then_save_it_back(win, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"attached text")
    win.choose_attachment = lambda: str(source)
    win.markup_ui.tool_actions[Kind.ATTACH].trigger()
    win.view.shapeDrawn.emit("point", 0, QRectF(300, 300, 0, 0))
    [item] = _items(win, Kind.ATTACH)
    assert item.spec.text == "notes.txt" and item.spec.source == b"attached text"
    assert win.markup.tool is None  # (one and done: a file is chosen each time, user 2026-10-17)
    saved = []
    win.save_attachment = lambda name, data: saved.append((name, data))
    win.markup.edit_text(item.item_id)  # (double-click)
    assert saved == [("notes.txt", b"attached text")]


def test_pencil_eraser_cuts_a_drawing_in_one_undo_step(win):
    win.markup_ui.tool_actions[Kind.INK].trigger()
    stroke = [QPointF(x, 650) for x in range(100, 401, 4)]
    win.view.shapeDrawn.emit("ink", 0, stroke)
    [drawing] = _items(win, Kind.INK)
    win.markup_ui.tool_actions[Kind.INK_ERASER].trigger()
    assert win.view.cursor_shape == Qt.CursorShape.BitmapCursor  # (the brush circle)
    size = win.edit.eraser_size
    QTest.keyClick(win.view, Qt.Key.Key_Plus)
    assert win.edit.eraser_size > size
    win.view.shapeDrawn.emit("ink", 0, [QPointF(250, 630), QPointF(250, 670)])
    [cut] = _items(win, Kind.INK)
    assert len(cut.spec.strokes) == 2
    assert win.undo_stack.undoText() == "Erase Drawing"
    win.undo_stack.undo()
    [back] = _items(win, Kind.INK)
    assert len(back.spec.strokes) == 1 and len(back.spec.points) == len(drawing.spec.points)


def test_polygon_by_clicks_closes_on_the_first_corner(win):
    win.markup_ui.tool_actions[Kind.POLYGON].trigger()
    vp = win.view.viewport()
    for x, y in [(100, 500), (220, 470), (260, 580), (100, 500)]:  # back on the first corner
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, x, y))
    [item] = _items(win, Kind.POLYGON)
    assert len(item.spec.points) == 3
    win.undo_stack.undo()
    assert _items(win, Kind.POLYGON) == []


def test_polyline_finishes_with_double_click_or_enter(win):
    win.markup_ui.tool_actions[Kind.POLYLINE].trigger()
    vp = win.view.viewport()
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 80, 600))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 150, 560))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 220, 640))  # a double-click: click,
    QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 220, 640))  # then the double-click event
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 300, 600))
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 350, 650))
    QTest.keyClick(win.view, Qt.Key.Key_Return)
    lines = _items(win, Kind.POLYLINE)
    assert sorted(len(i.spec.points) for i in lines) == [2, 3]
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 80, 700))
    QTest.keyClick(win.view, Qt.Key.Key_Escape)  # drops the unfinished one
    assert len(_items(win, Kind.POLYLINE)) == 2


def test_callout_drag_then_type(win, tmp_path):
    win.markup_ui.tool_actions[Kind.CALLOUT].trigger()
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 120, 300))
    QTest.mouseMove(vp, _pos(win, 200, 260))
    QTest.mouseMove(vp, _pos(win, 320, 220))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 320, 220))
    _type(win, "Check this number")
    [item] = _items(win, Kind.CALLOUT)
    assert item.spec.text == "Check this number"
    tip = item.spec.points[2]
    assert abs(tip[0] - 120) < 1 and abs(tip[1] - 300) < 1
    win.save_document() if hasattr(win, "save_document") else win.session.save()
    path = win.session.path
    reopened = PdfSession.open(path)
    annot = next(a for a in reopened.doc[0].annots() if a.type[1] == "FreeText")
    assert "/CL" in reopened.doc.xref_object(annot.xref)  # a real callout line, as Acrobat shows it
    reopened.close()


def test_insert_text_at_cursor_snaps_to_a_character_gap(win):
    win.markup_ui.tool_actions[Kind.CARET].trigger()
    page = win.session.doc[0]
    word = next(w for w in page.get_text("words") if w[4] == "Public")
    # click a little inside the word, just right of its first letter
    x, y = word[0] + 1.5, (word[1] + word[3]) / 2
    win.view.shapeDrawn.emit("point", 0, QRectF(x, y, 0, 0))
    _type(win, "John")
    [item] = _items(win, Kind.CARET)
    assert item.spec.text == "John"
    caret_x = (item.spec.rect[0] + item.spec.rect[2]) / 2
    assert abs(caret_x - word[0]) < 0.6  # at the gap before "Public", not where the mouse was


@pytest.mark.parametrize("kind", [Kind.HIGHLIGHT, Kind.UNDERLINE, Kind.STRIKEOUT, Kind.REPLACE, Kind.CALLOUT])
def test_text_tools_show_the_i_beam_over_text_and_the_crosshair_elsewhere(win, kind):
    """User 2026-10-18: "make them a +, but change to an I Beam when I'm over text"."""
    win.markup.set_tool(kind)
    QCoreApplication.processEvents()  # (Text Callout shows the text format bar: the page moves down)
    vp = win.view.viewport()
    QTest.mouseMove(vp, _pos(win, 95, 68))  # (on "Customer", the first line)
    assert vp.cursor().shape() == Qt.CursorShape.IBeamCursor
    QTest.mouseMove(vp, _pos(win, 420, 520))  # (empty page)
    assert vp.cursor().shape() == Qt.CursorShape.CrossCursor


def test_a_box_drawn_off_the_text_marks_every_word_it_touches(win):
    """"I should be able to draw a box and highlight or underline, etc everything under the box"."""
    win.markup.set_tool(Kind.UNDERLINE)
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 55, 84))  # (left of the text, between lines 1 and 2)
    QTest.mouseMove(vp, _pos(win, 120, 100))
    QTest.mouseMove(vp, _pos(win, 140, 112))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 140, 112))
    [item] = _items(win, Kind.UNDERLINE)
    lines = len(item.spec.points) // 4
    assert lines == 2  # (lines 2 and 3, where the box is: not line 1 above it)
    page = win.session.doc[0]
    marked = page.get_textbox(pymupdf.Rect(item.spec.rect))
    assert "Name" in marked and "Customer" not in marked
    assert win.markup.tool is Kind.UNDERLINE  # (the tool stays on)


def test_a_drag_starting_on_a_word_still_selects_from_it(win):
    win.markup.set_tool(Kind.HIGHLIGHT)
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 80, 68))
    QTest.mouseMove(vp, _pos(win, 110, 68))
    QTest.mouseMove(vp, _pos(win, 125, 68))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=_pos(win, 125, 68))
    [item] = _items(win, Kind.HIGHLIGHT)
    assert len(item.spec.points) // 4 == 1
