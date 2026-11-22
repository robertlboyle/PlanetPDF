"""The Pages / Bookmarks panel (user 2026-10-01): it flies out from the right edge of the
document and collapses again, like a menu. Two buttons at the top of the right-hand tool
strip open it on Pages or on Bookmarks; clicking the open one again collapses it.
"Page x of y" sits at the bottom; every thumbnail has its page number underneath.

It lives inside the window's central splitter (next to the page view), not in a dock,
so it never stacks with the tool panels (Redaction, Comments, …).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from planetpdf.ui import fa

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

PAGES = "pages"
BOOKMARKS = "bookmarks"
TITLES = {PAGES: "Pages", BOOKMARKS: "Bookmarks"}
ICONS = {PAGES: "copy", BOOKMARKS: "bookmark"}
DEFAULT_WIDTH = 190


class SidePanel(QWidget):
    def __init__(self, window: MainWindow, pages: QWidget, bookmarks: QWidget) -> None:
        super().__init__(window)
        self.w = window
        self.setObjectName("sidePanel")
        self.setMinimumWidth(90)  # (drag the splitter: the thumbnails shrink / grow with it)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(8, 3, 2, 3)
        self.title = QLabel(self)
        self.title.setStyleSheet("font-weight: 600;")
        header.addWidget(self.title, 1)
        self.collapse_button = QToolButton(self)
        self.collapse_button.setIcon(fa.icon("angles-right"))
        self.collapse_button.setToolTip("Collapse the panel (F4)")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.clicked.connect(self.collapse)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.stack = QStackedWidget(self)
        self.pages, self.bookmarks = pages, bookmarks
        self.stack.addWidget(pages)
        self.stack.addWidget(bookmarks)
        layout.addWidget(self.stack, 1)
        self.footer = QLabel(self)
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer.setStyleSheet("padding: 4px; border-top: 1px solid palette(mid);")
        layout.addWidget(self.footer)

        # The buttons on the right-hand strip (also View ▸ Show/Hide ▸ Navigation Panes)
        self.group = QActionGroup(window)
        self.group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.actions: dict[str, QAction] = {}
        for key in (PAGES, BOOKMARKS):
            action = QAction(fa.icon(ICONS[key]), TITLES[key], window, checkable=True)
            action.setToolTip(f"{TITLES[key]}: click to open, click again to collapse")
            action.triggered.connect(lambda on, k=key: self.show_panel(k) if on else self.collapse())
            self.group.addAction(action)
            self.actions[key] = action
        self.current = PAGES
        self.hide()
        self._sync()

    @property
    def is_open(self) -> bool:
        return not self.isHidden()

    def show_panel(self, key: str = PAGES) -> None:
        """Fly out on Pages or Bookmarks."""
        self.current = key
        self.stack.setCurrentWidget(self.pages if key == PAGES else self.bookmarks)
        self.show()
        self._sync()

    def collapse(self) -> None:
        self.hide()
        self._sync()

    def toggle(self) -> None:
        self.collapse() if self.is_open else self.show_panel(self.current)

    def _sync(self) -> None:
        self.title.setText(TITLES[self.current])
        for key, action in self.actions.items():
            action.setChecked(self.is_open and key == self.current)

    def update_page(self, current: int, count: int) -> None:
        """Footer: "Page x of y" (current is 0-based; count 0 = no document)."""
        self.footer.setText(f"Page {current + 1} of {count}" if count else "No document")
