"""PDF Standards ▸ Check Compliance (user 2026-10-04; not "Preflight", Adobe's name):
check the open document against PDF/A, PDF/X or PDF/E (core/compliance.py), see what's a
problem and where, then Fix and Save a copy (checked again here) or save the report."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from planetpdf.core import compliance as P
from planetpdf.ui import fa, plain
from planetpdf.ui.tasks import TaskCanceled, page_progress_text, run_in_background

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

ICONS = {P.PASS: ("circle-check", "#2e7d32"), P.NA: ("circle-minus", "#8a8a8a"),
         P.ADDED: ("circle-plus", "#1a5fb4"), P.WARN: ("triangle-exclamation", "#b26a00"),
         P.FAIL: ("circle-xmark", "#c62828")}
PAGE_ROLE = Qt.ItemDataRole.UserRole


class ComplianceDialog(QDialog):
    def __init__(self, window: MainWindow, profile: str = "pdfa") -> None:
        super().__init__(window)
        self.w = window
        self.result: P.Result | None = None
        self.setWindowTitle("Check Compliance")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Check against:", self))
        self.profile = QComboBox(self)
        for key, name in P.PROFILES.items():
            self.profile.addItem(name, key)
        self.profile.setCurrentIndex(max(self.profile.findData(profile), 0))
        row.addWidget(self.profile, 1)
        self.analyze = QPushButton(fa.icon("magnifying-glass"), "Analyze", self)
        self.analyze.clicked.connect(self.run)
        row.addWidget(self.analyze)
        layout.addLayout(row)
        self.claimed = plain.plain(QLabel(self))  # (what the file says it meets: the file's own words)
        self.summary = QLabel(self)
        self.summary.setStyleSheet("font-weight: 600;")
        self.summary.setWordWrap(True)
        layout.addWidget(self.claimed)
        layout.addWidget(self.summary)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Check", "Result"])
        self.tree.setColumnWidth(0, 440)
        self.tree.itemDoubleClicked.connect(self._go)
        self.tree.setToolTip("Double-click a page to go to it")
        layout.addWidget(self.tree, 1)
        self.fixed_label = QLabel(self)  # (after Fix and Save: the copy, what changed, what's left)
        self.fixed_label.setWordWrap(True)
        self.fixed_label.setVisible(False)
        layout.addWidget(self.fixed_label)
        note = QLabel("Only real problems fail. <b>N/A</b>: the file doesn't use it. <b>Added when saved</b>: "
                      "what every file of the standard carries, added when you save in that format. "
                      "<b>To fix problems</b>, click <b>Fix and Save</b>: PlanetPDF makes a corrected copy (the open "
                      "document doesn't change) and checks it again. This covers the rules that most often fail; "
                      "it isn't a certified validator.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)
        buttons = QHBoxLayout()
        self.convert = QPushButton(fa.icon("wand-magic-sparkles"), "", self)
        self.convert.clicked.connect(self._convert)
        self.open_fixed = QPushButton(fa.icon("folder-open"), "Open Fixed Copy", self)
        self.open_fixed.clicked.connect(self._open_fixed)
        self.open_fixed.setVisible(False)
        self.save_report = QPushButton("Save Report…", self)
        self.save_report.clicked.connect(self._save_report)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(self.convert)
        buttons.addWidget(self.open_fixed)
        buttons.addWidget(self.save_report)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.fixed_path: Path | None = None
        self.profile.currentIndexChanged.connect(lambda _i: self.run())
        self._show(None)

    @property
    def key(self) -> str:
        return self.profile.currentData()

    def run(self) -> P.Result | None:
        session = self.w.session
        if session is None:
            return None
        data = session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)  # (a plain copy to read)
        encrypted = bool(session.doc.metadata.get("encryption"))
        profile = self.key

        def work(progress):
            with pymupdf.open("pdf", data) as doc:
                result = P.check(doc, profile, progress)
            if encrypted:  # (the copy is plain: the open file isn't)
                for check in result.checks:
                    if check.title == "Not password protected" and check.status == P.PASS:
                        check.fail("The file is password protected.")
            return result

        try:
            result = run_in_background(self, "Check Compliance", f"Checking against {P.PROFILES[profile]}", work,
                                       describe=page_progress_text(f"Checking against {P.PROFILES[profile]}",
                                                                   verb="Checking page"))
        except TaskCanceled:
            return None
        self.fixed_path = None
        self.fixed_label.setVisible(False)
        self.open_fixed.setVisible(False)
        self._show(result)
        return result

    @property
    def standard_name(self) -> str:
        return P.SHORT[self.key]

    def _show(self, result: P.Result | None, *, of_copy: bool = False) -> None:
        self.result = result
        self.tree.clear()
        name = self.standard_name
        failing = result is not None and not result.passed
        self.convert.setText(f"Fix and Save as {name}…" if failing or result is None else f"Save as {name}…")
        self.convert.setToolTip(f"Save a copy that meets {P.PROFILES[self.key]}: the problems listed are fixed "
                                "in the copy, and the copy is checked again")
        self.convert.setDefault(failing)
        self.save_report.setEnabled(result is not None)
        if result is None:
            self.claimed.setText("")
            self.summary.setText("Click Analyze to check this document.")
            return
        whose = "The fixed copy" if of_copy else "The file"
        self.claimed.setText(f"{whose} says it meets: {result.claimed or 'no standard'}")
        text = result.summary()
        if failing and not of_copy:
            text += f" Click Fix and Save as {name}… to make a copy that meets it."
        self.summary.setText(text)
        color = ICONS[P.PASS if result.passed else P.FAIL][1]
        self.summary.setStyleSheet(f"font-weight: 600; color: {color};")
        for check in result.checks:
            icon, tint = ICONS[check.status]
            item = QTreeWidgetItem([check.title, P.LABELS[check.status]])
            item.setIcon(0, fa.icon(icon, tint))
            item.setForeground(1, QColor(tint))
            for detail in check.details:
                QTreeWidgetItem(item, [detail])
            if check.status in (P.FAIL, P.WARN) and check.fix:
                how = QTreeWidgetItem(item, ["Not fixed automatically: change this in the document, then try again."
                                             if of_copy else f"How to fix: {check.fix}"])
                how.setIcon(0, fa.icon("wand-magic-sparkles", "#2e7d32"))
                how.setForeground(0, QColor("#2e7d32"))
            if check.pages:
                pages = sorted(check.pages)
                shown = ", ".join(str(p + 1) for p in pages[:30]) + ("…" if len(pages) > 30 else "")
                pages_item = QTreeWidgetItem(item, [f"Pages: {shown}"])
                pages_item.setData(0, PAGE_ROLE, pages[0])
                pages_item.setForeground(0, QColor("#1a5fb4"))
            self.tree.addTopLevelItem(item)
            item.setExpanded(check.status in (P.FAIL, P.WARN))

    def _go(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        page = item.data(0, PAGE_ROLE)
        if page is not None and self.fixed_path is None:  # (pages of the open document)
            self.w.view.go_to_page(page)

    def fix(self, target: Path | None = None) -> P.Result | None:
        """Fix and Save: the corrected copy, checked again and shown here."""
        target = self.w.save_as_standard(self.key, target, tell=False)
        if target is None:
            return None
        with pymupdf.open(target) as doc:
            result = P.check(doc, self.key)
        self.fixed_path = target
        fixed, notes = getattr(self.w, "last_conversion", ([], []))
        changed = fixed + notes
        lines = [f"<b>Saved the fixed copy {target.name}</b>"
                 + (": it meets the standard." if result.passed else ": some problems are left (below).")]
        if changed:
            lines.append("Changed in the copy: " + "; ".join(changed[:8]) + ("…" if len(changed) > 8 else ""))
        self.fixed_label.setText("<br>".join(lines))
        self.fixed_label.setStyleSheet(f"color: {ICONS[P.PASS if result.passed else P.WARN][1]};")
        self.fixed_label.setVisible(True)
        self.open_fixed.setVisible(True)
        self._show(result, of_copy=True)
        return result

    def _convert(self) -> None:
        self.fix()

    def _open_fixed(self) -> None:
        if self.fixed_path is not None:
            path = self.fixed_path
            self.accept()
            self.w.open_file(path)

    def _save_report(self) -> None:
        if self.result is None:
            return
        session = self.w.session
        name = self.fixed_path.name if self.fixed_path is not None else session.path.name
        suggested = session.path.with_name(f"{Path(name).stem} compliance.txt")
        from planetpdf.ui.save_path import choose_save_path

        path = choose_save_path(self, "Save Compliance Report", suggested, "Text files (*.txt)", suffix=".txt",
                                last_directory=self.w.settings.last_directory)
        if path:
            Path(path).write_text(self.result.as_text(name), encoding="utf-8")
