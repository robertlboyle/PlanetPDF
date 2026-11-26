"""Scene items drawn on top of pages: redaction-mark overlays and the drawing rubber band."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsSimpleTextItem

from planetpdf.ui import plain

MARK_FILL = QColor(0, 0, 0, 70)
MARK_PEN = QColor(210, 30, 30)
SELECTED_FILL = QColor(30, 120, 255, 70)
SELECTED_PEN = QColor(30, 120, 255)


class MarkOverlay(QGraphicsRectItem):
    """One rectangle of a redaction mark, in page-item (displayed page) coordinates."""

    def __init__(self, mark_id: str, rect: QRectF, parent: QGraphicsItem) -> None:
        super().__init__(rect, parent)
        self.mark_id = mark_id
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        pen = QPen(SELECTED_PEN if selected else MARK_PEN, 2 if selected else 1)
        pen.setCosmetic(True)
        if not selected:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(SELECTED_FILL if selected else MARK_FILL))
        self.setZValue(2 if selected else 1)


FIELD_FILL = QColor(204, 215, 255, 110)
FIELD_FOCUS_PEN = QColor(30, 90, 220)


class DropArrow(QGraphicsRectItem):
    """The ▾ button at the right of a dropdown (user 2026-10-02: "doesn't show the arrow to
    be clear that it is a dropdown"). Drawn by the viewer only: PDF dropdowns have no arrow,
    so printouts and other viewers stay as they were. Clicks go to the field under it."""

    def __init__(self, parent: QGraphicsItem, field_rect: QRectF, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(0.4)  # (under field / selection overlays, over the page)
        self.field_rect = QRectF(field_rect)
        # an empty dropdown says so (user 2026-10-02: "there is no visible text on the drop
        # down"); the viewer's hint only, the PDF stays empty
        self.placeholder = placeholder
        self.place(field_rect)

    def place(self, r: QRectF) -> None:
        width = max(8.0, min(r.height() * 0.9, 16.0, r.width() / 3))
        self.setRect(QRectF(r.right() - width, r.top(), width, r.height()))

    def boundingRect(self) -> QRectF:
        return self.field_rect if self.placeholder else super().boundingRect()

    def paint(self, painter, option, widget=None) -> None:
        if self.placeholder:
            f = self.field_rect
            font = QFont("Arial")
            font.setItalic(True)
            font.setPixelSize(max(1, round(min(f.height() * 0.6, 12))))
            painter.save()
            painter.setFont(font)
            painter.setPen(QColor(130, 130, 130))
            painter.drawText(f.adjusted(3, 0, -self.rect().width() - 2, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.placeholder)
            painter.restore()
        r = self.rect().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.save()
        painter.setPen(QPen(QColor(150, 150, 150), 0))
        painter.setBrush(QColor(236, 236, 236))
        painter.drawRect(r)
        side = min(r.width() * 0.5, r.height() * 0.4)
        cx, cy = r.center().x(), r.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(60, 60, 60))
        painter.drawPolygon([QPointF(cx - side / 2, cy - side / 4), QPointF(cx + side / 2, cy - side / 4),
                             QPointF(cx, cy + side / 3)])
        painter.restore()


class SignatureLook(QGraphicsRectItem):
    """An unsigned signature field as other viewers and printouts show it (the box with a
    line and "Sign here" that PlanetPDF stores: ``fields.draw_empty_signature``). MuPDF
    always draws its own red "SIGN" tag instead (sideways on turned pages), so the view
    covers that with the real look (user 2026-10-02: show fields as in the final PDF)."""

    def __init__(self, parent: QGraphicsItem, field_rect: QRectF) -> None:
        super().__init__(field_rect, parent)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(0.4)

    def boundingRect(self) -> QRectF:
        return self.rect().adjusted(-1.5, -1.5, 1.5, 1.5)  # (MuPDF's tag reaches just past the box)

    def paint(self, painter, option, widget=None) -> None:
        r = self.rect()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawRect(r.adjusted(-1.5, -1.5, 1.5, 1.5))  # (covers MuPDF's tag entirely)
        painter.setPen(QPen(QColor(89, 89, 89), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        size = max(3.0, min(8.0, r.height() * 0.22))
        base = r.bottom() - max(size * 1.4, r.height() * 0.28)
        painter.setPen(QPen(QColor(140, 140, 140), 0.6))
        painter.drawLine(QPointF(r.left() + 4, base), QPointF(r.right() - 4, base))
        font = QFont("Arial")
        font.setPixelSize(max(1, round(size)))
        painter.setFont(font)
        painter.setPen(QColor(115, 115, 115))
        painter.drawText(QPointF(r.left() + 4, base + size + 1), "Sign here")
        painter.restore()


class FieldOverlay(QGraphicsRectItem):
    """A clickable form-field area. Tinted when field highlighting is on."""

    def __init__(self, key: str, rect: QRectF, parent: QGraphicsItem, fillable: bool, look: str = "") -> None:
        """``look``: "text" (a text or date field: the I-beam, user 2026-10-24), "button" (a push
        button: the pointing hand, though it holds no value), or the dropdown / signature looks."""
        super().__init__(rect, parent)
        self.key = key
        self.fillable = fillable
        self.setZValue(0.5)
        if fillable and look == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif fillable or look == "button":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_state(highlighted=False, focused=False)

    def set_state(self, *, highlighted: bool, focused: bool) -> None:
        if focused:
            pen = QPen(FIELD_FOCUS_PEN, 2)
        else:
            pen = QPen(Qt.PenStyle.NoPen)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(FIELD_FILL if highlighted and self.fillable else Qt.GlobalColor.transparent))


MARKUP_SELECT_PEN = QColor(30, 120, 255)
HANDLE_SIZE = 7.0  # screen pixels (handles ignore zoom)
FIELD_DESIGN_FILL = QColor(170, 200, 255, 90)  # Prepare Form: Acrobat's light blue field boxes
FIELD_DESIGN_PEN = QColor(70, 120, 210, 200)
FIELD_DESIGN_TEXT = QColor(25, 60, 140)


class MarkupOverlay(QGraphicsRectItem):
    """Selection frame for a markup item; corner handles appear when selected."""

    show_bounds = False  # Edit PDF ▸ Show bounding boxes: a light frame round every item

    def __init__(self, item_id: str, rect: QRectF, parent: QGraphicsItem, movable: bool, keep_aspect: bool,
                 resizable: bool = True, stretchable: bool = False) -> None:
        super().__init__(rect, parent)
        self.item_id = item_id
        self.movable = movable
        self.keep_aspect = keep_aspect
        self.resizable = resizable  # (a line of page text moves but has no corner handles)
        self.stretchable = stretchable  # Shift + a corner changes the proportions (stamps)
        self.setZValue(1.5)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor if movable else Qt.CursorShape.PointingHandCursor)
        self.handles: list[QGraphicsRectItem] = []
        # Prepare Form: the field as it will look in the PDF; its tint and name only while
        # it's selected (user 2026-10-02: "show it like you are looking at the PDF unless the
        # item being edited is selected"); a light outline under the mouse
        self.tint: QColor | None = None
        self._label: QGraphicsSimpleTextItem | None = None
        self._selected = False
        self._hover = False
        self.set_selected(False)

    def set_label(self, text: str, tooltip: str = "", tint: QColor | None = FIELD_DESIGN_FILL) -> None:
        """Prepare Form: the field's name inside its box (when it fits; shown while
        selected), a tint (selected), a tooltip. (Dropdown arrows: ``DropArrow``.)"""
        self.tint = tint
        self.setToolTip(plain.tip(tooltip or text))
        if self._label is not None and self._label.scene() is not None:
            self._label.scene().removeItem(self._label)
        self._label = None

        r = self.rect()
        if text:
            font = QFont()
            font.setPointSizeF(7.0)
            shown = QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, max(r.width(), 60))
            self._label = QGraphicsSimpleTextItem(shown, self)
            self._label.setFont(font)
            self._label.setBrush(QBrush(FIELD_DESIGN_TEXT))
            self._label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._place_label()
        self.set_selected(False)

    def _place_label(self) -> None:
        """Just above the box, at its left (so it never covers what the field shows); inside
        at the top when the box is at the very top of the page."""
        r, box = self.rect(), self._label.boundingRect()
        above = r.top() - box.height() - 1
        self._label.setPos(r.left(), above if above >= 0 else r.top() + 1)

    def setRect(self, rect: QRectF) -> None:  # (moved / resized: the label goes along)
        super().setRect(rect)
        if self._label is not None:
            self._place_label()


    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        if self.tint is not None and not self._selected:
            self._style()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        if self.tint is not None and not self._selected:
            self._style()
        super().hoverLeaveEvent(event)

    def _style(self) -> None:
        selected = self._selected
        if self.tint is not None:  # (a form field in Prepare Form)
            if selected:
                pen = QPen(MARKUP_SELECT_PEN, 1.2)
                brush = QColor(self.tint)
            elif self._hover:
                pen = QPen(FIELD_DESIGN_PEN, 0.8)
                pen.setStyle(Qt.PenStyle.DashLine)
                brush = QColor(Qt.GlobalColor.transparent)
            else:  # as it will look in the PDF
                pen = QPen(QColor(Qt.GlobalColor.transparent), 0.8)
                brush = QColor(Qt.GlobalColor.transparent)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setBrush(QBrush(brush))
            if self._label is not None:
                self._label.setVisible(selected)
            return
        idle = QColor(120, 140, 170, 150) if self.show_bounds else QColor(Qt.GlobalColor.transparent)
        pen = QPen(MARKUP_SELECT_PEN if selected else idle, 1.2 if selected else 0.8)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(30, 120, 255, 18) if selected else Qt.GlobalColor.transparent))

    def set_selected(self, selected: bool, handles: bool = True) -> None:
        """``handles`` False: one of several selected objects (moved together, no resizing)."""
        self._selected = selected
        self._style()
        for handle in self.handles:
            if handle.scene() is not None:
                handle.scene().removeItem(handle)
        self.handles = []
        if selected and handles and self.movable and self.resizable:
            for corner in range(4):
                handle = QGraphicsRectItem(self)
                handle.setData(0, corner)
                handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                handle.setRect(-HANDLE_SIZE / 2, -HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE)
                handle.setBrush(QBrush(QColor("white")))
                handle_pen = QPen(MARKUP_SELECT_PEN, 1)
                handle_pen.setCosmetic(True)
                handle.setPen(handle_pen)
                handle.setCursor(
                    Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor
                )
                self.handles.append(handle)
            self.place_handles()

    def place_handles(self) -> None:
        r = self.rect()
        corners = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()]
        for handle, point in zip(self.handles, corners):
            handle.setPos(point)


class LinkOverlay(QGraphicsRectItem):
    """Clickable link area (invisible; pointing hand + tooltip with the target)."""

    def __init__(self, link_id: int, rect: QRectF, parent: QGraphicsItem, tooltip: str) -> None:
        super().__init__(rect, parent)
        self.link_id = link_id
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.GlobalColor.transparent))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(plain.tip(tooltip))  # (the link's address, as written)
        self.setZValue(0.4)


class SelectionBox(QGraphicsRectItem):
    """Edit PDF: the dashed box dragged around objects to select them all."""

    def __init__(self, parent: QGraphicsItem) -> None:
        super().__init__(parent)
        pen = QPen(MARKUP_SELECT_PEN, 1)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(30, 120, 255, 28)))
        self.setZValue(4)


class RubberBand(QGraphicsRectItem):
    def __init__(self, parent: QGraphicsItem) -> None:
        super().__init__(parent)
        pen = QPen(SELECTED_PEN, 1.5)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(0, 0, 0, 90)))
        self.setZValue(3)
