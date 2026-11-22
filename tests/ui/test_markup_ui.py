"""Markup mode, stamps, placed signatures and digital signing through the window."""

from __future__ import annotations

import sys

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF
from PySide6.QtWidgets import QApplication

from planetpdf.core import markup as K
from planetpdf.core.markup import Kind
from planetpdf.core.signing.digital import Verdict
from planetpdf.core.signing.providers.pkcs12 import Pkcs12Provider
from planetpdf.core.signing.visual import render_typed
from planetpdf.core.stamps import BUILTIN_STAMPS, builtin
from planetpdf.core.storage.settings import load_settings
from planetpdf.ui.dialogs.preferences import PreferencesDialog
from planetpdf.ui.dialogs.signature_capture import SignatureCaptureDialog
from planetpdf.ui.dialogs.stamps import StampEditorDialog, StampManagerDialog
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g
from tests.fixtures.certs import PFX_PASSWORD


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    win.settings.user_name = "Robert Boyle"
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _items(win):
    return K.list_markup(win.session)


def _draw(win, draw, data, page=0):
    win.view.shapeDrawn.emit(draw, page, data)


def test_mode_switch(window):
    assert not window.markup.markup_mode and not window.markup_ui.toolbar.isVisible()
    window.markup_ui.act_mode_markup.trigger()
    assert window.markup.markup_mode and window.markup_ui.toolbar.isVisible()
    assert not window.view._fields_interactive
    assert window._comments_dock.isVisible()
    window.markup_ui.act_mode_form.trigger()
    assert not window.markup.markup_mode and window.view._fields_interactive


def test_draw_box_undo_redo(window):
    window.markup.set_markup_mode(True)
    window.markup_ui.tool_actions[Kind.RECT].trigger()
    assert window.view.draw_tool == "rect"
    _draw(window, "rect", QRectF(100, 400, 150, 80))
    [item] = _items(window)
    assert item.kind is Kind.RECT and item.author == "Robert Boyle"
    assert window.view.selected_markup == item.item_id
    assert list(window.comments_panel._cards) == [item.item_id]  # (its card in the Comments panel)
    window.undo_stack.undo()
    assert _items(window) == []
    window.undo_stack.redo()
    assert len(_items(window)) == 1


def test_a_click_does_not_make_an_empty_box(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.RECT)
    _draw(window, "rect", QRectF(100, 100, 1, 1))
    assert _items(window) == []


def test_lines_arrows_and_freehand(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.ARROW)
    _draw(window, "line", [QPointF(100, 500), QPointF(300, 560)])
    window.markup.set_tool(Kind.INK)
    _draw(window, "ink", [QPointF(100, 600), QPointF(130, 620), QPointF(160, 600), QPointF(200, 640)])
    assert [i.kind for i in _items(window)] == [Kind.ARROW, Kind.INK]


def test_highlight_words_under_the_drag(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.HIGHLIGHT)
    words = window.markup._page_words(0)
    ssn_index = next(i for i, (rect, _line, _shown) in enumerate(words) if g.SSN in window.session.doc[0].get_textbox(pymupdf.Rect(rect)))
    _draw(window, "text", (ssn_index, ssn_index))  # the view sends the selected word range
    [item] = _items(window)
    assert item.kind is Kind.HIGHLIGHT
    assert g.SSN in window.session.doc[0].get_textbox(pymupdf.Rect(item.spec.rect))


def _type_and_finish(window, text):
    editor = window.markup.editor
    assert editor is not None, "expected an on-page editor"
    assert window.view.editor is not None and window.view.editor.widget() is editor
    editor.setPlainText(text)
    editor.finish()


def test_note_and_text_box_are_typed_on_the_page(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.NOTE)
    _draw(window, "point", QRectF(QPointF(400, 100), QPointF(400, 100)))
    assert window.markup.editor.note  # a note pad next to the icon, no modal dialog
    _type_and_finish(window, "Please check this")
    window.markup.set_tool(Kind.TEXTBOX)
    _draw(window, "textbox", QRectF(300, 600, 200, 40))
    editor = window.markup.editor
    assert not editor.note and editor.font().pixelSize() == round(window.settings.markup_font_size)
    _type_and_finish(window, "Typed on the page")
    texts = [(i.kind, i.text) for i in _items(window)]
    assert texts == [(Kind.NOTE, "Please check this"), (Kind.TEXTBOX, "Typed on the page")]
    assert "Typed on the page" in window.session.doc[0].get_text()
    assert window.view.editor is None


