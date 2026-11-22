from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.pagerange import format_page_ranges
from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit


class RotateDialog(QDialog):
    """Choose pages, direction and angle. ``pages()`` and ``degrees()`` give the result
    (degrees positive = clockwise)."""

    def __init__(self, page_count: int, selected: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rotate Pages")
        self._page_count = page_count
        self._selected = selected

        scope = QGroupBox("Pages", self)
        self.rb_selected = QRadioButton(f"Selected pages ({format_page_ranges(selected)})", scope)
        self.rb_all = QRadioButton(f"All pages (1-{page_count})", scope)
        self.rb_range = QRadioButton("These pages:", scope)
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected), scope)
        self._scope_group = QButtonGroup(self)
        for rb in (self.rb_selected, self.rb_all, self.rb_range):
            self._scope_group.addButton(rb)
        (self.rb_selected if selected else self.rb_all).setChecked(True)
        self.rb_selected.setEnabled(bool(selected))
        scope_layout = QVBoxLayout(scope)
        for widget in (self.rb_selected, self.rb_all, self.rb_range, self.range_edit):
            scope_layout.addWidget(widget)

        self.direction = QComboBox(self)
        self.direction.addItems(["Clockwise", "Counterclockwise"])
        self.angle = QComboBox(self)
        self.angle.addItems(["90°", "180°", "270°"])
        form = QFormLayout()
        form.addRow("Direction:", self.direction)
        form.addRow("Angle:", self.angle)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scope)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self.range_edit.line.textEdited.connect(lambda _t: self.rb_range.setChecked(True))
        self.range_edit.validityChanged.connect(self._update_ok)
        self._scope_group.buttonToggled.connect(lambda *_a: self._update_ok())
        self._update_ok()

    def _update_ok(self) -> None:
        ok = not self.rb_range.isChecked() or self.range_edit.is_valid()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def pages(self) -> list[int]:
        if self.rb_all.isChecked():
            return list(range(self._page_count))
        if self.rb_selected.isChecked():
            return list(self._selected)
        return self.range_edit.pages()

    def degrees(self) -> int:
        amount = (90, 180, 270)[self.angle.currentIndex()]
        return amount if self.direction.currentIndex() == 0 else -amount
