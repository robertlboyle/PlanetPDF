"""Protect ▸ Advanced Options (user screenshot 2026-10-03): Document Security ▸ (the
security method), Security Properties, Create Security Envelope, Manage Security
Policies (``core.security``)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)

from planetpdf.core import security as SEC

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


class SecurityUi:
    def __init__(self, window: MainWindow) -> None:
        self.w = window
        group = QActionGroup(window)
        self.method_actions: list[QAction] = []
        for key, text, tip in (("none", "No Security", "Remove the password or certificate protection"),
                               ("password", "Password Security…", "Require a password to open, or to change, it"),
                               ("certificate", "Certificate Security…", "Only the people you choose can open it")):
            action = QAction(text, window, checkable=True)
            action.setToolTip(tip)
            action.setData(key)
            action.triggered.connect(lambda _c=False, k=key: self.set_method(k))
            group.addAction(action)
            self.method_actions.append(action)

    # --- Document Security ▸ ---------------------------------------------------------------------

    def current_method(self) -> str:
        rows = dict(SEC.describe(self.w.session)) if self.w.session is not None else {}
        method = rows.get("Security method", "")
        return "certificate" if method.startswith("Certificate") else "password" if method.startswith("Password") else "none"

    def show_method(self) -> None:
        current = self.current_method()
        for action in self.method_actions:
            action.setEnabled(self.w.session is not None)
            action.setChecked(action.data() == current)

    def set_method(self, key: str) -> None:
        if self.w.session is None:
            return
        if key == "none":
            self.w.act_unprotect.trigger()
        elif key == "password":
            self.w.act_protect.trigger()
        else:
            self.w.act_encrypt_certificate.trigger()

    # --- Security Properties ----------------------------------------------------------------------

    def properties_dialog(self) -> None:
        if self.w.session is None:
            return
        dialog = QDialog(self.w)
        dialog.setWindowTitle("Security Properties")
        dialog.setMinimumWidth(420)
        form = QFormLayout()
        for what, how in SEC.describe(self.w.session):
            value = QLabel(how, dialog)
            value.setWordWrap(True)
            form.addRow(f"{what}:", value)
        change = QPushButton("Change Settings…", dialog)
        change.clicked.connect(lambda: (dialog.accept(), self._change_settings()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.addButton(change, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(buttons)
        dialog.exec()

    def _change_settings(self) -> None:
        method = self.current_method()
        self.set_method("password" if method == "none" else method)

    # --- Create Security Envelope -------------------------------------------------------------------

    def envelope_dialog(self) -> None:
        w = self.w
        dialog = EnvelopeDialog(w)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        protection = self._ask_protection(dialog.with_certificate.isChecked())
        if protection is None:
            return
        target, _ = QFileDialog.getSaveFileName(w, "Save Security Envelope",
                                                str(Path(w.settings.last_directory or ".") / "Security Envelope.pdf"),
                                                "PDF files (*.pdf)")
        if not target:
            return
        try:
            data = SEC.make_envelope(dialog.files(), protection, note=dialog.note.toPlainText().strip())
            Path(target).write_bytes(data)
        except Exception as exc:  # (a file that can't be read, a bad certificate…)
            QMessageBox.warning(w, "Create Security Envelope", str(exc))
            return
        w.statusBar().showMessage(f"Saved {Path(target).name}: {len(dialog.files())} file(s) in a protected envelope.",
                                  8000)

    def _ask_protection(self, certificate: bool):
        if certificate:
            from planetpdf.ui.dialogs.cert_encryption import EncryptCertificateDialog

            dialog = EncryptCertificateDialog(self.w, None)
        else:
            from planetpdf.ui.dialogs.document_tools import ProtectDialog

            dialog = ProtectDialog(self.w)
        return dialog.protection() if dialog.exec() == QDialog.DialogCode.Accepted else None

    # --- Manage Security Policies ---------------------------------------------------------------------

    def policies_dialog(self) -> None:
        PoliciesDialog(self).exec()

    def apply_policy(self, policy: SEC.SecurityPolicy) -> bool:
        """Put a policy on the open document (applied when it's saved, like Protect)."""
        from planetpdf.core.document import Protection
        from planetpdf.ui.commands import SetProtectionCommand
        from planetpdf.ui.dialogs.document_tools import ProtectDialog

        w = self.w
        if w.session is None:
            return False
        if policy.kind == "certificate":
            w.push_command(SetProtectionCommand(w, policy.certificate_protection(), f"Apply Policy {policy.name}"))
            return True
        dialog = ProtectDialog(w)  # (the passwords are asked for: a policy never keeps them)
        dialog.setWindowTitle(f"Apply Policy: {policy.name}")
        dialog.prefill(Protection("", "", **policy.permissions))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        w.push_command(SetProtectionCommand(w, dialog.protection(), f"Apply Policy {policy.name}"))
        return True


class EnvelopeDialog(QDialog):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        self.setWindowTitle("Create Security Envelope")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Files to send in the envelope (they travel inside one protected PDF):", self))
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        add = QPushButton("Add Files…", self)
        add.clicked.connect(self._add)
        current = QPushButton("Add This Document", self)
        current.setEnabled(window.session is not None and window.session.path.exists())
        current.clicked.connect(lambda: self.add_files([window.session.path]))
        remove = QPushButton("Remove", self)
        remove.clicked.connect(lambda: [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()])
        for button in (add, current, remove):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel("Note on the cover page (optional):", self))
        self.note = QTextEdit(self)
        self.note.setFixedHeight(60)
        layout.addWidget(self.note)
        self.with_password = QRadioButton("Protect with a password", self)
        self.with_certificate = QRadioButton("Encrypt for certain people (their certificates)", self)
        self.with_password.setChecked(True)
        layout.addWidget(self.with_password)
        layout.addWidget(self.with_certificate)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Next…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Add Files", self.w.settings.last_directory or "")
        self.add_files([Path(n) for n in names])

    def add_files(self, paths: list[Path]) -> None:
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.list.addItem(item)

    def files(self) -> list[Path]:
        return [Path(self.list.item(n).data(Qt.ItemDataRole.UserRole)) for n in range(self.list.count())]

    def _accept(self) -> None:
        if not self.files():
            QMessageBox.information(self, self.windowTitle(), "Add at least one file.")
            return
        self.accept()


