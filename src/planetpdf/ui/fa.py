"""Font Awesome icons.

Only the icons PlanetPDF uses are bundled, as SVGs copied from the Font Awesome packages by
``scripts/update_icons.py`` and drawn in the menu-icon gray like Acrobat's: **Font Awesome
Free** in ``resources/icons/free/`` (every copy; free to share), and **Font Awesome Pro** in
``resources/icons/fa/`` for builds made with a Pro license (Planet Networks'; never in a
public copy). An icon comes from ``fa/`` when it's there, else from ``free/`` (user
2026-10-24). ``icon(name)`` gives an empty icon for a name that isn't bundled, so a missing
icon never breaks a menu.

Each icon also has a white "on" version: a button that's switched on (the open tool, the
Select / Hand tool) shows reversed, white on a dark tile (``CHECKED_STYLE``; user request
2026-10-01: the red highlight hid the gray icon).
"""

from __future__ import annotations

import os
from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from planetpdf.core.resources import resources_dir

GRAY = "#6b6b6b"  # (as bundled)
ON_COLOR = "#ffffff"
SIZES = (16, 20, 24, 32, 48)
def checked_style(dark: bool = False, *, contrast: bool = False) -> str:
    """For toolbars: a switched-on button is a tile with the white icon on it (dark gray
    on light themes; bright blue on dark ones, where a dark tile wouldn't stand out), and its
    text in bold (user 2026-10-17: the tool that's on must stand out). In a contrast theme:
    the system's highlight colors, and a border."""
    if contrast:
        return ("QToolButton:checked { background-color: palette(highlight); color: palette(highlighted-text); "
                "border: 2px solid palette(highlighted-text); border-radius: 4px; font-weight: bold; }")
    tile, edge, hover = ("#2f7fd1", "#1d5fa3", "#3d8fe3") if dark else ("#3a3f47", "#22262c", "#4a505a")
    return (
        f"QToolButton:checked {{ background-color: {tile}; border: 1px solid {edge}; border-radius: 4px; color: white; "
        f"font-weight: bold; }}"
        f"QToolButton:checked:hover {{ background-color: {hover}; }}"
    )


CHECKED_STYLE = checked_style(False)


def _folders() -> tuple[str, ...]:
    """Where icons are looked for, in order. ``PLANETPDF_ICONS=free`` uses only Free, to see or
    test what a copy without Pro icons looks like."""
    return ("free",) if os.environ.get("PLANETPDF_ICONS", "").lower() == "free" else ("fa", "free")


def icons_dir():
    """The folder icons come from: Pro (``fa/``) when this copy has it, else Free (``free/``)."""
    pro = resources_dir() / "icons" / "fa"
    if "fa" in _folders() and pro.is_dir() and any(pro.glob("*.svg")):
        return pro
    return resources_dir() / "icons" / "free"


def icon_path(name: str):
    """The SVG for ``name``: Pro's if bundled, else Free's (its stand-in when Free lacks it)."""
    for folder in _folders():
        path = resources_dir() / "icons" / folder / f"{name}.svg"
        if path.is_file():
            return path
    return resources_dir() / "icons" / "free" / f"{name}.svg"  # (missing: callers check is_file)


def _pixmaps(svg: bytes) -> list[QPixmap]:
    renderer = QSvgRenderer(QByteArray(svg))
    out = []
    for size in SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        box = renderer.viewBoxF()
        scale = size / max(box.width(), box.height(), 1)
        w, h = box.width() * scale, box.height() * scale
        renderer.render(painter, QRectF((size - w) / 2, (size - h) / 2, w, h))
        painter.end()
        out.append(pixmap)
    return out


_default_color: list[str | None] = [None]  # (a contrast theme's text color; None = the gray)


def set_default_color(color: str | None) -> None:
    """Icons drawn from now on in ``color`` instead of the gray (a contrast theme)."""
    if _default_color[0] != color:
        _default_color[0] = color
        _icon.cache_clear()


def icon(name: str | None, color: str | None = None) -> QIcon:
    """The icon ``name`` (in ``color``, else the gray or a contrast theme's color)."""
    return _icon(name, color or _default_color[0])


icon.cache_clear = lambda: _icon.cache_clear()  # (tests)


@cache
def _icon(name: str | None, color: str | None = None) -> QIcon:
    """``color``: drawn in that color instead of the menu gray (the tool strip: each tool
    in its own color, like Acrobat; user 2026-10-03). Switched on, it's white either way."""
    if not name:
        return QIcon()
    path = icon_path(name)
    if not path.is_file():
        return QIcon()
    if color is None:
        result = QIcon(str(path))
    else:
        result = QIcon()
        tinted = path.read_bytes().replace(f'fill="{GRAY}"'.encode(), f'fill="{color}"'.encode(), 1)
        for pixmap in _pixmaps(tinted):
            result.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.Off)
            result.addPixmap(pixmap, QIcon.Mode.Active, QIcon.State.Off)
    white = path.read_bytes().replace(f'fill="{GRAY}"'.encode(), f'fill="{ON_COLOR}"'.encode(), 1)
    for pixmap in _pixmaps(white):  # the reversed look of a switched-on button
        result.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.On)
        result.addPixmap(pixmap, QIcon.Mode.Active, QIcon.State.On)
    return result


def has(name: str) -> bool:
    return icon_path(name).is_file()
