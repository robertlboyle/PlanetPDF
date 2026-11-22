"""Several documents in one window, as tabs (user 2026-10-02: "open multiple documents
inside one copy of the program"; a document goes into a window of its own only when
popped out).

A ``Document`` is everything that belongs to one open file rather than to the window:
the session, its undo history, where the view was, the signature check results and its
auto-save copy. The window shows one document at a time (``MainWindow.document``);
switching tabs stashes the view state of the old one and shows the new one. Moving a
document to another window takes all of it along, undo history included (the undo
commands only keep a reference to their window, which is re-pointed: ``rebind``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent, QUndoCommand, QUndoStack
from PySide6.QtWidgets import QApplication, QTabBar

if TYPE_CHECKING:
    from planetpdf.core.document import PdfSession
    from planetpdf.ui.main_window import MainWindow

TEAR_DISTANCE = 30  # px the mouse must leave the tab bar by (up or down) to pull a tab out


@dataclass(eq=False)
class Document:
    session: PdfSession
    undo_stack: QUndoStack
    signature_change_accepted: bool = False
    view_state: dict | None = None  # where the view was when another tab was shown
    signature_statuses: list | None = None  # checked once, shown again on switching back
    autosave_entry: str | None = None  # recovery copy (ui/recovery.py)
    autosave_lock: object = None
    extras: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.session.path.name

    def is_dirty(self) -> bool:
        return not self.undo_stack.isClean()


def rebind(stack: QUndoStack, window: MainWindow) -> None:
    """The document moved to another window: its undo commands act on that one now."""

    def visit(command: QUndoCommand) -> None:
        if hasattr(command, "_window"):
            command._window = window
        for n in range(command.childCount()):
            visit(command.child(n))

    for n in range(stack.count()):
        visit(stack.command(n))


class DocumentTabs(QTabBar):
    """One tab per open document. Drag sideways to reorder; drag a tab out of the bar to
    give it a window of its own, or onto another window to move it there (``tornOff``)."""

    tornOff = Signal(int, QPoint)  # tab index, global position where the mouse was let go

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("documentTabs")
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._press: QPoint | None = None
        self.tearing = False

    def document(self, index: int) -> Document | None:
        return self.tabData(index) if 0 <= index < self.count() else None

    def index_of(self, doc: Document) -> int:
        return next((n for n in range(self.count()) if self.tabData(n) is doc), -1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.tabAt(event.position().toPoint()) >= 0:
            self._press = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.tearing:
            return
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            y = event.position().y()
            if y < -TEAR_DISTANCE or y > self.height() + TEAR_DISTANCE:
                self.start_tear()
                return
        super().mouseMoveEvent(event)

    def start_tear(self) -> None:
        """The tab left the bar: end Qt's own tab move (so the tab snaps back into the
        row) and follow the mouse until it's let go."""
        self.tearing = True
        at = QPointF(self.mapFromGlobal(self._press or QCursor.pos()))
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, at, at, Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        super().mouseReleaseEvent(release)
        QApplication.setOverrideCursor(Qt.CursorShape.DragMoveCursor)

    def finish_tear(self, global_pos: QPoint, *, cancel: bool = False) -> None:
        if not self.tearing:
            return
        self.tearing = False
        self._press = None
        QApplication.restoreOverrideCursor()
        if not cancel:
            self.tornOff.emit(self.currentIndex(), global_pos)

    def mouseReleaseEvent(self, event) -> None:
        if self.tearing:
            self.finish_tear(event.globalPosition().toPoint())
            return
        self._press = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.tearing and event.key() == Qt.Key.Key_Escape:
            self.finish_tear(QCursor.pos(), cancel=True)
            return
        super().keyPressEvent(event)
