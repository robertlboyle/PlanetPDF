"""The Bookmarks tool (user 2026-10-04): select text with the I-beam, then Enter, + or
right-click ▸ Add Bookmark makes a bookmark of it at that place; Add Bookmarks
Automatically finds the table of contents (else the headings) and adds what you tick."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from planetpdf.core import bookmark_detect
from planetpdf.core import links as L
from planetpdf.core.text import selection_text
from planetpdf.ui import fa
from planetpdf.ui.side_panel import BOOKMARKS
from planetpdf.ui.tasks import TaskCanceled, run_in_background

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

MAX_TITLE = 120


def title_from(text: str) -> str:
    title = " ".join(text.split())
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"


def insert_in_order(marks: list[L.Bookmark], new: L.Bookmark) -> tuple[list[L.Bookmark], int]:
    """``new`` after the last bookmark that comes before it in the document (page, then
    height), at that bookmark's level; returns the list and where it went."""
    at = 0
    for n, mark in enumerate(marks):
        if mark.page >= 0 and (mark.page, mark.y) <= (new.page, new.y):
            at = n + 1
            while at < len(marks) and marks[at].level > mark.level:  # (past its children)
                at += 1
    level = marks[at - 1].level if at else 1
    new = L.Bookmark(level, new.title, new.page, new.y)
    return marks[:at] + [new] + marks[at:], at


class SectionsDialog(QDialog):
    """What was found, as a tree with a check box each; Add, or Replace the bookmarks."""

    def __init__(self, parent, sections: list[bookmark_detect.Section], existing: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Bookmarks Automatically")
        self.resize(520, 520)
        self.sections = sections
        layout = QVBoxLayout(self)
        source = ("the table of contents" if sections and sections[0].found == "toc" else "the headings")
        layout.addWidget(QLabel(f"Found {len(sections)} sections in {source}. Untick any you don't want.", self))
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Bookmark", "Page"])
        self.tree.setColumnWidth(0, 400)
        self.items: list[QTreeWidgetItem] = []
        parents: list[QTreeWidgetItem] = []
        for section in sections:
            item = QTreeWidgetItem([section.title, str(section.page + 1)])
            item.setCheckState(0, Qt.CheckState.Checked)
            del parents[section.level - 1 :]
            (parents[-1].addChild(item) if parents else self.tree.addTopLevelItem(item))
            parents.append(item)
            self.items.append(item)
        self.tree.expandAll()
        layout.addWidget(self.tree, 1)
        self.replace = QRadioButton(f"Replace the {existing} bookmarks already here", self)
        self.keep = QRadioButton("Add them to the bookmarks already here", self)
        self.replace.setChecked(True)
        for button in (self.replace, self.keep):
            button.setVisible(existing > 0)
            layout.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add Bookmarks")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[bookmark_detect.Section]:
        return [s for s, item in zip(self.sections, self.items) if item.checkState(0) == Qt.CheckState.Checked]


class BookmarkTool(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        self.on = False
        self.act_add = QAction(fa.icon("bookmark"), "Add Bookmark", window)
        self.act_add.setToolTip("Bookmark the selected text at its place (Enter or +), or the current page")
        self.act_add.triggered.connect(self.add)
        # Enter / + in the page view while this tool is open and text is selected (a key
        # filter, not a shortcut: a form field's editor on the page still gets its Enter)
        window.view.installEventFilter(self)
        self.act_auto = QAction(fa.icon("list-tree"), "Add Bookmarks Automatically…", window)
        self.act_auto.setToolTip("Find the table of contents or the chapter headings and bookmark them")
        self.act_auto.triggered.connect(self.add_automatically)
        window._doc_actions += [self.act_add, self.act_auto]

    def eventFilter(self, watched, event) -> bool:
        if (self.on and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Plus)
                and not event.modifiers() & ~(Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.ShiftModifier)
                and self.w.text_tools.selection and self.w.view.scene().focusItem() is None):
            self.add()
            return True
        return False

    def set_on(self, on: bool) -> None:
        """The tool opened / closed: the I-beam selects text, the Bookmarks panel shows."""
        if on == self.on:
            return
        self.on = on
        w = self.w
        if on:
            w.text_tools.act_select.setChecked(True)
            if w.session is not None:
                w.side_panel.show_panel(BOOKMARKS)
            w.statusBar().showMessage("Select text, then press Enter or + (or right-click) to bookmark it.", 8000)
        elif w.text_tools.act_select.isChecked():
            w.text_tools.act_select.setChecked(False)

    def add(self) -> None:
        w = self.w
        if w.session is None:
            return
        selection = w.text_tools.selection
        title = title_from(selection_text(selection[1])) if selection else ""
        if not title:
            w.bookmarks_panel.add_current()  # (the current page; type its name in the list)
            return
        index, words = selection
        y = max(min(word.rect[1] for word in words) - 4, 0.0) if w.session.doc[index].rotation == 0 else 0.0
        panel = w.bookmarks_panel
        marks, at = insert_in_order(list(panel.bookmarks), L.Bookmark(1, title, index, y))
        panel._push(marks, "Add Bookmark")
        w.text_tools.clear_selection()
        w.side_panel.show_panel(BOOKMARKS)
        panel.select_index(at)
        w.statusBar().showMessage(f"Added the bookmark “{title}”.", 4000)

    def add_automatically(self) -> None:
        w = self.w
        session = w.session
        if session is None:
            return
        data = session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)

        def work(progress):
            with pymupdf.open("pdf", data) as doc:
                return bookmark_detect.find_sections(doc, progress)

        try:
            sections = run_in_background(w, "Add Bookmarks Automatically", "Looking for sections", work)
        except TaskCanceled:
            return
        if not sections:
            w.show_message("Add Bookmarks Automatically",
                           "PlanetPDF didn't find a table of contents or chapter headings in this document.\n\n"
                           "Select a heading with the I-beam and press Enter to bookmark it yourself.")
            return
        existing = list(w.bookmarks_panel.bookmarks)
        dialog = SectionsDialog(w, sections, len(existing))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [L.Bookmark(s.level, s.title, s.page, s.y) for s in dialog.chosen()]
        if not chosen:
            return
        marks = chosen if dialog.replace.isChecked() or not existing else existing + chosen
        w.bookmarks_panel._push(marks, f"Add {len(chosen)} Bookmarks")
        w.side_panel.show_panel(BOOKMARKS)
        w.statusBar().showMessage(f"Added {len(chosen)} bookmarks.", 4000)
