"""View ▸ Zoom tools that only change the view (Acrobat; user screenshot 2026-10-02):
the Loupe Tool, Pan & Zoom and Reflow. Nothing here edits the document."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAccessible, QColor, QPainter, QPen, QTextCursor, QTransform
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.render import render_area
from planetpdf.ui.viewer.qimage import to_pixmap

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

LOUPE_SIZE = QSize(400, 260)  # the close-up, screen pixels
LOUPE_ZOOMS = (100, 150, 200, 300, 400, 600, 800, 1200, 1600, 2400, 3200, 6400)  # percent
LOUPE_START = 4  # 400 %
MARKER_COLOR = QColor(220, 40, 40)


class LoupeWindow(QWidget):
    """Loupe Tool: a separate small window with a close-up of the spot clicked (or dragged
    over) on the page; a red box on the page shows which part it is."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, Qt.WindowType.Tool)
        self.setWindowTitle("Loupe Tool")
        self._window = window
        self._page: int | None = None
        self._point = QPointF()
        self._marker: QGraphicsRectItem | None = None
        self.image = QLabel(self)
        self.image.setFixedSize(LOUPE_SIZE)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet("background: white; border: 1px solid #888;")
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, len(LOUPE_ZOOMS) - 1)
        self.slider.setValue(LOUPE_START)
        self.slider.setToolTip("Magnification")
        self.zoom_label = QLabel(self)
        self.zoom_label.setMinimumWidth(48)
        self.slider.valueChanged.connect(lambda _v: self.refresh())
        row = QHBoxLayout()
        row.addWidget(QLabel("Magnification:"))
        row.addWidget(self.slider, 1)
        row.addWidget(self.zoom_label)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image)
        layout.addLayout(row)
        window.view.loupeMoved.connect(self.show_at)

    @property
    def zoom(self) -> float:
        return LOUPE_ZOOMS[self.slider.value()] / 100

    def start(self) -> None:
        """The tool was picked: look at the middle of the current page until clicked."""
        view = self._window.view
        if self._page is None or self._page >= view.page_count:
            page = max(view.current_page, 0)
            w, h = self._window.session.page_size(page)
            self._page, self._point = page, QPointF(w / 2, h / 2)
        if not self.isVisible():
            corner = self._window.mapToGlobal(self._window.rect().topRight())
            self.adjustSize()
            self.move(corner.x() - self.width() - 60, corner.y() + 140)
        self.show()
        self.refresh()

    def show_at(self, page: int, point: QPointF) -> None:
        self._page, self._point = page, QPointF(point)
        if not self.isVisible():
            self.start()
        else:
            self.refresh()

    def area(self) -> QRectF:
        """What the close-up shows, in the page's displayed coordinates."""
        dpi = self._window.view.logicalDpiX() / 72.0
        w, h = LOUPE_SIZE.width() / (self.zoom * dpi), LOUPE_SIZE.height() / (self.zoom * dpi)
        if self._window.view.view_rotation in (90, 270):  # (the page is shown turned)
            w, h = h, w
        return QRectF(self._point.x() - w / 2, self._point.y() - h / 2, w, h)

    def refresh(self) -> None:
        self.zoom_label.setText(f"{LOUPE_ZOOMS[self.slider.value()]}%")
        session = self._window.session
        if session is None or self._page is None or self._page >= session.page_count:
            self.image.clear()
            self._set_marker(None)
            return
        area = self.area()
        dpr = self.devicePixelRatioF()
        scale = self.zoom * self._window.view.logicalDpiX() / 72.0 * dpr
        pixmap = to_pixmap(render_area(session, self._page, (area.left(), area.top(), area.right(), area.bottom()), scale))
        rotation = self._window.view.view_rotation
        if rotation:
            pixmap = pixmap.transformed(QTransform().rotate(rotation))
        pixmap.setDevicePixelRatio(dpr)
        self.image.setPixmap(pixmap)
        self._set_marker(area)

    def _set_marker(self, area: QRectF | None) -> None:
        import shiboken6

        if self._marker is not None and shiboken6.isValid(self._marker) and self._marker.scene() is not None:
            self._marker.scene().removeItem(self._marker)
        self._marker = None
        page = self._window.view.page_item(self._page) if area is not None and self._page is not None else None
        if page is None:
            return
        self._marker = QGraphicsRectItem(area, page)
        pen = QPen(MARKER_COLOR, 2)
        pen.setCosmetic(True)
        self._marker.setPen(pen)
        self._marker.setZValue(1000)

    def closeEvent(self, event) -> None:
        self._set_marker(None)
        if self._window.view.view_tool == "loupe":
            self._window.view.set_view_tool(None)
        super().closeEvent(event)


