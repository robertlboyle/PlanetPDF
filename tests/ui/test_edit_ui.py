from __future__ import annotations

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QRectF, Qt

from planetpdf.core.markup import Kind
from PySide6.QtWidgets import QApplication

from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind
from planetpdf.ui.dialogs.field_properties import FieldPropertiesDialog
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, tmp_path, path):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(path)
    QCoreApplication.processEvents()
    return win


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = _window(qapp, tmp_path, pdf_copy("text.pdf"))
    yield win
    win.undo_stack.setClean()
    win.close()


def _ids(win, prefix):
    return [k for k in win.view._markup_items if k.startswith(prefix)]


def test_modes_switch_and_overlays(window):
    ui = window.markup_ui
    ui.act_mode_edit.trigger()
    assert window.edit.mode == "edit" and ui.act_mode_edit.isChecked() and window.markup.suspended
    assert window.edit_ui.edit_toolbar.isVisible() and not ui.toolbar.isVisible()
    assert len(_ids(window, "txt:0:")) == len(g.TEXT_LINES) + 1  # plus the page number
    ui.act_mode_markup.trigger()
    assert window.edit.mode is None and window.markup.markup_mode and not _ids(window, "txt:")
    ui.act_mode_design.trigger()
    assert window.edit.mode == "design" and not window.markup.markup_mode and window.edit_ui.design_toolbar.isVisible()
    ui.act_mode_form.trigger()
    assert window.edit.mode is None and not window.markup.suspended and window.view._fields_interactive
    assert ui.act_mode_form.isChecked()


def test_retype_a_line_on_the_page_and_undo(window):
    window.markup_ui.set_mode("edit")
    line_id = next(i for i in _ids(window, "txt:0:") if "Phone" in window.edit._line(i).text)
    window.view.markupClicked.emit(line_id)  # the first click selects the line (to move it)
    window.view.markupClickReleased.emit(line_id)
    assert window.edit.editor is None and window.view.selected_markup == line_id
    window.view.markupClicked.emit(line_id)  # a click on the selected line edits it
    window.view.markupClickReleased.emit(line_id)
    editor = window.edit.editor
    assert editor is not None and editor.toPlainText() == f"Phone: {g.PHONE}" and editor.single_line
    editor.setPlainText("Phone: (555) 222-3333")
    editor.finish()
    text = window.session.doc[0].get_text()
    assert "222-3333" in text and g.PHONE not in text
    assert window.is_dirty() and window.undo_stack.undoText() == "Edit Text"
    assert any("222-3333" in window.edit._line(i).text for i in _ids(window, "txt:0:"))  # overlays follow
    window.undo_stack.undo()
    assert g.PHONE in window.session.doc[0].get_text()


