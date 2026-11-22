"""Notes in pop-up boxes on the page (user 2026-10-04: "the box should have a line pointing to
the note and the note should be movable and stretchable … collapse then open when I click on
it").

A comment's text shows in a box joined to its item (a highlight, a sticky-note icon, a
shape…) by a line. The box is the PDF's own pop-up (/Popup: its /Rect and /Open, which
Acrobat uses too), drawn by PlanetPDF because MuPDF doesn't draw pop-ups. Its header (author
and date) moves it, the corner grip resizes it, "–" collapses it; clicking the item opens it
again; double-clicking the text edits it. The view (``PageView``) sends what happened to the
markup controller, which changes the PDF.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem

HEADER = 16.0  # points: the bar with the author and date (drag it to move the box)
GRIP = 10.0  # the corner that resizes it
BUTTON = 12.0  # "–" (collapse)
MIN_SIZE = (90.0, 50.0)
TEXT_SIZE = 9.0
Z = 30  # over the page, its items and their selection frames


@dataclass(frozen=True)
class NotePopup:
    """What the view needs to show one note box (displayed page coordinates)."""

    item_id: str
    rect: QRectF
    anchor: QRectF  # the item it belongs to
    title: str  # "Robert Boyle  10/4/2026 2:15 PM"
    text: str
    color: QColor  # the item's color (the box is a pale tint of it)
    replies: tuple = ()  # its thread (user 2026-10-04): (reply id, "author  date", text), oldest first


def pale(color: QColor) -> QColor:
    """The box's background: the item's color, mostly white."""
    return QColor(round(255 - (255 - color.red()) * 0.22), round(255 - (255 - color.green()) * 0.22),
                  round(255 - (255 - color.blue()) * 0.22))


class NoteAnchor(QGraphicsRectItem):
    """An invisible click area over a collapsed (or open) note's item: a click opens the
    box (outside Comment mode, where the item itself isn't selectable)."""

    def __init__(self, item_id: str, rect: QRectF, parent: QGraphicsItem) -> None:
        super().__init__(rect.adjusted(-2, -2, 2, 2), parent)
        self.item_id = item_id
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(Z - 1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to open the note")


class NotePopupItem(QGraphicsRectItem):
    """One open note: the box, and its line to the item (a sibling, so it can reach out of
    the box)."""

    def __init__(self, data: NotePopup, parent: QGraphicsItem) -> None:
        rect = QRectF(data.rect)
        rect.setHeight(max(rect.height(), fitted_height(rect.width(), data.text, data.replies)))  # (its text shows)
        super().__init__(rect, parent)
        self.data_ = data
        self.item_id = data.item_id
        self.setZValue(Z)
        self.setAcceptHoverEvents(True)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.line = QGraphicsLineItem(parent)
        pen = QPen(data.color.darker(140), 1.4)
        pen.setCosmetic(True)
        self.line.setPen(pen)
        self.line.setZValue(Z - 0.5)
        self.place_line()

    # --- geometry --------------------------------------------------------------------------------

    def header_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.left(), r.top(), r.width(), HEADER)

    def button_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.right() - BUTTON - 3, r.top() + (HEADER - BUTTON) / 2, BUTTON, BUTTON)

    def grip_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.right() - GRIP, r.bottom() - GRIP, GRIP, GRIP)

    def body_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.left() + 4, r.top() + HEADER + 2, r.width() - 8, r.height() - HEADER - 6)

    def boundingRect(self) -> QRectF:
        # (the shadow is drawn past the box: without this, dragging it left streaks behind)
        return self.rect().adjusted(-1, -1, SHADOW + 1, SHADOW + 1)

    def reply_rect(self) -> QRectF:
        """"Reply" in the header, left of "–" (Acrobat's thread reply)."""
        b = self.button_rect()
        return QRectF(b.left() - REPLY_WIDTH - 4, self.rect().top(), REPLY_WIDTH, HEADER)

    def blocks(self) -> list[tuple[str, str, QRectF]]:
        """The text's parts in the page's coordinates (`thread_blocks`)."""
        body = self.body_rect()
        return [(item, kind, r.translated(body.left(), body.top()))
                for item, kind, r in thread_blocks(body.width(), self.data_.text, self.data_.replies)]

    def text_target(self, point: QPointF) -> str:
        """Whose words a click in the text edits: a reply's, or the comment's own."""
        for item, _kind, r in self.blocks():
            if item and r.top() - 3 <= point.y() <= r.bottom():
                return item
        return self.item_id

    def part_at(self, point: QPointF) -> str:
        """What a press at ``point`` (the page's coordinates) does (user 2026-10-04): "close"
        ("–"), a corner ("tl" / "tr" / "bl" / "br": resize), "move" (the header or the edge
        of the box), or "text" (inside: edit the note)."""
        if self.button_rect().adjusted(-2, -2, 2, 2).contains(point):
            return "close"
        if self.reply_rect().contains(point):
            return "reply"
        r = self.rect()
        near_left, near_right = point.x() - r.left() <= GRIP, r.right() - point.x() <= GRIP
        near_top, near_bottom = point.y() - r.top() <= GRIP * 0.6, r.bottom() - point.y() <= GRIP
        if (near_left or near_right) and (near_top or near_bottom):
            return ("t" if near_top else "b") + ("l" if near_left else "r")
        if self.header_rect().contains(point) or not r.adjusted(EDGE, EDGE, -EDGE, -EDGE).contains(point):
            return "move"
        return "text"

    def set_box(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self.setRect(rect)
        self.place_line()
        self.update()

    def place_line(self) -> None:
        """From the box's nearest edge point to the nearest point of its item."""
        box, anchor = self.rect(), self.data_.anchor
        target = QPointF(min(max(box.center().x(), anchor.left()), anchor.right()),
                         min(max(box.center().y(), anchor.top()), anchor.bottom()))
        start = QPointF(min(max(target.x(), box.left()), box.right()), min(max(target.y(), box.top()), box.bottom()))
        if box.contains(target):  # (the box covers its item: no line)
            self.line.setLine(QLineF(start, start))
        else:
            self.line.setLine(QLineF(start, target))

    def detach(self) -> None:
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self.line)
            scene.removeItem(self)

    # --- drawing ---------------------------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self.rect()
        color = self.data_.color
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # a soft shadow, the box, its header
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(r.translated(SHADOW, SHADOW), 2, 2)
        border = QPen(color.darker(130), 0.8)
        painter.setPen(border)
        painter.setBrush(pale(color))
        painter.drawRoundedRect(r, 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        header = QPainterPath()
        header.addRoundedRect(self.header_rect(), 2, 2)
        painter.drawPath(header)
        luma = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        ink = QColor("#1a1a1a") if luma > 150 else QColor("white")  # (dark on yellow, white on blue)
        font = note_font()  # (no hinting: it's zoomed, spaces kept)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(ink)
        title_box = self.header_rect().adjusted(4, 0, -BUTTON - REPLY_WIDTH - 12, 0)
        painter.drawText(title_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(self.data_.title, Qt.TextElideMode.ElideRight,
                                                          int(title_box.width())))
        # "Reply" (underlined, like a link) and "–": collapse
        font.setBold(False)
        font.setUnderline(True)
        painter.setFont(font)
        painter.drawText(self.reply_rect(), Qt.AlignmentFlag.AlignCenter, "Reply")
        font.setUnderline(False)
        b = self.button_rect()
        painter.setPen(QPen(ink, 1.2))
        painter.drawLine(QPointF(b.left() + 3, b.center().y()), QPointF(b.right() - 3, b.center().y()))
        # the comment, then its replies (a thin line, who and when, the words)
        flags = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        for item, kind, block in self.blocks():
            if kind == "rule":
                painter.setPen(QPen(color.darker(130), 0.5))
                painter.drawLine(block.topLeft(), block.topRight())
            elif kind == "title":
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor("#555555"))
                painter.drawText(block, flags, self._reply_title(item))
            else:
                font.setBold(False)
                painter.setFont(font)
                painter.setPen(QColor("#1a1a1a"))
                text = self.data_.text if not item else self._reply_text(item)
                painter.drawText(block, flags, text or ("(no text: click to type)" if not item else ""))
        # the resize grip: three short diagonal strokes in the corner
        g = self.grip_rect()
        painter.setPen(QPen(color.darker(150), 0.8))
        for k in (3, 6, 9):
            painter.drawLine(QPointF(g.right() - k, g.bottom() - 1), QPointF(g.right() - 1, g.bottom() - k))

    def _reply_title(self, reply_id: str) -> str:
        return next((t for i, t, _x in self.data_.replies if i == reply_id), "")

    def _reply_text(self, reply_id: str) -> str:
        return next((x for i, _t, x in self.data_.replies if i == reply_id), "")

    # (the cursor over a box is set by the view, PageView._popup_cursor: a tool's cursor
    # would otherwise win over an item's)


