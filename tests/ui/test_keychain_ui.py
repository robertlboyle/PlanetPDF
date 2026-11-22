"""Signing and encrypting with the Mac Keychain from the UI (2026-10-14), with the test Digital
ID in a throwaway keychain (never the user's)."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from tests.core.test_mac_keychain import keychain  # noqa: F401 (the fixture)

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the Mac Keychain")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def test_keychain_only(keychain, monkeypatch):  # noqa: F811
    """The Keychain functions, but only ever looking in the throwaway keychain."""
    from planetpdf.core.signing.providers import mac_keychain as MK

    real_certs, real_provider = MK.keychain_certificates, MK.KeychainProvider
    monkeypatch.setattr(MK, "keychain_certificates", lambda **kw: real_certs(**{**kw, "keychains": keychain}))
    monkeypatch.setattr(MK, "KeychainProvider", lambda thumbprint, **kw: real_provider(thumbprint, keychains=keychain))
    return keychain


def test_sign_with_a_keychain_digital_id(window, test_keychain_only, tmp_path):
    from planetpdf.ui.dialogs.digital_sign import DigitalSignDialog

    dialog = DigitalSignDialog(window)
    labels = [dialog.source.itemText(i) for i in range(dialog.source.count())]
    assert labels[0].startswith("Digital ID file") and labels[1].startswith("Keychain: PlanetPDF Test Signer")
    dialog.source.setCurrentIndex(1)
    assert not dialog.password.isVisibleTo(dialog)  # (no password: the Keychain has the key)
    assert dialog.unlock()
    assert window.finish_digital_sign(dialog.provider, 0, (72, 650, 300, 720), False, tmp_path / "signed.pdf")
    [status] = window.check_signatures()
    assert status.intact and status.signer == "PlanetPDF Test Signer"


def test_recipients_come_from_the_keychain(window, test_keychain_only, monkeypatch):
    from planetpdf.ui.dialogs import store_certs
    from planetpdf.ui.dialogs.cert_encryption import EncryptCertificateDialog

    dialog = EncryptCertificateDialog(window)
    assert dialog.add_store.text() == "Add from Keychain…" and not dialog.add_store.isHidden()
    from planetpdf.core.signing.providers import system_store

    [mine] = system_store.all_certificates()
    picker = store_certs.StoreCertificateDialog(system_store.all_certificates(), dialog, system_store.name())
    assert picker.windowTitle() == "Keychain Certificates"
    assert picker.list.item(0).text().endswith("— yours")
    monkeypatch.setattr(store_certs, "choose_store_certificate", lambda parent, purpose="encrypt": mine.der)
    dialog._add_from_windows()
    assert dialog.recipients == [mine.der]


def test_preferences_offer_what_macos_trusts(window):
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    dialog = PreferencesDialog(window)
    assert dialog.trust_windows.text() == "Also trust the certificates macOS trusts"
    assert not dialog.trust_windows.isHidden()
