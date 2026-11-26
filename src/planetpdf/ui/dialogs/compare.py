"""Compare the open document with another PDF: a list of the changes (click one to
see it in the open document, highlighted) and a side-by-side comparison report."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from planetpdf.core import compare as C
from planetpdf.ui.plain import escape, tip

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

HIGHLIGHT = "compare"


class CompareDialog(QDialog):
    """Non-modal: the list stays open while you look at the document."""

    def __init__(self, window: MainWindow, other: pymupdf.Document, other_path: Path, result: C.Comparison,
                 current_is_new: bool) -> None:
        super().__init__(window)
        self.setWindowTitle(f"Compare with {other_path.name}")
        self.setModal(False)
        self.resize(560, 520)
        self._window = window
        self._other = other
        self._other_path = other_path
        self.result = result
        self.current_is_new = current_is_new
        current_name = window.session.path.name
        old_name, new_name = (other_path.name, current_name) if current_is_new else (current_name, other_path.name)
        self.heading = QLabel(f"<b>Old:</b> {escape(old_name)}<br><b>New:</b> {escape(new_name)}<br><br>{escape(result.summary())}")
        self.heading.setTextFormat(Qt.TextFormat.RichText)
        self.list = QListWidget()
        for change in result.changes:
            item = QListWidgetItem(self._label(change))
            item.setForeground(QBrush(QColor.fromRgbF(*[c * 0.75 for c in C.COLORS[change.kind]])))
            item.setToolTip(tip(f"Old: {change.old_text or '—'}\nNew: {change.new_text or '—'}"))
            item.setData(Qt.ItemDataRole.UserRole, change)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(lambda item, _prev: self.show_change(item.data(Qt.ItemDataRole.UserRole) if item else None))
        self.swap_button = QPushButton("Swap Old and New")
        self.swap_button.clicked.connect(self._swap)
        self.report_button = QPushButton("Save Comparison Report…")
        self.report_button.clicked.connect(lambda: self.save_report())
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.swap_button)
        buttons.addStretch(1)
        buttons.addWidget(self.report_button)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.list, 1)
        layout.addWidget(QLabel("Highlighted in the open document: green = added, amber = changed. "
                                "Removed text only exists in the other file."))
        layout.addLayout(buttons)
        self._highlight_all()

    def _mine(self, change: C.Change):
        return change.new_places if self.current_is_new else change.old_places

    def _label(self, change: C.Change) -> str:
        mine, theirs = (change.new_page, change.old_page) if self.current_is_new else (change.old_page, change.new_page)
        where = f"p.{mine + 1}" if mine is not None else f"other file p.{theirs + 1}" if theirs is not None else ""
        return f"{C.LABELS[change.kind]}  ({where})  {change.describe(70)}"

    def _highlight_all(self, current: tuple[int, int] | None = None) -> None:
        rects: dict[int, list[QRectF]] = {}
        for change in self.result.changes:
            for page, r in self._mine(change):
                rects.setdefault(page, []).append(QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1]))
        self._window.view.set_highlights(HIGHLIGHT, rects, QColor(255, 170, 0, 90), current)

    def show_change(self, change: C.Change | None) -> None:
        if change is None:
            return
        places = self._mine(change)
        if not places:
            self._window.statusBar().showMessage("That text is only in the other file.", 4000)
            return
        page, r = places[0]
        rect = QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1])
        # index of this rect within its page's highlight list, so it's drawn stronger
        index = 0
        for other in self.result.changes:
            if other is change:
                break
            index += sum(1 for p, _r in self._mine(other) if p == page)
        self._highlight_all((page, index))
        self._window.view.show_rect(page, rect)

    def _swap(self) -> None:
        self.current_is_new = not self.current_is_new
        old = self._window.session.doc if not self.current_is_new else self._other
        new = self._window.session.doc if self.current_is_new else self._other
        self.result = C.compare(old, new)
        current = self._window.session.path.name
        old_name, new_name = (self._other_path.name, current) if self.current_is_new else (current, self._other_path.name)
        self.heading.setText(f"<b>Old:</b> {escape(old_name)}<br><b>New:</b> {escape(new_name)}<br><br>{escape(self.result.summary())}")
        self.list.clear()
        for change in self.result.changes:
            item = QListWidgetItem(self._label(change))
            item.setForeground(QBrush(QColor.fromRgbF(*[c * 0.75 for c in C.COLORS[change.kind]])))
            item.setData(Qt.ItemDataRole.UserRole, change)
            self.list.addItem(item)
        self._highlight_all()

    def save_report(self, path: Path | None = None, open_it: bool = True) -> Path | None:
        session = self._window.session
        if path is None:
            default = session.path.with_name(f"{session.path.stem} - comparison.pdf")
            from planetpdf.ui.save_path import choose_save_path

            path = choose_save_path(self, "Save Comparison Report", default,
                                    last_directory=self._window.settings.last_directory)
            if not path:
                return None
        if path.resolve() in (session.path.resolve(), self._other_path.resolve()):
            QMessageBox.warning(self, "Comparison Report", "Choose a different file name; that's one of the documents being compared.")
            return None
        # copies of both, so the report is drawn in the background (user 2026-10-23)
        mine = session.work_copy().doc
        other = pymupdf.open(stream=self._other.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
        old, new = (other, mine) if self.current_is_new else (mine, other)
        old_name, new_name = (self._other_path.name, session.path.name) if self.current_is_new else (session.path.name, self._other_path.name)
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        try:
            run_in_background(self, "Comparison Report", f"Writing {path.name}",
                              lambda progress: C.comparison_report(old, new, self.result, path, old_name, new_name,
                                                                   progress))
        except (TaskCanceled, C.ReportCanceled):
            return None
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Comparison Report", f"The report couldn't be saved:\n\n{exc}")
            return None
        finally:
            mine.close()
            other.close()
        if open_it:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        return path

    def closeEvent(self, event) -> None:
        self._window.view.clear_highlights(HIGHLIGHT)
        if not self._other.is_closed:
            self._other.close()
        super().closeEvent(event)
