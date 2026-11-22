"""Bookmarks (outline) panel: navigate, add, rename (in place) and delete."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import links as L

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


class SetBookmarksCommand(QUndoCommand):
    def __init__(self, window: MainWindow, old: list[L.Bookmark], new: list[L.Bookmark], text: str) -> None:
        super().__init__(text)
        self._window, self._old, self._new = window, old, new

    def redo(self) -> None:
        L.set_bookmarks(self._window.session, self._new)
        self._window.bookmarks_panel.refresh()

    def undo(self) -> None:
        L.set_bookmarks(self._window.session, self._old)
        self._window.bookmarks_panel.refresh()


class BookmarksPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.bookmarks: list[L.Bookmark] = []
        self.tree = QTreeWidget(self)
        self.tree.setAccessibleName("Bookmarks")
        self.tree.setHeaderHidden(True)
        # a click only jumps there (user 2026-10-04): renaming is F2 or right-click ▸ Rename,
        # never a click on the selected one; the place changes only by Set Destination
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.itemClicked.connect(lambda item, _c: self._go(item))
        self.tree.itemActivated.connect(lambda item, _c: self._go(item))
        self.tree.itemChanged.connect(self._renamed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.empty = QLabel("This document has no bookmarks.", self)
        self.empty.setStyleSheet("color: gray;")
        self.add_button = QPushButton("Add", self)
        self.add_button.setToolTip("Bookmark the current page")
        self.add_button.clicked.connect(self.add_current)
        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self.delete_selected)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        self._filling = False

    def refresh(self) -> None:
        session = self._window.session
        self.bookmarks = L.get_bookmarks(session) if session is not None else []
        self._filling = True
        self.tree.clear()
        parents: list[QTreeWidgetItem] = []
        for index, mark in enumerate(self.bookmarks):
            item = QTreeWidgetItem([mark.title])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(0, f"Page {mark.page + 1}" if mark.page >= 0 else "")
            del parents[mark.level - 1 :]
            if parents:
                parents[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents.append(item)
        self.tree.expandToDepth(0)
        self._filling = False
        self.empty.setVisible(not self.bookmarks)
        self.add_button.setEnabled(session is not None)
        self.delete_button.setEnabled(bool(self.bookmarks))

    def _index(self, item: QTreeWidgetItem | None) -> int | None:
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _go(self, item: QTreeWidgetItem) -> None:
        index = self._index(item)
        if index is None:
            return
        mark = self.bookmarks[index]
        if mark.page >= 0:
            page = self._window.session.doc[mark.page]
            import pymupdf

            shown = pymupdf.Point(0, mark.y) * page.rotation_matrix
            self._window.view.go_to_point(mark.page, shown.y if page.rotation in (0, 180) else 0)

    def _push(self, new: list[L.Bookmark], text: str) -> None:
        self._window.push_command(SetBookmarksCommand(self._window, list(self.bookmarks), new, text))

    def add_current(self) -> None:
        if self._window.session is None:
            return
        page = max(self._window.view.current_page, 0)
        new = list(self.bookmarks) + [L.Bookmark(1, f"Page {page + 1}", page)]
        self._push(new, "Add Bookmark")
        last = self.tree.topLevelItem(self.tree.topLevelItemCount() - 1)
        if last is not None:
            self.tree.setCurrentItem(last)
            self.tree.editItem(last, 0)  # type the title right in the list

    def select_index(self, index: int) -> None:
        """Show and select the bookmark at ``index`` (in ``bookmarks``)."""
        found = self.tree.findItems("", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive)
        for item in found:
            if self._index(item) == index:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return

    def delete_selected(self) -> None:
        index = self._index(self.tree.currentItem())
        if index is None:
            return
        level = self.bookmarks[index].level
        end = index + 1  # children go too
        while end < len(self.bookmarks) and self.bookmarks[end].level > level:
            end += 1
        self._push(self.bookmarks[:index] + self.bookmarks[end:], "Delete Bookmark")

    def _renamed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._filling:
            return
        index = self._index(item)
        title = item.text(0).strip()
        if index is None or not title or title == self.bookmarks[index].title:
            return
        new = list(self.bookmarks)
        new[index] = dataclasses.replace(new[index], title=title)  # (its place and look stay)
        self._push(new, "Rename Bookmark")

    # --- right-click ----------------------------------------------------------------------

    def context_menu(self, item: QTreeWidgetItem | None) -> QMenu:
        """Go To, Rename, Set Destination, Delete (Acrobat's bookmark menu)."""
        menu = QMenu(self)
        index = self._index(item)
        go = menu.addAction("Go to Bookmark", lambda: self._go(item))
        rename = menu.addAction("Rename\tF2", lambda: self.tree.editItem(item, 0))
        here = menu.addAction("Set Destination", lambda: self.set_destination(index))
        here.setToolTip("Point this bookmark at the selected text, or else the page shown now")
        menu.addSeparator()
        delete = menu.addAction("Delete", self.delete_selected)
        for action in (go, rename, here, delete):
            action.setEnabled(index is not None)
        menu.addSeparator()
        menu.addAction("Add Bookmark", self._window.bookmark_tool.add)
        return menu

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is not None:
            self.tree.setCurrentItem(item)
        self.context_menu(item).exec(self.tree.viewport().mapToGlobal(pos))

    def set_destination(self, index: int | None) -> None:
        """Acrobat's Set Destination: the only way a bookmark's place changes. The selected
        text's place, else the top of the page shown now."""
        if index is None or self._window.session is None:
            return
        selection = self._window.text_tools.selection
        if selection:
            page, words = selection
            y = max(min(word.rect[1] for word in words) - 4, 0.0) if self._window.session.doc[page].rotation == 0 else 0.0
        else:
            page, y = max(self._window.view.current_page, 0), 0.0
        old = self.bookmarks[index]
        look = tuple((k, v) for k, v in old.original if k in L.LOOK_KEYS)  # (its color and style stay)
        new = list(self.bookmarks)
        new[index] = L.Bookmark(old.level, old.title, page, y, look)
        self._push(new, "Set Bookmark Destination")
