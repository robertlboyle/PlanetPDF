"""Signatures panel: the status of every digital signature in the open document."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from planetpdf.core.signing.digital import SignatureStatus, Verdict

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

VERDICT_STYLE = {
    Verdict.VALID: ("✔ Valid", "#1e8449"),
    Verdict.VALID_UNTRUSTED: ("✔ Intact (signer not trusted)", "#b35900"),
    Verdict.MODIFIED: ("⚠ Changed after signing", "#b35900"),
    Verdict.INVALID: ("✖ Invalid", "#c0392b"),
}


class SignaturesPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.tree = QTreeWidget(self)
        self.tree.setAccessibleName("Signatures")
        self.tree.setHeaderLabels(["Status", "Signed by", "When", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.sign_button = QPushButton("Digitally Sign…", self)
        self.sign_button.clicked.connect(lambda: window.digitally_sign())
        self.check_button = QPushButton("Check Again", self)
        self.check_button.clicked.connect(lambda: window.check_signatures())
        hint = QLabel("Signers show as “not trusted” until their certificate (or its authority) is added in Preferences.", self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.tree, 1)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(self.sign_button)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        self.show_statuses([])

    def show_statuses(self, statuses: list[SignatureStatus], error: str = "") -> None:
        self.tree.clear()
        for status in statuses:
            text, color = VERDICT_STYLE[status.verdict]
            when = status.signing_time.strftime("%Y-%m-%d %H:%M") if status.signing_time else ""
            if status.timestamped:
                when += " (timestamped)"
            elif when:
                when += " (signer's clock)"  # (not proven: no timestamp from a trusted server)
            row = QTreeWidgetItem([text, status.signer, when, status.summary])
            row.setForeground(0, QColor(color))
            row.setToolTip(3, status.summary)
            self.tree.addTopLevelItem(row)
        for col in range(3):
            self.tree.resizeColumnToContents(col)
        if error:
            self.summary.setText(error)
        elif not statuses:
            self.summary.setText("This document has no digital signatures.")
        else:
            worst = max(statuses, key=lambda s: list(Verdict).index(s.verdict)).verdict
            text = f"{len(statuses)} signature(s). Overall: {VERDICT_STYLE[worst][0]}."
            if any(s.verdict in (Verdict.VALID, Verdict.VALID_UNTRUSTED) and not s.revocation_checked
                   for s in statuses):  # (said up front, not only in each one's details: security review 2026-11-25)
                text += " Whether the certificates were revoked wasn't checked (Preferences ▸ Signatures)."
            self.summary.setText(text)
        self.check_button.setEnabled(self._window.session is not None)
        self.sign_button.setEnabled(self._window.session is not None)