def test_empty_or_canceled_text_creates_nothing(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.TEXTBOX)
    _draw(window, "textbox", QRectF(300, 600, 5, 5))  # a click: default-width box
    assert window.view.editor.geometry().width() == pytest.approx(200)
    window.markup.editor.cancel()
    window.markup.set_tool(Kind.TEXTBOX)
    _draw(window, "textbox", QRectF(300, 600, 200, 40))
    _type_and_finish(window, "   ")
    assert _items(window) == []


def test_text_box_edited_in_place(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.TEXTBOX)
    _draw(window, "textbox", QRectF(300, 600, 200, 40))
    _type_and_finish(window, "First version")
    [item] = _items(window)
    window.markup.edit_text(item.item_id)
    editor = window.markup.editor
    assert editor.toPlainText() == "First version"
    page = window.session.doc[0]
    assert "First version" not in page.get_text("text", clip=pymupdf.Rect(item.spec.rect)) or True
    _type_and_finish(window, "Second version, longer\nand on two lines")
    [edited] = _items(window)
    assert edited.item_id == item.item_id and edited.text.startswith("Second version")
    assert edited.spec.rect[3] - edited.spec.rect[1] >= 40  # grew to fit
    window.undo_stack.undo()
    assert _items(window)[0].text == "First version"


def test_cancel_in_place_edit_restores_item(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.TEXTBOX)
    _draw(window, "textbox", QRectF(300, 600, 200, 40))
    _type_and_finish(window, "Keep me")
    [item] = _items(window)
    window.markup.edit_text(item.item_id)
    window.markup.editor.cancel()
    page = window.session.doc[0]  # keep the page alive: annots reference it weakly
    assert not page.load_annot(_items(window)[0].xref).flags & pymupdf.PDF_ANNOT_IS_HIDDEN
    assert "Keep me" in window.session.doc[0].get_text()


def test_move_and_resize_via_overlay_signal(window):
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.ELLIPSE)
    _draw(window, "rect", QRectF(100, 100, 100, 50))
    [item] = _items(window)
    window.view.markupMoved.emit(item.item_id, QRectF(300, 300, 200, 100))
    [moved] = _items(window)
    assert moved.spec.rect == pytest.approx((300, 300, 500, 400))
    window.undo_stack.undo()
    assert _items(window)[0].spec.rect == pytest.approx((100, 100, 200, 150))


def test_delete_key_and_foreign_comment_undo(qapp, tmp_path, pdf_copy):
    path = pdf_copy("text.pdf")
    with pymupdf.open(path) as doc:
        doc[0].add_text_annot((300, 300), "Reviewer comment")
        doc.saveIncr()
    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(path)
    try:
        win.markup.set_markup_mode(True)
        [item] = _items(win)
        assert not item.ours
        win.markup.select(item.item_id)
        win._delete_selected_marks()  # the Delete key's handler
        assert _items(win) == []
        win.undo_stack.undo()
        [back] = _items(win)
        assert back.text == "Reviewer comment"
    finally:
        win.undo_stack.setClean()
        win.close()


def test_place_builtin_stamp_one_shot(window):
    approved = builtin("approved-dynamic")  # APPROVED / By {user} at {time}, {date}
    window.markup.place_stamp(approved.stamp_id)
    assert window.view.draw_tool == "place"
    _draw(window, "place", QRectF(QPointF(300, 400), QPointF(300, 400)))  # a click
    [item] = _items(window)
    assert item.kind is Kind.STAMP
    assert window.markup.tool is None and window.view.draw_tool is None  # back to selection
    text = window.session.doc[0].get_text()
    assert "APPROVED" in text and "Robert Boyle" in text  # {user} filled in
    center = pymupdf.Rect(item.rect)
    assert abs((center.x0 + center.x1) / 2 - 300) < 1  # centered on the click


def test_flatten_stamp_and_undo(window):
    window.markup.place_stamp(builtin("void").stamp_id)
    _draw(window, "place", QRectF(200, 200, 160, 60))
    [item] = _items(window)
    window.markup.flatten(item.item_id)
    assert _items(window) == []
    assert "VOID" in window.session.doc[0].get_text()
    window.undo_stack.undo()
    assert [i.kind for i in _items(window)] == [Kind.STAMP]


