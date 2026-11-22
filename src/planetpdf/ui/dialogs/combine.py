"""Combine Files / New PDF from Images: pick and order files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.combine import IMAGE_SUFFIXES, CombineOptions

FILTER_ALL = "PDFs and images (*.pdf " + " ".join(f"*{s}" for s in sorted(IMAGE_SUFFIXES)) + ");;All files (*)"
FILTER_IMAGES = "Images (" + " ".join(f"*{s}" for s in sorted(IMAGE_SUFFIXES)) + ")"


class CombineDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, images_only: bool = False, ocr_available: bool = False,
                 start_dir: str = "") -> None:
        super().__init__(parent)
        self.images_only = images_only
        self._start_dir = start_dir
        self.setWindowTitle("New PDF from Images" if images_only else "Combine Files")
        self.resize(560, 440)
        intro = QLabel(
            "Add scans or pictures; each becomes a page, in this order."
            if images_only
            else "Add PDFs and images, then drag them into the order you want. They're combined into one new PDF.",
            self,
        )
        intro.setWordWrap(True)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Files to combine")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        add = QPushButton("Add Files…", self)
        add.clicked.connect(self._add)
        remove = QPushButton("Remove", self)
        remove.clicked.connect(self._remove)
        up = QPushButton("Move Up", self)
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move Down", self)
        down.clicked.connect(lambda: self._move(1))
        side = QVBoxLayout()
        for button in (add, remove, up, down):
            side.addWidget(button)
        side.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side)

        self.paper = QComboBox(self)
        self.paper.addItem("Same size as the image", "image")
        self.paper.addItem("Letter (fit the image)", "letter")
        self.paper.addItem("A4 (fit the image)", "a4")
        self.ocr = QCheckBox("Recognize text (OCR) so the result is searchable", self)
        self.ocr.setEnabled(ocr_available)
        self.ocr.setChecked(images_only and ocr_available)
        form = QFormLayout()
        form.addRow("Image pages:", self.paper)
        form.addRow("", self.ocr)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = self.buttons.addButton("Create PDF…", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(row, 1)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.list.model().rowsInserted.connect(lambda *_a: self._update())
        self.list.model().rowsRemoved.connect(lambda *_a: self._update())
        self._update()

    def add_paths(self, paths: list[Path]) -> None:
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)

    def files(self) -> list[Path]:
        return [Path(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())]

    def options(self) -> CombineOptions:
        return CombineOptions(image_page=self.paper.currentData())

    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Files", self._start_dir, FILTER_IMAGES if self.images_only else FILTER_ALL)
        self.add_paths([Path(p) for p in paths])

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def _update(self) -> None:
        self.ok.setEnabled(self.list.count() > 0)