SHADOW = 1.5
EDGE = 5.0  # points: the band round the box that moves it (inside it: the text, to edit)
REPLY_WIDTH = 30.0
CURSORS = {"close": Qt.CursorShape.PointingHandCursor, "reply": Qt.CursorShape.PointingHandCursor,
           "move": Qt.CursorShape.SizeAllCursor,
           "text": Qt.CursorShape.IBeamCursor, "tl": Qt.CursorShape.SizeFDiagCursor,
           "br": Qt.CursorShape.SizeFDiagCursor, "tr": Qt.CursorShape.SizeBDiagCursor,
           "bl": Qt.CursorShape.SizeBDiagCursor}


def note_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Arial", "Helvetica", "Noto Sans"])
    font.setPixelSize(round(TEXT_SIZE))
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def thread_blocks(width: float, text: str, replies=()) -> list[tuple[str, str, QRectF]]:
    """The note's text laid out at `width` (from the body's top-left): ("", "text", rect)
    for the comment, then for each reply ("", "rule", line), (id, "title", rect), (id,
    "text", rect)."""
    from PySide6.QtGui import QFontMetricsF

    metrics = QFontMetricsF(note_font())
    bold = note_font()
    bold.setBold(True)
    title_height = QFontMetricsF(bold).height()
    flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop)
    w = max(width, 10.0)

    def height_of(words: str) -> float:
        return metrics.boundingRect(QRectF(0, 0, w, 100000), flags, words or " ").height()

    out = [("", "text", QRectF(0, 0, w, height_of(text)))]
    y = out[0][2].bottom()
    for reply_id, _title, words in replies:
        y += 4
        out.append(("", "rule", QRectF(0, y, w, 0)))
        y += 3
        out.append((reply_id, "title", QRectF(0, y, w, title_height)))
        y += title_height
        out.append((reply_id, "text", QRectF(0, y, w, height_of(words))))
        y = out[-1][2].bottom()
    return out


