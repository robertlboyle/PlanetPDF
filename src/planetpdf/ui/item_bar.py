"""The small bar above a selected placed item (like Acrobat's Fill & Sign): smaller /
larger text, and delete. Stays the same size on screen whatever the zoom."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from planetpdf.ui import fa


def color_swatch(color: QColor, size: int = 16) -> QIcon:
    """The text color: an "A" drawn in that color (user 2026-10-01: color the A itself, no
    bar under it: it isn't an underline). A thin dark outline keeps white / pale colors
    visible."""
    from PySide6.QtGui import QFont, QPainterPath, QPen

    scale = 2  # (drawn at twice the size: crisp on high-DPI screens)
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont(painter.font())
    font.setPixelSize(int(size * scale * 0.95))
    font.setBold(True)
    path = QPainterPath()
    path.addText(0, 0, font, "A")
    box = path.boundingRect()
    path.translate((pixmap.width() - box.width()) / 2 - box.left(), (pixmap.height() - box.height()) / 2 - box.top())
    outline = QPen(QColor(0, 0, 0, 110), scale * 0.6)
    painter.setPen(outline)
    painter.setBrush(color)
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class ItemBar(QFrame):
    def __init__(self, *, on_smaller: Callable[[], None] | None, on_larger: Callable[[], None] | None,
                 on_delete: Callable[[], None], on_color: Callable[[], None] | None = None,
                 color: QColor | None = None, symbol: bool = False) -> None:
        super().__init__()
        self.setStyleSheet(
            "ItemBar { background: white; border: 1px solid #9aa7b8; border-radius: 4px; }"
            "QToolButton { border: none; padding: 1px 6px; font-weight: bold; }"
            "QToolButton:hover { background: #e3ecfa; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(0)
        self.smaller = self.larger = None
        if on_smaller is not None and on_larger is not None:
            if symbol:  # (a symbol: − / +, like Acrobat's Fill & Sign)
                self.smaller = self._button("−", "Smaller (Ctrl+[)", on_smaller, 12)
                self.larger = self._button("+", "Larger (Ctrl+])", on_larger, 12)
            else:
                self.smaller = self._button("A−", "Smaller text (Ctrl+[)", on_smaller, 9)
                self.larger = self._button("A+", "Larger text (Ctrl+])", on_larger, 12)
            layout.addWidget(self.smaller)
            layout.addWidget(self.larger)
        self.color = None
        if on_color is not None:
            self.color = self._button("", "Text color", on_color, 10)
            self.color.setIcon(color_swatch(color or QColor("black")))
            layout.addWidget(self.color)
        self.delete = self._button("", "Delete (Del)", on_delete, 10)
        self.delete.setIcon(fa.icon("trash"))  # the plain Font Awesome bin, not Windows' glossy one (user)
        layout.addWidget(self.delete)

    def _button(self, text: str, tip: str, slot, point_size: int) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setToolTip(tip)
        font = button.font()
        font.setPointSize(point_size)
        button.setFont(font)
        button.clicked.connect(slot)
        return button
