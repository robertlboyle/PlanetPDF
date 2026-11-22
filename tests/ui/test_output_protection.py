"""New PDFs made from a password-protected document can keep a password (backlog item,
built 2026-10-02): export pages, split, reduce size, image-only PDF, redacted copy."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core.document import Protection
from planetpdf.core.export import ExportFormat, ExportOptions
from planetpdf.core.optimize import ReduceOptions
from planetpdf.ui.dialogs import document_tools as DT
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    from planetpdf.core.document import PdfSession

    w = MainWindow(tmp_path / "profile")
    w._add_document(PdfSession.open(pdf_copy("encrypted.pdf"), g.ENCRYPTED_USER_PW))  # (as opened with its password)
    w.answers = []
    w.ask_three = lambda title, text: (w.answers.append(text), w.answer)[1]
    w.answer = True
    w.show_message = lambda title, text: None
    yield w
    w.undo_stack.setClean()
    w.close()


def _opens_with(path, password):
    with pymupdf.open(path) as doc:
        return doc.needs_pass and bool(doc.authenticate(password))


def test_export_pages_asks_and_uses_the_dialog_filled_in(win, tmp_path, monkeypatch):
    seen = {}

    def confirm(dialog):
        seen["open"] = dialog.open_pw.text()
        seen["copy"] = dialog.allow["allow_copy"].isChecked()
        dialog.perm_pw.setText("perm")
        dialog.perm_pw2.setText("perm")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DT.ProtectDialog, "exec", confirm)
    target = tmp_path / "pages.pdf"
    assert win.export_pages([0], target)
    assert "password protected" in win.answers[-1] and "permissions password isn't known" in win.answers[-1]
    assert seen["open"] == g.ENCRYPTED_USER_PW  # (the password it was opened with)
    assert _opens_with(target, g.ENCRYPTED_USER_PW)


def test_passwords_set_this_session_are_reused_without_asking_for_them(win, tmp_path, monkeypatch):
    win.session.set_protection(Protection("fresh", "owner2"))
    monkeypatch.setattr(DT.ProtectDialog, "exec", lambda d: pytest.fail("no dialog: the passwords are known"))
    target = tmp_path / "pages.pdf"
    assert win.export_pages([0], target)
    assert _opens_with(target, "fresh")


def test_no_means_unprotected_and_cancel_writes_nothing(win, tmp_path):
    win.answer = False
    assert win.export_pages([0], tmp_path / "plain.pdf")
    with pymupdf.open(tmp_path / "plain.pdf") as doc:
        assert not doc.needs_pass
    win.answer = None
    assert not win.export_pages([0], tmp_path / "never.pdf")
    assert not (tmp_path / "never.pdf").exists()


def test_split_reduce_and_image_pdf_are_protected(win, tmp_path):
    win.session.set_protection(Protection("split-pw"))
    paths = win.split_to([("", [0]), ("", [0])], tmp_path / "parts")
    assert len(paths) == 2 and all(_opens_with(p, "split-pw") for p in paths)
    win.reduce_size_to(tmp_path / "small.pdf", ReduceOptions())
    assert _opens_with(tmp_path / "small.pdf", "split-pw")
    files = win.export_to(ExportFormat.IMAGE_PDF, [0], tmp_path / "flat.pdf", ExportOptions(dpi=50))
    assert files and _opens_with(files[0], "split-pw")


def test_unprotected_documents_never_ask(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile2")
    w.open_file(pdf_copy("text.pdf"))
    w.ask_three = lambda *a: pytest.fail("not protected: nothing to ask")
    assert w.export_pages([0], tmp_path / "x.pdf")
    w.close()