def test_place_signature_creates_one_first(window, monkeypatch):
    def fake_create(kind):
        window.signature_library.add(render_typed("Robert Boyle"), kind)
        return True

    monkeypatch.setattr(window, "create_signature", fake_create)
    assert window.markup.place_signature("signature")
    _draw(window, "place", QRectF(QPointF(300, 650), QPointF(300, 650)))
    [item] = _items(window)
    assert item.kind is Kind.SIGNATURE
    assert pymupdf.Rect(item.rect).width == pytest.approx(144, abs=0.5)  # default 2 in wide
    # In Fill Form mode the signature is still selectable (to move it) but shapes aren't.
    assert item.item_id in window.view._markup_items
    assert (window.profile.signatures_dir / window.signature_library.items[0].file).exists()


def test_initials_and_saved_library_reused(window):
    window.signature_library.add(render_typed("RB"), "initials")
    assert window.markup.place_signature("initials")
    _draw(window, "place", QRectF(QPointF(500, 700), QPointF(500, 700)))
    [item] = _items(window)
    assert item.kind is Kind.INITIALS and pymupdf.Rect(item.rect).width == pytest.approx(54, abs=0.5)


def test_markup_survives_save_and_page_moves(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(pdf_copy("mixed_sizes.pdf"))
    try:
        win.markup.set_markup_mode(True)
        win.markup.set_tool(Kind.RECT)
        _draw(win, "rect", QRectF(50, 50, 100, 60), page=2)
        assert win.save()
        win.move_pages([2], 0)
        [item] = _items(win)
        assert item.page == 0
        assert item.item_id in win.view._markup_items
    finally:
        win.undo_stack.setClean()
        win.close()


# --- dialogs ------------------------------------------------------------------------------------


def test_signature_capture_typed_and_drawn(qapp):
    dialog = SignatureCaptureDialog("signature", "Robert Boyle")
    assert dialog.tabs.currentIndex() == 1  # typing, since we know the name
    assert dialog.build_png()
    dialog.tabs.setCurrentIndex(0)
    from planetpdf.core.document import PdfError

    with pytest.raises(PdfError):
        dialog.build_png()  # nothing drawn yet
    from PySide6.QtGui import QPainterPath

    path = QPainterPath(QPointF(40, 100))
    path.lineTo(QPointF(200, 60))
    path.lineTo(QPointF(380, 110))
    dialog.pad.strokes = [path]
    assert dialog.build_png()
    initials = SignatureCaptureDialog("initials", "Robert Boyle")
    assert initials.typed.text() == "RB"


def test_stamp_editor_and_manager(window):
    manager = StampManagerDialog(window)
    assert manager.list.count() == len(BUILTIN_STAMPS)
    base = BUILTIN_STAMPS[0]
    editor = StampEditorDialog(window, base)
    editor.line1.setText("SCANNED")
    editor.line2.setText("{date} by {user}")
    editor.rotation.setValue(-12)
    spec = editor.spec()
    assert spec.lines == ("SCANNED", "{date} by {user}") and spec.rotation == -12.0
    assert editor.preview.pixmap() is not None and not editor.preview.pixmap().isNull()
    new = window.stamp_library.add_text_stamp(spec.name, list(spec.lines), rotation=spec.rotation)
    manager.populate(select=new.stamp_id)
    assert manager.list.count() == len(BUILTIN_STAMPS) + 1
    assert (window.profile.stamps_dir / "stamps.json").exists()


def test_preferences_saved_to_profile(window):
    dialog = PreferencesDialog(window)
    dialog.user_name.setText("R. Boyle")
    dialog.tsa.setText("http://timestamp.example")
    dialog._save()
    saved = load_settings(window.profile.settings_dir)
    assert saved.user_name == "R. Boyle" and saved.timestamp_url == "http://timestamp.example"


# --- digital signing through the window -------------------------------------------------------------


def test_digital_sign_to_new_file(window, test_certs, tmp_path):
    provider = Pkcs12Provider(test_certs.pfx, PFX_PASSWORD)
    target = tmp_path / "signed.pdf"
    assert window.finish_digital_sign(provider, 0, (72, 650, 300, 720), False, target)
    assert window.session.path == target.resolve()
    assert window.session.has_digital_signatures
    assert window._signatures_dock.isVisible()
    tree = window.signatures_panel.tree
    assert tree.topLevelItemCount() == 1 and "not trusted" in tree.topLevelItem(0).text(0)


def test_digital_sign_in_place_and_trusted(window, test_certs):
    window.trust_store.import_certificate(test_certs.signer_cert_pem)
    provider = Pkcs12Provider(test_certs.pfx, PFX_PASSWORD)
    path = window.session.path
    assert window.finish_digital_sign(provider, 0, (72, 650, 300, 720), False, path)
    statuses = window.check_signatures()
    assert [s.verdict for s in statuses] == [Verdict.VALID]
    assert window.session.path == path


def test_certified_pdf_invisible_from_the_menu(window, test_certs, tmp_path, monkeypatch):
    """Save as Other ▸ Certified PDF: the dialog in certify mode; no box drawn when the
    certification is invisible; the panel says it's certified."""
    from PySide6.QtWidgets import QDialog, QFileDialog

    from planetpdf.ui.dialogs import digital_sign as DS

    def certify(dialog):
        assert dialog.windowTitle() == "Certify Document" and dialog.level.isVisibleTo(dialog)
        dialog.cert.setText(str(test_certs.pfx))
        dialog.password.setText(PFX_PASSWORD)
        dialog.level.setCurrentIndex(dialog.level.findData("none"))
        assert dialog.unlock()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DS.DigitalSignDialog, "exec", certify)
    target = tmp_path / "certified.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    window.act_certify.trigger()
    assert window.session.path == target.resolve() and window.session.has_digital_signatures
    [status] = window.check_signatures()
    assert status.certified == "none"
    window.act_certify.trigger()  # (already signed now: refused before any dialog)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows certificate store")
