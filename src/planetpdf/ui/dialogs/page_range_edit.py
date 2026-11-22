"""A pages box ("1,5,8-12") with live validation."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from planetpdf.core.pagerange import PageRangeError, parse_page_ranges


class PageRangeEdit(QWidget):
    validityChanged = Signal(bool)

    def __init__(self, page_count: int, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_count = page_count
        self.line = QLineEdit(text, self)
        self.line.setPlaceholderText("e.g. 1,5,8-12")
        self.line.setClearButtonEnabled(True)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.line)
        layout.addLayout(row)
        layout.addWidget(self.status)
        self.line.textChanged.connect(self._revalidate)
        self._valid = False
        self._revalidate()

    def pages(self) -> list[int]:
        """0-based pages. Raises PageRangeError if the text is invalid."""
        return parse_page_ranges(self.line.text(), self._page_count)

    def is_valid(self) -> bool:
        return self._valid

    def _revalidate(self) -> None:
        try:
            count = len(self.pages())
            self.status.setText(f"{count} page{'s' if count != 1 else ''} of {self._page_count}")
            self.status.setStyleSheet("")
            valid = True
        except PageRangeError as exc:
            self.status.setText(str(exc) if self.line.text().strip() else "Enter pages, e.g. 1,5,8-12")
            self.status.setStyleSheet("color: #c0392b;")
            valid = False
        if valid != self._valid:
            self._valid = valid
            self.validityChanged.emit(valid)
