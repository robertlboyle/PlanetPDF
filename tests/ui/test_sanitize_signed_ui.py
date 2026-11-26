"""Sanitize Document on a signed PDF in the window (security review 2026-11-25): the dialog says
the signatures go, Save Sanitized Copy (the default) leaves the signed file alone, in place the
signatures are removed so Save writes a whole new file, and Undo gives a document that still saves
(it was left unsaveable: "incremental needs original file")."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from planetpdf.core.redaction.sanitize import SanitizeOptions, signed_fields
from planetpdf.core.signing.digital import SignOptions, sign_pdf, validate_signatures
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore, Pkcs12Provider
from planetpdf.ui.dialogs.sanitize import SanitizeDialog
from planetpdf.ui.main_window import MainWindow
from tests.core.test_sanitize_signed import SECRET, _all_bytes, _with_hidden_information
from tests.fixtures.certs import PFX_PASSWORD


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def signed(tmp_path, test_certs):
    source = _with_hidden_information(tmp_path / "source.pdf")
    target = tmp_path / "signed.pdf"
    sign_pdf(source, target, Pkcs12Provider(test_certs.pfx, PFX_PASSWORD), 0, (72, 600, 300, 680),
             SignOptions(reason="Approval"))
    return target


@pytest.fixture
def win(qapp, tmp_path, signed):
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(signed)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_the_dialog_warns_and_defaults_to_a_copy(qapp):
    dialog = SanitizeDialog(0, 0, True, sanitize_only=True)
    assert dialog.ok.text() == "Save Sanitized Copy…" and dialog.ok.isDefault()
    assert any("also removes the signatures" in label.text() for label in dialog.findChildren(QLabel))
    dialog.ok.click()
    assert dialog.choice == "copy"


def test_in_place_removes_the_signatures_and_undo_still_saves(win, signed, tmp_path):
    original = signed.read_bytes()
    assert win.session.has_digital_signatures
    assert win.sanitize_document(SanitizeOptions())  # (in place: "Remove Signatures and Sanitize")
    assert not win.session.has_digital_signatures and signed_fields(win.session.doc) == []
    win.undo_stack.undo()
    assert win.session.has_digital_signatures  # (the signed document is back)
    win.session.doc[0].insert_text((72, 100), "After undo")
    win.session.mark_modified()
    win.session.save()  # (failed before: "incremental needs original file")
    assert signed.read_bytes().startswith(original)
    [status] = validate_signatures(signed, FileTrustStore(tmp_path / "no-trust"))
    assert status.intact


def test_in_place_then_save_leaves_nothing_of_the_signed_version(win, signed):
    assert win.sanitize_document(SanitizeOptions())
    win.session.save()
    assert SECRET not in _all_bytes(signed) and signed.read_bytes().count(b"%%EOF") == 1


def test_save_sanitized_copy_leaves_the_signed_file_alone(win, signed, tmp_path, monkeypatch):
    original = signed.read_bytes()
    out = tmp_path / "copy.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    assert win.save_sanitized_copy(SanitizeOptions())
    assert signed.read_bytes() == original and win.session.has_digital_signatures
    assert SECRET not in _all_bytes(out)
    with pymupdf.open(out) as doc:
        assert signed_fields(doc) == []


def test_canceling_the_signed_document_question_takes_the_change_back(win, signed, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from planetpdf.ui.commands import DocumentSnapshotCommand

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    command = DocumentSnapshotCommand.applied(win, lambda s: s.doc.set_metadata({"title": "Changed"}), "Change")
    assert win.session.doc.metadata["title"] == "Changed"
    win.push_command(command)  # (asks "This document is digitally signed … Continue?": Cancel)
    assert win.undo_stack.count() == 0
    assert win.session.doc.metadata["title"] != "Changed"  # (was left changed, with nothing to undo)
