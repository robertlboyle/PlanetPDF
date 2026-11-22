"""Edit PDF ▸ Link ▸ Add/Edit Web or Document Link (Acrobat): what a link area opens."""

from __future__ import annotations

import pymupdf
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import links as L

DELETE = 2  # result code: Delete Link pressed (editing an existing link)


class LinkDialog(QDialog):
    def __init__(self, page_count: int, link: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Link" if link else "Create Link")
        self.setMinimumWidth(420)
        self.web = QRadioButton("Open a web page:", self)
        self.page = QRadioButton("Go to a page in this document:", self)
        group = QButtonGroup(self)
        group.addButton(self.web)
        group.addButton(self.page)
        self.address = QLineEdit(self)
        self.address.setPlaceholderText("e.g. www.example.com or someone@example.com")
        self.address.textEdited.connect(lambda _t: self.web.setChecked(True))
        self.page_number = QSpinBox(self)
        self.page_number.setRange(1, max(1, page_count))
        self.page_number.valueChanged.connect(lambda _v: self.page.setChecked(True))
        self.web.setChecked(True)
        if link is not None:
            if link.get("kind") == pymupdf.LINK_URI:
                self.address.setText(link.get("uri", ""))
            elif link.get("page", -1) >= 0:
                self.page_number.setValue(link["page"] + 1)
                self.page.setChecked(True)
        form = QFormLayout()
        form.addRow(self.web)
        form.addRow("    Address:", self.address)
        form.addRow(self.page)
        form.addRow("    Page:", self.page_number)
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        if link is not None:
            delete = self.buttons.addButton("Delete Link", QDialogButtonBox.ButtonRole.DestructiveRole)
            delete.clicked.connect(lambda: self.done(DELETE))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)

    def target(self) -> dict:
        """``uri`` or ``target_page`` (0-based) for ``links.link_dict``."""
        if self.web.isChecked():
            return {"uri": L.normalize_uri(self.address.text())}
        return {"target_page": self.page_number.value() - 1}

    def _accept(self) -> None:
        if self.web.isChecked() and not self.address.text().strip():
            self.error.setText("Enter the web address (or choose a page).")
            return
        self.accept()


class RemoveLinksDialog(QDialog):
    """Edit PDF ▸ Link ▸ Remove Links: every link on the chosen pages."""

    def __init__(self, page_count: int, selected: list[int], parent: QWidget | None = None) -> None:
        from planetpdf.core.pagerange import format_page_ranges
        from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit

        super().__init__(parent)
        self.setWindowTitle("Remove Links")
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        form = QFormLayout()
        form.addRow("Remove every link on pages:", self.range_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Remove")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.range_edit.validityChanged.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def pages(self) -> list[int]:
        return self.range_edit.pages()
