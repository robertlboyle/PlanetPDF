"""Tool cursors drawn from the bundled icons (user 2026-10-24: "show the correct cursor for the
tool/function that is currently in use"): the pen for free-form drawing (Acrobat's pencil),
the magnifier with + for Marquee Zoom and the plain magnifier for Dynamic Zoom. Black with a
white edge, so they show on any page; the hot spot is the pen's tip / the lens's center."""

from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QCursor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from planetpdf.ui import fa

SIZE = 24  # (device-independent pixels, like the system's cursors)
# name -> (icon, hot spot as a fraction of the size); Font Awesome draws the pen's tip at the
# bottom left and the lens in the top left two thirds
CURSORS = {
    "pen": ("pen", (0.06, 0.94)),
    "zoom_in": ("magnifying-glass-plus", (0.40, 0.40)),
    "zoom": ("magnifying-glass", (0.40, 0.40)),
}


def _render(svg: bytes, color: str) -> QPixmap:
    tinted = svg.replace(f'fill="{fa.GRAY}"'.encode(), f'fill="{color}"'.encode(), 1)
    renderer = QSvgRenderer(QByteArray(tinted))
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    box = renderer.viewBoxF()
    inner = SIZE - 4  # (room for the white edge)
    scale = inner / max(box.width(), box.height(), 1)
    w, h = box.width() * scale, box.height() * scale
    renderer.render(painter, QRectF((SIZE - w) / 2, (SIZE - h) / 2, w, h))
    painter.end()
    return pixmap


@cache
def cursor(name: str) -> QCursor:
    """The tool cursor ``name`` (a key of ``CURSORS``); the crosshair when its icon is missing."""
    icon, (hx, hy) = CURSORS[name]
    path = fa.icon_path(icon)
    if not path.is_file():
        return QCursor(Qt.CursorShape.CrossCursor)
    svg = path.read_bytes()
    edge, ink = _render(svg, "#ffffff"), _render(svg, "#1a1a1a")
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        painter.drawPixmap(dx, dy, edge)
    painter.drawPixmap(0, 0, ink)
    painter.end()
    return QCursor(pixmap, round(hx * (SIZE - 1)), round(hy * (SIZE - 1)))
