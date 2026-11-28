"""Fill & Sign's editor, now one click away with Add Text on (security review 2026-11-25 of
1.0.6): an item that can't be drawn again isn't deleted by a move; canceling a signed document's
question puts a dragged item back; a click's wobble isn't a move; copied pages don't share ids;
and only items this user's PlanetPDF made take Add Text's clicks."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from planetpdf.core import markup as K
from planetpdf.core import pages as P
from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.markup import Kind, list_markup
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _blank(path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(path)
    doc.close()
    return path


def _window(tmp_path, name, pdf):
    w = MainWindow(tmp_path / name)
    w.resize(1300, 900)
    w.show()
    w.open_file(pdf)
    w.activateWindow()
    QCoreApplication.processEvents()
    return w


@pytest.fixture
def win(qapp, tmp_path):
    w = _window(tmp_path, "profile", _blank(tmp_path / "blank.pdf"))
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _vp(win, x, y):
    return win.view.mapFromScene(win.view._items[0].mapToScene(QPointF(x, y)))


def _texts(win):
    return [i for i in list_markup(win.session) if i.kind is Kind.TEXT]


def _add_text(win, qapp, x, y, text):
    win.markup.set_tool(Kind.TEXT)
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=_vp(win, x, y))
    QCoreApplication.processEvents()
    QTest.keyClicks(qapp.focusWidget(), text)
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Return)
    QCoreApplication.processEvents()
    return next(i for i in _texts(win) if i.spec.text == text)


def test_an_item_that_cant_be_drawn_again_isnt_deleted_by_a_move(win, qapp):
    item = _add_text(win, qapp, 100, 200, "Keep me")
    win.session.doc.xref_set_key(item.xref, "PPDFSource", "null")  # (another program kept only its look)
    win.markup.refresh()
    stripped = next(i for i in list_markup(win.session) if i.item_id == item.item_id)
    with pytest.raises(PdfError):
        K.replace_markup(win.session, stripped.spec, K.transformed(stripped.spec, (300, 300, 400, 320)))
    assert [i.item_id for i in list_markup(win.session)] == [item.item_id]  # (still there)
    win.markup._on_moved(item.item_id, QRectF(300, 300, 100, 20))
    assert [i.item_id for i in list_markup(win.session)] == [item.item_id]
    assert "can't be moved" in win.statusBar().currentMessage()


def test_canceling_the_signed_question_after_a_drag_shows_the_item_again(win, qapp, monkeypatch):
    item = _add_text(win, qapp, 100, 200, "Dragged")
    monkeypatch.setattr(type(win.session), "has_digital_signatures", property(lambda self: True))
    win._signature_change_accepted = False
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    win.markup._lift(item.item_id)  # (the drag hides it)
    win.markup._on_moved(item.item_id, QRectF(300, 300, 100, 20))  # (dropped: the question, canceled)
    [after] = _texts(win)
    page = win.session.doc[0]  # (annotations only weakly hold their page)
    annot = page.load_annot(after.xref)
    assert not annot.flags & pymupdf.PDF_ANNOT_IS_HIDDEN
    assert after.spec.rect == item.spec.rect


def test_a_clicks_wobble_doesnt_move_an_item(win, qapp):
    item = _add_text(win, qapp, 100, 200, "Steady")
    center = win.view._markup_items[item.item_id].rect().center()
    vp = win.view.viewport()
    start = _vp(win, center.x(), center.y())
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(vp, start + type(start)(1, 1))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + type(start)(1, 1))
    QCoreApplication.processEvents()
    [after] = _texts(win)
    assert after.spec.rect == item.spec.rect


def test_pages_inserted_twice_dont_share_ids(win, qapp, tmp_path):
    _add_text(win, qapp, 100, 200, "Twin")
    data = win.session.doc.tobytes()
    P.insert_pdf_pages(win.session, data, 1, salt="a")
    P.insert_pdf_pages(win.session, data, 2, salt="b")
    ids = [i.item_id for i in _texts(win)]
    assert len(ids) == 3 and len(set(ids)) == 3
    for item_id in ids:
        K.find_item(win.session, item_id)  # (each one found, none ambiguous)
    again = pymupdf.open()
    again.insert_pdf(pymupdf.open("pdf", data))
    again.insert_pdf(pymupdf.open("pdf", data))  # (copied by another program: the same id twice)
    again.save(tmp_path / "twins.pdf")
    session = PdfSession.open(tmp_path / "twins.pdf")
    try:
        twin = next(i for i in list_markup(session) if i.kind is Kind.TEXT).item_id
        with pytest.raises(PdfError, match="share the same identity"):
            K.find_item(session, twin)
    finally:
        session.close()


def test_items_another_planetpdf_made_dont_take_add_texts_clicks(qapp, tmp_path):
    first = _window(tmp_path, "profile-a", _blank(tmp_path / "shared.pdf"))
    try:
        item = _add_text(first, qapp, 100, 200, "Theirs")
        assert next(i for i in _texts(first) if i.item_id == item.item_id).made_here
        first.markup.set_tool(None)
        first.session.save()
        first.undo_stack.setClean()
    finally:
        first.close()
    second = _window(tmp_path, "profile-b", tmp_path / "shared.pdf")  # (another user's PlanetPDF)
    try:
        [theirs] = _texts(second)
        assert not theirs.made_here
        second.markup.set_tool(Kind.TEXT)
        center = second.view._markup_items[theirs.item_id].rect().center()
        QTest.mouseClick(second.view.viewport(), Qt.MouseButton.LeftButton, pos=_vp(second, center.x(), center.y()))
        QCoreApplication.processEvents()
        assert second.view.editor is not None  # (a click on it adds text, as on the page)
    finally:
        second.markup.set_tool(None)
        second.undo_stack.setClean()
        second.close()


def test_inserting_or_combining_someone_elses_pages_doesnt_seal_their_items(qapp, tmp_path):
    """Security review 2026-11-27, finding 13: Insert Pages and Combine Files gave another user's
    (or a crafted file's) "PlanetPDF" items this user's seal, so they took Add Text's clicks."""
    from planetpdf.core.combine import CombineOptions, combine_files

    first = _window(tmp_path, "profile-a", _blank(tmp_path / "theirs.pdf"))
    try:
        _add_text(first, qapp, 100, 200, "Theirs")
        first.markup.set_tool(None)
        first.session.save()
        first.undo_stack.setClean()
    finally:
        first.close()
    second = _window(tmp_path, "profile-b", _blank(tmp_path / "mine.pdf"))  # (another user's PlanetPDF)
    try:
        _add_text(second, qapp, 100, 300, "Mine")
        second.markup.set_tool(None)
        P.insert_pdf_pages(second.session, (tmp_path / "theirs.pdf").read_bytes(), 1)
        by_text = {i.text: i for i in _texts(second)}
        assert by_text["Mine"].made_here and not by_text["Theirs"].made_here
        P.insert_pdf_pages(second.session, second.session.doc.tobytes(), 2)  # (its own pages again)
        assert sorted(i.made_here for i in _texts(second) if i.text == "Mine") == [True, True]
        combined = tmp_path / "combined.pdf"
        combine_files([tmp_path / "mine.pdf", tmp_path / "theirs.pdf"], combined, CombineOptions())
        session = PdfSession.open(combined)
        try:
            assert [i.made_here for i in list_markup(session) if i.kind is Kind.TEXT and i.text == "Theirs"] == [False]
        finally:
            session.close()
    finally:
        second.undo_stack.setClean()
        second.close()


def test_deleting_one_of_twin_items_changes_nothing(qapp, tmp_path):
    """Security review 2026-11-27: the delete failed with "share the same identity", yet the document
    was marked changed, with "Undo Delete Rectangle" in the Edit menu."""
    from planetpdf.ui.commands import RemoveMarkupCommand

    source = _window(tmp_path, "profile-twin", _blank(tmp_path / "one.pdf"))
    try:
        _add_text(source, qapp, 100, 200, "Twin")
        data = source.session.doc.tobytes()
        source.undo_stack.setClean()
    finally:
        source.close()
    twins = pymupdf.open()
    twins.insert_pdf(pymupdf.open("pdf", data))
    twins.insert_pdf(pymupdf.open("pdf", data))  # (another program: the same id twice)
    twins.save(tmp_path / "twins.pdf")
    win = _window(tmp_path, "profile-twin", tmp_path / "twins.pdf")
    try:
        twin = next(i for i in list_markup(win.session) if i.kind is Kind.TEXT)
        with pytest.raises(PdfError, match="share the same identity"):
            win.push_command(RemoveMarkupCommand(win, twin.item_id, "Text"))
        assert win.undo_stack.count() == 0 and win.undo_stack.isClean()
    finally:
        win.undo_stack.setClean()
        win.close()
