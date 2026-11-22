"""Placed text and signatures stay adjustable until the page is flattened (user request)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind
from planetpdf.core.redaction.marks import to_display
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _shown(win, item):
    return to_display(win.session.doc[item.page], item.spec.rect)  # fresh Page: adding items invalidates old ones


def _text_items(win):
    return [i for i in win.markup.items if i.kind is Kind.TEXT]


def _place(win, text="Zebedee Quill", at=QPointF(300, 400), select=True):
    """Add Text, typed and finished; then selected (a click on it), unless ``select`` is False."""
    win.act_add_placed_text.trigger()
    win.view.shapeDrawn.emit("point", 0, QRectF(at, at))
    editor = win.markup.editor
    editor.setPlainText(text)
    editor.finish()
    [item] = [i for i in _text_items(win) if i.spec.text == text]
    assert win.view.selected_markup is None  # (finished typing: not left selected)
    if select:
        win.markup.select(item.item_id)
    return item


def test_added_text_is_not_left_selected(window):
    """User 2026-10-22: after Add Text, clicking away or Enter left the text selected, its
    handles hiding where it sits, and they stayed when another tool (the signature) was chosen."""
    item = _place(window, select=False)
    assert window.view.selected_group == [] and window.view.item_bar is None
    window.markup.select(item.item_id)  # (clicked on later: selected as usual)
    assert window.view.selected_markup == item.item_id
    window.markup.set_tool(Kind.SIGNATURE)  # another tool: the handles go
    assert window.view.selected_group == [] and window.view.item_bar is None
    window.markup.set_tool(None)
    window.markup.select(item.item_id)
    window.edit.set_tool("text")  # (Edit PDF's tools too)
    assert window.view.selected_group == []


def test_text_tools_show_the_i_beam(window):
    window.act_add_placed_text.trigger()
    assert window.view.cursor_shape == Qt.CursorShape.IBeamCursor
    window.markup.set_tool(Kind.TEXTBOX)  # Comment ▸ Text box too
    assert window.view.cursor_shape == Qt.CursorShape.IBeamCursor
    for kind in (Kind.NOTE, Kind.CARET):
        window.markup.set_tool(kind)
        assert window.view.cursor_shape == Qt.CursorShape.IBeamCursor, kind
    # (highlight, underline… : the I-beam over text, the crosshair elsewhere: test_comment_tools)
    for kind in (Kind.RECT, Kind.ELLIPSE, Kind.LINE, Kind.POLYGON, Kind.HIGHLIGHT):
        window.markup.set_tool(kind)
        assert window.view.cursor_shape == Qt.CursorShape.CrossCursor, kind
    window.markup.set_tool(None)
    assert window.view.cursor_shape == Qt.CursorShape.ArrowCursor


def test_place_text_in_fill_and_sign_then_move_resize_and_resize_text(window):
    assert not window.markup.markup_mode  # Fill & Sign
    item = _place(window)
    shown = _shown(window, item)
    # the I-beam click marks the middle of the first line
    assert shown.x0 == pytest.approx(300 - 2, abs=0.5)
    assert shown.y0 < 400 < shown.y0 + item.spec.style.font_size * 1.15 + 2
    assert window.view.selected_markup == item.item_id and window.view.item_bar is not None

    # drag it somewhere else
    window.view.markupMoved.emit(item.item_id, QRectF(100, 500, shown.width, shown.height))
    [item] = _text_items(window)
    assert tuple(_shown(window, item))[:2] == pytest.approx((100, 500))

    # A+ from the bar over it, then Ctrl+[ twice
    size = item.spec.style.font_size
    bar = window.view.item_bar.widget()
    bar.larger.click()
    [item] = _text_items(window)
    assert item.spec.style.font_size > size
    window.view.setFocus()
    QTest.keyClick(window.view, Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window.view, Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.ControlModifier)
    [item] = _text_items(window)
    assert item.spec.style.font_size < size
    assert tuple(_shown(window, item))[:2] == pytest.approx((100, 500))  # grows from its corner

    # dragging a corner scales the text
    r = _shown(window, item)
    before = item.spec.style.font_size
    window.view.markupMoved.emit(item.item_id, QRectF(r.x0, r.y0, r.width * 2, r.height * 2))
    [item] = _text_items(window)
    assert item.spec.style.font_size == pytest.approx(before * 2, rel=0.02)


def test_arrow_keys_nudge_and_merge_into_one_undo(window):
    item = _place(window)
    start = _shown(window, item)
    steps_before = window.undo_stack.count()
    window.view.setFocus()
    for key in (Qt.Key.Key_Right, Qt.Key.Key_Right, Qt.Key.Key_Down):
        QTest.keyClick(window.view, key)
    QTest.keyClick(window.view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    [item] = _text_items(window)
    now = _shown(window, item)
    assert (now.x0 - start.x0, now.y0 - start.y0) == pytest.approx((2 - 10, 1))
    assert window.view.current_page == 0  # didn't turn the page
    assert window.undo_stack.count() == steps_before + 1 and window.undo_stack.undoText() == "Nudge Text"
    window.undo_stack.undo()
    [item] = _text_items(window)
    assert tuple(_shown(window, item)) == pytest.approx(tuple(start))
    # nothing selected: Right turns the page again
    window.markup.select("")
    QTest.keyClick(window.view, Qt.Key.Key_Right)
    assert window.view.current_page == 1


def test_text_color_before_and_after_placing(window, monkeypatch):
    from PySide6.QtGui import QColor

    picks = [QColor("#1f5fd6"), QColor("#c0392b")]
    monkeypatch.setattr(window, "pick_color", lambda initial, title: picks.pop(0))
    # nothing selected: Text Color… sets the color for the next text (and the toolbar swatch)
    window.markup.select("")
    window.act_text_color.trigger()
    assert window.act_text_color.data() == "#1f5fd6"
    item = _place(window)
    assert QColor.fromRgbF(*item.spec.style.color).name() == "#1f5fd6"
    assert window.markup.editor is None
    # selected: the color button in the bar above it recolors it, and is the new default
    window.markup.select(item.item_id)
    window.view.item_bar.widget().color.click()
    [item] = _text_items(window)
    assert QColor.fromRgbF(*item.spec.style.color).name() == "#c0392b"
    assert window.undo_stack.undoText() == "Text Color"
    assert window.act_text_color.data() == "#c0392b"
    window.act_flatten_page.trigger()  # keeps the color on the page
    span = next(s for b in window.session.doc[0].get_text("dict")["blocks"] for line in b.get("lines", [])
                for s in line["spans"] if "Zebedee" in s["text"])
    assert f"#{span['color']:06x}" == "#c0392b"
    window.undo_stack.undo()
    window.undo_stack.undo()
    [item] = _text_items(window)
    assert QColor.fromRgbF(*item.spec.style.color).name() == "#1f5fd6"


def test_double_click_edits_the_text_in_place(window):
    item = _place(window)
    window.view.markupDoubleClicked.emit(item.item_id)
    editor = window.markup.editor
    assert editor.toPlainText() == "Zebedee Quill"
    editor.setPlainText("Zebedee Quill\nLine two")
    editor.finish()
    [item] = _text_items(window)
    assert item.spec.text == "Zebedee Quill\nLine two"


def test_signatures_nudge_too_and_flatten_page(window, monkeypatch):
    from planetpdf.core import markup as K
    from planetpdf.core.markup import Style
    from planetpdf.core.signing.visual import signature_source_pdf
    from PIL import Image
    import io

    png = io.BytesIO()
    Image.new("RGBA", (200, 60), (0, 0, 120, 255)).save(png, "PNG")
    sig = K.MarkupSpec(mark_id=K.new_id(), page=0, kind=Kind.SIGNATURE, rect=(72, 600, 216, 643),
                       source=signature_source_pdf(png.getvalue()), style=Style())
    window.markup.add(sig)
    _place(window)
    assert window.view.selected_markup is not None
    window.markup.select(sig.mark_id)
    window.view.setFocus()
    QTest.keyClick(window.view, Qt.Key.Key_Up)
    moved = next(i for i in window.markup.items if i.kind is Kind.SIGNATURE)
    assert moved.spec.rect[1] == pytest.approx(599)

    contents = window.session.doc[0].get_contents()
    window.act_flatten_page.trigger()
    assert not [i for i in window.markup.items if i.kind in (Kind.TEXT, Kind.SIGNATURE)]
    assert window.undo_stack.undoText() == "Flatten Page"
    assert len(window.session.doc[0].get_contents()) == len(contents) + 2
    window.undo_stack.undo()
    assert {i.kind for i in window.markup.items} >= {Kind.TEXT, Kind.SIGNATURE}
    assert window.save()
    doc = pymupdf.open(window.session.path)
    assert len([a for a in doc[0].annots()]) == 2  # still movable after saving and reopening


def test_placed_text_movable_in_edit_pdf_mode(window):
    item = _place(window)
    window.markup_ui.set_mode("edit")
    overlay = "mk:" + item.item_id
    assert overlay in window.view._markup_items
    window.view.markupClicked.emit(overlay)
    assert window.view.item_bar is not None
    shown = window.view.markup_rect(overlay)[1]
    window.view.markupMoved.emit(overlay, shown.translated(30, 0))
    [item] = _text_items(window)
    assert to_display(window.session.doc[0], item.spec.rect).x0 == pytest.approx(shown.left() + 30)


def test_enter_finishes_added_text_and_shift_enter_starts_a_line(window):
    """User 2026-10-22: in Add Text, Enter finishes the text; Shift+Enter starts a new line."""
    window.act_add_placed_text.trigger()
    window.view.shapeDrawn.emit("point", 0, QRectF(QPointF(300, 400), QPointF(300, 400)))
    editor = window.markup.editor
    editor.setFocus()
    QTest.keyClicks(editor, "First")
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(editor, "Second")
    assert window.markup.editor is editor  # (still typing)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert window.markup.editor is None
    [item] = [i for i in _text_items(window) if i.spec.text.startswith("First")]
    assert item.spec.text == "First\nSecond"
    assert window.view.selected_markup is None
