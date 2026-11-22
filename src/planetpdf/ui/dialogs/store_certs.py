"""Pick a certificate from this computer's own store (Windows' certificate store or the Mac
Keychain): e.g. a recipient for Encrypt with Certificate (your own and other people's)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


def _where(cert) -> str:
    if cert.store == "keychain":
        return "yours" if cert.has_private_key else "other people"
    return "Personal" if cert.store == "MY" else "Other People"


class StoreCertificateDialog(QDialog):
    def __init__(self, certificates, parent: QWidget | None = None, store: str = "Windows") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{store} Certificates")
        self.setMinimumWidth(520)
        self.chosen: bytes | None = None
        self.list = QListWidget(self)
        for cert in certificates:
            item = QListWidgetItem(f"{cert.label} — {_where(cert)}" + (" (expired)" if cert.expired else ""), self.list)
            item.setData(Qt.ItemDataRole.UserRole, cert.der)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        if not certificates:
            layout.addWidget(QLabel(f"There are no certificates in your {store}.", self))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


def choose_store_certificate(parent: QWidget, purpose: str = "encrypt") -> bytes | None:
    from planetpdf.core.signing.providers import system_store

    dialog = StoreCertificateDialog(system_store.all_certificates(), parent, system_store.name())
    return dialog.chosen if dialog.exec() == QDialog.DialogCode.Accepted else None
