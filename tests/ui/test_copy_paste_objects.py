"""Edit ▸ Copy / Paste copy the object that is selected (user 2026-11-24: "When I have an object
selected and I hit copy/paste, it should make a copy of that object. Instead, it seems to make a
copy of some random thing I selected before")."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core.forms import fields as F
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, pdf_copy, name: str, tool: str) -> MainWindow:
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 950)
    w.show()
    w.open_file(pdf_copy(name))
    w.tool_panes.open(tool)
    if tool == "prepare_form" and w.markup_ui.mode != "design":
        w.center_for(None)
        w.markup_ui.set_mode("design")
    QCoreApplication.processEvents()
    return w


@pytest.fixture
def closing():
    windows = []
    yield windows.append
    for w in windows:
        w.edit.finish_editing()
        w.undo_stack.setClean()
        w.close()


def _count(win, text, page=0):
    return win.session.doc[page].get_text().count(text)


def _line_id(win, words, page=0):
    return next(k for k in win.view._markup_items if k.startswith(f"txt:{page}:") and words in win.edit._line(k).text)


def test_copy_paste_copies_the_selected_line_not_an_old_text_selection(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "text.pdf", "edit")
    closing(win)
    first, other = g.TEXT_LINES[0], g.TEXT_LINES[2]
    # an old text selection (the "random thing selected before") and an item copied long ago
    from planetpdf.core.text import page_words

    win.text_tools.set_selection(0, page_words(win.session, 0)[:2])
    assert win.text_tools.selected_text()
    key = _line_id(win, other)
    win.view.set_selected_markup(key)
    before = _count(win, other)
    assert win.clipboard_tools.copy()
    assert QApplication.clipboard().text() == other  # (other programs get its text)
    assert win.clipboard_tools.paste()
    assert _count(win, other) == before + 1 and _count(win, first) == 1
    assert win.undo_stack.undoText() == "Paste"
    copy_key = win.view.selected_markup  # the copy is selected, a little down and to the right
    original, copied = win.edit._line(key), win.edit._line(copy_key)
    assert copied.text == other
    assert copied.origin[0] - original.origin[0] == pytest.approx(12, abs=1)
    assert copied.origin[1] - original.origin[1] == pytest.approx(12, abs=1)
    assert win.clipboard_tools.paste()  # again: further on
    assert _count(win, other) == before + 2
    assert win.edit._line(win.view.selected_markup).origin[1] - original.origin[1] == pytest.approx(24, abs=1)
    win.undo_stack.undo()
    win.undo_stack.undo()
    assert _count(win, other) == before


def test_several_objects_paste_onto_another_page_in_the_same_place(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "text.pdf", "edit")
    closing(win)
    keys = [_line_id(win, g.TEXT_LINES[0]), _line_id(win, g.TEXT_LINES[1])]
    win.view.set_group(keys)
    origins = [win.edit._line(k).origin for k in keys]
    assert win.clipboard_tools.copy()
    win.view.go_to_page(1)
    QCoreApplication.processEvents()
    assert win.view.current_page == 1
    assert win.clipboard_tools.paste()
    assert win.undo_stack.undoText() == "Paste 2 Objects"
    for text in g.TEXT_LINES[:2]:
        assert _count(win, text, page=1) == 2
    pasted = sorted(win.edit._line(k).origin for k in win.view.selected_group)
    assert pasted == [pytest.approx(o, abs=1) for o in sorted(origins)]  # (same place, no offset)


def test_an_image_and_a_shape_are_copied(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "image.pdf", "edit")
    closing(win)
    key = next(k for k in win.view._markup_items if k.startswith("img:0:"))
    win.view.set_selected_markup(key)
    assert win.clipboard_tools.copy() and win.clipboard_tools.paste()
    rects = sorted(tuple(round(v) for v in i["bbox"]) for i in win.session.doc[0].get_image_info())
    assert len(rects) == 2
    assert rects[1][0] - rects[0][0] == 12 and rects[1][1] - rects[0][1] == 12

    shapes = _window(tmp_path, pdf_copy, "vector.pdf", "edit")
    closing(shapes)
    before = len(shapes.session.doc[0].get_drawings())
    vec = next(k for k in shapes.view._markup_items if k.startswith("vec:0:")
               and pymupdf.Rect(shapes.edit._drawing(k).rect).width > 50)
    shapes.view.set_selected_markup(vec)
    assert shapes.clipboard_tools.copy() and shapes.clipboard_tools.paste()
    assert len(shapes.session.doc[0].get_drawings()) == before + 1
    assert shapes.view.selected_markup and shapes.view.selected_markup.startswith("vec:")


def test_cut_takes_the_selected_line_away(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "text.pdf", "edit")
    closing(win)
    text = g.TEXT_LINES[1]
    win.view.set_selected_markup(_line_id(win, text))
    assert win.clipboard_tools.cut()
    assert _count(win, text) == 0
    assert win.clipboard_tools.paste()
    assert _count(win, text) == 1


def test_a_form_field_is_copied_with_a_new_name(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "acroform.pdf", "prepare_form")
    closing(win)
    assert win.edit.mode == "design"
    key = next(k for k in win.view._markup_items if k.startswith("fld:full_name"))
    win.view.set_selected_markup(key)
    assert win.clipboard_tools.copy() and win.clipboard_tools.paste()
    names = {f.name for f in F.list_fields(win.session)}
    new = names - set(g.FORM_FIELDS)
    assert len(new) == 1
    [name] = new
    copy = F.get_field(win.session, name)
    original = F.get_field(win.session, "full_name")
    assert copy.kind is original.kind
    assert copy.widgets[0].rect[1] - original.widgets[0].rect[1] == pytest.approx(12, abs=0.5)
    assert win.view.selected_markup and win.view.selected_markup.startswith(f"fld:{name}")


def test_nothing_selected_still_copies_selected_page_text(qapp, tmp_path, pdf_copy, closing):
    win = _window(tmp_path, pdf_copy, "text.pdf", "edit")
    closing(win)
    win.view.set_selected_markup(None)
    win.clipboard_tools.select_all()
    assert win.clipboard_tools.copy()
    assert g.TEXT_LINES[0] in QApplication.clipboard().text()


def test_copies_are_shared_by_every_window_and_password_fields_stay_password_fields(tmp_path):
    """Security review 2026-11-25: a paste in a second window put back that window's older copy,
    and a copied password field lost its password flag."""
    from planetpdf.core.forms import design as D
    from planetpdf.core.forms import fields as F
    from planetpdf.ui import clipboard_tools as CT

    CT._Copied.items, CT._Copied.objects = ["older"], None
    first, second = CT.ClipboardTools.__new__(CT.ClipboardTools), CT.ClipboardTools.__new__(CT.ClipboardTools)
    first._copied = ["newest"]
    assert second._copied == ["newest"]  # (one copy, the newest, whichever window pastes)
    CT._Copied.items = []

    import pymupdf

    from planetpdf.core.document import PdfSession

    doc = pymupdf.open()
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "pin", pymupdf.Rect(72, 72, 200, 92)
    w.field_flags = F.FF_PASSWORD
    w.field_value = "1234"
    page.add_widget(w)
    doc.save(tmp_path / "form.pdf")
    session = PdfSession.open(tmp_path / "form.pdf")
    try:
        spec = D.field_spec(session, "pin")
        assert spec.password
        from dataclasses import replace

        D.create_field(session, replace(spec, name="pin2", value=None))
        [copy] = [f for f in F.list_fields(session) if f.name == "pin2"]
        assert copy.password
    finally:
        session.close()


def test_cut_removes_nothing_when_some_objects_cant_be_copied(qapp, tmp_path, pdf_copy, closing, monkeypatch):
    """Security review 2026-11-25: Cut could remove more than Paste puts back."""
    from planetpdf.ui.edit_controller import CopiedObjects

    win = _window(tmp_path, pdf_copy, "text.pdf", "edit")
    closing(win)
    deleted = []
    monkeypatch.setattr(type(win.view), "selected_group", property(lambda self: ["txt:0:0", "lnk:0:0"]))
    monkeypatch.setattr(win.edit, "copy_selection",
                        lambda: CopiedObjects(None, (), (), 0, "x", 2, left_out=1))
    monkeypatch.setattr(win.edit, "delete", lambda *a, **k: deleted.append(True))
    assert not win.clipboard_tools.cut()
    assert deleted == [] and "nothing was cut" in win.statusBar().currentMessage()