def test_a_line_in_several_fonts_is_retyped_as_one(qapp, tmp_path):
    """User 2026-10-23: clicking the bold word of "Please read the terms carefully." edits the
    whole line, each word in its font; a word typed after "terms" is bold like it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    x = 72.0
    for text, font in (("Please read the ", "helv"), ("terms", "hebo"), (" carefully.", "helv")):
        page.insert_text((x, 100), text, fontname=font, fontsize=12)
        x += pymupdf.Font(font).text_length(text, 12)
    doc.save(tmp_path / "mixed.pdf")
    win = _window(qapp, tmp_path, tmp_path / "mixed.pdf")
    try:
        win.markup_ui.set_mode("edit")
        bold = next(i for i in _ids(win, "txt:0:") if win.edit._line(i).text == "terms")
        win.edit.edit_line(bold)
        editor = win.edit.editor
        assert editor.toPlainText() == "Please read the terms carefully."
        assert editor.char_sources()[16:21] == [1] * 5  # ("terms": the bold run)
        cursor = editor.textCursor()
        cursor.setPosition(21)  # (just after "terms")
        editor.setTextCursor(cursor)
        editor.insertPlainText(" and fees")
        editor.finish()
        spans = [(s["text"].strip(), s["font"]) for b in win.session.doc[0].get_text("dict")["blocks"]
                 for line in b["lines"] for s in line["spans"] if s["text"].strip()]
        assert spans == [("Please read the", "Helvetica"), ("terms and fees", "Helvetica-Bold"),
                         ("carefully.", "Helvetica")]
        assert win.undo_stack.undoText() == "Edit Text"
        win.undo_stack.undo()
        assert " ".join(win.session.doc[0].get_text().split()) == "Please read the terms carefully."
    finally:
        win.undo_stack.setClean()
        win.close()


def test_add_text_keeps_its_i_beam_except_over_the_selected_text(blank):
    """User 2026-10-24: after Enter the cursor didn't come back to the I-beam; it should be the
    I-beam unless the mouse is over a selected box (a press there moves it)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    win = blank
    view = win.view

    def move_to(x, y):
        pos = view.mapFromScene(view._items[0].mapToScene(QPointF(x, y)))
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def shape():
        return view.viewport().cursor().shape()

    win.markup_ui.set_mode("edit")
    win.edit.set_tool("text")
    view.shapeDrawn.emit("point", 0, QRectF(100, 100, 0, 0))
    editor = win.markup.editor
    editor.insertPlainText("Hello")
    QApplication.sendEvent(editor, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    QCoreApplication.processEvents()
    assert win.edit.tool == "text" and shape() == Qt.CursorShape.IBeamCursor
    view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)  # (what the new text's frame does under a still mouse)
    view.refresh_tool_cursor()
    assert shape() == Qt.CursorShape.IBeamCursor
    view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
    move_to(400, 500)  # (over empty page: the tool's cursor)
    assert shape() == Qt.CursorShape.IBeamCursor

    [text_id] = [i for i in view._markup_items if i.startswith("mk:")]
    view.set_selected_markup(text_id)  # (the text selected: a press on it moves it)
    _page, shown = view.markup_rect(text_id)
    move_to(shown.center().x(), shown.center().y())
    assert shape() == Qt.CursorShape.SizeAllCursor
    move_to(400, 500)
    assert shape() == Qt.CursorShape.IBeamCursor


def test_delete_key_and_add_text(window):
    window.markup_ui.set_mode("edit")
    line_id = next(i for i in _ids(window, "txt:0:") if "Email" in window.edit._line(i).text)
    window.edit.finish_editing()
    window.view.set_selected_markup(line_id)
    window._delete_selected_marks()
    assert g.EMAIL not in window.session.doc[0].get_text()
    window.edit.set_tool("text")
    window.view.shapeDrawn.emit("point", 0, QRectF(100, 600, 0, 0))
    assert window.view.cursor_shape == Qt.CursorShape.IBeamCursor or window.edit.tool is None
    editor = window.markup.editor
    assert editor is not None and not editor.single_line and not editor.wrap
    editor.setPlainText("Typed on the page")
    editor.finish()
    # an item you can still move and resize (until the page is flattened)
    [item] = [i for i in window.markup.items if i.kind is Kind.TEXT]
    assert item.spec.text == "Typed on the page"
    assert "mk:" + item.item_id in window.view._markup_items  # selectable in Edit PDF mode
    # Add Text stays on (user 2026-10-17): the next click is more text; Esc doesn't stop it
    assert window.edit.tool == "text" and window.view.draw_tool == "point"
    window.view.drawToolCanceled.emit()
    assert window.edit.tool == "text"
    window.markup_ui.set_mode("form")  # closing the tool stops it
    assert window.edit.tool is None


@pytest.fixture
def imaged(qapp, tmp_path, pdf_copy):
    win = _window(qapp, tmp_path, pdf_copy("image.pdf"))
    yield win
    win.undo_stack.setClean()
    win.close()


def test_move_replace_add_image(imaged, tmp_path):
    win = imaged
    win.markup_ui.set_mode("edit")
    [image_id] = _ids(win, "img:")
    win.view.markupMoved.emit(image_id, QRectF(300, 400, 100, 100))
    [moved] = win.edit._page_images(0)
    assert moved.rect == pytest.approx((300, 400, 400, 500), abs=0.5)
    win.undo_stack.undo()
    assert win.edit._page_images(0)[0].rect == pytest.approx(tuple(g.IMAGE_RECT), abs=0.5)
    png = tmp_path / "blue.png"
    Image.new("RGB", (40, 20), (0, 0, 255)).save(png)
    win.edit.replace_image(_ids(win, "img:")[0], png)
    assert win.session.doc[0].get_pixmap(clip=pymupdf.Rect(160, 160, 170, 170), dpi=72).pixel(2, 2)[:3] == (0, 0, 255)
    win.edit.add_image(png)
    assert win.view.draw_tool == "place"
    win.view.shapeDrawn.emit("place", 0, QRectF(400, 600, 80, 40))
    # one and done (user 2026-10-17): it places this picture, then the arrow with it selected
    assert len(win.edit._page_images(0)) == 2 and win.edit.tool is None
    assert win.view.selected_markup.startswith("img:0:")
    win.edit.add_image(png)
    win.view.drawToolCanceled.emit()  # Esc cancels it
    assert win.edit.tool is None and win.view.draw_tool is None


