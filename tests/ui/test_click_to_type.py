"""A click on the page with no tool on adds text, also right after a signature is placed;
text added is remembered in the profile and suggested while typing (user 2026-11-16)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind, list_markup
from planetpdf.core.storage.text_history import TextHistory
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(path)
    doc.close()
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(path)
    w.activateWindow()
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _click(win, x, y):
    item = win.view._items[0]
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=win.view.mapFromScene(item.mapToScene(QPointF(x, y))))
    QCoreApplication.processEvents()


def _texts(win):
    return [i.spec.text for i in list_markup(win.session) if i.kind is Kind.TEXT]


def _type_and_finish(qapp, text):
    QTest.keyClicks(qapp.focusWidget(), text)
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Return)
    QCoreApplication.processEvents()


def test_a_click_with_no_tool_adds_text(win, qapp):
    assert win.markup.tool is None
    _click(win, 150, 200)
    assert win.view.editor is not None
    _type_and_finish(qapp, "Typed here")
    assert _texts(win) == ["Typed here"]


def test_a_click_after_placing_a_signature_adds_text(win, qapp):
    sig = pymupdf.open()
    sig.new_page(width=144, height=40)
    source = sig.tobytes()
    win.markup.place_source(Kind.SIGNATURE, source, "Signature")
    _click(win, 200, 300)  # drops the signature: the tool goes back to the arrow
    assert win.markup.tool is None
    assert [i.kind for i in list_markup(win.session)] == [Kind.SIGNATURE]
    _click(win, 200, 500)  # the next click starts typing
    assert win.view.editor is not None
    _type_and_finish(qapp, "Date: today")
    assert _texts(win) == ["Date: today"]


def test_switched_off_in_preferences(win):
    win.settings.click_adds_text = False
    _click(win, 150, 200)
    assert win.view.editor is None


def test_remembered_text_is_suggested_and_taken_with_tab(win, qapp):
    _click(win, 150, 200)
    _type_and_finish(qapp, "John Smith")
    assert TextHistory.load(win.profile.text_history_path).entries == ["John Smith"]  # (in the profile)

    _click(win, 150, 300)
    editor = win.markup.editor
    QTest.keyClicks(editor, "jo")
    assert editor.toPlainText() == "john Smith" and editor.suggestion == "John Smith"
    QTest.keyClick(editor, Qt.Key.Key_Tab)  # taken; typing goes on after it
    QTest.keyClicks(editor, " Jr.")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QCoreApplication.processEvents()
    assert sorted(_texts(win)) == ["John Smith", "john Smith Jr."]


def test_typing_on_backspace_and_shift_delete(win, qapp):
    win.text_history.remember("Planet Networks")
    win.text_history.remember("Planet PDF")
    _click(win, 150, 200)
    editor = win.markup.editor
    QTest.keyClicks(editor, "Pl")
    assert editor.toPlainText() == "Planet PDF"
    QTest.keyClick(editor, Qt.Key.Key_Down)  # the other one
    assert editor.toPlainText() == "Planet Networks"
    QTest.keyClicks(editor, "u")  # typing on replaces the suggestion
    assert editor.toPlainText() == "Plu" and editor.suggestion == ""
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == "Pl"
    QTest.keyClicks(editor, "a")
    assert editor.toPlainText() == "Planet PDF"
    QTest.keyClick(editor, Qt.Key.Key_Backspace)  # takes the suggestion away, not the "a"
    assert editor.toPlainText() == "Pla"
    QTest.keyClicks(editor, "n")
    QTest.keyClick(editor, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)  # forgets it
    assert editor.toPlainText() == "Plan" and win.text_history.entries == ["Planet Networks"]


def test_nothing_remembered_when_switched_off(win, qapp):
    win.settings.remember_text = False
    win.text_history.remember("Something")
    _click(win, 150, 200)
    QTest.keyClicks(win.markup.editor, "So")
    assert win.markup.editor.toPlainText() == "So"
    QTest.keyClick(win.markup.editor, Qt.Key.Key_Return)
    QCoreApplication.processEvents()
    assert win.text_history.entries == ["Something"]


def test_preferences_option_for_private_numbers(win):
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    win.text_history.remember("Card 4111 1111 1111 1111", allow_private=True)
    dialog = PreferencesDialog(win)
    assert not dialog.remember_private.isChecked()  # (off by default)
    dialog.remember_text.setChecked(False)
    assert not dialog.remember_private.isEnabled()
    dialog.remember_text.setChecked(True)
    dialog._save()  # (saved off: any such text remembered before is forgotten)
    assert not win.settings.remember_private_numbers
    assert not any("4111" in e for e in win.text_history.entries)
    dialog.remember_private.setChecked(True)
    dialog._save()
    assert win.settings.remember_private_numbers
    win.markup._remember_text("SSN 123-45-6789")
    assert win.text_history.entries[0] == "SSN 123-45-6789"


def test_a_click_on_an_item_the_arrow_cant_select_doesnt_add_text(win, qapp):
    """Review 2026-11-27 (seen while testing): a click on an item that keeps only its look (or another
    program's comment) started a new text box on top of it."""
    page = win.session.doc[0]
    page.add_rect_annot(pymupdf.Rect(100, 150, 300, 250))  # (another program's: not ours)
    win.session.mark_modified()
    win.markup.refresh()
    QCoreApplication.processEvents()
    _click(win, 200, 200)
    assert win.view.editor is None and _texts(win) == []
    _click(win, 200, 500)  # (empty paper: as before)
    assert win.view.editor is not None