def fitted_height(width: float, text: str, replies=()) -> float:
    """How tall a note's box must be for its text and replies at this width (user
    2026-10-04: it grows with its text): the header, the laid-out text, the margins."""
    blocks = thread_blocks(max(width - 8, 10), text, replies)
    return max(MIN_SIZE[1], HEADER + 2 + blocks[-1][2].bottom() + 6 + 2)


def resized(start: QRectF, origin: QPointF, pos: QPointF, bounds: QRectF, corner: str = "br", text: str = "",
            replies=()) -> QRectF:
    """The box dragged by a corner: never smaller than ``MIN_SIZE``, nor shorter than its
    text needs at the new width, nor off the page; the opposite corner stays."""
    dx, dy = pos.x() - origin.x(), pos.y() - origin.y()
    left, top, right, bottom = start.left(), start.top(), start.right(), start.bottom()
    if "l" in corner:
        left = min(max(bounds.left(), left + dx), right - MIN_SIZE[0])
    else:
        right = max(min(bounds.right(), right + dx), left + MIN_SIZE[0])
    if "t" in corner:
        top = min(max(bounds.top(), top + dy), bottom - MIN_SIZE[1])
    else:
        bottom = max(min(bounds.bottom(), bottom + dy), top + MIN_SIZE[1])
    need = fitted_height(right - left, text, replies)
    if bottom - top < need:  # (the text always fits: it grows downwards, or up from the bottom)
        if "t" in corner:
            top = max(bounds.top(), bottom - need)
        else:
            bottom = min(bounds.bottom(), top + need)
    return QRectF(left, top, right - left, bottom - top)


def moved(start: QRectF, origin: QPointF, pos: QPointF, bounds: QRectF) -> QRectF:
    """The box dragged by its header, kept on the page."""
    box = start.translated(pos - origin)
    box.moveLeft(min(max(box.left(), bounds.left()), bounds.right() - box.width()))
    box.moveTop(min(max(box.top(), bounds.top()), bounds.bottom() - box.height()))
    return box
