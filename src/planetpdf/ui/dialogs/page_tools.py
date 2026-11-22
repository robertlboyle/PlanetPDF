"""Organize Pages ▸ Replace and ▸ More ▸ Page Labels (Acrobat)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.page_labels import STYLES, LabelRule
from planetpdf.core.pagerange import format_page_ranges
from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit


class PageLabelsDialog(QDialog):
    """One row per range: from which page, the numbering style, a prefix, the first number.
    A range runs until the next one starts."""

    def __init__(self, page_count: int, rules: list[LabelRule], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Page Labels")
        self.setMinimumWidth(560)
        self._page_count = page_count
        self.table = QTableWidget(0, 4, self)
        self.table.setAccessibleName("Page label rules")
        self.table.setHorizontalHeaderLabels(["From page", "Style", "Prefix", "Start at"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for rule in rules or [LabelRule(0)]:
            self.add_row(rule)
        add = QPushButton("Add Range", self)
        add.clicked.connect(lambda: self.add_row(LabelRule(min(self._page_count - 1, self._next_start()))))
        remove = QPushButton("Remove Range", self)
        remove.clicked.connect(self._remove_row)
        clear = QPushButton("Remove All Labels", self)
        clear.setToolTip("Pages are numbered 1, 2, 3 again")
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        row = QHBoxLayout()
        for button in (add, remove, clear):
            row.addWidget(button)
        row.addStretch(1)
        note = QLabel("Labels are how a reader's page box and the Pages panel number the pages "
                      "(e.g. i–iv for the front matter, then 1, 2, 3). The pages themselves don't change.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _next_start(self) -> int:
        starts = [self.table.cellWidget(r, 0).value() for r in range(self.table.rowCount())]
        return max(starts) if starts else 0

    def add_row(self, rule: LabelRule) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        start = QSpinBox(self.table)
        start.setRange(1, max(1, self._page_count))
        start.setValue(rule.start + 1)
        style = QComboBox(self.table)
        for key, text in STYLES.items():
            style.addItem(text, key)
        style.setCurrentIndex(max(0, style.findData(rule.style)))
        prefix = QLineEdit(rule.prefix, self.table)
        first = QSpinBox(self.table)
        first.setRange(1, 1_000_000)
        first.setValue(rule.first)
        for col, widget in enumerate((start, style, prefix, first)):
            self.table.setCellWidget(r, col, widget)

    def _remove_row(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()} or {self.table.rowCount() - 1}, reverse=True)
        for r in rows:
            if r >= 0:
                self.table.removeRow(r)

    def rules(self) -> list[LabelRule]:
        out = []
        for r in range(self.table.rowCount()):
            start, style, prefix, first = (self.table.cellWidget(r, c) for c in range(4))
            out.append(LabelRule(start.value() - 1, style.currentData(), prefix.text(), first.value()))
        return out


class ReplacePagesDialog(QDialog):
    """Replace these pages of the document with pages of another PDF."""

    def __init__(self, page_count: int, selected: list[int], start_dir: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Replace Pages")
        self.setMinimumWidth(480)
        self._start_dir = start_dir
        self.path: Path | None = None
        self.original = PageRangeEdit(page_count, format_page_ranges(selected) or "1", self)
        self.file_label = QLabel("(choose a PDF)", self)
        choose = QPushButton("Choose File…", self)
        choose.clicked.connect(self._choose)
        self.replacement = QLineEdit("all", self)
        self.replacement.setToolTip("Pages of the chosen file, e.g. 1-3 or all")
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(choose)
        form = QFormLayout()
        form.addRow("Replace pages:", self.original)
        form.addRow("With pages from:", file_row)
        form.addRow("Its pages:", self.replacement)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Replace")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Replacement Pages", self._start_dir, "PDF files (*.pdf)")
        if path:
            self.use_file(Path(path))

    def use_file(self, path: Path) -> None:
        self.path = path
        self.file_label.setText(path.name)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
