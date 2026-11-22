from __future__ import annotations

import shutil

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from planetpdf.core import compare as C
from planetpdf.ui.dialogs.compare import HIGHLIGHT
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


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


def _older_version(fixture_dir, tmp_path):
    """text.pdf without the phone number on page 2, plus a line of its own."""
    doc = pymupdf.open(fixture_dir / "text.pdf")
    page = doc[1]
    page.add_redact_annot(page.search_for(g.PHONE)[0])
    page.apply_redactions()
    page.insert_text((72, 400), "Only in the old one", fontsize=12)
    path = tmp_path / "older.pdf"
    doc.save(path)
    doc.close()
    return path


def test_compare_lists_and_highlights_changes(window, fixture_dir, tmp_path, monkeypatch):
    dialog = window.compare_documents(_older_version(fixture_dir, tmp_path))
    assert dialog is not None and dialog.isVisible()
    kinds = {(c.kind, c.old_text, c.new_text) for c in dialog.result.changes}
    assert (C.ADDED, "", g.PHONE) in kinds  # the open (newer) file has the phone number
    assert (C.REMOVED, "Only in the old one", "") in kinds
    assert dialog.list.count() == len(dialog.result.changes)
    assert window.view._highlight_data[HIGHLIGHT][0].keys() == {1}  # added text is on page 2 of the open file

    added = next(i for i, c in enumerate(dialog.result.changes) if c.kind == C.ADDED)
    dialog.list.setCurrentRow(added)
    assert window.view._highlight_data[HIGHLIGHT][2] == (1, 0)  # drawn stronger
    report = dialog.save_report(tmp_path / "report.pdf", open_it=False)
    assert report is not None and pymupdf.open(report).page_count == 4  # summary + 3 page pairs

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    assert dialog.save_report(window.session.path, open_it=False) is None and warned  # never over a compared file

    dialog._swap()  # now the open file is the old one: the phone number was "removed"
    assert (C.REMOVED, g.PHONE, "") in {(c.kind, c.old_text, c.new_text) for c in dialog.result.changes}
    dialog.close()
    assert HIGHLIGHT not in window.view._highlight_data


def test_compare_identical(window, fixture_dir, tmp_path):
    same = tmp_path / "same.pdf"
    shutil.copyfile(fixture_dir / "text.pdf", same)
    dialog = window.compare_documents(same)
    assert dialog.result.identical and "No differences" in dialog.heading.text()
    dialog.close()
