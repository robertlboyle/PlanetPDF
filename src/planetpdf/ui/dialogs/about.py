"""Help ▸ About PlanetPDF: the version, What's New (the changes in each version, the manual's
whats-new.html; user 2026-10-18: "keep a running changes log in the file and in the help about"),
and every third-party component with its license (from resources/licenses, made by
scripts/update_notices.py; user request 2026-10-02)."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf import APP_NAME, __version__
from planetpdf.core.resources import resources_dir

NOTICES_FILE = "THIRD-PARTY-NOTICES.txt"
RULE = "=" * 78


def licenses_dir():
    return resources_dir() / "licenses"


def load_components() -> list[dict]:
    try:
        return json.loads((licenses_dir() / "components.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def load_notices() -> str:
    try:
        return (licenses_dir() / NOTICES_FILE).read_text(encoding="utf-8")
    except OSError:
        return "(The third-party notices file is missing from this installation.)"


def heading(component: dict) -> str:
    return f"{component['name']} {component['version']}".rstrip()


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(820, 640)
        title = QLabel(f"<h2>{APP_NAME} {__version__}</h2>"
                       "<p>A free PDF editor from Planet Networks, under the GNU AGPL-3.0-or-later: anyone "
                       "may use it, read its source, change it, and share it "
                       "(<a href='https://github.com/robertlboyle/PlanetPDF'>https://github.com/robertlboyle/PlanetPDF</a>). No warranty.<br>"
                       "Keyboard shortcuts follow Adobe Acrobat. The manual: Help ▸ PlanetPDF Help (F1).</p>", self)
        third_party = QLabel(f"{APP_NAME} is built with the components below, each under its own license. "
                             "Choose one to see its license text.", self)
        third_party.setWordWrap(True)
        title.setWordWrap(True)
        title.setOpenExternalLinks(True)  # (the source code's page)

        self.components = load_components()
        self.table = QTableWidget(len(self.components), 3, self)
        self.table.setAccessibleName("Components")
        self.table.setHorizontalHeaderLabels(["Component", "Version", "License"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for row, c in enumerate(self.components):
            name = c["name"] + (f"  (in {c['part_of']})" if c.get("part_of") else "")
            for col, text in enumerate((name, c["version"], c["license"])):
                item = QTableWidgetItem(text)
                item.setToolTip(c.get("url", ""))
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.text = QPlainTextEdit(self)
        self.text.setAccessibleName("License text")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlainText(load_notices())
        self._headings = self._index_headings()
        self.table.currentCellChanged.connect(lambda row, *_: self.show_component(row))

        split = QSplitter(Qt.Orientation.Vertical, self)
        split.addWidget(self.table)
        split.addWidget(self.text)
        split.setSizes([260, 300])

        from planetpdf.core import help as H

        self.whats_new = QTextBrowser(self)
        self.whats_new.setAccessibleName("What's New")
        self.whats_new.setOpenExternalLinks(False)
        self.whats_new.setOpenLinks(False)
        try:
            self.whats_new.setHtml(H.prepared((H.help_dir() / "whats-new.html").read_text(encoding="utf-8")))
        except OSError:
            self.whats_new.setPlainText("The list of changes isn't there.")
        notices = QWidget(self)
        column = QVBoxLayout(notices)
        column.setContentsMargins(0, 4, 0, 0)
        column.addWidget(third_party)
        column.addWidget(split, 1)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.whats_new, "What's New")
        self.tabs.addTab(notices, "Third-Party Software")

        open_file = QPushButton("Open Notices File", self)
        open_file.setToolTip(str(licenses_dir() / NOTICES_FILE))
        open_file.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(licenses_dir() / NOTICES_FILE))))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(open_file)
        row.addStretch(1)
        row.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(row)

    def _index_headings(self) -> dict[str, int]:
        """Each component's section in the notices: heading text -> block (line) number."""
        found = {}
        doc = self.text.document()
        block = doc.firstBlock()
        previous = ""
        while block.isValid():
            line = block.text()
            if previous == RULE and line and line != RULE and line not in found:
                found[line] = block.blockNumber()
            previous = line
            block = block.next()
        return found

    def show_component(self, row: int) -> None:
        """Scroll the license text so the component's section starts at the top."""
        if not 0 <= row < len(self.components):
            return
        number = self._headings.get(heading(self.components[row]))
        if number is None:
            return
        cursor = QTextCursor(self.text.document().findBlockByNumber(max(0, number - 1)))
        self.text.moveCursor(QTextCursor.MoveOperation.End)  # (then back: the section lands at the top)
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()

    def section_at_top(self) -> str:
        """The component heading shown at the top of the license text (for tests)."""
        block = self.text.firstVisibleBlock()
        for _ in range(3):
            if block.text() and block.text() != RULE:
                return block.text()
            block = block.next()
        return ""
