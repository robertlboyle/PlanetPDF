"""Header & Footer / Watermark / Bates / Background ▸ Update and Remove in the window."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core import page_marks as PM
from planetpdf.ui.commands import PageContentCommand
from planetpdf.ui.dialogs import document_tools as DT
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    for n in range(3):
        doc.new_page(width=612, height=792).insert_text((72, 300), f"Body {n + 1}", fontname="helv")
    path = tmp_path / "three.pdf"
    doc.save(path)
    w = MainWindow(tmp_path / "profile")
    w.open_file(path)
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    w.undo_stack.setClean()
    w.close()


def _text(win, i):
    return win.session.doc[i].get_text()


def _add_header(win, text="ACME", pages=(0, 1, 2)):
    spec = PM.HeaderFooterSpec(texts={"top_center": text})
    win.push_command(PageContentCommand(win, list(pages), lambda s: PM.add_header_footer(s, list(pages), spec), "Add"))


def test_remove_header_is_one_undo_step(win):
    _add_header(win)
    assert win.remove_page_marks("header") == 3
    assert all("ACME" not in _text(win, i) for i in range(3))
    assert win.undo_stack.undoText() == "Remove Header & Footer"
    win.undo_stack.undo()
    assert all("ACME" in _text(win, i) for i in range(3))


def test_update_shows_the_settings_and_replaces_them(win, monkeypatch):
    _add_header(win, "OLD TEXT", pages=(0, 1))
    seen = {}

    def run(dialog):
        seen["text"] = dialog.fields["top_center"].text()
        seen["title"] = dialog.windowTitle()
        dialog.fields["top_center"].setText("NEW TEXT")
        dialog.range_edit.line.setText("2-3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DT.HeaderFooterDialog, "exec", run)
    win.update_page_marks("header")
    assert seen == {"text": "OLD TEXT", "title": "Update Header & Footer"}
    assert "OLD TEXT" not in _text(win, 0) and "NEW TEXT" not in _text(win, 0)
    assert "NEW TEXT" in _text(win, 1) and "NEW TEXT" in _text(win, 2) and "OLD TEXT" not in _text(win, 1)
    win.undo_stack.undo()
    assert "OLD TEXT" in _text(win, 0) and "NEW TEXT" not in _text(win, 2)


def test_background_add_update_remove(win, monkeypatch):
    def first_page(dialog):
        dialog.range_edit.line.setText("1")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DT.BackgroundDialog, "exec", first_page)
    win.background_dialog()
    assert PM.pages_with(win.session, "background") == [0]

    def bigger(dialog):
        assert dialog.opacity.value() == 100 and dialog.range_edit.line.text() == "1"  # (filled in)
        dialog.opacity.setValue(40)
        dialog.range_edit.line.setText("all")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DT.BackgroundDialog, "exec", bigger)
    win.update_page_marks("background")
    spec, pages = PM.saved_spec(win.session, "background")
    assert spec.opacity == 0.4 and pages == [0, 1, 2]
    win.remove_page_marks("background")
    assert PM.pages_with(win.session, "background") == []


def test_nothing_to_remove_says_why(win):
    assert win.remove_page_marks("watermark") == 0
    assert "No watermark added by PlanetPDF" in win.messages[-1]
    win.update_page_marks("bates")
    assert "can't be told apart" in win.messages[-1]
