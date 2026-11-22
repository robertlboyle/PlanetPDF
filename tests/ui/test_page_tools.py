"""Organize Pages ▸ Replace and ▸ More ▸ Page Labels."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core.page_labels import LabelRule, get_rules, label
from planetpdf.ui.dialogs import page_tools as PT
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pdf(path, prefix, count):
    doc = pymupdf.open()
    for n in range(count):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"{prefix} {n + 1}", fontname="helv")
    doc.save(path)
    return path


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.open_file(_pdf(tmp_path / "doc.pdf", "Original", 5))
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    w.undo_stack.setClean()
    w.close()


def _texts(win):
    return [page.get_text().strip() for page in win.session.doc]


def test_replace_pages_is_one_undo_step(win, tmp_path):
    other = _pdf(tmp_path / "new.pdf", "New", 3)
    assert win.replace_pages([1, 2], other, "1-3")
    assert _texts(win) == ["Original 1", "New 1", "New 2", "New 3", "Original 4", "Original 5"]
    assert win.undo_stack.undoText() == "Replace 2 Pages"
    win.undo_stack.undo()
    assert _texts(win) == [f"Original {n}" for n in range(1, 6)]


def test_replacing_every_page_works(win, tmp_path):
    other = _pdf(tmp_path / "new.pdf", "New", 2)
    assert win.replace_pages(list(range(5)), other)
    assert _texts(win) == ["New 1", "New 2"]


def test_page_labels_show_under_thumbnails_and_undo(win, monkeypatch):
    def front_matter(dialog):
        dialog.table.cellWidget(0, 1).setCurrentIndex(dialog.table.cellWidget(0, 1).findData("r"))
        dialog.add_row(LabelRule(2, "D", "", 1))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PT.PageLabelsDialog, "exec", front_matter)
    win.page_labels_dialog()
    assert [label(win.session, i) for i in range(5)] == ["i", "ii", "1", "2", "3"]
    assert [win.thumbnails.item(i).text() for i in range(5)] == ["i", "ii", "1", "2", "3"]
    assert "labeled" in win.thumbnails.item(0).toolTip()
    win.undo_stack.undo()
    assert get_rules(win.session) == [] and win.thumbnails.item(0).text() == "1"
    win.undo_stack.redo()

    def none(dialog):
        dialog.table.setRowCount(0)  # Remove All Labels
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PT.PageLabelsDialog, "exec", none)
    win.page_labels_dialog()
    assert get_rules(win.session) == [] and win.undo_stack.undoText() == "Remove Page Labels"