class _PanCanvas(QWidget):
    """The current page, small, with the part the window shows as a red box; drag the
    box (or click) to move around the page."""

    def __init__(self, window: MainWindow, parent: QWidget) -> None:
        super().__init__(parent)
        self._window = window
        self.setMinimumSize(200, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _layout(self) -> tuple[QRectF, QRectF] | None:
        """(the page's scene rect, where it's drawn in this widget)."""
        view = self._window.view
        page = view.page_item(max(view.current_page, 0))
        if page is None:
            return None
        source = page.mapRectToScene(page.boundingRect())
        avail = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        k = min(avail.width() / source.width(), avail.height() / source.height())
        size = QSize(int(source.width() * k), int(source.height() * k))
        target = QRectF(avail.center().x() - size.width() / 2, avail.center().y() - size.height() / 2,
                        size.width(), size.height())
        return source, target

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0x60, 0x63, 0x66))
        layout = self._layout()
        if layout is None:
            return
        source, target = layout
        view = self._window.view
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        view.scene().render(painter, target, source)
        shown = view.mapToScene(view.viewport().rect()).boundingRect() & source
        if not shown.isEmpty():
            k = target.width() / source.width()
            box = QRectF(target.left() + (shown.left() - source.left()) * k, target.top() + (shown.top() - source.top()) * k,
                         shown.width() * k, shown.height() * k)
            painter.setPen(QPen(MARKER_COLOR, 2))
            painter.setBrush(QColor(220, 40, 40, 30))
            painter.drawRect(box)

    def _pan_to(self, pos: QPointF) -> None:
        layout = self._layout()
        if layout is None:
            return
        source, target = layout
        k = source.width() / target.width()
        self._window.view.centerOn(QPointF(source.left() + (pos.x() - target.left()) * k,
                                           source.top() + (pos.y() - target.top()) * k))

    def mousePressEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._pan_to(event.position())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pan_to(event.position())

    def mouseReleaseEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class PanZoomWindow(QWidget):
    """Pan & Zoom: a small window with the whole page and a box for the part on screen."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, Qt.WindowType.Tool)
        self.setWindowTitle("Pan & Zoom")
        self._window = window
        self.canvas = _PanCanvas(window, self)
        bar = QHBoxLayout()
        for action, icon in ((window.act_prev, "circle-arrow-up"), (window.act_next, "circle-arrow-down"),
                             (window.act_zoom_out, "circle-minus"), (window.act_zoom_in, "circle-plus")):
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            from planetpdf.ui import fa

            button.setIcon(fa.icon(icon))
            bar.addWidget(button)
        self.zoom_label = QLabel(self)
        bar.addWidget(self.zoom_label)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(bar)
        self.resize(240, 340)
        view = window.view
        for signal in (view.horizontalScrollBar().valueChanged, view.verticalScrollBar().valueChanged,
                       view.zoomChanged, view.currentPageChanged):
            signal.connect(self._changed)

    def _changed(self, *_args) -> None:
        if self.isVisible():
            self.zoom_label.setText(f"{round(self._window.view.zoom * 100)}%")
            self.canvas.update()

    def start(self) -> None:
        if not self.isVisible():
            corner = self._window.mapToGlobal(self._window.rect().topRight())
            self.move(corner.x() - self.width() - 60, corner.y() + 140)
        self.show()
        self.raise_()
        self._changed()


def reading_order(page) -> list[tuple[str, str]]:
    """The page's text as (tag, text) paragraphs in reading order, for Reflow and screen readers:
    "h2" for a heading (a block in larger type than the page's usual text, or bold and short),
    "p" for a paragraph, and a "[Picture]" paragraph for each picture."""
    blocks = page.get_text("dict", sort=True)["blocks"]
    sizes: dict[float, int] = {}
    for block in blocks:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                sizes[round(span["size"], 1)] = sizes.get(round(span["size"], 1), 0) + len(span["text"].strip())
    body = max(sizes, key=sizes.get) if sizes else 0
    out = []
    for block in blocks:
        if block.get("type") == 1:
            out.append(("p", "[Picture]"))
            continue
        text, biggest, bold = "", 0.0, True
        for line in block.get("lines", ()):
            spans = [s for s in line["spans"] if s["text"].strip()]
            line_text = "".join(s["text"] for s in line["spans"]).strip()
            if not line_text:
                continue
            biggest = max([biggest] + [s["size"] for s in spans])
            bold = bold and all(s["flags"] & 16 for s in spans)
            if text.endswith("-") and line_text[:1].islower():  # a word broken with a hyphen
                text = text[:-1] + line_text
            else:
                text = f"{text} {line_text}" if text else line_text
        if not text:
            continue
        heading = body and len(text) <= 120 and (biggest >= body * 1.15 or (bold and len(text) <= 60 and biggest >= body))
        out.append(("h2" if heading else "p", text))
    return out


class ReflowView(QTextBrowser):
    """View ▸ Zoom ▸ Reflow (Ctrl+4): the current page's text, rewrapped to the window's
    width (for reading on a narrow window or at a large size). Left / Right, Page Up /
    Down at the top / bottom, turn the page; Ctrl+4 again goes back to the page."""

    pageStep = Signal(int)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.setOpenLinks(False)
        self.document().setDocumentMargin(24)
        self.setAccessibleDescription("Page Down at the end or Ctrl+Page Down turns the page; Ctrl+4 goes back to the page")

    def loadResource(self, kind, url):  # noqa: N802 (Qt)
        return None  # (the page's own words, escaped: nothing is ever loaded; security review 2026-11-25)

    def refresh(self) -> None:
        session = self._window.session
        if session is None:
            self.clear()
            return
        index = max(self._window.view.current_page, 0)
        size = max(9, round(12 * self._window.view.zoom))
        parts = [f'<p style="color:#888; font-size:{max(8, size - 3)}pt">Page {index + 1} of {session.page_count}</p>']
        paragraphs = reading_order(session.doc[index])
        parts += [f"<{tag}>{html.escape(text)}</{tag}>" for tag, text in paragraphs]
        if all(text == "[Picture]" for _tag, text in paragraphs):
            parts.append("<p><i>(No text on this page. If it is a scan, Scan &amp; OCR, Recognize Text "
                         "makes its words readable.)</i></p>")
        self.setHtml(f'<div style="font-size:{size}pt">{"".join(parts)}</div>')
        self.setAccessibleName(f"Text of page {index + 1} of {session.page_count}")
        # with a screen reader running: a caret the arrow keys move, to read by word, line and
        # paragraph (without one, the arrow keys scroll and turn pages, as before)
        flags = Qt.TextInteractionFlag.TextBrowserInteraction
        if QAccessible.isActive():
            flags |= Qt.TextInteractionFlag.TextSelectableByKeyboard
        self.setTextInteractionFlags(flags)
        self.moveCursor(QTextCursor.MoveOperation.Start)  # (a screen reader starts at the top)

    def keyPressEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not event.modifiers() and not QAccessible.isActive():
            self.pageStep.emit(-1 if key == Qt.Key.Key_Left else 1)  # (with a screen reader they move the caret)
            return
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp) and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.pageStep.emit(1 if key == Qt.Key.Key_PageDown else -1)
            return
        if key == Qt.Key.Key_PageDown and bar.value() >= bar.maximum():
            self.pageStep.emit(1)
            return
        if key == Qt.Key.Key_PageUp and bar.value() <= bar.minimum():
            self.pageStep.emit(-1)
            return
        super().keyPressEvent(event)
