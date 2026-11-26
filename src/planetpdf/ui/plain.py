"""Text from a document or a file is shown as written, never as rich text (security review
2026-11-25, finding 3).

Qt shows a tooltip, a label, or a message as rich text (HTML) whenever its first line looks like a
tag, and rich text loads the pictures it names. A PDF whose link address was
``http://a/<img src="file://SERVER/share/x.png">`` made Qt open that picture as soon as the
pointer rested on the link, and Windows signed in to the server to fetch it. So:

- ``escape`` / ``tip``: text from a document, made safe to show (``tip`` keeps line breaks);
- ``plain``: a label that only ever shows plain text;
- ``TextGuard`` (one per application, ``install``): the safety net for any place that still hands
  Qt such text. A tooltip that would load something (``risky``) is shown escaped instead, and a
  label about to be shown for the first time with such text is switched to plain text.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGraphicsView,
    QLabel,
    QMenu,
    QTabBar,
    QToolTip,
    QWidget,
)

# What makes Qt's rich text load something: a picture, a style sheet, a background picture.
_RISKY = re.compile(r"<\s*(img|link)\b|\bsrc\s*=|\bbackground(-image)?\s*[=:]|url\s*\(|@import", re.IGNORECASE)


def risky(text: object) -> bool:
    """True when ``text`` shown as rich text could make Qt load a file or a picture."""
    return isinstance(text, str) and "<" in text and _RISKY.search(text) is not None


def escape(text: object) -> str:
    """``text`` made safe to put into rich text (shown exactly as written)."""
    return html.escape(str(text), quote=False)


def tip(text: object) -> str:
    """A tooltip (or rich-text label) showing ``text`` exactly as written, line breaks kept."""
    text = "" if text is None else str(text)
    if "<" not in text and "&" not in text:
        return text  # (shown as plain text by Qt anyway)
    return "<p style='white-space:pre-wrap'>" + escape(text) + "</p>"


def plain(label: QLabel, text: object | None = None) -> QLabel:
    """``label`` shows only plain text (and ``text``, when given)."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    if text is not None:
        label.setText(str(text))
    return label


def _tooltip_text(widget: QWidget, pos) -> str:
    """The tooltip Qt would show for a ToolTip event at ``pos`` on ``widget``."""
    parent = widget.parentWidget()
    if isinstance(parent, QGraphicsView) and widget is parent.viewport():
        for item in parent.items(pos):
            if item.toolTip():
                return item.toolTip()
        return ""
    if isinstance(parent, QAbstractItemView) and widget is parent.viewport():
        index = parent.indexAt(pos)
        value = index.data(Qt.ItemDataRole.ToolTipRole) if index.isValid() else None
        return value if isinstance(value, str) else ""
    if isinstance(widget, QTabBar):
        i = widget.tabAt(pos)
        if i >= 0 and widget.tabToolTip(i):
            return widget.tabToolTip(i)
    if isinstance(widget, QMenu):
        action = widget.actionAt(pos)
        return action.toolTip() if action is not None and widget.toolTipsVisible() else ""
    return widget.toolTip()


class TextGuard(QObject):
    """Application event filter: no tooltip or label loads what document text names."""

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        kind = event.type()
        if kind == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            text = _tooltip_text(watched, event.pos())
            if risky(text):
                QToolTip.showText(event.globalPos(), tip(text), watched)
                return True
        elif kind == QEvent.Type.Polish and isinstance(watched, QLabel):
            if watched.textFormat() != Qt.TextFormat.PlainText and risky(watched.text()):
                watched.setTextFormat(Qt.TextFormat.PlainText)
        return False


def install(app: QApplication | None = None) -> None:
    """Starts guarding tooltips and labels (once per application)."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_planetpdf_text_guard", None) is not None:
        return
    guard = TextGuard(app)
    app.installEventFilter(guard)
    app._planetpdf_text_guard = guard
