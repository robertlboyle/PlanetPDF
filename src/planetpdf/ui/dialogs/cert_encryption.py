"""Protection ▸ Encrypt with Certificate (choose who can open the file) and opening a
certificate-encrypted file with your Digital ID."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import cert_encryption as CE
from planetpdf.core.document import PdfError

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

CERT_FILTER = "Certificates (*.cer *.crt *.pem *.der)"
PFX_FILTER = "Digital IDs (*.pfx *.p12)"


class EncryptCertificateDialog(QDialog):
    def __init__(self, window: MainWindow, current: CE.CertificateProtection | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Encrypt with Certificate")
        self.setMinimumWidth(520)
        self.recipients: list[bytes] = list(current.recipients) if current else []
        intro = QLabel("Only the people listed can open the file, each with their own Digital ID "
                       "(private key). No password is shared. Keep yourself on the list, or you "
                       "won't be able to open it either.", self)
        intro.setWordWrap(True)
        self.list = QListWidget(self)
        add_file = QPushButton("Add Certificate File…", self)
        add_file.setToolTip("Someone's public certificate (.cer, .crt, .pem): they can send it to you")
        add_file.clicked.connect(self._add_file)
        add_me = QPushButton("Add Me (Digital ID)…", self)
        add_me.setToolTip("Your own .pfx / .p12 file: its certificate is added (the password unlocks it once)")
        add_me.clicked.connect(self._add_me)
        from planetpdf.core.signing.providers import system_store

        self.add_store = QPushButton(f"Add from {system_store.name()}…", self)
        self.add_store.setToolTip("A certificate from the Windows certificate store")
        self.add_store.clicked.connect(self._add_from_windows)
        self.add_store.setVisible(_store_available())
        remove = QPushButton("Remove", self)
        remove.clicked.connect(self._remove)
        buttons_row = QHBoxLayout()
        for b in (add_file, add_me, self.add_store, remove):
            buttons_row.addWidget(b)
        buttons_row.addStretch(1)
        self.allow = {
            "allow_print": QCheckBox("Printing", self),
            "allow_copy": QCheckBox("Copying text and images", self),
            "allow_edit": QCheckBox("Changing the document", self),
            "allow_comments": QCheckBox("Adding comments", self),
            "allow_forms": QCheckBox("Filling in forms", self),
        }
        grid = QGridLayout()
        for i, (key, box) in enumerate(self.allow.items()):
            box.setChecked(getattr(current, key) if current else True)
            grid.addWidget(box, i // 2, i % 2)
        note = QLabel("AES-256, applied when you save. PlanetPDF keeps the recipients' public "
                      "certificates in the file so it can encrypt it for them again after changes.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Encrypt")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(QLabel("Who can open it:", self))
        layout.addWidget(self.list)
        layout.addLayout(buttons_row)
        layout.addWidget(QLabel("Allow them:", self))
        layout.addLayout(grid)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(box)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        self.list.addItems([CE.certificate_name(der) for der in self.recipients])

    def add_recipient(self, der: bytes) -> None:
        if der not in self.recipients:
            self.recipients.append(der)
        self._refresh()

    def _add_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Recipient's Certificate", "", CERT_FILTER)
        if not path:
            return
        try:
            for der in CE.load_certificate_file(Path(path)):
                self.add_recipient(der)
        except PdfError as exc:
            self.error.setText(str(exc))

    def _add_me(self) -> None:
        start = self._window.settings.last_certificate
        path, _ = QFileDialog.getOpenFileName(self, "Your Digital ID", str(Path(start).parent) if start else "", PFX_FILTER)
        if not path:
            return
        password, ok = QInputDialog.getText(self, "Digital ID Password", f"Password for {Path(path).name}:",
                                            QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            self.add_recipient(CE.certificate_from_pfx(Path(path), password))
        except PdfError as exc:
            self.error.setText(str(exc))

    def _add_from_windows(self) -> None:  # (Windows' store or the Mac Keychain)
        from planetpdf.ui.dialogs.store_certs import choose_store_certificate

        der = choose_store_certificate(self, purpose="encrypt")
        if der:
            self.add_recipient(der)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            del self.recipients[self.list.row(item)]
        self._refresh()

    def protection(self) -> CE.CertificateProtection:
        return CE.CertificateProtection(tuple(self.recipients), **{k: b.isChecked() for k, b in self.allow.items()})

    def _accept(self) -> None:
        if not self.recipients:
            self.error.setText("Add at least one person who can open it (yourself, usually).")
            return
        self.accept()


def _store_available() -> bool:
    from planetpdf.core.signing.providers import system_store

    return system_store.available()


class OpenWithDigitalIdDialog(QDialog):
    """This file was encrypted for certain people: open it with your Digital ID."""

    def __init__(self, window: MainWindow, path: Path, error: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self.setWindowTitle("Certificate-Encrypted File")
        self.setMinimumWidth(480)
        intro = QLabel(f"{path.name} was encrypted for certain people. Open it with your Digital ID "
                       "(the .pfx / .p12 file with your private key).", self)
        intro.setWordWrap(True)
        self.pfx = QLineEdit(window.settings.last_certificate, self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.pfx, 1)
        row.addWidget(browse)
        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Digital ID:", row)
        form.addRow("Password:", self.password)
        self.error = QLabel(error, self)
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(box)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Your Digital ID", "", PFX_FILTER)
        if path:
            self.pfx.setText(path)