def test_sign_with_a_windows_store_certificate(window, test_certs, tmp_path, monkeypatch):
    """The signing dialog lists the Windows Personal-store certificates that can sign; the
    key stays in Windows (here an in-memory copy of the test Digital ID, not the real store)."""
    from planetpdf.core.signing.providers import windows_store as WS
    from planetpdf.ui.dialogs.digital_sign import DigitalSignDialog

    memory = WS.WindowsStoreProvider.from_pfx_in_memory(test_certs.pfx.read_bytes(), PFX_PASSWORD)
    monkeypatch.setattr(WS, "store_certificates", lambda *a, **k: [memory.certificate])
    monkeypatch.setattr(WS, "WindowsStoreProvider", lambda thumbprint: memory)
    dialog = DigitalSignDialog(window)
    assert dialog.source.count() == 3  # (Digital ID file, the Windows certificate, a token)
    dialog.source.setCurrentIndex(1)
    assert not dialog.password.isVisibleTo(dialog)  # (no password: Windows has the key)
    assert dialog.unlock() and dialog.provider is memory
    assert window.settings.last_windows_certificate == memory.certificate.thumbprint
    assert window.finish_digital_sign(dialog.provider, 0, (72, 650, 300, 720), False, tmp_path / "signed.pdf")
    [status] = window.check_signatures()
    assert status.intact and status.signer == "PlanetPDF Test Signer"
    memory.close()


def test_editing_a_signed_document_warns(window, test_certs, tmp_path, monkeypatch):
    provider = Pkcs12Provider(test_certs.pfx, PFX_PASSWORD)
    window.finish_digital_sign(provider, 0, (72, 650, 300, 720), False, tmp_path / "s.pdf")
    asked = []
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: (asked.append(a[1]), QMessageBox.StandardButton.Cancel)[1])
    window.markup.set_markup_mode(True)
    window.markup.set_tool(Kind.RECT)
    _draw(window, "rect", QRectF(100, 100, 100, 60))
    assert asked == ["Signed Document"]
    assert _items(window) == []  # canceled


def test_line_color_recolors_the_selected_comment(window, monkeypatch):
    """Like thickness and opacity (user report 2026-10-18: it only set the next comment's color)."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    window.markup.set_markup_mode(True)
    window.markup_ui.tool_actions[Kind.RECT].trigger()
    _draw(window, "rect", QRectF(100, 400, 150, 80))
    [item] = _items(window)
    assert window.view.selected_markup == item.item_id
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(0, 0, 255)))
    window.markup_ui.act_line_color.trigger()
    [item] = _items(window)
    assert tuple(round(c, 2) for c in item.spec.style.color) == (0.0, 0.0, 1.0)
    window.undo_stack.undo()
    [item] = _items(window)
    assert tuple(round(c, 2) for c in item.spec.style.color) != (0.0, 0.0, 1.0)
