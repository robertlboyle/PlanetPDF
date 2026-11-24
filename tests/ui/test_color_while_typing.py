"""Edit PDF: a color picked while a line is being typed in is kept (user 2026-11-24: "When picking a
color for text (specifically black) it never changes the color when clicking ok" … "I'm referring
to editing text"). The color window ended the typing, the words were written in the old color, and
the new color went onto the line as it was before."""

from __future__ import annotations

import pymupdf
import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from planetpdf.ui.inline_editor import KEEPS_EDITOR
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Red line of text", fontsize=12, fontname="helv", color=(1, 0, 0))
    doc.save(tmp_path / "c.pdf")
    w = MainWindow(tmp_path / "profile")
    w.resize(1200, 900)
    w.show()
    w.open_file(tmp_path / "c.pdf")
    w.tool_panes.open("edit")
    QCoreApplication.processEvents()
    yield w
    w.edit.finish_editing()
    w.undo_stack.setClean()
    w.close()


def _colors(win):
    return {s["text"]: s["color"] for b in win.session.doc[0].get_text("dict")["blocks"]
            for l in b.get("lines", []) for s in l["spans"]}


def _key(win, word):
    return next(k for k in win.view._markup_items if k.startswith("txt:0:") and word in win.edit._line(k).text)


def test_a_color_picked_while_typing_goes_on_the_edited_words(win, monkeypatch):
    key = _key(win, "Red")
    win.view.set_selected_markup(key)
    win.edit.edit_line(key)
    QCoreApplication.processEvents()
    win.edit.editor.setPlainText("Red line of text, edited")
    monkeypatch.setattr(win, "pick_color", lambda initial, title: QColor("black"))
    win.text_format.bars[0]._pick_color()
    QCoreApplication.processEvents()
    assert win.edit.editor is not None and shiboken6.isValid(win.edit.editor)  # (still typing)
    assert _colors(win) == {"Red line of text": 0xFF0000}  # (nothing written yet)
    assert win.text_format.current_format()[0]["color"] == (0.0, 0.0, 0.0)  # the bar shows it
    win.edit.finish_editing()
    QCoreApplication.processEvents()
    assert _colors(win) == {"Red line of text, edited": 0}  # every word black
    assert win.undo_stack.undoText() == "Edit Text"  # one step
    win.undo_stack.undo()
    assert _colors(win) == {"Red line of text": 0xFF0000}


def test_only_the_color_changed_while_typing_still_counts(win, monkeypatch):
    key = _key(win, "Red")
    win.view.set_selected_markup(key)
    win.edit.edit_line(key)
    QCoreApplication.processEvents()
    monkeypatch.setattr(win, "pick_color", lambda initial, title: QColor("#0000ff"))
    win.text_format.bars[0]._pick_color()
    win.edit.finish_editing()
    QCoreApplication.processEvents()
    assert _colors(win) == {"Red line of text": 0x0000FF}


def test_the_color_window_doesnt_end_the_typing(win, monkeypatch):
    key = _key(win, "Red")
    win.edit.edit_line(key)
    QCoreApplication.processEvents()
    editor = win.edit.editor
    window = QWidget()
    window.setProperty(KEEPS_EDITOR, True)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: window))
    editor._finish_unless_formatting()
    assert win.edit.editor is editor and shiboken6.isValid(editor)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: None))