@pytest.fixture
def blank(qapp, tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(path)
    doc.close()
    win = _window(qapp, tmp_path, path)
    yield win
    win.undo_stack.setClean()
    win.close()


def test_prepare_form_create_move_properties_delete(blank, monkeypatch):
    win = blank
    opened = []
    monkeypatch.setattr(win.edit, "edit_properties", lambda name: opened.append(name))
    win.markup_ui.set_mode("design")
    win.edit.set_tool(FieldKind.TEXT)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 72, 2, 2))  # a click: standard size
    [f] = F.list_fields(win.session)
    assert f.name == "Text1" and pymupdf.Rect(f.widgets[0].rect).width == pytest.approx(160)
    assert not opened and win.edit.tool is FieldKind.TEXT and len(_ids(win, "fld:")) == 1  # (stays on)
    win.edit.set_tool(FieldKind.RADIO)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 120, 14, 14))
    assert opened == ["Choice1"]
    radio = F.get_field(win.session, "Choice1")
    assert radio.kind is FieldKind.RADIO and len(radio.widgets) == 2

    fld = next(i for i in _ids(win, "fld:") if i.startswith("fld:Text1"))
    win.view.markupMoved.emit(fld, QRectF(200, 300, 180, 24))
    assert F.get_field(win.session, "Text1").widgets[0].rect == pytest.approx((200, 300, 380, 324))

    from planetpdf.core.forms import design as D

    old = D.field_spec(win.session, "Choice1")
    assert old.labels == ("Choice 1", "Choice 2")  # (each button shows its text)
    dialog = FieldPropertiesDialog(old)
    dialog.name.setText("size")
    for row, text in zip(dialog.buttons.rows, ("S", "M")):
        row.text.setPlainText(text)
    dialog.buttons.add_row().text.setPlainText("L")
    dialog.required.setChecked(True)
    win.edit.change_field("Choice1", dialog.spec())
    size = F.get_field(win.session, "size")
    assert [w.on_state for w in size.widgets] == ["S", "M", "L"] and size.required
    assert [w.label for w in size.widgets] == ["S", "M", "L"]
    win.undo_stack.undo()
    assert [w.on_state for w in F.get_field(win.session, "Choice1").widgets] == ["Choice1", "Choice2"]
    win.undo_stack.undo()  # the move
    assert F.get_field(win.session, "Text1").widgets[0].rect == pytest.approx((72, 72, 232, 92))

    win.view.set_selected_markup(next(i for i in _ids(win, "fld:") if i.startswith("fld:Text1")))
    win._delete_selected_marks()
    assert "Text1" not in {x.name for x in F.list_fields(win.session)}
    win.undo_stack.undo()
    assert "Text1" in {x.name for x in F.list_fields(win.session)}
    win.undo_stack.redo()
    assert "Text1" not in {x.name for x in F.list_fields(win.session)}
    assert win.save()  # (deleting widgets used to leave the file locked on Windows)
    names = {x.name for x in F.list_fields(win.session)}
    assert names == {"Choice1"}  # the properties change was undone before the delete


def test_prepare_form_shows_fields_as_in_the_pdf_until_selected(blank):
    """User 2026-10-02: "show it like you are looking at the PDF unless the item being
    edited is selected"."""
    win = blank
    win.markup_ui.set_mode("design")
    win.edit.set_tool(FieldKind.TEXT)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 72, 2, 2))
    [item_id] = _ids(win, "fld:")
    overlay = win.view._markup_items[item_id]
    win.view.set_selected_markup(None)
    assert overlay.brush().color().alpha() == 0 and overlay.pen().color().alpha() == 0
    assert overlay._label is not None and not overlay._label.isVisible()  # (no name tag)
    win.view.set_selected_markup(item_id)
    assert overlay.brush().color().alpha() > 0 and overlay._label.isVisible()
    assert overlay.toolTip().startswith("Text field: Text1")


