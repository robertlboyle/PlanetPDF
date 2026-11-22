"""View ▸ Rulers & Grids ▸ Rulers (Acrobat): inch rulers along the top and the left of
the page view, measuring from the top-left corner of the page under the mouse (as it is
shown). Drag from a ruler onto a page to place a guide there (View ▸ Rulers & Grids ▸
Guides); right-click a ruler to clear the guides."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QMenu, QWidget

if TYPE_CHECKING:
    from planetpdf.ui.viewer.page_view import PageView

RULER_SIZE = 20  # pixels
INCH = 72.0  # points


class Ruler(QWidget):
    def __init__(self, view: PageView, orientation: str) -> None:
        super().__init__(view)
        self._view = view
        self.orientation = orientation  # "h" (the top ruler) or "v" (the left one)
        self._marker: int | None = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.SizeVerCursor if orientation == "h" else Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag onto a page to add a guide. Right-click to clear the guides.")
        self.hide()

    def set_marker(self, view_pos: QPoint) -> None:
        self._marker = view_pos.x() if self.orientation == "h" else view_pos.y()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Window))
        text_color = palette.color(QPalette.ColorRole.WindowText)
        origin, scale = self._view.ruler_origin()
        start = origin.x() if self.orientation == "h" else origin.y()
        length = self.width() if self.orientation == "h" else self.height()
        per_inch = INCH * scale  # pixels per inch at this zoom
        steps = 8 if per_inch >= 64 else 4 if per_inch >= 32 else 2 if per_inch >= 16 else 1
        font = QFont(self.font())
        font.setPointSizeF(7)
        painter.setFont(font)
        first = int((0 - start) // (per_inch / steps)) - 1
        last = int((length - start) // (per_inch / steps)) + 1
        for n in range(first, last + 1):
            pos = start + n * per_inch / steps
            if not -1 <= pos <= length + 1:
                continue
            major = n % steps == 0
            half = steps >= 2 and n % (steps // 2) == 0
            tick = RULER_SIZE if major else RULER_SIZE * (0.45 if half else 0.25)
            painter.setPen(QPen(text_color, 1))
            if self.orientation == "h":
                painter.drawLine(int(pos), RULER_SIZE, int(pos), int(RULER_SIZE - tick))
                if major:
                    painter.drawText(int(pos) + 2, 9, str(n // steps))
            else:
                painter.drawLine(RULER_SIZE, int(pos), int(RULER_SIZE - tick), int(pos))
                if major:
                    painter.save()
                    painter.translate(9, int(pos) + 2)
                    painter.rotate(90)
                    painter.drawText(0, 0, str(n // steps))
                    painter.restore()
        if self._marker is not None:
            painter.setPen(QPen(QColor(220, 40, 40), 1))
            if self.orientation == "h":
                painter.drawLine(self._marker, 0, self._marker, RULER_SIZE)
            else:
                painter.drawLine(0, self._marker, RULER_SIZE, self._marker)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
        edge = QRect(self.rect())
        if self.orientation == "h":
            painter.drawLine(edge.bottomLeft(), edge.bottomRight())
        else:
            painter.drawLine(edge.topRight(), edge.bottomRight())

    # --- dragging a guide out -------------------------------------------------------------------

    def _guide_axis(self) -> str:
        return "h" if self.orientation == "h" else "v"  # (the top ruler gives horizontal guides)

    def _view_pos(self, event) -> QPoint:
        return self._view.viewport().mapFromGlobal(event.globalPosition().toPoint())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        found = self._view.guide_position(self._guide_axis(), self._view_pos(event))
        self._view.preview_guide((self._guide_axis(), found[1]) if found else None)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._view.preview_guide(None)
        found = self._view.guide_position(self._guide_axis(), self._view_pos(event))
        if found is not None:  # (let go back over the ruler, or off the pages: no guide)
            self._view.add_guide(self._guide_axis(), found[1])

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        clear = menu.addAction("Clear Guides", self._view.clear_guides)
        clear.setEnabled(bool(self._view.guides))
        menu.exec(event.globalPos())
