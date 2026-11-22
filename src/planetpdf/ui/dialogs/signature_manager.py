"""Manage saved signatures and initials (stored in the roaming profile)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.signing.visual import INK_BLACK, INK_BLUE, ink_color
from planetpdf.ui.viewer.qimage import png_preview

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


class SignatureManagerDialog(QDialog):
    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Signatures and Initials")
        self.resize(520, 420)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Saved signatures and initials")
        self.list.setIconSize(QSize(220, 70))
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.new_signature = QPushButton("New Signature…", self)
        self.new_signature.clicked.connect(lambda: self._create("signature"))
        self.new_initials = QPushButton("New Initials…", self)
        self.new_initials.clicked.connect(lambda: self._create("initials"))
        self.default_button = QPushButton("Use by Default", self)
        self.default_button.clicked.connect(self._make_default)
        self.color_button = QPushButton("Change Color…", self)
        self.color_button.clicked.connect(self._change_color)
        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self._delete)
        where = QLabel(f"Saved in your profile: {window.profile.signatures_dir}", self)
        where.setWordWrap(True)
        where.setStyleSheet("color: gray;")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        side = QVBoxLayout()
        for button in (self.new_signature, self.new_initials, self.default_button, self.color_button, self.delete_button):
            side.addWidget(button)
        side.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side)
        layout = QVBoxLayout(self)
        layout.addLayout(row, 1)
        layout.addWidget(where)
        layout.addWidget(buttons)
        self.populate()

    def populate(self) -> None:
        library = self._window.signature_library
        self.list.clear()
        defaults = {library.default(k).sig_id for k in ("signature", "initials") if library.default(k)}
        for item in library.items:
            label = f"{item.name} ({item.kind})" + ("  ✓ default" if item.sig_id in defaults else "")
            row = QListWidgetItem(QIcon(png_preview(library.png(item), 220, 70)), label)
            row.setData(Qt.ItemDataRole.UserRole, item.sig_id)
            self.list.addItem(row)
        self._update_buttons()

    def _selected(self) -> str | None:
        rows = self.list.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        self.default_button.setEnabled(has)
        self.color_button.setEnabled(has)
        self.delete_button.setEnabled(has)

    def _create(self, kind: str) -> None:
        if self._window.create_signature(kind):
            self.populate()

    def _make_default(self) -> None:
        sig_id = self._selected()
        if sig_id:
            self._window.signature_library.make_default(sig_id)
            self.populate()

    def _change_color(self) -> None:
        sig_id = self._selected()
        if not sig_id:
            return
        library = self._window.signature_library
        current = ink_color(library.png(library.get(sig_id))) or INK_BLUE
        for i, rgb in enumerate((INK_BLUE, INK_BLACK)):  # quick picks
            QColorDialog.setCustomColor(i, QColor.fromRgbF(*rgb))
        color = QColorDialog.getColor(QColor.fromRgbF(*current), self, "Signature Color")
        if color.isValid():
            self.recolor_selected((color.redF(), color.greenF(), color.blueF()))

    def recolor_selected(self, rgb) -> None:
        """New placements use the new color; ones already on pages keep theirs."""
        sig_id = self._selected()
        if sig_id:
            self._window.signature_library.recolor(sig_id, rgb)
            self.populate()
            self._select(sig_id)

    def _select(self, sig_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == sig_id:
                self.list.setCurrentRow(row)

    def _delete(self) -> None:
        sig_id = self._selected()
        if sig_id and self._window.ask("Delete Signature", "Delete this saved signature from your profile?"):
            self._window.signature_library.remove(sig_id)
            self.populate()
