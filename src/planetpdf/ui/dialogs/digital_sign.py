"""Choose the certificate and details for a digital (cryptographic) signature."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError
from planetpdf.core.signing.digital import CERTIFY_LEVELS
from planetpdf.core.signing.providers.pkcs12 import Pkcs12Provider

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


class DigitalSignDialog(QDialog):
    """On accept, ``provider`` holds the unlocked certificate (the password isn't kept)."""

    def __init__(self, window: MainWindow, parent: QWidget | None = None, *, certify: bool = False) -> None:
        super().__init__(parent or window)
        self._window = window
        s = window.settings
        self.provider = None
        self.certifying = certify
        self.setWindowTitle("Certify Document" if certify else "Digitally Sign")
        self.setMinimumWidth(480)
        intro = QLabel(
            "Certifying says you are the author and fixes which changes others may make afterwards "
            "(anything else shows the document as changed). It must be the first signature." if certify else
            "A digital signature proves who signed and that the document hasn't changed since. "
            "After signing, any further edit will show as a change after signing (or break the "
            "signature), so finish all edits first.",
            self,
        )
        intro.setWordWrap(True)
        self.cert = QLineEdit(s.last_certificate, self)
        self.cert.setPlaceholderText("Your certificate file (.pfx or .p12)")
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        cert_row = QHBoxLayout()
        cert_row.addWidget(self.cert, 1)
        cert_row.addWidget(browse)
        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.reason = QLineEdit(s.sign_reason, self)
        self.reason.setPlaceholderText("e.g. I approve this document")
        self.location = QLineEdit(s.sign_location, self)
        has_visual = window.signature_library.default("signature") is not None
        self.show_image = QCheckBox("Show my saved signature in the signature box", self)
        self.show_image.setChecked(has_visual)
        self.show_image.setEnabled(has_visual)
        tsa = s.timestamp_url or "none (set one in Preferences for a trusted time of signing)"
        tsa_label = QLabel(f"Timestamp server: {tsa}", self)
        tsa_label.setWordWrap(True)
        tsa_label.setStyleSheet("color: gray;")
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)

        # Where the certificate comes from: a Digital ID file, or this computer's own store
        # (Windows' certificate store, the Mac Keychain), whose keys stay there (smart cards,
        # company certificates)
        from planetpdf.core.signing.providers import system_store

        self.windows_certs = []  # (the system store's Digital IDs; the name is from before the Mac)
        if system_store.available():
            try:
                self.windows_certs = system_store.signing_certificates()
            except Exception:
                self.windows_certs = []
        self.source = QComboBox(self)
        self.source.addItem("Digital ID file (.pfx / .p12)", "file")
        for cert in self.windows_certs:
            self.source.addItem(f"{system_store.name()}: {cert.label}", cert.thumbprint)
        self.source.addItem("Smart card or USB token (its PKCS#11 driver)", "pkcs11")
        last = s.last_windows_certificate
        if last and self.source.findData(last) >= 0:
            self.source.setCurrentIndex(self.source.findData(last))
        self.source.currentIndexChanged.connect(self._source_changed)
        form = QFormLayout()
        form.addRow("Sign with:", self.source)
        self.cert_label = QLabel("Certificate:", self)
        self.password_label = QLabel("Password:", self)
        form.addRow(self.cert_label, cert_row)
        form.addRow(self.password_label, self.password)
        self._cert_row_widgets = [self.cert, browse, self.cert_label, self.password, self.password_label]
        # a token: the vendor's PKCS#11 driver (remembered) and the token's PIN (never kept)
        self.driver = QLineEdit(s.pkcs11_module, self)
        self.driver.setPlaceholderText("The token's PKCS#11 driver, e.g. C:\\Windows\\System32\\eTPKCS11.dll")
        driver_browse = QPushButton("Browse…", self)
        driver_browse.clicked.connect(self._browse_driver)
        driver_row = QHBoxLayout()
        driver_row.addWidget(self.driver, 1)
        driver_row.addWidget(driver_browse)
        self.pin = QLineEdit(self)
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.driver_label, self.pin_label = QLabel("Token driver:", self), QLabel("PIN:", self)
        form.addRow(self.driver_label, driver_row)
        form.addRow(self.pin_label, self.pin)
        self._token_widgets = [self.driver, driver_browse, self.pin, self.driver_label, self.pin_label]
        form.addRow("Reason:", self.reason)
        form.addRow("Location:", self.location)
        self.level = QComboBox(self)
        self.level.setAccessibleName("Certify to allow")
        for key, text in CERTIFY_LEVELS.items():
            self.level.addItem(text, key)
        self.level.setCurrentIndex(self.level.findData("forms"))
        self.visible = QCheckBox("Show a visible certification box on the page", self)
        self.visible.setChecked(False)
        if certify:
            form.addRow("Allowed afterwards:", self.level)
            form.addRow("", self.visible)
            self.visible.toggled.connect(lambda on: self.ok.setText("Next: Draw Signature Box" if on else "Certify"))
        else:
            self.level.hide()
            self.visible.hide()
        form.addRow("", self.show_image)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = buttons.addButton("Certify" if certify else "Next: Draw Signature Box", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(tsa_label)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._source_changed()

    def _source_changed(self, *_args) -> None:
        source = self.source.currentData()
        for widget in self._cert_row_widgets:
            widget.setVisible(source == "file")
        for widget in self._token_widgets:
            widget.setVisible(source == "pkcs11")

    def _browse_driver(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Token Driver (PKCS#11)", "C:/Windows/System32",
                                              "Drivers (*.dll *.so *.dylib)")
        if path:
            self.driver.setText(path)

    def _browse(self) -> None:
        start = str(Path(self.cert.text()).parent) if self.cert.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Choose Certificate", start, "Certificates (*.pfx *.p12)")
        if path:
            self.cert.setText(path)

    def unlock(self) -> bool:
        s = self._window.settings
        thumbprint = self.source.currentData()
        if thumbprint == "pkcs11":  # a smart card / USB token through its own driver
            from planetpdf.core.signing.providers.pkcs11_token import Pkcs11Provider

            driver = self.driver.text().strip()
            if not driver:
                self.error.setText("Choose the token's driver (ask IT where its PKCS#11 DLL is).")
                return False
            try:
                self.provider = Pkcs11Provider(Path(driver), self.pin.text())
            except PdfError as exc:
                self.error.setText(str(exc))
                return False
            finally:
                self.pin.clear()  # never keep the PIN around
            s.pkcs11_module = driver
            s.sign_reason = self.reason.text().strip()
            s.sign_location = self.location.text().strip()
            return True
        if thumbprint != "file":  # the system's store (it asks for a PIN or password itself if needed)
            from planetpdf.core.signing.providers import system_store

            try:
                self.provider = system_store.provider(thumbprint)
            except PdfError as exc:
                self.error.setText(str(exc))
                return False
            s.last_windows_certificate = thumbprint
            s.sign_reason = self.reason.text().strip()
            s.sign_location = self.location.text().strip()
            return True
        path = self.cert.text().strip()
        if not path:
            self.error.setText("Choose your certificate file.")
            return False
        try:
            self.provider = Pkcs12Provider(Path(path), self.password.text())
        except PdfError as exc:
            self.error.setText(str(exc))
            return False
        finally:
            self.password.clear()  # never keep the password around
        s = self._window.settings
        s.last_certificate = path
        s.sign_reason = self.reason.text().strip()
        s.sign_location = self.location.text().strip()
        return True

    def _accept(self) -> None:
        if self.unlock():
            self.accept()
