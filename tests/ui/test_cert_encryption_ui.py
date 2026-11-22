"""Encrypt with Certificate in the window: choose recipients, save, open again with a
Digital ID, keep it encrypted for new files."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core import cert_encryption as CE
from planetpdf.ui.dialogs import cert_encryption as CD
from planetpdf.ui.main_window import MainWindow
from tests.fixtures.certs import PFX_PASSWORD, make_test_certs


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    return make_test_certs(tmp_path_factory.mktemp("certs"))


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    w.undo_stack.setClean()
    w.close()


def _pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "Board minutes", fontname="helv")
    doc.save(path)
    return path


def test_encrypt_save_and_open_with_digital_id(win, certs, tmp_path, monkeypatch):
    path = _pdf(tmp_path / "minutes.pdf")
    assert win.open_file(path)

    def choose(dialog):
        dialog.add_recipient(CE.certificate_from_pfx(certs.pfx, PFX_PASSWORD))
        dialog.allow["allow_copy"].setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CD.EncryptCertificateDialog, "exec", choose)
    win.encrypt_with_certificate()
    assert win.undo_stack.undoText() == "Encrypt with Certificate"
    assert win.save()
    assert CE.is_certificate_encrypted(path)
    win.close_document()

    def unlock(dialog):
        dialog.pfx.setText(str(certs.pfx))
        dialog.password.setText(PFX_PASSWORD)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CD.OpenWithDigitalIdDialog, "exec", unlock)
    assert win.open_file(path)
    assert "Board minutes" in win.session.doc[0].get_text()
    assert not win.session.certificate_protection.allow_copy and win.messages == []  # (ours: recipients known)


def test_wrong_digital_id_asks_again(win, certs, tmp_path, monkeypatch):
    path = _pdf(tmp_path / "minutes.pdf")
    path.write_bytes(CE.encrypt_bytes(path.read_bytes(),
                                      CE.CertificateProtection((CE.certificate_from_pfx(certs.pfx, PFX_PASSWORD),))))
    tries = []

    def attempt(dialog):
        tries.append(dialog.error.text())
        dialog.pfx.setText(str(certs.pfx))
        dialog.password.setText("wrong" if len(tries) == 1 else PFX_PASSWORD)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CD.OpenWithDigitalIdDialog, "exec", attempt)
    assert win.open_file(path)
    assert len(tries) == 2 and "password" in tries[1].lower()

@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows certificate store")
def test_opens_with_a_certificate_in_windows_without_asking(win, certs, tmp_path, monkeypatch):
    """Backlog item (2026-10-03): a file encrypted for a certificate in Windows opens
    straight away; the Digital ID dialog isn't needed."""
    from planetpdf.core.signing.providers.windows_store import WindowsStoreProvider

    path = _pdf(tmp_path / "minutes.pdf")
    path.write_bytes(CE.encrypt_bytes(path.read_bytes(),
                                      CE.CertificateProtection((CE.certificate_from_pfx(certs.pfx, PFX_PASSWORD),))))
    real = CE.decrypt_with_windows
    # (the test Digital ID in a temporary in-memory store, not the user's real one)
    monkeypatch.setattr(CE, "decrypt_with_windows", lambda data, providers=None: real(
        data, [WindowsStoreProvider.from_pfx_in_memory(certs.pfx.read_bytes(), PFX_PASSWORD)]))
    monkeypatch.setattr(CD.OpenWithDigitalIdDialog, "exec", lambda dialog: pytest.fail("asked for a Digital ID"))
    assert win.open_file(path)
    assert "Board minutes" in win.session.doc[0].get_text()