class PoliciesDialog(QDialog):
    """Saved protection settings, to put on documents again (Acrobat's security policies)."""

    def __init__(self, ui: SecurityUi) -> None:
        super().__init__(ui.w)
        self.ui = ui
        self.root = ui.w.profile.root
        self.policies = SEC.load_policies(self.root)
        self.setWindowTitle("Manage Security Policies")
        self.setMinimumSize(520, 320)
        layout = QVBoxLayout(self)
        note = QLabel("A policy keeps protection settings to use again. Passwords are never kept: you type them "
                      "each time a password policy is applied.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.buttons = {}
        for key, text, slot in (("password", "New Password Policy…", lambda: self._new("password")),
                                ("certificate", "New Certificate Policy…", lambda: self._new("certificate")),
                                ("from_document", "From This Document…", self._from_document),
                                ("apply", "Apply to Document", self._apply),
                                ("delete", "Delete", self._delete)):
            button = QPushButton(text, self)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[key] = button
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.list.itemSelectionChanged.connect(self._update)
        self._refill()

    def _refill(self) -> None:
        self.list.clear()
        for policy in self.policies:
            item = QListWidgetItem(f"{policy.name}  —  {'Password' if policy.kind == 'password' else 'Certificate'}: "
                                   f"{policy.summary()}")
            item.setToolTip(policy.description or policy.summary())
            self.list.addItem(item)
        self._update()

    def _update(self) -> None:
        chosen = self.list.currentRow() >= 0 and bool(self.list.selectedItems())
        has_doc = self.ui.w.session is not None
        self.buttons["apply"].setEnabled(chosen and has_doc)
        self.buttons["delete"].setEnabled(chosen)
        self.buttons["from_document"].setEnabled(has_doc and self.ui.current_method() != "none")

    def _name(self) -> str | None:
        name, ok = QInputDialog.getText(self, "Policy Name", "Name for the policy:", QLineEdit.EchoMode.Normal)
        name = name.strip()
        if not ok or not name:
            return None
        if any(p.name.casefold() == name.casefold() for p in self.policies):
            QMessageBox.information(self, self.windowTitle(), f"There's already a policy called “{name}”.")
            return None
        return name

    def _new(self, kind: str) -> None:
        protection = self.ui._ask_protection(kind == "certificate")
        if protection is None:
            return
        name = self._name()
        if name:
            self.add(SEC.policy_from_protection(name, protection))

    def _from_document(self) -> None:
        protection, _complete = self.ui.w.session.known_protection()
        if protection is None:
            return
        name = self._name()
        if name:
            self.add(SEC.policy_from_protection(name, protection))

    def add(self, policy: SEC.SecurityPolicy) -> None:
        self.policies.append(policy)
        SEC.save_policies(self.root, self.policies)
        self._refill()

    def _apply(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.policies) and self.ui.apply_policy(self.policies[row]):
            self.accept()

    def _delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.policies):
            del self.policies[row]
            SEC.save_policies(self.root, self.policies)
            self._refill()
