"""Prepare Form as in Acrobat (user screenshots 2026-10-03): the bar (Select, field tools in
Acrobat's order, Keep Tool Selected, Preview), the panel (Align, Center, Match Size,
Distribute, Fields), buttons and image fields."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication

from planetpdf.core.forms import buttons as B
from planetpdf.core.forms import design as D
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind
from planetpdf.ui.edit_controller import BARCODE_FIELD, IMAGE_FIELD
from planetpdf.ui.forms_controller import field_key
from planetpdf.ui.form_panel import align, center_on_page, distribute, match_size
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "blank.pdf")
    w = MainWindow(tmp_path / "profile")
    w.resize(1100, 850)
    w.show()
    w.open_file(tmp_path / "blank.pdf")
    w.edit.edit_properties = lambda name: None
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _field_ids(w):
    return sorted(k for k in w.view._markup_items if k.startswith("fld:"))


def _make_texts(w, boxes):
    w.start_form(detect=False, signatures=False)  # (Prepare Form ▸ Start)
    for box in boxes:
        w.edit.set_tool(FieldKind.TEXT)
        w.view.shapeDrawn.emit("box", 0, QRectF(*box))
    return _field_ids(w)


def _rects(w):
    return {f.name: pymupdf.Rect(f.widgets[0].rect) for f in F.list_fields(w.session)}


def test_geometry():
    a, b, c = QRectF(10, 10, 50, 20), QRectF(40, 60, 80, 30), QRectF(200, 300, 20, 10)
    assert [r.left() for r in align([a, b, c], "left")] == [10, 10, 10]
    assert [r.center().y() for r in align([a, b, c], "middle")] == [20, 20, 20]
    assert [(r.width(), r.height()) for r in match_size([a, b, c], "both")] == [(50, 20)] * 3
    spread = distribute([a, b, c], "down")
    gaps = [spread[1].top() - spread[0].bottom(), spread[2].top() - spread[1].bottom()]
    assert gaps[0] == pytest.approx(gaps[1]) and spread[2].top() == 300
    centered = center_on_page([a], QRectF(0, 0, 600, 800), "both")
    assert centered[0].center().x() == 300 and centered[0].center().y() == 400


def test_bar_follows_acrobat(win):
    win.start_form(detect=False, signatures=False)
    labels = [a.text() for a in win.edit_ui.design_toolbar.actions() if a.text()]
    tools = ["Select", "Edit text && images", "Add Text", "Text Field", "Checkbox", "Radio Buttons", "List Box",
             "Dropdown", "Button", "Image Field", "Date", "Signature Field", "Help"]
    assert [l for l in labels if l in tools] == tools
    assert win.tool_panes.bars["prepare_form"].preview_button.text() == "Preview"


def test_align_match_distribute_center_and_undo(win):
    ids = _make_texts(win, [(72, 72, 160, 20), (100, 150, 100, 30), (90, 400, 120, 20)])
    assert len(ids) == 3 and win._form_design_dock.isVisible()
    first = next(i for i in ids if i.startswith("fld:Text1"))
    win.view.set_group([first] + [i for i in ids if i != first])
    panel = win.form_panel
    panel.update_buttons()
    assert panel.buttons["align:left"].isEnabled() and panel.buttons["distribute:down"].isEnabled()
    assert panel.arrange("align", "left", "Align Left")
    assert {round(r.x0) for r in _rects(win).values()} == {72}
    assert win.undo_stack.undoText() == "Align Left"
    assert len(win.view.selected_group) == 3  # (still selected)
    assert panel.arrange("match", "both", "Match Width and Height")
    assert {(round(r.width), round(r.height)) for r in _rects(win).values()} == {(160, 20)}
    assert panel.arrange("distribute", "down", "Distribute Down")
    tops = sorted(r.y0 for r in _rects(win).values())
    assert tops[1] - tops[0] == pytest.approx(tops[2] - tops[1], abs=0.5)
    assert panel.arrange("center", "across", "Center Across the Page")
    assert {round(r.x0 + r.width / 2) for r in _rects(win).values()} == {306}
    for _ in range(4):
        win.undo_stack.undo()
    assert round(_rects(win)["Text2"].x0) == 100


def test_selected_fields_move_together_and_delete_together(win):
    ids = _make_texts(win, [(72, 72, 160, 20), (72, 120, 160, 20)])
    win.view.set_group(ids)
    win.view.markupGroupMoved.emit(ids, QPointF(10, 20))
    assert sorted(round(r.y0) for r in _rects(win).values()) == [92, 140]
    win.view.set_group(_field_ids(win))
    assert win.edit.delete()
    assert F.list_fields(win.session) == []
    win.undo_stack.undo()
    assert len(F.list_fields(win.session)) == 2


def test_fields_list_selects_on_the_page(win):
    _make_texts(win, [(72, 72, 160, 20), (72, 120, 160, 20)])
    panel = win.form_panel
    panel.refresh()
    top = panel.fields.topLevelItem(0)
    assert top.text(0) == "Page 1" and [top.child(i).text(0) for i in range(top.childCount())] == ["Text1", "Text2"]
    panel._select(top.child(1))
    assert win.view.selected_markup.startswith("fld:Text2")


def test_field_tool_stays_on(win):
    """User 2026-10-17: every tool stays on until another is chosen (the pin is gone)."""
    win.start_form(detect=False, signatures=False)
    win.edit.set_tool(FieldKind.TEXT)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 72, 2, 2))
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 120, 2, 2))
    assert win.edit.tool is FieldKind.TEXT
    win.view.drawToolCanceled.emit()  # Esc
    assert win.edit.tool is FieldKind.TEXT
    win.edit_ui.act_select_field.trigger()
    assert win.edit.tool is None and win.edit_ui.act_select_field.isChecked()


def test_preview_fills_the_form_then_back_to_editing(win):
    _make_texts(win, [(72, 72, 160, 20)])
    win.edit_ui.act_preview_form.setChecked(True)
    assert win.markup_ui.mode == "form" and win.edit.mode is None
    assert win.tool_panes.current == "prepare_form" and win.tool_panes.bars["prepare_form"].isVisible()
    assert win.tool_panes.bars["prepare_form"].preview_button.text() == "Edit"
    win.forms.set_value("Text1", "Robert")
    win.edit_ui.act_preview_form.setChecked(False)
    assert win.edit.mode == "design" and F.get_field(win.session, "Text1").value == "Robert"
    win.edit_ui.act_preview_form.setChecked(True)
    win.tool_panes.close()
    assert not win.edit_ui.act_preview_form.isChecked() and not win.tool_panes.previewing


def test_clear_button_and_image_field(win, tmp_path):
    _make_texts(win, [(72, 72, 160, 20)])
    win.edit.set_tool(FieldKind.BUTTON)
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 120, 2, 2))
    win.edit.change_field("Button1", D.FieldSpec("Button1", FieldKind.BUTTON, 0,
                                                 D.field_spec(win.session, "Button1").rect, caption="Clear",
                                                 action="reset"))
    win.edit.set_tool(IMAGE_FIELD)
    win.view.shapeDrawn.emit("box", 0, QRectF(300, 300, 2, 2))
    assert D.field_spec(win.session, "Image1").action == "image"
    win.edit_ui.act_preview_form.setChecked(True)
    win.forms.set_value("Text1", "Robert")
    win.forms.refresh()
    f = F.get_field(win.session, "Button1")
    win.forms.activate(field_key("Button1", f.widgets[0].xref), toggle=True)  # (a click on it)
    assert F.get_field(win.session, "Text1").value in ("", None)
    assert win.undo_stack.undoText() == "Clear Form"
    picture = tmp_path / "photo.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), False)
    pix.set_rect(pix.irect, (0, 128, 255))
    pix.save(picture)
    image = F.get_field(win.session, "Image1").widgets[0]
    assert win.forms.choose_picture("Image1", image.xref, picture)
    assert B.has_image(win.session.doc, image.xref)
    win.undo_stack.undo()
    assert not B.has_image(win.session.doc, F.get_field(win.session, "Image1").widgets[0].xref)


def test_barcode_tool_and_its_properties(win, monkeypatch):
    """Prepare Form ▸ Barcode (user 2026-10-23): placed like the other fields, set up in its
    properties, and drawn again (its page too) whenever a value changes while filling in."""
    from planetpdf.core.forms import barcodes
    from planetpdf.ui.dialogs.field_properties import FieldPropertiesDialog

    _make_texts(win, [(72, 72, 160, 20), (72, 100, 160, 20)])
    assert BARCODE_FIELD in win.edit_ui.field_actions
    win.edit.set_tool(BARCODE_FIELD)
    win.view.shapeDrawn.emit("box", 0, QRectF(300, 300, 2, 2))  # (a click: the standard size)
    spec = D.field_spec(win.session, "Barcode1")
    assert spec.action == "barcode" and spec.barcode == barcodes.BarcodeSettings()
    assert pymupdf.Rect(spec.rect).width == pytest.approx(144)

    dialog = FieldPropertiesDialog(spec, None, ["Text1", "Text2"])
    assert dialog.windowTitle() == "Barcode Properties" and dialog.encoded.count() == 2
    dialog.symbology.setCurrentIndex(dialog.symbology.findData("pdf417"))
    dialog.these_fields.setChecked(True)
    assert dialog.encoded.isEnabled()
    dialog._accept()  # (nothing ticked: says so)
    assert "Tick the fields" in dialog.error.text()
    dialog.encoded.item(1).setCheckState(Qt.CheckState.Checked)
    dialog._accept()
    assert dialog.result() == FieldPropertiesDialog.DialogCode.Accepted
    win.edit.change_field("Barcode1", dialog.spec())
    settings = barcodes.BarcodeSettings("pdf417", ("Text2",))
    assert D.field_spec(win.session, "Barcode1").barcode == settings

    refreshed = []
    monkeypatch.setattr(win.view, "refresh_page", lambda page: refreshed.append(page))
    xref = F.get_field(win.session, "Barcode1").widgets[0].xref
    before = win.session.doc.xref_get_key(xref, "AP/N")
    win.edit_ui.act_preview_form.setChecked(True)
    win.forms.set_value("Text2", "Robert")
    assert win.session.doc.xref_get_key(xref, "AP/N") != before and 0 in refreshed
    assert barcodes.data_text(win.session, settings) == "Text2\nRobert"
    win.undo_stack.undo()  # (the value back: the barcode follows)
    assert barcodes.data_text(win.session, settings) == "Text2\n"


def test_edit_text_and_images_keeps_the_prepare_form_bar(win):
    """User screenshot 2026-10-03: Prepare Form's second button, "Edit text & images"."""
    _make_texts(win, [(72, 72, 160, 20)])
    eui = win.edit_ui
    eui.act_edit_form_content.setChecked(True)
    assert win.edit.mode == "edit" and win.tool_panes.current == "prepare_form"
    assert win.tool_panes.bars["prepare_form"].isVisible() and win._edit_dock.isVisible()
    assert not win._form_design_dock.isVisible()
    eui.field_actions[FieldKind.CHECKBOX].trigger()  # (a field tool: back to the fields)
    assert win.edit.mode == "design" and win.edit.tool is FieldKind.CHECKBOX
    assert not eui.act_edit_form_content.isChecked() and win._form_design_dock.isVisible()
    eui.act_edit_form_content.setChecked(True)
    eui.act_preview_form.setChecked(True)  # (Preview takes over from editing the page)
    assert win.markup_ui.mode == "form" and not eui.act_edit_form_content.isChecked()
    eui.act_preview_form.setChecked(False)
    assert win.edit.mode == "design"