def test_a_radio_group_is_one_object_in_prepare_form(blank):
    """User 2026-10-02: "all radio buttons should be grouped together and should not be
    separate objects". One frame round the group; moving it moves every button."""
    win = blank
    win.markup_ui.set_mode("design")
    win.edit.set_tool(FieldKind.RADIO)
    opened = []
    win.edit.edit_properties = lambda name: opened.append(name)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 120, 2, 2))
    ids = _ids(win, "fld:Choice1")
    assert len(ids) == 1  # (not one per button)
    before = [w.rect for w in F.get_field(win.session, "Choice1").widgets]
    _page, shown = win.view.markup_rect(ids[0])
    win.view.markupMoved.emit(ids[0], shown.translated(50, 30))
    after = [w.rect for w in F.get_field(win.session, "Choice1").widgets]
    for (a0, a1, _a2, _a3), (b0, b1, _b2, _b3) in zip(before, after):
        assert b0 - a0 == pytest.approx(50, abs=0.5) and b1 - a1 == pytest.approx(30, abs=0.5)
    win.undo_stack.undo()
    assert [w.rect for w in F.get_field(win.session, "Choice1").widgets] == pytest.approx(before)
    # resizing: narrower wraps the text (taller buttons); the height dragged to is ignored
    _page, shown = win.view.markup_rect(ids[0])
    win.view.markupMoved.emit(ids[0], QRectF(shown.left(), shown.top(), 60, 400))
    widths = {round(pymupdf.Rect(w.rect).width) for w in F.get_field(win.session, "Choice1").widgets}
    total = pymupdf.Rect(F.get_field(win.session, "Choice1").widgets[-1].rect).y1 - shown.top()
    assert widths == {60} and total < 100  # (not stretched to the 400 pt dragged)


def test_radio_buttons_dialog_one_row_per_button_with_paragraphs(qapp):
    """User 2026-10-02: each button its own entry; pasted paragraphs keep their line breaks;
    the form-data value is made from the text (unless typed); other programs' values stay."""
    from planetpdf.core.forms import design as D

    spec = D.FieldSpec("terms", FieldKind.RADIO, 0, (72, 72, 300, 88), options=("Choice1", "Choice2"),
                       option_rects=((72, 72, 300, 88), (72, 96, 300, 112)), labels=("Choice 1", "Choice 2"))
    dialog = FieldPropertiesDialog(spec)
    rows = dialog.buttons.rows
    rows[0].text.setPlainText("I agree to the terms.\nAnd to the billing schedule.")
    rows[1].text.setPlainText("I do not agree.")
    third = dialog.buttons.add_row()
    third.text.setPlainText("I agree to the terms.\nBut not the rest.")
    third.value.setText("partly")
    third.value.textEdited.emit("partly")
    options, labels, rects = dialog.buttons.result()
    assert options == ("I agree to the terms.", "I do not agree.", "partly")
    assert labels[0] == "I agree to the terms.\nAnd to the billing schedule."
    assert rects[2] == (72, 120, 300, 136)  # (the new one below the last)
    dialog.buttons.remove_row(rows[1])
    assert len(dialog.buttons.rows) == 2 and not rows[0].remove.isEnabled()  # (at least two)
    dialog._accept()
    assert dialog.result() == FieldPropertiesDialog.DialogCode.Accepted
    # a group from another program: no texts, and its values are kept as they are
    foreign = D.FieldSpec("x", FieldKind.RADIO, 0, (0, 0, 14, 14), options=("Yes", "No"),
                          option_rects=((0, 0, 14, 14), (0, 20, 14, 34)))
    other = FieldPropertiesDialog(foreign)  # (kept: its widgets go with it)
    assert other.buttons.result()[0] == ("Yes", "No")


def test_field_properties_dialog_validates(qapp):
    from planetpdf.core.forms import design as D

    dialog = FieldPropertiesDialog(D.FieldSpec("pick", FieldKind.COMBO, 0, (0, 0, 100, 20), options=("A",)))
    dialog.choices.setPlainText("")
    dialog._accept()
    assert "choice" in dialog.error.text()
    dialog.choices.setPlainText("A\nB")
    dialog.editable.setChecked(True)
    dialog._accept()
    assert dialog.result() == FieldPropertiesDialog.DialogCode.Accepted
    assert dialog.spec().options == ("A", "B") and dialog.spec().editable_combo
