"""Organize Pages (Acrobat): every page as a large tile in place of the page view.

Drag tiles to reorder (Ctrl/Shift for several), drop PDF files between them to insert
their pages, hover a tile for rotate / delete buttons, double-click to open that page.
It is the sidebar's ``ThumbnailList`` in grid form, so every change goes through the same
undoable commands and the grid is rebuilt from the document afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from planetpdf.ui import fa
from planetpdf.ui.viewer.thumbnails import ThumbnailList

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

TILE = (180, 232)

# Page selector on the Organize bar (Acrobat's "All Pages ▾")
PAGE_SETS = ("All Pages", "Even Pages", "Odd Pages", "Portrait Pages", "Landscape Pages")


class OrganizeGrid(ThumbnailList):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, size=TILE, grid=True)
        self.w = window
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.moveRequested.connect(window.move_pages)
        self.copyRequested.connect(window.copy_pages)  # (Shift held on dropping: a copy)
        self.filesDropped.connect(lambda paths, row: window._on_files_dropped_on_thumbnails(paths, row))
        self.pageActivated.connect(self.open_page)
        self._hover_row: int | None = None
        self.hover = _HoverButtons(self)
        self.hover.hide()

    def open_page(self, row: int) -> None:
        """Double-click: back to the page view, on that page (Acrobat)."""
        self.w.tool_panes.close()
        self.w.view.go_to_page(row)

    # --- page sets ----------------------------------------------------------------------------

    def pages_in_set(self, name: str) -> list[int]:
        session = self.w.session
        if session is None:
            return []
        pages = range(session.page_count)
        if name == "Even Pages":
            return [p for p in pages if p % 2 == 1]  # page numbers 2, 4, …
        if name == "Odd Pages":
            return [p for p in pages if p % 2 == 0]
        if name in ("Portrait Pages", "Landscape Pages"):
            sizes = {p: session.page_size(p) for p in pages}
            portrait = name == "Portrait Pages"
            return [p for p in pages if (sizes[p][1] >= sizes[p][0]) == portrait]
        return list(pages)

    def select_set(self, name: str) -> None:
        self.select_pages(self.pages_in_set(name))
        self.setFocus()
        self.itemSelectionChanged.emit()  # (the bar's page box follows; select_pages is quiet)

    def select_range(self, text: str) -> None:
        """The bar's page box: pages typed in ("3", "1-5, 8"), or a set's name."""
        from planetpdf.core.pagerange import parse_page_ranges

        if text.strip() in PAGE_SETS:
            self.select_set(text.strip())
            return
        try:
            pages = parse_page_ranges(text, self.w.session.page_count)
        except Exception:
            self.w.statusBar().showMessage(f"“{text}” isn't a page range (e.g. 1-3, 7).", 5000)
            return
        if pages:
            self.select_pages(pages)
            self.setFocus()
            self.itemSelectionChanged.emit()

    def selection_text(self) -> str:
        """The selected pages as the bar's page box shows them ("3", "1-4, 7")."""
        from planetpdf.core.pagerange import format_page_ranges

        pages = self.selected_pages() if hasattr(self, "selected_pages") else []
        count = self.w.session.page_count if self.w.session is not None else 0
        if not pages or len(pages) == count:
            return "All Pages"
        return format_page_ranges(pages)

    # --- hover buttons ------------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self._hover_at(event.position().toPoint())

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if not self.hover.underMouse():
            self._hover_at(None)

    def _hover_at(self, pos: QPoint | None) -> None:
        index = self.indexAt(pos) if pos is not None else None
        row = index.row() if index is not None and index.isValid() else None
        if row == self._hover_row:
            return
        self._hover_row = row
        if row is None:
            self.hover.hide()
            return
        rect = self.visualItemRect(self.item(row))
        self.hover.adjustSize()
        self.hover.move(rect.center().x() - self.hover.width() // 2, rect.top() + 8)
        self.hover.row = row
        self.hover.show()
        self.hover.raise_()

    def rotate(self, row: int, degrees: int) -> None:
        self.w.rotate_pages([row], degrees)

    def delete(self, row: int) -> None:
        self.select_pages([row])
        self.w.delete_selected_pages()

    def _rebuild(self, mapping) -> None:
        self._hover_row = None
        self.hover.hide()
        super()._rebuild(mapping)


class _HoverButtons(QWidget):
    """Rotate counterclockwise / clockwise and delete, over the tile under the mouse."""

    def __init__(self, grid: OrganizeGrid) -> None:
        super().__init__(grid.viewport())
        self.row = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("organizeHover")
        self.setStyleSheet("#organizeHover { background: rgba(255,255,255,240); border: 1px solid #b8c0ca; border-radius: 5px; }"
                           "QToolButton { border: none; padding: 3px; } QToolButton:hover { background: #dde3ea; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(2)
        self.buttons = {}
        for key, icon, tip, slot in (
            ("ccw", "rotate-left", "Rotate counterclockwise", lambda: grid.rotate(self.row, -90)),
            ("cw", "rotate-right", "Rotate clockwise", lambda: grid.rotate(self.row, 90)),
            ("delete", "trash", "Delete page", lambda: grid.delete(self.row)),
        ):
            button = QToolButton(self)
            button.setIcon(fa.icon(icon))
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            layout.addWidget(button)
            self.buttons[key] = button
