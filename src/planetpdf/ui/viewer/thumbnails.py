"""Page thumbnails sidebar.

* Thumbnails render in the background, visible ones first.
* Drag thumbnails to reorder pages (multi-select with Ctrl/Shift); a line shows the
  drop position. Drop PDF files from outside to insert their pages at that position.
* The list never rearranges itself: drops are turned into requests that the main
  window executes as undoable commands, then the list is rebuilt from the document.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QWidget,
)

from planetpdf.core.document import PdfSession
from planetpdf.core.render import render_page
from planetpdf.ui.viewer.page_view import BACKGROUND
from planetpdf.ui.viewer.qimage import to_pixmap

THUMB_W = 110
THUMB_H = 140
MAX_THUMB_W = 600  # (the side panel can be dragged wide: thumbnails grow up to this)
RENDERS_PER_TICK = 3
DROP_LINE = QColor(0x3D, 0xA5, 0xFF)


def _pdf_paths(event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> list[Path]:
    return [
        Path(url.toLocalFile())
        for url in event.mimeData().urls()
        if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
    ]


class ThumbnailList(QListWidget):
    pageActivated = Signal(int)
    # (selected page indices, drop position 0..page_count)
    moveRequested = Signal(list, int)
    # (selected page indices, drop position): Shift held on dropping = a copy goes there
    copyRequested = Signal(list, int)
    # (PDF files dropped from outside, insert position 0..page_count)
    filesDropped = Signal(list, int)

    def __init__(self, parent: QWidget | None = None, size: tuple[int, int] = (THUMB_W, THUMB_H), grid: bool = False) -> None:
        super().__init__(parent)
        self.thumb_w, self.thumb_h = size
        self.grid = grid  # Organize Pages: rows of large pages, left to right
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight if grid else QListView.Flow.TopToBottom)
        self.setWrapping(grid)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(self.thumb_w, self.thumb_h))
        self.setSpacing(18 if grid else 6)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(self.thumb_w + 40 if grid else 90)  # (the sidebar can be dragged narrow)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Reordering pages is a drag of our own (mouse press / move / release), not Qt's
        # system drag: that one started but never delivered the drop on Windows (user bug
        # 2026-10-02, "where did you add the feature?"). Files dropped from Explorer still
        # arrive as system drops.
        self.setDragEnabled(False)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        # The viewport is what's under the mouse, and it was left not accepting drops, so a
        # PDF dragged onto the pages went past the list to the window and opened as a tab
        # instead of going in between the pages (user bug 2026-10-07).
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # we draw our own line between pages
        self._press: dict | None = None  # a press on a page (it may become a drag)
        self._dragging = False
        self._cursor_set = False  # our drag cursor is up (application-wide)
        self._foreign: ThumbnailList | None = None  # another window's list showing our drop line
        # Same backdrop as the page view, so white pages stand out.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, BACKGROUND)
        palette.setColor(QPalette.ColorRole.Text, QColor("white"))
        self.setPalette(palette)

        self._number_font = QFont(self.font())
        self._number_font.setBold(True)
        self._session: PdfSession | None = None
        self._unrendered: set[int] = set()
        self._drop_row: int | None = None
        self._timer = QTimer(self, interval=0)
        self._timer.timeout.connect(self._render_some)
        if not grid:  # (in the grid a click only selects; double-click opens the page)
            self.itemClicked.connect(lambda item: self.pageActivated.emit(self.row(item)))
        self.itemActivated.connect(lambda item: self.pageActivated.emit(self.row(item)))

    # --- document --------------------------------------------------------------

    def set_session(self, session: PdfSession | None) -> None:
        self._session = session
        self._rebuild([None] * (session.page_count if session else 0))

    def apply_page_mapping(self, mapping: list[int | None], select: list[int]) -> None:
        """Rebuild after a page change, reusing thumbnails of pages that still exist."""
        self._rebuild(mapping)
        self.select_pages(select)

    def _rebuild(self, mapping: list[int | None]) -> None:
        old_icons = [self.item(i).icon() for i in range(self.count())]
        old_unrendered = self._unrendered
        self._timer.stop()
        self.clear()
        self._unrendered = set()
        if self._session is None:
            return
        for new, old in enumerate(mapping):
            if old is not None and old < len(old_icons) and old not in old_unrendered:
                icon = old_icons[old]
            else:
                icon = self._placeholder(*self._session.page_size(new))
                self._unrendered.add(new)
            item = QListWidgetItem(icon, str(new + 1))  # the page number under each thumbnail
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setForeground(QColor("white"))  # (on the dark backdrop, whatever the theme)
            item.setFont(self._number_font)
            item.setToolTip(f"Page {new + 1}")
            self.addItem(item)
        # (with uniform item sizes Qt keeps the cell size it measured before: the new
        # items would sit in cells of the old thumbnail size, cropped. Measure again.)
        self.refresh_labels()
        self.setUniformItemSizes(False)
        self.setUniformItemSizes(True)
        self.doItemsLayout()
        if self._unrendered:
            self._timer.start()

    def refresh_labels(self) -> None:
        """Page labels (iii, A-1, …) under the thumbnails when the document has them."""
        if self._session is None:
            return
        from planetpdf.core.page_labels import label

        has_labels = bool(self._session.doc.get_page_labels())
        for i in range(self.count()):
            text = label(self._session, i) if has_labels else ""
            item = self.item(i)
            item.setText(text if text else str(i + 1))
            labeled = f" (labeled “{text}”)" if text and text != str(i + 1) else ""
            item.setToolTip(f"Page {i + 1}{labeled}")
            # what a screen reader says for it (the number alone said nothing about what it is)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"Page {i + 1} of {self.count()}{labeled}")

    # --- size follows the panel (user 2026-10-01: drag the splitter to grow / shrink them) --

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self.grid:
            if not hasattr(self, "_fit_timer"):
                self._fit_timer = QTimer(self, singleShot=True, interval=120)  # (once the drag pauses)
                self._fit_timer.timeout.connect(self._fit_to_width)
            self._fit_timer.start()

    def available_width(self) -> int:
        """The width a thumbnail can use, the same whether the scroll bar shows or not: its
        room is always kept free. (Measured from the viewport, which narrows when the bar
        appears, some files' thumbnails grew, needed the bar, shrank, lost it and grew again,
        without end: user bug 2026-10-17, "jump between two sizes … It seems to be constant".)"""
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, self)
        return self.contentsRect().width() - bar - 2 * self.spacing() - 2

    def _fit_to_width(self) -> None:
        width = max(60, min(MAX_THUMB_W, self.available_width()))
        if abs(width - self.thumb_w) < 6:
            return
        self.set_thumb_size(width, round(width * THUMB_H / THUMB_W))

    def set_thumb_size(self, width: int, height: int) -> None:
        """New thumbnail size: the current pictures are kept (scaled) until each page is
        rendered again at the new size, the visible ones first."""
        old = QSize(self.thumb_w, self.thumb_h)
        self.thumb_w, self.thumb_h = width, height
        self.setIconSize(QSize(width, height))
        # Qt sizes each cell from the icon's actual pixels: scale the current pictures to
        # the new size at once (soft for a moment), so no cell keeps the old size and crops
        # its page; the sharp renders replace them right after.
        for i in range(self.count()):
            item = self.item(i)
            picture = item.icon().pixmap(old)
            if not picture.isNull():
                item.setIcon(QIcon(picture.scaled(QSize(width, height), Qt.AspectRatioMode.IgnoreAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)))
        # (an icon-mode list keeps the cell size it measured first, and with uniform item
        # sizes it caches one size for all: give it the new grid, clear that cache, lay out)
        line = self.fontMetrics().height()
        self.setGridSize(QSize(width + 2 * self.spacing(), height + line + 2 * self.spacing()))
        self.setUniformItemSizes(False)
        self.setUniformItemSizes(True)
        self.doItemsLayout()
        if self._session is None:
            return
        self._unrendered = set(range(self.count()))
        self._timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._unrendered:
            self._timer.start()

    def refresh_page(self, index: int) -> None:
        """Re-render one thumbnail (its content changed)."""
        if self._session is not None and 0 <= index < self.count():
            self._unrendered.add(index)
            self._timer.start()

    # --- selection -------------------------------------------------------------

    def selected_pages(self) -> list[int]:
        return sorted(index.row() for index in self.selectedIndexes())

    def select_pages(self, pages: list[int]) -> None:
        self.blockSignals(True)
        self.clearSelection()
        for index in pages:
            if 0 <= index < self.count():
                self.item(index).setSelected(True)
        if pages and 0 <= pages[0] < self.count():
            self.setCurrentRow(pages[0], QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollToItem(self.item(pages[0]))
        self.blockSignals(False)

    def set_current_page(self, index: int) -> None:
        """Follow the page view, without destroying a multi-page selection."""
        if not 0 <= index < self.count() or len(self.selectedIndexes()) > 1:
            return
        if index != self.currentRow():
            self.blockSignals(True)
            self.setCurrentRow(index)
            self.blockSignals(False)
            self.scrollToItem(self.item(index))

    # --- drag and drop ---------------------------------------------------------

    def drop_row_at(self, pos: QPoint) -> int:
        """Insert position (0..count) for a drop at viewport position ``pos``."""
        if self.grid and not self._one_column():
            return self._grid_drop_row(pos)
        index = self.indexAt(pos)
        if not index.isValid():
            # In the gap between two pages (where the drop line is drawn) or past the ends:
            # before the first page whose middle is below the mouse. It used to mean "after
            # the last page", so a file dropped between pages 1 and 2 went to the end
            # (user bug 2026-10-07).
            for row in range(self.count()):
                if pos.y() < self.visualItemRect(self.item(row)).center().y():
                    return row
            return self.count()
        rect = self.visualRect(index)
        return index.row() + (1 if pos.y() > rect.center().y() else 0)

    def _one_column(self) -> bool:
        """The grid shows one page per row (a narrow window): it then works like a list,
        with a horizontal drop line."""
        if self.count() < 2:
            return True
        return self.visualItemRect(self.item(1)).top() > self.visualItemRect(self.item(0)).top()

    @staticmethod
    def _copying(event) -> bool:
        """Shift held while dropping one's own pages: copy them there (user 2026-10-02)."""
        return bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _grid_drop_row(self, pos: QPoint) -> int:
        """Grid: before the page whose row (by height) holds ``pos``, judged by x."""
        rows: list[int] = []
        for i in range(self.count()):
            rect = self.visualItemRect(self.item(i))
            if rect.top() - self.spacing() <= pos.y() <= rect.bottom() + self.spacing():
                rows.append(i)
                if pos.x() < rect.center().x():
                    return i
        if rows:
            return rows[-1] + 1
        if self.count() and pos.y() < self.visualItemRect(self.item(0)).top():
            return 0
        return self.count()

    # --- our own page drag: press on a page, move, let go (Shift: a copy) ------------------

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        # Qt's rubber band (drag a box to select pages) only from empty space: from a page
        # the drag moves pages, and a band started there stayed painted as a tall pink
        # bar after the drop (user bug 2026-10-02)
        self.setSelectionRectVisible(item is None)
        plain = not (event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if item is not None and item.isSelected() and plain and len(self.selectedItems()) > 1:
            # one of several selected pages: keep them all selected, it may be a drag of
            # all of them; a plain click (no drag) selects just this one on release
            self._press = {"pos": event.position().toPoint(), "item": item, "deferred": True}
            event.accept()
            return
        super().mousePressEvent(event)
        if item is not None and item.isSelected():
            self._press = {"pos": event.position().toPoint(), "item": item, "deferred": False}

    def mouseMoveEvent(self, event) -> None:
        press = self._press
        if press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if not self._dragging and (pos - press["pos"]).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
            if self._dragging:
                self._drag_to(pos)
            # (never to Qt while a page is held, not even the first few pixels: Qt would
            # start selecting by dragging and draw its band)
            self.setState(QAbstractItemView.State.NoState)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _drag_to(self, pos: QPoint) -> None:
        target = self._other_window_target(self.viewport().mapToGlobal(pos))
        self._show_on(target)
        if target is None:
            self._drop_row = self.drop_row_at(pos)
            copy = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            bar = self.verticalScrollBar()  # near the top / bottom edge: scroll on
            if pos.y() < 24:
                bar.setValue(bar.value() - 16)
            elif pos.y() > self.viewport().height() - 24:
                bar.setValue(bar.value() + 16)
        else:
            self._drop_row = None
            copy = True  # (into another document: always a copy)
        shape = Qt.CursorShape.DragCopyCursor if copy else Qt.CursorShape.DragMoveCursor
        # (an application-wide cursor: it shows over the other windows too)
        if self._cursor_set:
            QApplication.changeOverrideCursor(shape)
        else:
            QApplication.setOverrideCursor(shape)
            self._cursor_set = True
        self.viewport().update()

    def _other_window_target(self, global_pos: QPoint):
        """(window, widget, insert position) when the mouse is over another document's
        window: its Pages panel / Organize grid (at the drop line) or its page view
        (before or after the page under the mouse). None anywhere else."""
        from planetpdf.ui.viewer.page_view import PageView

        widget = QApplication.widgetAt(global_pos)  # (the window in front, on a real screen)
        own = self.window()
        if widget is None:  # (nothing found, e.g. off the screen Qt knows: PlanetPDF's windows by position)
            for window in QApplication.topLevelWidgets():
                if window is not own and window.isVisible() and window.frameGeometry().contains(global_pos):
                    widget = window.childAt(window.mapFromGlobal(global_pos)) or window
                    break
        while widget is not None and not isinstance(widget, (ThumbnailList, PageView)):
            widget = widget.parentWidget()
        if widget is None:
            return None
        window = widget.window()
        if window is own or not hasattr(window, "receive_pages") or getattr(window, "session", None) is None:
            return None
        if isinstance(widget, ThumbnailList):
            return window, widget, widget.drop_row_at(widget.viewport().mapFromGlobal(global_pos))
        scene = widget.mapToScene(widget.viewport().mapFromGlobal(global_pos))
        page = widget._page_at(scene)
        if page is None:
            return window, widget, window.session.page_count
        lower = page.mapFromScene(scene).y() > page.boundingRect().height() / 2
        return window, widget, page.index + (1 if lower else 0)

    def _show_on(self, target) -> None:
        """The drop line in the other window's page list."""
        widget = target[1] if target is not None and isinstance(target[1], ThumbnailList) else None
        if self._foreign is not None and self._foreign is not widget:
            self._foreign._drop_row = None
            self._foreign.viewport().update()
        self._foreign = widget
        if widget is not None:
            widget._drop_row = target[2]
            widget.viewport().update()

    def mouseReleaseEvent(self, event) -> None:
        press, self._press = self._press, None
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            target = self._other_window_target(self.viewport().mapToGlobal(event.position().toPoint()))
            row = self.drop_row_at(event.position().toPoint())
            self._end_drag()
            pages = self.selected_pages()
            if target is not None:  # into another document: a copy goes there
                window, _widget, dest = target
                from planetpdf.core import pages as P

                data = P.pages_as_pdf(self._session, pages)
                name = self._session.path.name if self._session.path else "another document"
                QTimer.singleShot(0, lambda: window.receive_pages(data, dest, name, len(pages)))
                event.accept()
                return
            copy = bool((event.modifiers() | QApplication.keyboardModifiers()) & Qt.KeyboardModifier.ShiftModifier)
            signal = self.copyRequested if copy else self.moveRequested
            # (after this event: the handler rebuilds the list)
            QTimer.singleShot(0, lambda: signal.emit(pages, row))
            event.accept()
            return
        self._dragging = False
        if press is not None and press["deferred"]:  # a plain click on one of several
            item = press["item"]
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
            self.itemClicked.emit(item)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._dragging and event.key() == Qt.Key.Key_Escape:  # drop nothing
            self._dragging = False
            self._press = None
            self._end_drag()
            event.accept()
            return
        key = event.key()
        nav = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home,
               Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
        if key in nav and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            # Acrobat's page keys in the Pages panel too (user 2026-10-02): Left / Right are
            # previous / next page in the one-column panel, and the page shown follows
            if self._one_column() and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                event = QKeyEvent(event.type(), Qt.Key.Key_Up if key == Qt.Key.Key_Left else Qt.Key.Key_Down,
                                  event.modifiers())
            before = self.currentRow()
            super().keyPressEvent(event)
            row = self.currentRow()
            if not self.grid and row >= 0 and (row != before or key in (Qt.Key.Key_Home, Qt.Key.Key_End)):
                self.pageActivated.emit(row)
            return
        super().keyPressEvent(event)

    def _end_drag(self) -> None:
        self._drop_row = None
        self._show_on(None)
        if self._cursor_set:
            QApplication.restoreOverrideCursor()
            self._cursor_set = False
        self.viewport().update()
    def _is_internal(self, event) -> bool:
        return event.source() is self

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._session is not None and (self._is_internal(event) or _pdf_paths(event)):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._session is None or not (self._is_internal(event) or _pdf_paths(event)):
            event.ignore()
            return
        self._drop_row = self.drop_row_at(event.position().toPoint())
        moving = self._is_internal(event) and not self._copying(event)
        event.setDropAction(Qt.DropAction.MoveAction if moving else Qt.DropAction.CopyAction)  # ("+" while copying)
        event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event) -> None:
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        row = self.drop_row_at(event.position().toPoint())
        self._drop_row = None
        self.viewport().update()
        if self._is_internal(event):
            pages = self.selected_pages()
            signal = self.copyRequested if self._copying(event) else self.moveRequested
            # Defer until the drag has fully finished: the handler rebuilds this list.
            QTimer.singleShot(0, lambda: signal.emit(pages, row))
        else:
            paths = _pdf_paths(event)
            if not paths:
                event.ignore()
                return
            QTimer.singleShot(0, lambda: self.filesDropped.emit(paths, row))
        # CopyAction, never MoveAction: with MoveAction Qt would delete the dragged rows itself.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None or self.count() == 0:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QPen(DROP_LINE, 3))
        if self.grid and not self._one_column():  # pages side by side: a vertical line between them
            if self._drop_row < self.count():
                rect = self.visualItemRect(self.item(self._drop_row))
                x = rect.left() - self.spacing() // 2
            else:
                rect = self.visualItemRect(self.item(self.count() - 1))
                x = rect.right() + self.spacing() // 2
            painter.drawLine(x, rect.top(), x, rect.bottom())
        else:
            if self._drop_row < self.count():
                y = self.visualItemRect(self.item(self._drop_row)).top() - self.spacing() // 2
            else:
                y = self.visualItemRect(self.item(self.count() - 1)).bottom() + self.spacing() // 2
            painter.drawLine(4, y, self.viewport().width() - 4, y)
        painter.end()

    # --- rendering -------------------------------------------------------------

    def _scale_for(self, width: float, height: float) -> float:
        return min(self.thumb_w / width, self.thumb_h / height)

    def _placeholder(self, width: float, height: float) -> QIcon:
        scale = self._scale_for(width, height)
        pixmap = QPixmap(max(1, round(width * scale)), max(1, round(height * scale)))
        pixmap.fill(QColor("white"))
        return QIcon(_framed(pixmap, self.thumb_w, self.thumb_h))

    def _visible_rows(self) -> list[int]:
        first = self.indexAt(self.viewport().rect().topLeft()).row()
        last = self.indexAt(self.viewport().rect().bottomLeft()).row()
        if first < 0:
            return []
        if last < 0:
            last = self.count() - 1
        return [r for r in range(first, last + 1) if r in self._unrendered]

    def _render_some(self) -> None:
        if self.grid and not self.isVisible():  # the Organize grid renders only while shown
            self._timer.stop()
            return
        if self._session is None or not self._unrendered:
            self._timer.stop()
            return
        rows = self._visible_rows() or sorted(self._unrendered)
        for index in rows[:RENDERS_PER_TICK]:
            self._unrendered.discard(index)
            scale = self._scale_for(*self._session.page_size(index)) * self.devicePixelRatioF()
            pixmap = to_pixmap(render_page(self._session, index, scale))
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
            self.item(index).setIcon(QIcon(_framed(pixmap, self.thumb_w, self.thumb_h)))


def _framed(pixmap: QPixmap, box_w: int = THUMB_W, box_h: int = THUMB_H) -> QPixmap:
    """Border the page and center it on a fixed box_w x box_h transparent canvas, so
    portrait and landscape pages line up and highlight identically when selected."""
    dpr = pixmap.devicePixelRatio()
    canvas = QPixmap(round(box_w * dpr), round(box_h * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    w = pixmap.width() / dpr
    h = pixmap.height() / dpr
    x = (box_w - w) / 2
    y = (box_h - h) / 2
    painter = QPainter(canvas)
    painter.drawPixmap(QRectF(x, y, w, h), pixmap, QRectF(pixmap.rect()))
    painter.setPen(QPen(QColor(0, 0, 0, 110), 0))
    painter.drawRect(QRectF(x, y, w - 1 / dpr, h - 1 / dpr))
    painter.end()
    return canvas
