from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.redaction.apply import RedactionResult
from planetpdf.core.redaction.sanitize import LABELS
from planetpdf.core.redaction.verify import Status

_STATUS_STYLE = {
    Status.PASS: ("PASS", QColor(0x1E, 0x84, 0x49)),
    Status.WARN: ("WARNING", QColor(0xB3, 0x59, 0x00)),
    Status.FAIL: ("FAIL", QColor(0xC0, 0x39, 0x2B)),
}


class VerificationReportDialog(QDialog):
    """Pass/fail report shown after redacting. ``open_requested`` is True if the user
    chose to open the redacted file.

    ``held``: the checks failed, so the redacted file was *not* saved yet (user 2026-11-21, a
    security review: a failed redaction must never quietly replace anything). The choices are
    Discard (the default) and, when ``can_keep``, Save Anyway after a second question
    (``keep_requested``)."""

    def __init__(self, result: RedactionResult, can_open: bool, parent: QWidget | None = None, *,
                 held: bool = False, can_keep: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Redaction Verification")
        self.resize(640, 480)
        self.open_requested = False
        self.keep_requested = False
        report = result.report
        layout = QVBoxLayout(self)

        color = "#c0392b" if not report.passed else ("#b35900" if report.warnings else "#1e8449")
        headline = QLabel(f"<h3 style='color:{color}'>{report.summary()}</h3>", self)
        headline.setWordWrap(True)
        layout.addWidget(headline)
        if held:
            note = QLabel("<b>The redacted file was not saved</b>, and nothing was replaced. Fix the problems "
                          "below (for example, mark what is left, or turn on “Also remove shapes that touch "
                          "the box”) and apply the redactions again.", self)
            note.setWordWrap(True)
            layout.addWidget(note)

        where = "Would have been saved to" if held else "Saved to"
        facts = [f"{where}: {result.output}", f"{result.marks_applied} area(s) redacted on {result.pages_redacted} page(s)."]
        overlaps = {k: v for k, v in result.overlapping_removed.items() if v}
        if overlaps:
            facts.append("Also removed because they overlapped a mark: " + ", ".join(f"{v} {k}" for k, v in overlaps.items()) + ".")
        if result.sanitized is not None:
            cleaned = [LABELS[k][0].lower() for k, v in result.sanitized.removed.items() if v]
            if cleaned:
                facts.append("Removed hidden information: " + ", ".join(cleaned) + ".")
        info = QLabel("\n".join(facts), self)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(info)

        tree = QTreeWidget(self)
        tree.setHeaderLabels(["Result", "Check", "Details"])
        tree.setRootIsDecorated(False)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        order = {Status.FAIL: 0, Status.WARN: 1, Status.PASS: 2}
        for check in sorted(report.checks, key=lambda c: order[c.status]):
            text, color = _STATUS_STYLE[check.status]
            item = QTreeWidgetItem([text, check.title, check.detail])
            item.setForeground(0, color)
            item.setToolTip(2, check.detail)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        layout.addWidget(tree, 1)

        buttons = QDialogButtonBox(self)
        if held:
            discard = buttons.addButton("Discard", QDialogButtonBox.ButtonRole.RejectRole)
            discard.setDefault(True)
            if can_keep:
                keep = buttons.addButton("Save Anyway…", QDialogButtonBox.ButtonRole.DestructiveRole)
                keep.clicked.connect(self._keep)
        else:
            buttons.addButton(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Copy Report", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(report.as_text()))
        if can_open and not held:
            open_button = buttons.addButton("Open Redacted File", QDialogButtonBox.ButtonRole.AcceptRole)
            open_button.clicked.connect(self._open)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _keep(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.warning(
            self, "Save a Failed Redaction?",
            "The checks found content that is still in the file. Anyone you send it to may be able to "
            "read it.\n\nSave it anyway, for example to look at it before redacting again?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            self.keep_requested = True
            self.accept()

    def _open(self) -> None:
        self.open_requested = True
        self.accept()
