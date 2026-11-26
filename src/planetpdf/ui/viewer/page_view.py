"""Continuous-scroll page view.

Scene units are PDF points. Pages are stacked vertically and centered on x = 0.
The view transform applies zoom * (screen DPI / 72), so 100 % is physical size.

Pages render lazily, one per event-loop tick, only while visible. During a zoom
gesture the previous pixmaps are drawn scaled; crisp re-renders start once the
zoom has settled.
"""

from __future__ import annotations

import bisect
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
import shiboken6
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from planetpdf.core.document import PdfSession
from planetpdf.core.render import render_page
from planetpdf.ui.viewer.overlays import (
    DropArrow,
    FieldOverlay,
    LinkOverlay,
    MarkOverlay,
    MarkupOverlay,
    RubberBand,
    SelectionBox,
    SignatureLook,
)
from planetpdf.ui.viewer.qimage import to_pixmap

# page index -> [(mark id, [rect in displayed page coordinates, ...]), ...]
OverlayMap = dict[int, list[tuple[str, list[QRectF]]]]
# page index -> [(field key, rect in displayed page coordinates, fillable[, dropdown]), ...]
FieldMap = dict[int, list[tuple]]
# page index -> [(item id, rect in displayed page coordinates, movable, keep aspect), ...]
MarkupMap = dict[int, list[tuple[str, QRectF, bool, bool]]]


@dataclass
class DrawStyle:
    """How live drawing previews look: matches the annotation that will be created."""

    color: QColor = field(default_factory=lambda: QColor(210, 30, 30))
    width: float = 2.0  # points (zooms with the page, like the real annotation)
    fill: QColor | None = None
    opacity: float = 1.0
    text_markup: str = "highlight"  # highlight | underline | strikeout


def _pen(style: DrawStyle) -> QPen:
    pen = QPen(style.color, style.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    return pen


CALLOUT_BOX = (160.0, 36.0)  # a new callout's text box (points); it grows as you type
POLY_TOOLS = ("polygon", "polyline")
CLOSE_PIXELS = 8  # a polygon click this close (on screen) to the first corner closes it


def callout_box(tip: QPointF, end: QPointF) -> QRectF:
    """Where a callout's text box goes when dragged from ``tip`` to ``end``: from ``end``
    away from the tip (so the leader line never crosses the box)."""
    w, h = CALLOUT_BOX
    x = end.x() if end.x() >= tip.x() else end.x() - w
    y = end.y() - h / 2
    return QRectF(x, y, w, h)


def callout_preview_path(tip: QPointF, end: QPointF) -> QPainterPath:
    box = callout_box(tip, end)
    attach = QPointF(box.left() if end.x() >= tip.x() else box.right(), box.center().y())
    knee = QPointF(attach.x() - 12 if end.x() >= tip.x() else attach.x() + 12, attach.y())
    path = QPainterPath(tip)
    path.lineTo(knee)
    path.lineTo(attach)
    path.addRect(box)
    return path


def arrow_path(a: QPointF, b: QPointF, width: float) -> QPainterPath:
    """A line from a to b with a closed arrowhead at b (like PDF's ClosedArrow)."""
    import math

    path = QPainterPath(a)
    path.lineTo(b)
    length = max(6.0, width * 4.0)
    angle = math.atan2(b.y() - a.y(), b.x() - a.x())
    left = QPointF(b.x() - length * math.cos(angle - math.pi / 7), b.y() - length * math.sin(angle - math.pi / 7))
    right = QPointF(b.x() - length * math.cos(angle + math.pi / 7), b.y() - length * math.sin(angle + math.pi / 7))
    head = QPainterPath(b)
    head.lineTo(left)
    head.lineTo(right)
    head.closeSubpath()
    path.addPath(head)
    return path


def _on_widget(items) -> bool:
    """A widget on the page is under the mouse: an editor, the item bar, or a dropdown's
    open list of choices. On the real display Qt puts that list on the page as a proxy of
    its own (offscreen it's a separate window), so checking only for the editor let a click
    on a choice reach the page: "clicked away", the editor closed, the pick lost (user bug
    2026-10-04, "it still says Choose…")."""
    return any(isinstance(i, QGraphicsProxyWidget) for i in items)


def _resized(start: QRectF, corner: int, pos: QPointF, keep_aspect: bool) -> QRectF:
    """``start`` with one corner (0 TL, 1 TR, 2 BL, 3 BR) dragged to ``pos``; the
    opposite corner stays put."""
    anchor = [start.bottomRight(), start.bottomLeft(), start.topRight(), start.topLeft()][corner]
    rect = QRectF(anchor, pos).normalized()
    if keep_aspect and start.height() > 0 and rect.height() > 0:
        aspect = start.width() / start.height()
        width = max(rect.width(), rect.height() * aspect)
        height = width / aspect
        x = anchor.x() - width if pos.x() < anchor.x() else anchor.x()
        y = anchor.y() - height if pos.y() < anchor.y() else anchor.y()
        rect = QRectF(x, y, width, height)
    if rect.width() < 4 or rect.height() < 4:
        return QRectF(start)
    return rect
MIN_DRAG = 3.0  # points; smaller drags are treated as clicks
WHEEL_TURN_PAUSE = 0.35  # seconds: page-at-a-time views turn at most one page per flick
# Automatically Scroll: + / - step through these (screen pixels per second)
AUTO_SCROLL_SPEEDS = (15, 25, 40, 60, 90, 130, 180, 250, 350, 500)
AUTO_SCROLL_START = 3

PAGE_GAP = 12.0  # points between pages
# View ▸ Rulers & Grids (Acrobat's defaults: lines every inch, 4 subdivisions)
GRID_SPACING = 72.0  # points
GRID_SUBDIVISIONS = 4
GRID_COLOR = QColor(80, 140, 230, 90)
GRID_MINOR_COLOR = QColor(80, 140, 230, 40)
GUIDE_COLOR = QColor(0, 170, 200)
ALIGN_COLOR = QColor(230, 0, 140)  # the dotted guides while dragging (magenta, like most editors)
UNSNAPPED_MODES = ("ink", "text", "place", None)  # freehand, text selection, stamp ghost: never snapped
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
ZOOM_STEP = 1.2  # (Ctrl + mouse wheel: smooth)
# Zoom In / Zoom Out (Ctrl+= / Ctrl+-, the buttons) go through Acrobat's levels, so the same
# percentages come back each time (user 2026-11-21: 100, 120, 144, 173… was "not the same amount").
ZOOM_LEVELS = (0.125, 0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
# Rendered pages kept, by size (Preferences ▸ "Page cache", user 2026-10-07; it used to be
# 40 pages whatever their size: gigabytes at high zoom). Pages on screen are never dropped.
DEFAULT_CACHE_MB = 200
SETTLE_MS = 150
BACKGROUND = QColor(0x60, 0x63, 0x66)


def next_zoom(zoom: float, *, up: bool) -> float:
    """The next of ``ZOOM_LEVELS`` above (or below) ``zoom``; from a fitted or typed zoom, the nearest
    level in that direction."""
    if up:
        return next((z for z in ZOOM_LEVELS if z > zoom * 1.001), MAX_ZOOM)
    return next((z for z in reversed(ZOOM_LEVELS) if z < zoom / 1.001), MIN_ZOOM)


class ZoomMode(Enum):
    CUSTOM = "custom"
    FIT_WIDTH = "fit_width"
    FIT_PAGE = "fit_page"
    FIT_HEIGHT = "fit_height"  # the page's height fills the window (Acrobat's Fit Height)
    FIT_VISIBLE = "fit_visible"  # what's printed on the page fills the width (margins left out)


class LayoutMode(Enum):
    SINGLE = "single"  # one page per row (Acrobat's Enable Scrolling)
    TWO_UP = "two_up"  # facing pages, two per row (Two Page Scrolling)
    GRID_2X2 = "grid_2x2"  # two per row; Fit Page fits two rows (four pages)
    # Acrobat's page-at-a-time views (user 2026-10-02): the same rows, but only the
    # current one is shown; Page Down / Up (and the wheel past its edge) turn the page(s)
    SINGLE_PAGE = "single_page"  # Single Page View
    TWO_PAGE = "two_page"  # Two Page View

    @property
    def columns(self) -> int:
        return 1 if self in (LayoutMode.SINGLE, LayoutMode.SINGLE_PAGE) else 2

    @property
    def paged(self) -> bool:
        return self in (LayoutMode.SINGLE_PAGE, LayoutMode.TWO_PAGE)

    @property
    def rows_to_fit(self) -> int:
        return 2 if self is LayoutMode.GRID_2X2 else 1


class PageItem(QGraphicsItem):
    def __init__(self, view: PageView, index: int, width: float, height: float) -> None:
        super().__init__()
        self._view = view
        self.index = index
        self._rect = QRectF(0, 0, width, height)

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.fillRect(self._rect, Qt.GlobalColor.white)
        pixmap = self._view.pixmap_for(self.index)
        if pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self._rect, pixmap, QRectF(pixmap.rect()))
        painter.setPen(QPen(QColor(0, 0, 0, 90), 0))
        painter.drawRect(self._rect)


class PageView(QGraphicsView):
    currentPageChanged = Signal(int)
    historyChanged = Signal()  # Previous View / Next View availability
    handToolChanged = Signal(bool)
    viewToolChanged = Signal(str)  # Dynamic Zoom / Loupe Tool on ("dynamic_zoom", "loupe") or off ("")
    loupeMoved = Signal(int, QPointF)  # Loupe Tool: (page, point as displayed) to look at
    zoomChanged = Signal(float)
    autoScrollChanged = Signal(bool)  # Automatically Scroll started / stopped
    autoScrollSpeedChanged = Signal(float)  # its new speed (pixels per second)
    brushStep = Signal(int)  # the Eraser's + and - keys: +1 bigger, -1 smaller
    # (page index, rect in displayed page coordinates) drawn with the area tool
    areaDrawn = Signal(int, QRectF)
    # (mark id, add to selection?) clicked; ("", False) = clicked empty space
    markClicked = Signal(str, bool)
    areaToolCanceled = Signal()
    fieldClicked = Signal(str)  # field key
    fieldTab = Signal(bool)  # Tab pressed while a field is focused: True = forward
    # (tool, page index, QRectF | list[QPointF]) in displayed page coordinates
    shapeDrawn = Signal(str, int, object)
    drawToolCanceled = Signal()
    markupClicked = Signal(str)  # item id, "" = clicked empty space
    markupMoved = Signal(str, QRectF)  # item id, new rect (displayed page coordinates)
    markupMovedToPage = Signal(str, int, QRectF)  # item id, the page it was dropped on, rect there
    markupDoubleClicked = Signal(str)
    markupContextMenu = Signal(str, QPoint)
    # notes in pop-up boxes (ui/viewer/note_popups.py): moved / resized (new displayed rect),
    # opened (a click on its item) or collapsed ("–"), double-clicked to edit
    popupMoved = Signal(str, QRectF)
    popupToggled = Signal(str, bool)
    popupEditRequested = Signal(str)  # (a comment's id, or one of its replies')
    popupReplyRequested = Signal(str)  # "Reply" in a note's box (user 2026-10-04)
    markupDragStarted = Signal(str)  # the item starts moving: controller lifts it off the page
    markupDragCanceled = Signal(str)
    markupClickReleased = Signal(str)  # pressed and let go without moving it (a plain click)
    # A plain click on a page with no tool on, on nothing there (no item, field, or link), that
    # didn't only put away a text selection, redaction marks, or a field being filled in: the
    # markup controller adds text there (user 2026-11-16). (page, point as displayed)
    pageClicked = Signal(int, QPointF)
    groupChanged = Signal(list)  # Edit PDF: the selected objects (box, Shift / Ctrl+Shift clicks)
    selectionChanged = Signal()  # whatever is selected changed (one item or a group; the text format bar follows it)
    markupGroupMoved = Signal(list, QPointF)  # those objects, dragged by (dx, dy) as displayed
    markupGroupMovedToPage = Signal(list, int, list)  # dropped on another page: ids, page, rects there
    linkClicked = Signal(int)
    pageContextMenu = Signal(QPoint)  # right-click on the page (not on markup)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._back: list[tuple[int, float]] = []  # view history
        self._forward: list[tuple[int, float]] = []
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(BACKGROUND)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # Always-on vertical bar: stops fit-width from oscillating as the bar appears/disappears.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        # Files dragged in from Explorer belong to the window (open them). QGraphicsView takes
        # drops by default and its scene accepts the drag-enter but refuses the move, so a PDF
        # dropped on the page area showed the "no" cursor and did nothing (user bug 2026-10-07).
        # Page drags between windows are our own mouse drag, not Qt drops.
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)

        self._session: PdfSession | None = None
        self._view_rotation = 0  # View ▸ Rotate View (display only)
        self._items: list[PageItem] = []
        self._row_tops: list[float] = []  # scene y of each row
        self._row_heights: list[float] = []
        self._row_first_page: list[int] = []
        self._page_row: list[int] = []  # row of each page
        self._shown_row: int | None = None  # page-at-a-time views: the row on show
        self._last_wheel_turn = 0.0
        self._turned_by_wheel = False
        self._auto_timer = QTimer(self, interval=16)  # Automatically Scroll
        self._auto_timer.timeout.connect(self._auto_tick)
        self._auto_direction = 1
        self._auto_speed = AUTO_SCROLL_START
        self._auto_carry = 0.0
        self._auto_last = 0.0
        self._auto_dwell = 0
        self._content_width = 0.0
        self._layout = LayoutMode.SINGLE
        self._cover_separate = False
        self._zoom = 1.0
        self._zoom_mode = ZoomMode.FIT_WIDTH
        self._current_page = -1
        self._cache: OrderedDict[int, tuple[float, QPixmap]] = OrderedDict()
        self.cache_limit = DEFAULT_CACHE_MB * 1024 * 1024  # bytes (MainWindow sets it from the settings)
        self._pending: set[int] = set()

        self._overlay_data: OverlayMap = {}
        self._overlays: dict[str, list[MarkOverlay]] = {}
        self._selected_marks: set[str] = set()
        self._area_tool = False
        self._draw_tool: str | None = None
        self._drag: dict | None = None  # the drag in progress (drawing, moving, resizing)
        self._fields_interactive = True
        self._markup_interactive = False
        self._markup_data: MarkupMap = {}
        self._popup_data: dict[int, list] = {}  # page -> NotePopup (open notes)
        self._anchor_data: dict[int, list] = {}  # page -> (item id, rect): click opens the note
        self._popup_items: dict[str, object] = {}
        self._anchor_items: list = []
        self._markup_items: dict[str, MarkupOverlay] = {}
        self._selected_markup: str | None = None
        self._draw_style = DrawStyle()
        self._word_provider: Callable[[int], list[tuple[QRectF, object]]] | None = None
        self._ghost_pixmap: QPixmap | None = None  # stamp/signature following the cursor
        self._poly: dict | None = None  # a polygon / polyline being clicked point by point
        self._group: list[str] = []  # several selected objects (Edit PDF), else empty
        # (global pos) -> the page whose thumbnail is under it, while dragging objects
        self.drag_page_picker: Callable[[QPoint], int | None] | None = None
        self._group_select = False
        self._ghost_size = QSizeF()
        self._ghost_item: QGraphicsPixmapItem | None = None
        self._lingering: dict[int, list[QGraphicsItem]] = {}  # previews kept until re-render
        # name -> (page -> rects, color, current (page, index) or None); e.g. find, selection
        self._highlight_data: dict[str, tuple[dict[int, list[QRectF]], QColor, tuple[int, int] | None]] = {}
        self._highlight_items: dict[str, list[QGraphicsItem]] = {}
        self._link_data: dict[int, list[tuple[int, QRectF, str]]] = {}
        self._link_items: list[LinkOverlay] = []
        self.viewport().setMouseTracking(True)

        self._field_data: FieldMap = {}
        self._field_items: dict[str, FieldOverlay] = {}
        self._fields_highlighted = True
        self._focused_field: str | None = None
        self._editor: QGraphicsProxyWidget | None = None

        self._render_timer = QTimer(self, singleShot=True, interval=0)
        self._render_timer.timeout.connect(self._render_next)
        self._settle_timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle_timer.timeout.connect(self.viewport().update)

        self.verticalScrollBar().valueChanged.connect(self._update_current_page)

    # --- document --------------------------------------------------------------

    def set_session(self, session: PdfSession | None, *, upright: bool = False, state: dict | None = None) -> None:
        """``upright``: a file just opened (Rotate View is never saved, so each file opens
        as it is; a save or undo snapshot reloading the same file keeps the view turned).
        ``state``: switching back to a document tab: its view as it was (``view_state``)."""
        self._back, self._forward = [], []
        self.historyChanged.emit()
        self._cache.clear()
        self._session = session
        if state is not None:
            self._view_rotation = state["rotation"]
            self._back, self._forward = list(state["back"]), list(state["forward"])
            self.historyChanged.emit()
        elif upright or session is None:
            self._view_rotation = 0
        self._build_items()
        if session is None:
            return
        if state is None:
            self._apply_zoom_mode()
            self.go_to_page(0)
            self._update_current_page()
            return
        if state["mode"] is ZoomMode.CUSTOM:
            self.set_zoom(state["zoom"])
        else:
            self._zoom_mode = state["mode"]
            self._apply_zoom_mode()
        self.horizontalScrollBar().setValue(state["h"])
        self.verticalScrollBar().setValue(state["v"])
        self._update_current_page()

    def view_state(self) -> dict:
        """Where the view is (zoom, scroll position, Rotate View, Previous / Next View),
        to come back to when the document's tab is shown again."""
        return {"rotation": self._view_rotation, "zoom": self._zoom, "mode": self._zoom_mode,
                "h": self.horizontalScrollBar().value(), "v": self.verticalScrollBar().value(),
                "back": list(self._back), "forward": list(self._forward)}

    def apply_page_mapping(self, mapping: list[int | None], focus: int) -> None:
        """Rebuild after pages were moved/inserted/deleted, reusing renders that still apply.

        ``mapping[new_index]`` is the page's previous index, or None for a new page.
        """
        old_cache = self._cache
        self._cache = OrderedDict(
            (new, old_cache[old]) for new, old in enumerate(mapping) if old is not None and old in old_cache
        )
        self._build_items()
        self._current_page = max(0, min(focus, len(self._items) - 1))
        self._apply_zoom_mode()
        self.go_to_page(focus)
        self.currentPageChanged.emit(self._current_page)
        self.viewport().update()

    # --- layout ----------------------------------------------------------------

    @property
    def layout_mode(self) -> LayoutMode:
        return self._layout

    @property
    def cover_separate(self) -> bool:
        return self._cover_separate

    def set_layout(self, mode: LayoutMode, *, cover_separate: bool | None = None) -> None:
        """Switch between single / two-up / 2x2, keeping the current page in view."""
        if cover_separate is not None:
            self._cover_separate = cover_separate
        self._layout = mode
        if mode is LayoutMode.GRID_2X2:
            self._zoom_mode = ZoomMode.FIT_PAGE
        page = max(self._current_page, 0)
        self._build_items()
        if self._session is not None:
            self._current_page = min(page, len(self._items) - 1)  # so Fit Page fits this row
            self._apply_zoom_mode()
            self.go_to_page(page)
            self.viewport().update()

    # --- View ▸ Rulers & Grids: grid, snap, guides (user request 2026-10-02) -------------------

    show_grid = False
    snap_to_grid = False
    show_guides = True

    def set_grid(self, on: bool) -> None:
        self.show_grid = on
        self.viewport().update()

    def set_snap(self, on: bool) -> None:
        self.snap_to_grid = on

    # dotted guides and snapping to other objects while one is dragged (user 2026-11-24;
    # View ▸ Rulers & Grids ▸ Snap to Objects; Alt held while dragging = no snapping)
    snap_to_objects = True

    def set_snap_to_objects(self, on: bool) -> None:
        self.snap_to_objects = on

    def _aligned(self, page, moving: QRectF, skip: set[str]) -> QPointF:
        """The nudge that lines ``moving`` (page coordinates) up with the other objects on its
        page; the dotted guides are drawn until the drag ends. No nudge with Snap to Grid on
        (the grid wins) or Alt held."""
        from planetpdf.ui.viewer import align_guides as AG

        self._set_align_lines([])
        if (not self.snap_to_objects or self.snap_to_grid
                or QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier):
            return QPointF()
        cache = self._drag if self._drag is not None else {}
        targets = cache.get("align_targets")
        if targets is None:  # (once per drag: the boxes don't change while it lasts)
            targets = [QRectF(o.rect()) for key, o in self._markup_items.items()
                       if o.parentItem() is page and key not in skip and o.isVisible()]
            if self.align_targets is not None:
                try:  # (the page's own text; not what is being dragged)
                    start = cache["start"] if isinstance(cache.get("start"), QRectF) else moving
                    targets += [r for r in self.align_targets(page.index) if not r.intersects(start)]
                except Exception:  # (guides are a help: never let them stop the drag)
                    pass
            cache["align_targets"] = targets
        if not targets:
            return QPointF()
        scale = (page.sceneTransform() * self.transform()).map(QLineF(0, 0, 1, 0)).length() or 1.0
        found = AG.align(moving, targets, AG.SNAP_PIXELS / scale)
        self._set_align_lines([(page, line) for line in found.lines])
        return QPointF(found.dx, found.dy)

    # page index -> more boxes to line up with (page coordinates as displayed): the page's own
    # text lines outside Edit PDF (MainWindow sets it)
    align_targets = None

    def snapped(self, point: QPointF) -> QPointF:
        """``point`` (page coordinates) on the nearest grid crossing when Snap to Grid is on."""
        if not self.snap_to_grid:
            return point
        step = GRID_SPACING / GRID_SUBDIVISIONS
        return QPointF(round(point.x() / step) * step, round(point.y() / step) * step)

    @property
    def guides(self) -> list[tuple[str, float]]:
        """("h" | "v", position in points from the page's top / left edge as shown), on
        every page (not saved: like Acrobat's, they are for the session)."""
        return self.__dict__.setdefault("_guides", [])

    def add_guide(self, orientation: str, position: float) -> None:
        self.guides.append((orientation, float(position)))
        self.viewport().update()

    def clear_guides(self) -> None:
        self.guides.clear()
        self.viewport().update()

    def set_guides_shown(self, on: bool) -> None:
        self.show_guides = on
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        if self.rulers_shown:  # (zoom, scroll, page changes: the rulers follow)
            for ruler in self._rulers:
                ruler.update()
        guides = self.guides if self.show_guides else []
        self._draw_align_lines(painter)
        if not (self.show_grid or guides) or not self._items:
            return
        painter.save()
        for page in self._items:
            if not page.isVisible():
                continue
            box = page.mapRectToScene(page.boundingRect())
            if not box.intersects(rect):
                continue
            painter.save()
            painter.setClipRect(box)
            # page coordinates as displayed: the page item's own (view rotation included)
            painter.setTransform(page.sceneTransform(), True)
            bounds = page.boundingRect()
            if self.show_grid:
                step = GRID_SPACING / GRID_SUBDIVISIONS
                n = 0
                x = 0.0
                while x <= bounds.width():
                    pen = QPen(GRID_COLOR if n % GRID_SUBDIVISIONS == 0 else GRID_MINOR_COLOR, 0)
                    painter.setPen(pen)
                    painter.drawLine(QPointF(x, 0), QPointF(x, bounds.height()))
                    x += step
                    n += 1
                n = 0
                y = 0.0
                while y <= bounds.height():
                    painter.setPen(QPen(GRID_COLOR if n % GRID_SUBDIVISIONS == 0 else GRID_MINOR_COLOR, 0))
                    painter.drawLine(QPointF(0, y), QPointF(bounds.width(), y))
                    y += step
                    n += 1
            painter.restore()
            # guides: from the page's top-left corner *as on screen* (like the rulers)
            pen = QPen(GUIDE_COLOR, 0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for orientation, position in guides + ([self._guide_preview] if getattr(self, "_guide_preview", None) else []):
                if orientation == "h" and 0 <= position <= box.height():
                    painter.drawLine(QPointF(box.left(), box.top() + position), QPointF(box.right(), box.top() + position))
                elif orientation == "v" and 0 <= position <= box.width():
                    painter.drawLine(QPointF(box.left() + position, box.top()), QPointF(box.left() + position, box.bottom()))
        painter.restore()

    def _set_align_lines(self, lines: list) -> None:
        """The dotted guides to show (none when the drag ends). The whole view is repainted
        whenever they change: the view repaints only what it thinks changed, so a guide that
        was just dropped stayed on the screen looking like a real line (user 2026-11-24)."""
        if not lines and not getattr(self, "_align_lines", None):
            return
        self._align_lines = list(lines)
        self.viewport().update()

    def _draw_align_lines(self, painter: QPainter) -> None:
        lines = getattr(self, "_align_lines", None)
        if not lines:
            return
        if self._drag is None or self._drag.get("mode") not in ("move", "group"):
            self._align_lines = []  # (the drag ended or was canceled)
            QTimer.singleShot(0, self.viewport().update)  # (and its picture goes too)
            return
        painter.save()
        pen = QPen(ALIGN_COLOR, 1.0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for page, line in lines:
            transform = page.sceneTransform()
            painter.drawLine(transform.map(line))
        painter.restore()

    def guide_position(self, orientation: str, view_pos: QPoint) -> tuple[int, float] | None:
        """(page, position in points from that page's top / left edge as shown) under
        ``view_pos``, for a guide dragged out of a ruler; None off the pages."""
        scene = self.mapToScene(view_pos)
        page = self._page_at(scene)
        if page is None:
            return None
        box = page.mapRectToScene(page.boundingRect())
        value = scene.y() - box.top() if orientation == "h" else scene.x() - box.left()
        if self.snap_to_grid:
            step = GRID_SPACING / GRID_SUBDIVISIONS
            value = round(value / step) * step
        return page.index, value

    def preview_guide(self, guide: tuple[str, float] | None) -> None:
        self._guide_preview = guide
        self.viewport().update()

    # --- rulers and cursor coordinates ---------------------------------------------------------

    cursorMoved = Signal(int, QPointF)  # (page, point from its top-left as shown, in points); page -1 = off the pages
    track_cursor = False  # View ▸ Show/Hide ▸ Cursor Coordinates is on

    def ruler_origin(self) -> tuple[QPointF, float]:
        """Where the rulers measure from: (viewport pixel of the top-left corner of the page
        under the mouse, or of the current page; pixels per point)."""
        page = self.page_item(getattr(self, "_ruler_page", max(self._current_page, 0)))
        scale = self.transform().m11()
        if page is None:
            return QPointF(0, 0), scale
        box = page.mapRectToScene(page.boundingRect())
        return QPointF(self.mapFromScene(box.topLeft())), scale

    def set_rulers(self, on: bool) -> None:
        """View ▸ Rulers & Grids ▸ Rulers: inch rulers along the top and left."""
        from planetpdf.ui.viewer.rulers import RULER_SIZE, Ruler

        if not hasattr(self, "_rulers"):
            self._rulers = (Ruler(self, "h"), Ruler(self, "v"))
        size = RULER_SIZE if on else 0
        self.setViewportMargins(size, size, 0, 0)
        for ruler in self._rulers:
            ruler.setVisible(on)
        self._place_rulers()

    @property
    def rulers_shown(self) -> bool:
        return hasattr(self, "_rulers") and self._rulers[0].isVisible()

    def _place_rulers(self) -> None:
        if not hasattr(self, "_rulers"):
            return
        from planetpdf.ui.viewer.rulers import RULER_SIZE

        vp = self.viewport().geometry()
        top, left = self._rulers
        top.setGeometry(vp.left(), vp.top() - RULER_SIZE, vp.width(), RULER_SIZE)
        left.setGeometry(vp.left() - RULER_SIZE, vp.top(), RULER_SIZE, vp.height())
        top.update()
        left.update()

    def _track_cursor(self, view_pos: QPoint) -> None:
        scene = self.mapToScene(view_pos)
        page = self._page_at(scene)
        if page is None:
            self.cursorMoved.emit(-1, QPointF())
        else:
            box = page.mapRectToScene(page.boundingRect())
            self._ruler_page = page.index
            self.cursorMoved.emit(page.index, QPointF(scene.x() - box.left(), scene.y() - box.top()))
        if self.rulers_shown:
            for ruler in self._rulers:
                ruler.set_marker(view_pos)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self.rulers_shown:
            for ruler in self._rulers:
                ruler.update()

    @property
    def page_gap(self) -> float:
        return PAGE_GAP if getattr(self, "_show_gaps", True) else 0.0

    def set_show_gaps(self, on: bool) -> None:
        """View ▸ Page Display ▸ Show Gaps Between Pages (Acrobat; on by default)."""
        if on == getattr(self, "_show_gaps", True):
            return
        self._show_gaps = on
        if self._session is not None:
            page = max(self._current_page, 0)
            self._build_items()
            self._current_page = min(page, len(self._items) - 1)
            self._apply_zoom_mode()
            self.go_to_page(page)

    def page_item(self, index: int) -> PageItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    @property
    def view_rotation(self) -> int:
        return self._view_rotation

    def rotate_view(self, degrees: int) -> None:
        """View ▸ Rotate View: turn how every page is shown (the file isn't changed;
        reopening shows it upright again)."""
        self._view_rotation = (self._view_rotation + degrees) % 360
        if self._session is None:
            return
        page = max(self._current_page, 0)
        self._build_items()
        self._current_page = min(page, len(self._items) - 1)
        self._apply_zoom_mode()
        self.go_to_page(page)
        self.viewport().update()

    def _slots(self, count: int) -> list[tuple[int, int]]:
        """(row, column) for each page. With a separate cover, page 1 sits alone on the
        right of the first row, so even pages are on the left like a printed book."""
        cols = self._layout.columns
        offset = 1 if cols == 2 and self._cover_separate else 0
        return [divmod(i + offset, cols) for i in range(count)]

    def _build_items(self) -> None:
        # scene().clear() deletes the C++ items; forget our Python references first.
        self._overlays = {}
        self._field_items = {}
        self._markup_items = {}
        self._popup_items = {}
        self._anchor_items = []
        self._editor = None
        self._drag = None
        self._ghost_item = None
        self._lingering = {}
        self._highlight_items = {}
        self._link_items = []
        self.scene().clear()
        self._items.clear()
        self._row_tops.clear()
        self._row_heights.clear()
        self._row_first_page.clear()
        self._page_row.clear()
        self._pending.clear()
        self._current_page = -1
        if self._session is None:
            self.scene().setSceneRect(QRectF())
            return
        count = self._session.page_count
        page_sizes = [self._session.page_size(i) for i in range(count)]
        turned = self._view_rotation in (90, 270)  # Rotate View: laid out as turned
        sizes = [(h, w) for w, h in page_sizes] if turned else page_sizes
        slots = self._slots(count)
        rows = slots[-1][0] + 1
        heights = [0.0] * rows
        first_page = [-1] * rows
        for index, (row, _col) in enumerate(slots):
            heights[row] = max(heights[row], sizes[index][1])
            if first_page[row] < 0:
                first_page[row] = index
        max_width = max(w for w, _h in sizes)
        two_up = self._layout.columns == 2

        gap = self.page_gap  # (View ▸ Page Display ▸ Show Gaps Between Pages: off = touching)
        y = PAGE_GAP
        for row in range(rows):
            self._row_tops.append(y)
            y += heights[row] + gap
        y += PAGE_GAP - gap  # (the margin under the last row stays)
        self._row_heights = heights
        self._row_first_page = first_page

        for index, (row, col) in enumerate(slots):
            width, height = sizes[index]
            if not two_up:
                x = -width / 2
            elif col == 0:
                x = -width - gap / 2  # left page hugs the spine
            else:
                x = gap / 2
            item = PageItem(self, index, *page_sizes[index])
            top = self._row_tops[row] + (heights[row] - height) / 2
            if self._view_rotation:
                # the page (and everything on it) turned about its middle, its turned box
                # placed where an upright page of that size would be
                item.setTransformOriginPoint(item.boundingRect().center())
                item.setRotation(self._view_rotation)
                box = item.mapRectToScene(item.boundingRect())
                item.setPos(x - box.left(), top - box.top())
            else:
                item.setPos(x, top)
            self.scene().addItem(item)
            self._items.append(item)
            self._page_row.append(row)

        self._content_width = 2 * max_width + gap if two_up else max_width
        margin = PAGE_GAP
        half = self._content_width / 2
        self.scene().setSceneRect(-half - margin, 0, self._content_width + 2 * margin, y)
        self._shown_row = None
        if self._layout.paged:
            self._show_row(0)
        self._create_overlays()
        self._create_field_overlays()
        self._create_markup_overlays()
        self._create_note_popups()
        self._create_link_overlays()
        for name in list(self._highlight_data):
            self._create_highlights(name)

    # --- highlights (find results, text selection) and links ---------------------------------

    def set_highlights(
        self, name: str, rects: dict[int, list[QRectF]], color: QColor, current: tuple[int, int] | None = None
    ) -> None:
        """A named highlight layer; ``current`` (page, index) is drawn stronger."""
        self._highlight_data[name] = (rects, color, current)
        self._create_highlights(name)

    def clear_highlights(self, name: str) -> None:
        self._highlight_data.pop(name, None)
        self._create_highlights(name)

    def _create_highlights(self, name: str) -> None:
        for item in self._highlight_items.pop(name, []):
            if item.scene() is not None:
                self.scene().removeItem(item)
        data = self._highlight_data.get(name)
        if data is None:
            return
        rects, color, current = data
        items = []
        for page, page_rects in rects.items():
            if not 0 <= page < len(self._items):
                continue
            for i, rect in enumerate(page_rects):
                item = QGraphicsRectItem(rect, self._items[page])
                fill = QColor(color)
                is_current = current == (page, i)
                fill.setAlphaF(0.65 if is_current else 0.3)
                item.setBrush(QBrush(fill))
                pen = QPen(QColor(color).darker(150) if is_current else Qt.GlobalColor.transparent, 1.5)
                pen.setCosmetic(True)
                item.setPen(pen)
                item.setZValue(0.8)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                items.append(item)
        self._highlight_items[name] = items

    def set_link_overlays(self, links: dict[int, list[tuple[int, QRectF, str]]]) -> None:
        self._link_data = links
        self._create_link_overlays()

    def _create_link_overlays(self) -> None:
        for item in self._link_items:
            if item.scene() is not None:
                self.scene().removeItem(item)
        self._link_items = []
        for page, entries in self._link_data.items():
            if 0 <= page < len(self._items):
                for link_id, rect, tip in entries:
                    self._link_items.append(LinkOverlay(link_id, rect, self._items[page], tip))

    def show_rect(self, page_index: int, rect: QRectF) -> None:
        """Scroll so ``rect`` (displayed page coordinates) is comfortably in view."""
        if 0 <= page_index < len(self._items):
            self._remember(page_index)
            scene_rect = self._items[page_index].mapRectToScene(rect)
            visible = self.mapToScene(self.viewport().rect()).boundingRect()
            # Comfortable = inside the middle band of the view; otherwise center it.
            band = visible.adjusted(0, visible.height() * 0.2, 0, -visible.height() * 0.2)
            if not band.contains(scene_rect):
                self.centerOn(scene_rect.center())
            self._update_current_page()

    def go_to_point(self, page_index: int, y: float) -> None:
        """Jump so that ``y`` (displayed page coordinates) on the page is near the top."""
        if not 0 <= page_index < len(self._items):
            return
        if self._layout.paged:
            self.go_to_page(page_index)  # (page at a time: turn to it first; user bug 2026-10-04)
        self._remember(page_index)
        top = self._items[page_index].mapToScene(QPointF(0, max(0.0, y - 20))).y()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.mapFromScene(QPointF(0, top)).y())
        self._update_current_page(prefer=page_index)
        # the page jumped to is the current one, even when the point is low on it and the
        # next page fills most of the window (a heading near the bottom of a page)
        if self._current_page != page_index:
            self._current_page = page_index
            self.currentPageChanged.emit(page_index)

    # --- form fields ---------------------------------------------------------------

    def set_field_overlays(self, fields: FieldMap) -> None:
        self._field_data = fields
        self._create_field_overlays()

    def _create_field_overlays(self) -> None:
        import shiboken6

        for item in list(self._field_items.values()) + getattr(self, "_drop_arrows", []):
            if shiboken6.isValid(item) and item.scene() is not None:
                self.scene().removeItem(item)
        self._field_items = {}
        self._drop_arrows: list[DropArrow] = []
        for page, entries in self._field_data.items():
            if not 0 <= page < len(self._items):
                continue
            for key, rect, fillable, *more in entries:  # (optional 4th: "dropdown" / "signature")
                look = more[0] if more else ""
                self._field_items[key] = FieldOverlay(key, rect, self._items[page], fillable, look)
                self._field_items[key].setVisible(self._fields_interactive)
                if look in ("dropdown", "dropdown-empty", True):  # its ▾, in every mode (not in the PDF)
                    hint = "Choose…" if look == "dropdown-empty" else ""
                    self._drop_arrows.append(DropArrow(self._items[page], rect, hint))
                elif look == "signature":  # an unsigned one: as other viewers show it
                    self._drop_arrows.append(SignatureLook(self._items[page], rect))
        if self._focused_field not in self._field_items:
            self._focused_field = None
        self._update_field_states()

    def set_fields_highlighted(self, on: bool) -> None:
        self._fields_highlighted = on
        self._update_field_states()

    def set_focused_field(self, key: str | None) -> None:
        self._focused_field = key
        self._update_field_states()
        item = self._field_items.get(key) if key else None
        if item is not None:
            self.ensureVisible(item.sceneBoundingRect(), 40, 80)
            self._update_current_page()

    @property
    def focused_field(self) -> str | None:
        return self._focused_field

    def _update_field_states(self) -> None:
        for key, item in self._field_items.items():
            item.set_state(highlighted=self._fields_highlighted, focused=key == self._focused_field)

    def field_rect(self, key: str) -> tuple[int, QRectF] | None:
        item = self._field_items.get(key)
        if item is None:
            return None
        return item.parentItem().index, item.rect()

    def field_screen_rect(self, key: str):
        """Where a field really is on the screen (global coordinates): for menus that open
        under it (a dropdown's choices, a date field's calendar). Widgets inside the page
        can't tell: their own idea of their screen position is off (user bug 2026-10-04)."""
        from PySide6.QtCore import QRect

        item = self._field_items.get(key)
        if item is None:
            return None
        polygon = self.mapFromScene(item.mapToScene(item.rect()))
        box = polygon.boundingRect()
        return QRect(self.viewport().mapToGlobal(box.topLeft()), box.size())

    def show_editor(self, key: str, widget: QWidget) -> QGraphicsProxyWidget | None:
        """Place ``widget`` exactly over a field (in page coordinates, so it zooms)."""
        self.remove_editor()
        item = self._field_items.get(key)
        if item is None:
            return None
        proxy = QGraphicsProxyWidget(item.parentItem())
        proxy.setWidget(widget)
        proxy.setGeometry(item.rect())
        proxy.setZValue(4)
        self._editor = proxy
        widget.setFocus()
        return proxy

    def remove_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            if editor.scene() is not None:
                self.scene().removeItem(editor)
            editor.deleteLater()

    @property
    def editor(self) -> QGraphicsProxyWidget | None:
        return self._editor

    def focusNextPrevChild(self, forward: bool) -> bool:
        # Tab/Shift+Tab move between form fields while one is focused.
        if self._focused_field is not None:
            self.fieldTab.emit(forward)
            return True
        return super().focusNextPrevChild(forward)

    # --- redaction overlays and the area tool -------------------------------------

    def set_mark_overlays(self, overlays: OverlayMap) -> None:
        self._overlay_data = overlays
        self._create_overlays()

    def _create_overlays(self) -> None:
        for items in self._overlays.values():
            for item in items:
                if item.scene() is not None:
                    self.scene().removeItem(item)
        self._overlays = {}
        for page, marks in self._overlay_data.items():
            if not 0 <= page < len(self._items):
                continue
            for mark_id, rects in marks:
                self._overlays[mark_id] = [MarkOverlay(mark_id, r, self._items[page]) for r in rects]
        self.set_selected_marks(self._selected_marks & set(self._overlays))

    @property
    def selected_marks(self) -> set[str]:
        return set(self._selected_marks)

    def set_selected_marks(self, mark_ids: set[str]) -> None:
        self._selected_marks = set(mark_ids)
        for mark_id, items in self._overlays.items():
            for item in items:
                item.set_selected(mark_id in self._selected_marks)

    def show_mark(self, mark_id: str) -> None:
        """Scroll so the mark is visible."""
        items = self._overlays.get(mark_id)
        if items:
            self.ensureVisible(items[0].sceneBoundingRect(), 40, 80)
            self._update_current_page()

    # --- tools and interaction modes ---------------------------------------------------

    @property
    def area_tool(self) -> bool:
        return self._area_tool

    def set_area_tool(self, enabled: bool) -> None:
        """The redaction box tool."""
        self._area_tool = enabled
        if enabled:
            self._draw_tool = None
        self._cancel_drag()
        self._update_cursor()

    @property
    def draw_tool(self) -> str | None:
        return self._draw_tool

    def set_draw_tool(self, kind: str | None, *, text_cursor: bool = False, text_hover: bool = False,
                      tool_cursor: str | None = None) -> None:
        """Markup drawing: "rect", "ellipse", "line", "arrow", "ink", "textbox", "text"
        (text markup), "point" (a click), "place" (stamp/signature ghost) or "box".
        ``text_cursor``: the tool places text, so show the I-beam (text boxes always do).
        ``text_hover`` (highlight, underline, strike-out, Replace Text, Text Callout; user
        2026-10-18): the I-beam over text and the crosshair elsewhere; for the text-markup tools a
        drag that starts off the text draws a box, and every word it touches is marked.
        ``tool_cursor``: one of ``cursors.CURSORS`` (Marquee Zoom's magnifier); free-form
        drawing ("ink") shows the pen (user 2026-10-24)."""
        if kind and self.hand_tool:  # another tool: put the Hand away
            self.set_hand_tool(False)
            self.handToolChanged.emit(False)
        if kind and self.view_tool:
            self.set_view_tool(None)
        self._draw_tool = kind
        self._text_cursor = bool(kind) and (text_cursor or kind == "textbox")
        self._text_hover = bool(kind) and text_hover
        self._tool_cursor = (tool_cursor or ("pen" if kind == "ink" else None)) if kind else None
        if kind:
            self._area_tool = False
        if kind != "place":
            self.set_placement_ghost(None)
        self._cancel_drag()
        self._cancel_poly()
        self._update_cursor()

    @property
    def view_tool(self) -> str | None:
        return getattr(self, "_view_tool", None)

    def set_view_tool(self, name: str | None) -> None:
        """View ▸ Zoom tools that only change the view: "dynamic_zoom" (drag up to zoom in,
        down to zoom out) or "loupe" (click / drag to choose what the Loupe shows)."""
        if name == self.view_tool:
            return
        if name:
            if self.hand_tool:
                self.set_hand_tool(False)
                self.handToolChanged.emit(False)
            self._draw_tool = None
            self._area_tool = False
            self._cancel_drag()
        self._view_tool = name
        self._view_drag = None
        self._update_cursor()
        self.viewToolChanged.emit(name or "")

    def _update_cursor(self) -> None:
        from planetpdf.ui.viewer import cursors

        if self.view_tool == "dynamic_zoom":  # (the magnifier: drag up or down to zoom)
            self.viewport().setCursor(cursors.cursor("zoom"))
            return
        if self.view_tool == "loupe":
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        drawing = self._area_tool or self._draw_tool is not None
        brush = getattr(self, "_brush", None)
        if drawing and brush:
            self.viewport().setCursor(self._brush_cursor(brush))
            return
        if drawing and getattr(self, "_tool_cursor", None) and not self._area_tool:
            self.viewport().setCursor(cursors.cursor(self._tool_cursor))
            return
        if drawing and getattr(self, "_text_cursor", False):
            shape = Qt.CursorShape.IBeamCursor
        else:
            shape = Qt.CursorShape.CrossCursor if drawing else Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(shape)

    def _selected_cursor(self, scene_pos: QPointF) -> Qt.CursorShape | None:
        """Over the selected item a press moves it (or, on a corner handle, stretches it) even
        with a tool on (``_press_selected``): that cursor, else None."""
        if not self._markup_interactive or self._selected_markup is None:
            return None
        selected = self._markup_items.get(self._selected_markup)
        if selected is None or not selected.isVisible():
            return None
        items = self.scene().items(scene_pos)
        handle = next((i for i in items if i in selected.handles), None)
        if handle is not None:
            return handle.cursor().shape()
        return Qt.CursorShape.SizeAllCursor if selected in items and selected.movable else None

    def _tool_hover(self, scene_pos: QPointF) -> None:
        """With a tool on, the tool's own cursor (Add Text's I-beam, the crosshair…), except over
        the selected item (user 2026-10-24: after Enter the I-beam didn't come back)."""
        shape = self._selected_cursor(scene_pos)
        if shape is not None:
            if self.viewport().cursor().shape() != shape:
                self.viewport().setCursor(shape)
            return
        tool = (Qt.CursorShape.BitmapCursor if getattr(self, "_tool_cursor", None) and not self._area_tool
                else Qt.CursorShape.IBeamCursor if getattr(self, "_text_cursor", False) else Qt.CursorShape.CrossCursor)
        if self.viewport().cursor().shape() != tool:  # (left the selected item, or another cursor took over)
            self._update_cursor()

    def _pointer_hover(self, scene_pos: QPointF) -> None:
        """No tool (Acrobat's Select): the I-beam over page text, where a drag selects it (user
        2026-10-24), the arrow elsewhere; links, fields and objects keep their own cursors."""
        if self.hand_tool or getattr(self, "_pointer_style", None) is None or self._word_provider is None:
            return
        if any(i.isVisible() and i.hasCursor() for i in self.scene().items(scene_pos)):
            return  # (an item's own: Qt shows it)
        page = self._page_at(scene_pos)
        over = page is not None and self._over_text(page, page.mapFromScene(scene_pos))
        shape = Qt.CursorShape.IBeamCursor if over else Qt.CursorShape.ArrowCursor
        if self.viewport().cursor().shape() != shape:
            self.viewport().setCursor(shape)

    def refresh_tool_cursor(self) -> None:
        """Put the tool's cursor back now, without waiting for the mouse to move: an item made or
        changed under a still mouse (the frame of text just added, its handles) shows its own
        cursor (``QGraphicsItem.setCursor`` does that even while a tool is on)."""
        if not (self._area_tool or self._draw_tool is not None) or getattr(self, "_text_hover", False) \
                or getattr(self, "_brush", None):
            return  # (no tool; tools that choose by what's under the mouse; the Eraser's circle)
        point = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(point):
            shape = self._selected_cursor(self.mapToScene(point))
            if shape is not None:
                self.viewport().setCursor(shape)
                return
        self._update_cursor()

    def set_brush(self, diameter: float | None) -> None:
        """The eraser: the cursor becomes a circle as wide as the brush (in points, so it
        grows and shrinks with the zoom), None for the usual cursor."""
        self._brush = diameter
        self._update_cursor()

    def _brush_cursor(self, diameter: float) -> QCursor:
        a, b = self.mapFromScene(QPointF(0, 0)), self.mapFromScene(QPointF(diameter, 0))
        pixels = max(6, min(400, abs(b.x() - a.x())))
        dpr = self.devicePixelRatioF()
        side = int(pixels + 4)
        pixmap = QPixmap(int(side * dpr), int(side * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QRectF(2, 2, pixels, pixels)
        painter.setPen(QPen(QColor(255, 255, 255, 230), 3))  # a light halo: visible on dark pages too
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor(200, 30, 80), 1.5))
        painter.setBrush(QColor(255, 80, 120, 40))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor(200, 30, 80), 1))
        c = side / 2
        painter.drawLine(QPointF(c - 2, c), QPointF(c + 2, c))  # a small mark at the center
        painter.drawLine(QPointF(c, c - 2), QPointF(c, c + 2))
        painter.end()
        return QCursor(pixmap, int(side / 2), int(side / 2))

    @property
    def hand_tool(self) -> bool:
        return getattr(self, "_hand", False)

    def set_hand_tool(self, on: bool) -> None:
        """Acrobat's Hand tool: drag the pages around (nothing on the page is clickable)."""
        self._hand = on
        if on:
            if self.view_tool:
                self.set_view_tool(None)
            self._draw_tool = None
            self._area_tool = False
            self._cancel_drag()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if on else QGraphicsView.DragMode.NoDrag)
        if on:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._update_cursor()

    @property
    def cursor_shape(self) -> Qt.CursorShape:
        return self.viewport().cursor().shape()

    # --- the small bar above a selected item (text size, delete) -------------------------

    def set_item_bar(self, item_id: str | None, widget: QWidget | None = None) -> None:
        """Show ``widget`` just above the selected markup overlay ``item_id`` (constant
        screen size, like Acrobat's Fill & Sign bar); None removes it."""
        old, self._item_bar = getattr(self, "_item_bar", None), None
        if old is not None and shiboken6.isValid(old):  # (Qt deletes it with its page item)
            if old.scene() is not None:
                self.scene().removeItem(old)
            old.deleteLater()
        overlay = self._markup_items.get(item_id) if item_id else None
        if widget is None or overlay is None:
            return
        proxy = QGraphicsProxyWidget(overlay.parentItem())
        proxy.setWidget(widget)
        proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        proxy.setZValue(5)
        proxy.setPos(overlay.rect().topLeft())
        proxy.setTransform(QTransform.fromTranslate(0, -widget.sizeHint().height() - 6))  # screen pixels
        self._item_bar = proxy

    @property
    def item_bar(self) -> QGraphicsProxyWidget | None:
        bar = getattr(self, "_item_bar", None)
        return bar if bar is not None and shiboken6.isValid(bar) else None

    def set_interaction(self, *, fields: bool, markup: bool) -> None:
        """Which overlays react to clicks: form fields (Fill Form mode) and/or markup."""
        self._fields_interactive = fields
        self._markup_interactive = markup
        for item in self._field_items.values():
            item.setVisible(fields)

    # --- markup overlays ---------------------------------------------------------------

    def set_markup_overlays(self, overlays: MarkupMap, labels: dict[str, tuple[str, str]] | None = None) -> None:
        """``labels``: item id -> (text shown in the box, tooltip): Prepare Form's field names."""
        self._markup_data = overlays
        self._markup_labels = labels or {}
        self._create_markup_overlays()

    def _create_markup_overlays(self) -> None:
        for item in self._markup_items.values():
            if item.scene() is not None:
                self.scene().removeItem(item)
        self._markup_items = {}
        self.set_item_bar(None)  # the controller puts it back on the selection
        for page, entries in self._markup_data.items():
            if not 0 <= page < len(self._items):
                continue
            for item_id, rect, movable, keep_aspect, *more in entries:  # (optional: resizable, stretchable)
                self._markup_items[item_id] = MarkupOverlay(item_id, rect, self._items[page], movable, keep_aspect, *more)
                label = getattr(self, "_markup_labels", {}).get(item_id)
                if label is not None:  # (text, tooltip)
                    self._markup_items[item_id].set_label(label[0], label[1])
        if self._selected_markup not in self._markup_items:
            self._selected_markup = None
        group = [i for i in self._group if i in self._markup_items]
        if len(group) > 1:
            self.set_group(group)
        else:
            self.set_selected_markup(self._selected_markup)
        self.refresh_tool_cursor()

    # --- notes in pop-up boxes ---------------------------------------------------------------

    def set_note_popups(self, popups: dict[int, list], anchors: dict[int, list] | None = None) -> None:
        """``popups``: page -> open notes (``note_popups.NotePopup``); ``anchors``: page ->
        (item id, displayed rect) that open their note when clicked (outside Comment mode)."""
        self._popup_data = popups
        self._anchor_data = anchors or {}
        self._create_note_popups()

    def _create_note_popups(self) -> None:
        import shiboken6

        from planetpdf.ui.viewer.note_popups import NoteAnchor, NotePopupItem

        for item in list(self._popup_items.values()):
            if shiboken6.isValid(item):
                item.detach()
        for item in self._anchor_items:
            if shiboken6.isValid(item) and item.scene() is not None:
                self.scene().removeItem(item)
        self._popup_items, self._anchor_items = {}, []
        for page, entries in self._anchor_data.items():
            if 0 <= page < len(self._items):
                for item_id, rect in entries:
                    self._anchor_items.append(NoteAnchor(item_id, rect, self._items[page]))
        for page, entries in self._popup_data.items():
            if 0 <= page < len(self._items):
                for data in entries:
                    self._popup_items[data.item_id] = NotePopupItem(data, self._items[page])

    def popup_rect(self, item_id: str) -> QRectF | None:
        item = self._popup_items.get(item_id)
        return None if item is None else QRectF(item.rect())

    def _popup_cursor(self, event: QMouseEvent) -> bool:
        """Over a note's box the cursor says what a press does there (user 2026-10-04: the
        four-way arrow on its header and edge, the diagonal one on its corners, the I-beam in
        its text), also while a tool is on (whose cursor otherwise wins); it comes back when
        the mouse leaves the box."""
        from planetpdf.ui.viewer.note_popups import CURSORS, NotePopupItem

        scene_pos = self.mapToScene(event.position().toPoint())
        popup = next((i for i in self.scene().items(scene_pos) if isinstance(i, NotePopupItem)), None)
        viewport = self.viewport()
        if popup is None:
            saved = getattr(self, "_cursor_before_popup", None)
            if saved is not None:
                viewport.setCursor(saved)
                self._cursor_before_popup = None
            return False
        if getattr(self, "_cursor_before_popup", None) is None:
            self._cursor_before_popup = viewport.cursor()
        part = popup.part_at(popup.parentItem().mapFromScene(scene_pos))
        viewport.setCursor(CURSORS.get(part, Qt.CursorShape.ArrowCursor))
        popup.setToolTip({"close": "Collapse the note (click the item to open it again)",
                          "reply": "Reply to this comment", "move": "Drag to move the note",
                          "text": "Click to edit the note (or a reply)"}.get(
                             part, "Drag to resize the note"))
        event.accept()
        return True

    def _popup_press(self, event: QMouseEvent, under) -> bool:
        """A press on a note box: collapse, move or resize it (any tool)."""
        from planetpdf.ui.viewer.note_popups import NotePopupItem

        popup = next((i for i in under if isinstance(i, NotePopupItem)), None)
        if popup is None:
            return False
        page = popup.parentItem()
        origin = page.mapFromScene(self.mapToScene(event.position().toPoint()))
        part = popup.part_at(origin)
        if part == "close":
            self.popupToggled.emit(popup.item_id, False)
        elif part == "text":  # (a click in the text: edit it, or the reply clicked; user 2026-10-04)
            self.popupEditRequested.emit(popup.text_target(origin))
        elif part == "reply":
            self.popupReplyRequested.emit(popup.item_id)
        else:  # the edge or header moves it, a corner resizes it
            self._drag = {"mode": "popup_move" if part == "move" else "popup_resize", "corner": part,
                          "popup": popup, "start": QRectF(popup.rect()), "origin": origin, "page": page}
        event.accept()
        return True

    def set_bounding_boxes(self, on: bool) -> None:
        """Edit PDF: outline every editable line of text and image, not just the selected one."""
        MarkupOverlay.show_bounds = on
        self.set_selected_markup(self._selected_markup)

    @property
    def selected_markup(self) -> str | None:
        return self._selected_markup

    def set_selected_markup(self, item_id: str | None) -> None:
        if item_id != self._selected_markup or self._group:
            self.set_item_bar(None)
        self._group = []  # one object selected: no group
        self._selected_markup = item_id
        for key, item in self._markup_items.items():
            item.set_selected(key == item_id)
        self.refresh_tool_cursor()  # (handles made under the mouse put their own cursor up)
        self.selectionChanged.emit()

    # --- several objects selected (Edit PDF) --------------------------------------------------

    def set_group_select(self, on: bool) -> None:
        """Edit PDF: drag a box around objects, Shift+click adds, Ctrl+Shift+click removes;
        dragging any of them (or the arrow keys) moves them all."""
        self._group_select = on
        if not on and self._group:
            self.set_group([])

    @property
    def selected_group(self) -> list[str]:
        """Every selected object (one or more), in the order they were selected."""
        if self._group:
            return list(self._group)
        return [self._selected_markup] if self._selected_markup else []

    def set_group(self, ids: list[str]) -> None:
        ids = [i for i in dict.fromkeys(ids) if i in self._markup_items]
        if len(ids) <= 1:
            self.set_selected_markup(ids[0] if ids else None)
            return
        self.set_item_bar(None)
        self._group = ids
        self._selected_markup = ids[-1]
        for key, item in self._markup_items.items():
            item.set_selected(key in ids, handles=False)
        self.selectionChanged.emit()

    def show_area(self, page_index: int, rect: QRectF, *, flash: bool = True) -> None:
        """Scroll so that ``rect`` (displayed page coordinates) is in the middle of the view,
        and briefly mark it (user 2026-10-04: a comment clicked in the panel jumps to what it's
        about in the document)."""
        if not 0 <= page_index < len(self._items):
            return
        if self._layout.paged:
            self.go_to_page(page_index)
        page = self._items[page_index]
        self._remember(page_index)
        self.centerOn(page.mapToScene(rect.center()))
        self._update_current_page(prefer=page_index)
        if flash:
            from PySide6.QtCore import QTimer

            self.set_highlights("jump", {page_index: [rect.adjusted(-3, -3, 3, 3)]}, QColor(30, 110, 230))
            QTimer.singleShot(1200, self, lambda: self.clear_highlights("jump"))

    def show_markup(self, item_id: str) -> None:
        item = self._markup_items.get(item_id)
        if item is not None:
            self.ensureVisible(item.sceneBoundingRect(), 40, 80)
            self._update_current_page()

    def markup_rect(self, item_id: str) -> tuple[int, QRectF] | None:
        item = self._markup_items.get(item_id)
        return None if item is None else (item.parentItem().index, item.rect())

    # --- mouse ---------------------------------------------------------------------------

    def _page_at(self, scene_pos: QPointF) -> PageItem | None:
        """The page whose own area holds ``scene_pos``. Decided by the pages' geometry, not
        by the items under the mouse: a stamp's placement ghost (a child of the page it
        was last over) can hang over the next page, and asking "whose child is on top?"
        then measured the click against the wrong page, so the stamp landed off that
        page's edge, out of sight (user report 2026-10-01, two-page view)."""
        for page in self._items:
            if page.mapRectToScene(page.boundingRect()).contains(scene_pos):
                return page
        return None

    def _cancel_drag(self) -> None:
        drag, self._drag = self._drag, None
        self._set_align_lines([])
        self._lower_pages()
        if drag is None:
            return
        for item in [drag.get("item"), drag.get("ghost"), *drag.get("words_items", [])]:
            if item is not None and item.scene() is not None:
                self.scene().removeItem(item)
        for overlay, start in drag.get("members", []):  # a group drag: frames back in place
            overlay.setRect(start)
        if drag["mode"] in ("move", "resize"):
            overlay = drag["overlay"]
            if overlay.scene() is not None:
                overlay.setRect(drag["start"])
                overlay.place_handles()
            if drag.get("started"):
                self.markupDragCanceled.emit(overlay.item_id)

    def show_editor_at(self, page_index: int, rect: QRectF, widget: QWidget) -> QGraphicsProxyWidget | None:
        """An editor widget on the page at ``rect`` (displayed page coordinates)."""
        self.remove_editor()
        if not 0 <= page_index < len(self._items):
            return None
        proxy = QGraphicsProxyWidget(self._items[page_index])
        proxy.setWidget(widget)
        proxy.setGeometry(rect)
        proxy.setZValue(4)
        proxy.setData(0, "typing")  # (a click away only finishes it: see mousePressEvent)
        self._editor = proxy
        widget.setFocus()
        return proxy

    def _clamped(self, page: PageItem, view_pos) -> QPointF:
        pos = page.mapFromScene(self.mapToScene(view_pos))
        bounds = page.boundingRect()
        return QPointF(max(bounds.left(), min(bounds.right(), pos.x())), max(bounds.top(), min(bounds.bottom(), pos.y())))

    # --- WYSIWYG previews -----------------------------------------------------------------

    def set_draw_style(self, style: DrawStyle) -> None:
        self._draw_style = style

    def set_word_provider(self, provider: Callable[[int], list[tuple[QRectF, object]]] | None) -> None:
        """For text markup: page -> [(word rect as displayed, line key), ...] in reading order."""
        self._word_provider = provider

    def set_placement_ghost(self, pixmap: QPixmap | None, size: QSizeF | None = None) -> None:
        """Show ``pixmap`` (a stamp/signature) under the cursor at ``size`` points until placed."""
        self._ghost_pixmap = pixmap
        self._ghost_size = size or QSizeF()
        if pixmap is None and self._ghost_item is not None:
            if self._ghost_item.scene() is not None:
                self.scene().removeItem(self._ghost_item)
            self._ghost_item = None

    def _place_ghost(self, page: PageItem, rect: QRectF) -> None:
        if self._ghost_pixmap is None:
            return
        if self._ghost_item is None or self._ghost_item.parentItem() is not page:
            if self._ghost_item is not None and self._ghost_item.scene() is not None:
                self.scene().removeItem(self._ghost_item)
            self._ghost_item = QGraphicsPixmapItem(self._ghost_pixmap, page)
            self._ghost_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._ghost_item.setOpacity(0.75)
            self._ghost_item.setZValue(5)
        self._fit_pixmap_item(self._ghost_item, rect)

    @staticmethod
    def _fit_pixmap_item(item: QGraphicsPixmapItem, rect: QRectF) -> None:
        pixmap = item.pixmap()
        if pixmap.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        item.setTransform(QTransform.fromScale(rect.width() / pixmap.width(), rect.height() / pixmap.height()))
        item.setPos(rect.topLeft())

    def set_drag_ghost(self, pixmap: QPixmap) -> None:
        """While moving/resizing a markup item: the item's own picture follows the mouse."""
        drag = self._drag
        if drag is None or drag["mode"] not in ("move", "resize"):
            return
        ghost = QGraphicsPixmapItem(pixmap, drag["page"])
        ghost.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        ghost.setZValue(1.4)
        drag["ghost"] = ghost
        self._fit_pixmap_item(ghost, drag["overlay"].rect())

    def linger(self, page_index: int, item: QGraphicsItem) -> None:
        """Keep a preview on screen until the page has been re-rendered with the result,
        so the new annotation never flickers in."""
        self._lingering.setdefault(page_index, []).append(item)
        QTimer.singleShot(2500, lambda: self._drop_lingering(page_index))

    def _drop_lingering(self, page_index: int) -> None:
        import shiboken6

        if not shiboken6.isValid(self):
            return  # (the window closed meanwhile)
        for item in self._lingering.pop(page_index, []):
            if shiboken6.isValid(item) and item.scene() is not None:  # (gone with a rebuilt page)
                self.scene().removeItem(item)

    def _preview_item(self, mode: str, page: PageItem, origin: QPointF) -> QGraphicsItem | None:
        style = self._draw_style
        if mode in ("area", "box"):
            item = RubberBand(page)
            item.setRect(QRectF(origin, origin))
            return item
        if mode == "text":
            return None  # text markup previews are built per word as the drag moves
        pen = _pen(style)
        if mode == "rect":
            item = QGraphicsRectItem(QRectF(origin, origin), page)
        elif mode == "ellipse":
            item = QGraphicsEllipseItem(QRectF(origin, origin), page)
        elif mode == "textbox":
            item = QGraphicsRectItem(QRectF(origin, origin), page)
            pen = QPen(style.color, 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
        elif mode in ("line", "arrow", "ink", "callout"):
            item = QGraphicsPathItem(QPainterPath(origin), page)
        elif mode == "place":
            return None  # the ghost is the preview
        else:
            return None
        item.setPen(pen)
        if mode in ("rect", "ellipse") and style.fill is not None:
            item.setBrush(QBrush(style.fill))
        if mode == "arrow":
            item.setBrush(QBrush(style.color))
        item.setOpacity(style.opacity)
        item.setZValue(3)
        return item

    def _update_text_preview(self, drag: dict, pos: QPointF) -> None:
        """Select words from the one under the press to the one under the mouse, in
        reading order, merged into one box per line."""
        for item in drag.get("words_items", []):
            if item.scene() is not None:
                self.scene().removeItem(item)
        drag["words_items"] = []
        words = drag.get("words") or []
        band = drag.get("box")
        if band is not None:
            box = QRectF(drag["origin"], pos).normalized()
            band.setRect(box)
            inside = [i for i, (rect, _key) in enumerate(words) if rect.intersects(box)]
            drag["selection"] = inside or None
            self._draw_word_marks(drag, [words[i] for i in inside])
            return
        if not words:
            return

        def nearest(p: QPointF) -> int:
            def dist(r: QRectF) -> float:
                dx = max(r.left() - p.x(), 0, p.x() - r.right())
                dy = max(r.top() - p.y(), 0, p.y() - r.bottom())
                return dx * dx + dy * dy * 4  # prefer the same line

            return min(range(len(words)), key=lambda i: dist(words[i][0]))

        start = drag.setdefault("start_word", nearest(drag["origin"]))
        end = nearest(pos)
        lo, hi = min(start, end), max(start, end)
        drag["selection"] = (lo, hi)
        self._draw_word_marks(drag, words[lo : hi + 1])

    def _over_text(self, page: PageItem, origin: QPointF) -> bool:
        if self._word_provider is None:
            return False
        return any(r.adjusted(-2, -2, 2, 2).contains(origin) for r, _key in self._word_provider(page.index))

    def _draw_word_marks(self, drag: dict, chosen) -> None:
        """The marks a text drag will make: one box per line over the chosen words."""
        lines: dict[object, QRectF] = {}
        order: list[object] = []
        for rect, key in chosen:
            if key in lines:
                lines[key] = lines[key].united(rect)
            else:
                lines[key] = QRectF(rect)
                order.append(key)
        style = drag.get("style") or self._draw_style
        for key in order:
            rect = lines[key]
            if style.text_markup == "underline":
                rect = QRectF(rect.left(), rect.bottom() - max(1.0, rect.height() * 0.08), rect.width(), max(1.0, rect.height() * 0.08))
            elif style.text_markup == "strikeout":
                mid = rect.center().y()
                rect = QRectF(rect.left(), mid - max(0.5, rect.height() * 0.04), rect.width(), max(1.0, rect.height() * 0.08))
            item = QGraphicsRectItem(rect, drag["page"])
            item.setPen(QPen(Qt.PenStyle.NoPen))
            color = QColor(style.color)
            if style.text_markup == "highlight":
                color.setAlphaF(0.45 * style.opacity)
            item.setBrush(QBrush(color))
            item.setZValue(3)
            drag["words_items"].append(item)

    def set_pointer_text_selection(self, style: DrawStyle | None) -> None:
        """With no tool chosen (Acrobat's Select tool), dragging over page text selects it."""
        self._pointer_style = style

    # --- polygons and polylines: click point by point ------------------------------------------

    def _poly_click(self, page: PageItem, origin: QPointF) -> None:
        """Add a corner. Double-click, Enter, or (polygon) a click on the first corner
        finishes; Esc drops the shape. A shape stays on the page it was started on."""
        poly = self._poly
        bounds = page.boundingRect()
        origin = QPointF(max(bounds.left(), min(bounds.right(), origin.x())), max(bounds.top(), min(bounds.bottom(), origin.y())))
        origin = self.snapped(origin)
        if poly is None:
            item = QGraphicsPathItem(page)
            item.setPen(_pen(self._draw_style))
            item.setOpacity(self._draw_style.opacity)
            item.setZValue(3)
            self._poly = {"page": page, "points": [origin], "item": item, "closed": self._draw_tool == "polygon"}
        elif poly["page"] is not page:
            return
        else:
            first = poly["points"][0]
            screen = self.mapFromScene(page.mapToScene(origin)) - self.mapFromScene(page.mapToScene(first))
            if poly["closed"] and len(poly["points"]) >= 3 and abs(screen.x()) + abs(screen.y()) <= CLOSE_PIXELS:
                self._finish_poly()
                return
            poly["points"].append(origin)
        self._update_poly(None)

    def _update_poly(self, cursor: QPointF | None) -> None:
        poly = self._poly
        points = poly["points"] + ([cursor] if cursor is not None else [])
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        if poly["closed"] and len(points) >= 3:
            path.closeSubpath()
            if self._draw_style.fill is not None:
                poly["item"].setBrush(QBrush(self._draw_style.fill))
        poly["item"].setPath(path)

    def _finish_poly(self) -> None:
        poly, self._poly = self._poly, None
        if poly is None:
            return
        points, item = poly["points"], poly["item"]
        enough = 3 if poly["closed"] else 2
        if len(points) >= enough:
            self._update_poly_final(item, points, poly["closed"])
            self.shapeDrawn.emit("polygon" if poly["closed"] else "polyline", poly["page"].index, list(points))
            self.linger(poly["page"].index, item)
        elif item.scene() is not None:
            self.scene().removeItem(item)

    def _update_poly_final(self, item: QGraphicsPathItem, points: list[QPointF], closed: bool) -> None:
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        if closed:
            path.closeSubpath()
        item.setPath(path)

    def _cancel_poly(self) -> None:
        poly, self._poly = getattr(self, "_poly", None), None
        if poly is not None and poly["item"].scene() is not None:
            self.scene().removeItem(poly["item"])

    def _start_drawing(self, page: PageItem, origin: QPointF, mode: str | None = None, style: DrawStyle | None = None) -> None:
        mode = mode or ("area" if self._area_tool else self._draw_tool)
        if mode not in UNSNAPPED_MODES:
            origin = self.snapped(origin)
        item = self._preview_item(mode, page, origin)
        self._drag = {"mode": mode, "page": page, "origin": origin, "item": item, "points": [origin]}
        if style is not None:
            self._drag["style"] = style  # a pointer text selection (not text markup)
        if mode == "text":
            self._drag["words"] = self._word_provider(page.index) if self._word_provider else []
            if style is None and getattr(self, "_text_hover", False) and not self._over_text(page, origin):
                band = RubberBand(page)  # (off the text: a box marks every word it touches)
                band.setRect(QRectF(origin, origin))
                self._drag["box"] = band
            self._update_text_preview(self._drag, origin)

    def _group_press(self, event: QMouseEvent, scene_pos: QPointF, items) -> bool:
        """Edit PDF selection: True when the press was handled here.

        * Shift+click adds an object, Ctrl+Shift+click removes one;
        * pressing on one of several selected objects drags them all;
        * pressing on the empty page starts a selection box (Shift: adds to the selection)."""
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if not shift and len(self._group) <= 1:  # a resize handle of the one selected object
            for item in items:
                if isinstance(item.parentItem(), MarkupOverlay) and item in item.parentItem().handles:
                    return False
        hit = next((i for i in items if isinstance(i, MarkupOverlay)), None)
        if hit is not None and shift:
            current = self.selected_group
            if ctrl:
                new = [i for i in current if i != hit.item_id]
            else:
                new = current if hit.item_id in current else current + [hit.item_id]
            self.set_group(new)
            self.groupChanged.emit(self.selected_group)
            if not ctrl and len(self._group) > 1:
                self._start_group_drag(hit, scene_pos, collapse=False)
            return True
        if hit is not None and len(self._group) > 1 and hit.item_id in self._group:
            self._start_group_drag(hit, scene_pos, collapse=True)
            return True
        if hit is not None:
            return False  # one object: the usual click / drag
        page = self._page_at(scene_pos)
        if page is None:
            return False
        if not shift and (self._group or self._selected_markup):
            self.set_group([])
            self.markupClicked.emit("")  # (deselect)
        origin = page.mapFromScene(scene_pos)
        box = SelectionBox(page)
        box.setRect(QRectF(origin, origin))
        self._drag = {"mode": "marquee", "page": page, "origin": origin, "item": box, "add": shift}
        return True

    def _finish_group_drag(self, drag: dict) -> None:
        if drag["mode"] == "marquee":
            box, page = drag["item"], drag["page"]
            rect = box.rect()
            if box.scene() is not None:
                self.scene().removeItem(box)
            if rect.width() < MIN_DRAG or rect.height() < MIN_DRAG:
                return  # a click on the empty page (it already deselected)
            inside = [key for key, overlay in self._markup_items.items()
                      if overlay.parentItem() is page and overlay.rect().intersects(rect)]
            ids = (self.selected_group + inside) if drag["add"] else inside
            self.set_group(ids)
            self.groupChanged.emit(self.selected_group)
            return
        delta = drag.get("delta", QPointF())
        ids = [overlay.item_id for overlay, _start in drag["members"]]
        thumbnail = drag.get("thumbnail")
        if thumbnail is not None:  # let go over a thumbnail: onto that page, same places
            if any(overlay.parentItem().index != thumbnail for overlay, _start in drag["members"]):
                rects = [start for _overlay, start in drag["members"]]
                self.markupGroupMovedToPage.emit(ids, thumbnail, self._kept_on_page(thumbnail, rects))
            else:
                for overlay, start in drag["members"]:
                    overlay.setRect(start)
            return
        target = self._page_at(drag["release_scene"])
        if target is not None and any(overlay.parentItem() is not target for overlay, _start in drag["members"]):
            # dropped on another page: each object lands where it is under the mouse
            rects = []
            for overlay, start in drag["members"]:
                moved = start.translated(delta)
                top_left = target.mapFromScene(overlay.parentItem().mapToScene(moved.topLeft()))
                rects.append(QRectF(top_left, moved.size()))
            self.markupGroupMovedToPage.emit(ids, target.index, self._kept_on_page(target.index, rects))
            return
        if abs(delta.x()) + abs(delta.y()) > 0.5:
            self.markupGroupMoved.emit(ids, QPointF(delta))
            return
        for overlay, start in drag["members"]:  # (not moved: put the frames back)
            overlay.setRect(start)
        if drag["collapse"]:  # a plain click on one of several: select just that one
            self.set_group([])
            self._selected_markup = None
            self.markupClicked.emit(drag["hit"])
            self.groupChanged.emit(self.selected_group)

    def _kept_on_page(self, page_index: int, rects: list[QRectF]) -> list[QRectF]:
        """``rects`` shifted together (as one block) so the block lies on that page."""
        bounds = self._items[page_index].boundingRect()
        block = rects[0]
        for r in rects[1:]:
            block = block.united(r)
        dx = max(0.0, bounds.left() - block.left()) - max(0.0, block.right() - bounds.right())
        dy = max(0.0, bounds.top() - block.top()) - max(0.0, block.bottom() - bounds.bottom())
        return [r.translated(dx, dy) for r in rects]

    def _start_group_drag(self, hit: MarkupOverlay, scene_pos: QPointF, *, collapse: bool) -> None:
        page = hit.parentItem()
        members = [self._markup_items[i] for i in self._group if i in self._markup_items]
        self._drag = {"mode": "group", "page": page, "origin": page.mapFromScene(scene_pos), "hit": hit.item_id,
                      "members": [(m, QRectF(m.rect())) for m in members], "collapse": collapse}
        self._raise_pages([m.parentItem() for m in members])

    def _raise_pages(self, pages) -> None:
        """While objects are dragged, their page(s) are drawn above the others, so their
        outlines and pictures stay visible over the page they are dragged onto (pages
        added later would otherwise paint over them: user request 2026-10-01)."""
        self._lower_pages()
        self._raised = list({id(p): p for p in pages}.values())
        for page in self._raised:
            page.setZValue(1)

    def _lower_pages(self) -> None:
        import shiboken6

        for page in getattr(self, "_raised", []):
            if shiboken6.isValid(page):  # (the pages may have been rebuilt meanwhile)
                page.setZValue(0)
        self._raised = []

    def _press_selected(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        """With a tool on, a press on the selected item (its corner handles or itself) moves
        or stretches it instead of using the tool: tools stay on until another is chosen
        (user 2026-10-17), and a stamp or text just placed must still be adjustable."""
        if not self._markup_interactive or self._selected_markup is None:
            return False
        selected = self._markup_items.get(self._selected_markup)
        if selected is None or not selected.isVisible():
            return False
        items = self.scene().items(scene_pos)
        page = selected.parentItem()
        for item in items:
            if item in selected.handles:
                self._drag = {"mode": "resize", "overlay": selected, "corner": item.data(0),
                              "start": QRectF(selected.rect()), "page": page, "origin": page.mapFromScene(scene_pos)}
                event.accept()
                return True
        if selected in items and selected.movable:
            self._drag = {"mode": "move", "overlay": selected, "start": QRectF(selected.rect()), "page": page,
                          "origin": page.mapFromScene(scene_pos)}
            self._raise_pages([page])
            event.accept()
            return True
        return False

    # Set by the markup controller: may a press on this item, with the current tool on, select and
    # move it instead of using the tool? (Fill & Sign's Add Text and symbols, user 2026-11-24:
    # "After adding text … I should still be able to edit anything I have added and increase /
    # decrease the size of text and move it around … like edit mode".)
    grab_with_tool: Callable[[str], bool] | None = None

    def _press_added(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if not self._markup_interactive or self.grab_with_tool is None:
            return False
        for item in self.scene().items(scene_pos):
            if isinstance(item, MarkupOverlay) and item.isVisible() and self.grab_with_tool(item.item_id):
                self.markupClicked.emit(item.item_id)
                if item.movable:
                    self._drag = {"mode": "move", "overlay": item, "start": QRectF(item.rect()),
                                  "page": item.parentItem(), "origin": item.parentItem().mapFromScene(scene_pos)}
                    self._raise_pages([item.parentItem()])
                event.accept()
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.stop_auto_scroll()  # (a click on the page stops Automatically Scroll)
        self._press_at = event.position()  # (a move starts only past the drag distance from here)
        self._pending_click = None  # (set below only by a plain press on the page with no tool on)
        if self.view_tool and event.button() == Qt.MouseButton.LeftButton and self._session is not None:
            point = event.position().toPoint()
            self._view_drag = {"y": point.y(), "zoom": self._zoom, "view": point, "scene": self.mapToScene(point)}
            if self.view_tool == "loupe":
                self._loupe_at(point)
            event.accept()
            return
        if self.hand_tool and event.button() == Qt.MouseButton.LeftButton and self._session is not None \
                and self._fields_interactive:
            # Acrobat: the Hand tool fills in fields too (user 2026-10-02: "shouldn't I be able to
            # type/select while viewing it?"); anywhere else it pans
            scene_pos = self.mapToScene(event.position().toPoint())
            under = self.scene().items(scene_pos)
            if _on_widget(under):  # (an editor, or its open list of choices)
                QGraphicsView.mousePressEvent(self, event)
                return
            field = next((i for i in under if isinstance(i, FieldOverlay) and i.fillable), None)
            if field is not None:
                self.field_click_point = field.mapFromScene(scene_pos)
                self.fieldClicked.emit(field.key)
                event.accept()
                return
        if event.button() != Qt.MouseButton.LeftButton or self._session is None or self.hand_tool:
            super().mousePressEvent(event)  # (the Hand tool: Qt pans the view)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        under = self.scene().items(scene_pos)
        if _on_widget(under):
            super().mousePressEvent(event)  # typing into an on-page editor, its open list, the item's bar
            return
        if self._editor is not None and self._editor.data(0) == "typing":
            # Typing a comment / note / added text: the first click away only finishes it
            # (user 2026-10-04: it started a new comment with the tool still on); the next
            # click moves the box, or does what the tool does.
            widget = self._editor.widget()
            if hasattr(widget, "finish"):
                widget.finish()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        if self._popup_press(event, under):  # a note's box: before any tool (it sits on top)
            return
        if self._draw_tool is None and not self._area_tool:
            from planetpdf.ui.viewer.note_popups import NoteAnchor

            anchor = next((i for i in under if isinstance(i, NoteAnchor)), None)
            if anchor is not None:  # a click on a note's item opens its box
                self.popupToggled.emit(anchor.item_id, True)
                event.accept()
                return
        # (an eraser always erases, even over the selected object: user 2026-10-18, it moved the line)
        if self._draw_tool is not None and getattr(self, "_brush", None) is None and self._press_selected(event, scene_pos):
            return  # (the item just placed: move / stretch it while the tool stays on)
        if self._draw_tool is not None and self._press_added(event, scene_pos):
            return  # (anything added: selected and moved, as with no tool on)
        if self._area_tool or self._draw_tool is not None:
            page = self._page_at(scene_pos)
            if page is not None:
                origin = page.mapFromScene(scene_pos)
                if self._draw_tool == "point":
                    self.shapeDrawn.emit("point", page.index, QRectF(origin, origin))
                elif self._draw_tool in POLY_TOOLS:
                    self._poly_click(page, origin)
                else:
                    self._start_drawing(page, origin)
                event.accept()
                return
            super().mousePressEvent(event)
            return

        additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        # (no tool on: a plain click here may add text, see pageClicked; decided before the
        # press puts away a text selection, marks, or a field)
        click_types = (not additive and self._page_at(scene_pos) is not None and not self._selected_marks
                       and self._focused_field is None and not self._highlight_data.get("selection"))
        items = self.scene().items(scene_pos)
        if self._editor is not None and self._editor in items:
            super().mousePressEvent(event)  # clicks inside the active editor
            return
        if self._markup_interactive and self._group_select and self._group_press(event, scene_pos, items):
            event.accept()
            return
        if self._markup_interactive:
            for item in items:  # resize handles first
                parent = item.parentItem()
                if isinstance(parent, MarkupOverlay) and item in parent.handles:
                    self._drag = {"mode": "resize", "overlay": parent, "corner": item.data(0), "start": QRectF(parent.rect()),
                                  "page": parent.parentItem(), "origin": parent.parentItem().mapFromScene(scene_pos)}
                    event.accept()
                    return
            for item in items:
                if isinstance(item, MarkupOverlay):
                    self.markupClicked.emit(item.item_id)
                    if item.movable:
                        self._drag = {"mode": "move", "overlay": item, "start": QRectF(item.rect()),
                                      "page": item.parentItem(), "origin": item.parentItem().mapFromScene(scene_pos)}
                        self._raise_pages([item.parentItem()])
                    event.accept()
                    return
            if self._selected_markup is not None:
                self.markupClicked.emit("")
        for item in items:
            if isinstance(item, MarkOverlay):
                self.markClicked.emit(item.mark_id, additive)
                event.accept()
                return
        if self._fields_interactive:
            for item in items:
                if isinstance(item, FieldOverlay):
                    self.field_click_point = item.mapFromScene(scene_pos)  # (a list box: which choice)
                    self.fieldClicked.emit(item.key)
                    event.accept()
                    return
        for item in items:
            if isinstance(item, LinkOverlay):
                self.linkClicked.emit(item.link_id)
                event.accept()
                return
        if self._selected_marks and not additive:
            self.markClicked.emit("", False)
        if self._focused_field is not None:
            self.fieldClicked.emit("")  # clicked away from fields
        style = getattr(self, "_pointer_style", None)
        if style is not None and self._word_provider is not None:
            page = self._page_at(scene_pos)
            if page is not None:
                origin = page.mapFromScene(scene_pos)
                if any(r.adjusted(-2, -2, 2, 2).contains(origin) for r, _key in self._word_provider(page.index)):
                    self._start_drawing(page, origin, mode="text", style=style)  # press on text: select it
                    self._drag["click_types"] = click_types  # (let go without dragging: add text)
                    event.accept()
                    return
            self.shapeDrawn.emit("text", page.index if page is not None else 0, None)  # click away: deselect
        if click_types:
            page = self._page_at(scene_pos)
            self._pending_click = (page.index, page.mapFromScene(scene_pos), event.position())
        super().mousePressEvent(event)

    KEYBOARD_BOX = QSizeF(200, 60)  # a text box placed with Enter (points)
    # Set by the markup controller: is the tool on one Enter may place? (Only its placing tools:
    # never Select Text, nor a box picked for Crop, Snapshot, Link or Marquee Zoom, where a box
    # in the middle of the page would do something nobody asked for.)
    keyboard_placing: Callable[[], bool] | None = None

    def place_by_keyboard(self) -> bool:
        """Enter with a placing tool on (user 2026-10-25: without a mouse): a stamp, signature,
        added text, note or text box goes in the middle of what shows of the current page, as
        a click there would. Then the arrow keys move it (Shift: farther). False when the
        tool on isn't one of these."""
        mode = self._draw_tool
        allowed = self.keyboard_placing is not None and self.keyboard_placing()
        if not allowed or mode not in ("place", "point", "text", "textbox", "ellipse") \
                or not 0 <= self._current_page < len(self._items):
            return False
        page = self._items[self._current_page]
        shown = page.mapFromScene(self.mapToScene(self.viewport().rect())).boundingRect()
        visible = shown.intersected(page.boundingRect())
        center = (visible if not visible.isEmpty() else page.boundingRect()).center()
        index = self._current_page
        if mode == "place":
            ghost, self._ghost_item = self._ghost_item, None
            if ghost is not None and ghost.scene() is not None:
                self.scene().removeItem(ghost)
            self.shapeDrawn.emit("place", index, self._ghost_rect(center))
        elif mode == "textbox":
            size = self.KEYBOARD_BOX
            box = QRectF(center.x() - size.width() / 2, center.y() - size.height() / 2, size.width(), size.height())
            self.shapeDrawn.emit(mode, index, box)
        elif mode == "ellipse":  # (the Fill & Sign circle: a click, the size of the last one)
            self.shapeDrawn.emit("ellipse", index, QRectF(center, center))
        else:  # (a click: added text, a symbol, a sticky note)
            self.shapeDrawn.emit("point", index, QRectF(center, center))
        from planetpdf.ui import accessibility

        accessibility.announce(self, f"Placed on page {index + 1}. The arrow keys move it; Shift and an arrow key "
                                     "moves it farther; Delete removes it")
        return True

    def _ghost_rect(self, center: QPointF) -> QRectF:
        size = self._ghost_size
        return QRectF(center.x() - size.width() / 2, center.y() - size.height() / 2, size.width(), size.height())

    @staticmethod
    def _fit_aspect(box: QRectF, size: QSizeF) -> QRectF:
        if size.width() <= 0 or size.height() <= 0:
            return box
        scale = min(box.width() / size.width(), box.height() / size.height())
        return QRectF(box.left(), box.top(), size.width() * scale, size.height() * scale)

    def _loupe_at(self, view_point: QPoint) -> None:
        page = self._page_at(self.mapToScene(view_point))
        if page is not None:
            self.loupeMoved.emit(page.index, page.mapFromScene(self.mapToScene(view_point)))

    def _dynamic_zoom_to(self, view_drag: dict, y: int) -> None:
        """Dynamic Zoom: up zooms in, down zooms out, about the point first pressed."""
        self.set_zoom(view_drag["zoom"] * 1.01 ** (view_drag["y"] - y))
        moved = self.mapFromScene(view_drag["scene"]) - view_drag["view"]
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + moved.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + moved.y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.track_cursor or self.rulers_shown:  # (Cursor Coordinates, rulers)
            self._track_cursor(event.position().toPoint())
        if self.view_tool:
            view_drag = getattr(self, "_view_drag", None)
            if view_drag is not None:
                if self.view_tool == "dynamic_zoom":
                    self._dynamic_zoom_to(view_drag, event.position().toPoint().y())
                else:
                    self._loupe_at(event.position().toPoint())
            event.accept()
            return
        drag = self._drag
        # While a drawing tool is on (the Eraser's circle, the crosshair, the I-beam), the
        # objects under the mouse must not swap in their own cursors (four-way arrows,
        # pointing hand): Qt does that in QGraphicsView.mouseMoveEvent, so it's skipped.
        tool_on = self._area_tool or self._draw_tool is not None
        if drag is None and self._popup_cursor(event):
            return  # (over a note's box: its own cursor, whatever tool is on)
        if drag is None and self._poly is not None:
            self._update_poly(self._clamped(self._poly["page"], event.position().toPoint()))
            event.accept() if tool_on else super().mouseMoveEvent(event)
            return
        if drag is None and getattr(self, "_text_hover", False):
            scene_pos = self.mapToScene(event.position().toPoint())
            page = self._page_at(scene_pos)
            over = page is not None and self._over_text(page, page.mapFromScene(scene_pos))
            shape = Qt.CursorShape.IBeamCursor if over else Qt.CursorShape.CrossCursor
            if self.viewport().cursor().shape() != shape:
                self.viewport().setCursor(shape)
            event.accept()
            return
        if drag is None:
            # Hovering with a stamp/signature armed: it follows the cursor.
            if self._draw_tool == "place" and self._ghost_pixmap is not None:
                page = self._page_at(self.mapToScene(event.position().toPoint()))
                if page is not None:
                    center = page.mapFromScene(self.mapToScene(event.position().toPoint()))
                    self._place_ghost(page, self._ghost_rect(center))
            if tool_on and not getattr(self, "_brush", None):
                self._tool_hover(self.mapToScene(event.position().toPoint()))
            if tool_on:
                event.accept()
            else:
                super().mouseMoveEvent(event)
                self._pointer_hover(self.mapToScene(event.position().toPoint()))
            return
        page: PageItem = drag["page"]
        if drag["mode"] in ("move", "resize", "group") and not drag.get("past"):
            # A click's wobble isn't a move (security review 2026-11-25: a click meant to add text
            # nudged an item, and the alignment snap then pulled it up to 6 points)
            press = getattr(self, "_press_at", None)
            if press is not None and (event.position() - press).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            drag["past"] = True
        if drag["mode"] in ("move", "group") and self.drag_page_picker is not None:
            # Over a thumbnail in the Pages panel: show that page, to drop onto it
            hovered = self.drag_page_picker(event.globalPosition().toPoint())
            drag["thumbnail"] = hovered
            if hovered is not None and hovered != self._current_page:
                self.go_to_page(hovered)
        if drag["mode"] == "group":  # several objects move together
            pos = page.mapFromScene(self.mapToScene(event.position().toPoint()))
            delta = pos - drag["origin"]
            mine = [(o, s) for o, s in drag["members"] if o.parentItem() is page]
            if mine:  # lined up with the other objects as a whole (their outer box)
                box = QRectF(mine[0][1])
                for _o, start in mine[1:]:
                    box = box.united(start)
                delta += self._aligned(page, box.translated(delta), {o.item_id for o, _s in drag["members"]})
            drag["delta"] = delta
            for overlay, start in drag["members"]:
                overlay.setRect(start.translated(delta))
            event.accept()
            return
        if drag["mode"] == "marquee":
            drag["item"].setRect(QRectF(drag["origin"], self._clamped(page, event.position().toPoint())).normalized())
            event.accept()
            return
        if drag["mode"] == "move" and drag["overlay"].movable:
            # Moving an item may leave its page: it can be dropped on another one.
            pos = page.mapFromScene(self.mapToScene(event.position().toPoint()))
        else:
            pos = self._clamped(page, event.position().toPoint())
        mode = drag["mode"]
        if mode not in UNSNAPPED_MODES and mode != "move":
            pos = self.snapped(pos)  # (View ▸ Rulers & Grids ▸ Snap to Grid)
        item = drag.get("item")
        if mode in ("rect", "ellipse") and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift: a square / a perfect circle (Acrobat), growing the way the mouse goes
            side = max(abs(pos.x() - drag["origin"].x()), abs(pos.y() - drag["origin"].y()))
            pos = QPointF(drag["origin"].x() + (side if pos.x() >= drag["origin"].x() else -side),
                          drag["origin"].y() + (side if pos.y() >= drag["origin"].y() else -side))
        box = QRectF(drag["origin"], pos).normalized()
        if mode in ("area", "rect", "textbox", "box"):
            item.setRect(box)
        elif mode == "ellipse":
            item.setRect(box)
        elif mode == "line":
            path = QPainterPath(drag["origin"])
            path.lineTo(pos)
            item.setPath(path)
            drag["points"] = [drag["origin"], pos]
        elif mode == "arrow":
            item.setPath(arrow_path(drag["origin"], pos, self._draw_style.width))
            drag["points"] = [drag["origin"], pos]
        elif mode == "ink":
            drag["points"].append(pos)
            # Our own path, not item.path(): a path holding only its start point comes
            # back empty, so the first stroke segment started at the page's top-left
            # corner (user bug 2026-10-02, the eraser's preview).
            path = drag.setdefault("path", QPainterPath(drag["origin"]))
            path.lineTo(pos)
            item.setPath(QPainterPath(path))
        elif mode == "text":
            self._update_text_preview(drag, pos)
        elif mode == "callout":
            item.setPath(callout_preview_path(drag["origin"], pos))
            drag["points"] = [drag["origin"], pos]
        elif mode == "place":
            if box.width() >= MIN_DRAG and box.height() >= MIN_DRAG:
                self._place_ghost(page, self._fit_aspect(box, self._ghost_size))
            else:
                self._place_ghost(page, self._ghost_rect(drag["origin"]))
        elif mode in ("popup_move", "popup_resize"):
            from planetpdf.ui.viewer import note_popups as NP

            bounds = drag["page"].boundingRect()
            start, origin = drag["start"], drag["origin"]
            if mode == "popup_move":
                box = NP.moved(start, origin, pos, bounds)
            else:
                data = drag["popup"].data_
                box = NP.resized(start, origin, pos, bounds, drag["corner"], data.text, data.replies)
            drag["popup"].set_box(box)
        elif mode in ("move", "resize"):
            overlay = drag["overlay"]
            if not drag.get("started"):
                drag["started"] = True
                self.set_item_bar(None)
                self.markupDragStarted.emit(overlay.item_id)  # controller supplies the ghost
            if mode == "move":
                moved = drag["start"].translated(pos - drag["origin"])
                if self.snap_to_grid:  # the moved item's top-left corner lands on the grid
                    moved.moveTopLeft(self.snapped(moved.topLeft()))
                else:  # lined up with the objects near it (dotted guides)
                    moved.translate(self._aligned(page, moved, {overlay.item_id}))
                overlay.setRect(moved)
            else:
                stretch = overlay.stretchable and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                overlay.setRect(_resized(drag["start"], drag["corner"], pos, overlay.keep_aspect and not stretch))
            overlay.place_handles()
            if drag.get("ghost") is not None:
                self._fit_pixmap_item(drag["ghost"], overlay.rect())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.view_tool:
            self._view_drag = None
            event.accept()
            return
        drag = self._drag
        if drag is None:
            pending, self._pending_click = getattr(self, "_pending_click", None), None
            super().mouseReleaseEvent(event)
            if pending is not None and event.button() == Qt.MouseButton.LeftButton \
                    and (event.position() - pending[2]).manhattanLength() < QApplication.startDragDistance():
                self.pageClicked.emit(pending[0], pending[1])
            return
        self._drag = None
        self._set_align_lines([])  # (the guides go with the drag)
        self._lower_pages()
        mode, page = drag["mode"], drag["page"].index
        item = drag.get("item")
        if mode in ("popup_move", "popup_resize"):
            popup = drag["popup"]
            box = QRectF(popup.rect())
            if (abs(box.left() - drag["start"].left()) > 0.5 or abs(box.top() - drag["start"].top()) > 0.5
                    or abs(box.width() - drag["start"].width()) > 0.5 or abs(box.height() - drag["start"].height()) > 0.5):
                self.popupMoved.emit(popup.item_id, box)
            event.accept()
            return
        if mode in ("group", "marquee"):
            drag["release_scene"] = self.mapToScene(event.position().toPoint())
            self._finish_group_drag(drag)
            event.accept()
            return
        keep: list[QGraphicsItem] = []  # previews that stay until the result is rendered

        if mode == "area":
            rect = item.rect()
            if rect.width() >= MIN_DRAG and rect.height() >= MIN_DRAG:
                self.areaDrawn.emit(page, rect)
        elif mode in ("rect", "ellipse", "box", "textbox"):
            self.shapeDrawn.emit(mode, page, item.rect())  # tiny = a click; the controller decides
            if mode in ("rect", "ellipse"):
                keep.append(item)
        elif mode in ("line", "arrow"):
            a, b = drag["points"][0], drag["points"][-1]
            if QLineF(a, b).length() >= MIN_DRAG:
                self.shapeDrawn.emit(mode, page, [a, b])
                keep.append(item)
        elif mode == "ink":
            if len(drag["points"]) >= 2:
                self.shapeDrawn.emit("ink", page, list(drag["points"]))
                keep.append(item)
        elif mode == "callout":
            tip, end = drag["points"][0], drag["points"][-1]
            if QLineF(tip, end).length() < MIN_DRAG:  # a click: the box goes up and to the right
                end = QPointF(tip.x() + 40, tip.y() - 50)
            self.shapeDrawn.emit("callout", page, [tip, end])
        elif mode == "text" and drag.get("style") is not None:  # pointer text selection
            pos = self._clamped(drag["page"], event.position().toPoint())
            moved = QLineF(drag["origin"], pos).length() >= MIN_DRAG
            self.shapeDrawn.emit("text", page, drag.get("selection") if moved else None)  # a click: deselect
            if not moved and drag.get("click_types"):
                self.pageClicked.emit(page, QPointF(drag["origin"]))
        elif mode == "text":
            band = drag.get("box")
            if band is not None and band.scene() is not None:
                self.scene().removeItem(band)
            if drag.get("selection") is not None:
                self.shapeDrawn.emit("text", page, drag["selection"])
                keep += drag.get("words_items", [])
            else:  # a click, or a drag that caught no words (the Note tool: a sticky note there)
                origin = drag["origin"]
                self.shapeDrawn.emit("point", page, QRectF(origin, origin))
        elif mode == "place":
            pos = self._clamped(drag["page"], event.position().toPoint())
            box = QRectF(drag["origin"], pos).normalized()
            if box.width() < MIN_DRAG or box.height() < MIN_DRAG:
                box = self._ghost_rect(drag["origin"])
            else:
                box = self._fit_aspect(box, self._ghost_size)
            ghost, self._ghost_item = self._ghost_item, None
            self.shapeDrawn.emit("place", page, box)
            if ghost is not None:
                ghost.setOpacity(1.0)
                keep.append(ghost)
        elif mode in ("move", "resize"):
            overlay = drag["overlay"]
            new, old = overlay.rect(), drag["start"]
            ghost = drag.get("ghost")
            source = drag["page"]
            target = self._page_at(self.mapToScene(event.position().toPoint())) if mode == "move" else None
            thumbnail = drag.get("thumbnail") if mode == "move" else None
            if thumbnail is not None:  # let go over a thumbnail in the Pages panel
                if ghost is not None and ghost.scene() is not None:
                    self.scene().removeItem(ghost)
                if thumbnail != source.index:  # onto that page, where it was on its own
                    [kept] = self._kept_on_page(thumbnail, [QRectF(old)])
                    self.markupMovedToPage.emit(overlay.item_id, thumbnail, kept)
                else:
                    overlay.setRect(old)
                    overlay.place_handles()
                    if drag.get("started"):
                        self.markupDragCanceled.emit(overlay.item_id)
            elif target is not None and target is not source:
                # Dropped on another page: same place under the mouse, kept on that page.
                top_left = target.mapFromScene(source.mapToScene(new.topLeft()))
                bounds = target.boundingRect()
                x = min(max(top_left.x(), bounds.left()), bounds.right() - new.width())
                y = min(max(top_left.y(), bounds.top()), bounds.bottom() - new.height())
                if ghost is not None and ghost.scene() is not None:
                    self.scene().removeItem(ghost)
                self.markupMovedToPage.emit(overlay.item_id, target.index, QRectF(x, y, new.width(), new.height()))
            elif max(abs(new.left() - old.left()), abs(new.top() - old.top()), abs(new.width() - old.width()),
                     abs(new.height() - old.height())) > 0.5:
                if mode == "move":  # (dropped in the gap between pages: keep it on its own page)
                    bounds = source.boundingRect()
                    new = QRectF(min(max(new.left(), bounds.left()), bounds.right() - new.width()),
                                 min(max(new.top(), bounds.top()), bounds.bottom() - new.height()), new.width(), new.height())
                self.markupMoved.emit(overlay.item_id, QRectF(new))
                if ghost is not None:
                    keep.append(ghost)
            else:
                if ghost is not None and ghost.scene() is not None:
                    self.scene().removeItem(ghost)
                if drag.get("started"):
                    self.markupDragCanceled.emit(overlay.item_id)
                elif mode == "move":
                    self.markupClickReleased.emit(overlay.item_id)

        import shiboken6  # (a move can rebuild the pages, deleting these previews already)

        for leftover in [item, *drag.get("words_items", [])]:
            if leftover is not None and shiboken6.isValid(leftover) and leftover not in keep and leftover.scene() is not None:
                self.scene().removeItem(leftover)
        for kept in keep:
            if shiboken6.isValid(kept) and kept.scene() is not None:
                self.linger(page, kept)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.view_tool:
            self.mousePressEvent(event)
            return
        if self._poly is not None:  # (the first click of the double-click added the last point)
            self._finish_poly()
            event.accept()
            return
        from planetpdf.ui.viewer.note_popups import NotePopupItem

        for item in self.scene().items(self.mapToScene(event.position().toPoint())):
            if isinstance(item, NotePopupItem):  # a note's box: edit its text (or the reply's)
                self._drag = None
                self.popupEditRequested.emit(item.text_target(item.parentItem().mapFromScene(
                    self.mapToScene(event.position().toPoint()))))
                event.accept()
                return
        if self._markup_interactive:
            for item in self.scene().items(self.mapToScene(event.position().toPoint())):
                if isinstance(item, MarkupOverlay):
                    self.markupDoubleClicked.emit(item.item_id)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._markup_interactive:
            for item in self.scene().items(self.mapToScene(event.pos())):
                if isinstance(item, MarkupOverlay):
                    self.markupClicked.emit(item.item_id)
                    self.markupContextMenu.emit(item.item_id, event.globalPos())
                    event.accept()
                    return
        if self._session is not None:
            self.pageContextMenu.emit(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    fieldKey = Signal(str, int)  # (focused field key, Qt key) for keys a field handles

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.view_tool and event.key() == Qt.Key.Key_Escape:  # Dynamic Zoom / Loupe off
            self.set_view_tool(None)
            event.accept()
            return
        if self.auto_scrolling and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.stop_auto_scroll()
                event.accept()
                return
            speed = {Qt.Key.Key_Plus: 1, Qt.Key.Key_Equal: 1, Qt.Key.Key_Minus: -1, Qt.Key.Key_Underscore: -1}.get(key)
            if speed:  # + faster, - slower
                self.autoScrollSpeedChanged.emit(self.auto_scroll_speed(speed))
                event.accept()
                return
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.set_auto_direction(-1 if key == Qt.Key.Key_Up else 1)
                event.accept()
                return
        if getattr(self, "_brush", None) and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            # the Eraser: + bigger, - smaller (user 2026-10-02); "=" / "_" are the same keys unshifted
            step = {Qt.Key.Key_Plus: 1, Qt.Key.Key_Equal: 1, Qt.Key.Key_Minus: -1, Qt.Key.Key_Underscore: -1}.get(event.key())
            if step:
                self.brushStep.emit(step)
                event.accept()
                return
        if self._layout.paged and event.key() in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp) and not event.modifiers():
            # page at a time: Page Down / Up show the next / previous page(s), not a scroll
            self.turn_page(1 if event.key() == Qt.Key.Key_PageDown else -1)
            event.accept()
            return
        if self._poly is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_poly()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() \
                and self._drag is None and self._editor is None and self.scene().focusItem() is None \
                and self.place_by_keyboard():  # (never the Enter that finishes or breaks typed text)
            event.accept()
            return
        if self._poly is not None and event.key() == Qt.Key.Key_Escape:
            self._cancel_poly()  # (Esc again puts the tool away)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and (self._area_tool or self._draw_tool or self._drag is not None):
            was_area = self._area_tool
            self._cancel_drag()
            if was_area:
                self.areaToolCanceled.emit()
            elif self._draw_tool:
                self.drawToolCanceled.emit()
            event.accept()
            return
        if self._focused_field is not None and self._editor is None and event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        ):
            self.fieldKey.emit(self._focused_field, event.key())  # e.g. Space toggles a checkbox
            event.accept()
            return
        super().keyPressEvent(event)

    def refresh(self) -> None:
        """Drop cached renders and repaint (call after page content changes)."""
        self._cache.clear()
        self.viewport().update()

    def refresh_page(self, index: int) -> None:
        self._cache.pop(index, None)
        if 0 <= index < len(self._items):
            self._items[index].update()

    @property
    def page_count(self) -> int:
        return len(self._items)

    # --- navigation ------------------------------------------------------------

    @property
    def current_page(self) -> int:
        return self._current_page

    # --- page at a time (Single Page View / Two Page View) ---------------------------

    def _show_row(self, row: int, *, at_bottom: bool = False) -> None:
        """Show only this row of pages: the scene is just that row, so scrolling (when
        zoomed in) stays within it."""
        row = max(0, min(row, len(self._row_tops) - 1))
        self._shown_row = row
        for index, item in enumerate(self._items):
            item.setVisible(self._page_row[index] == row)
        rect = self.scene().sceneRect()
        top = self._row_tops[row] - PAGE_GAP
        self.scene().setSceneRect(rect.x(), top, rect.width(), self._row_heights[row] + 2 * PAGE_GAP)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum() if at_bottom else bar.minimum())

    def turn_page(self, step: int) -> bool:
        """Page Down / Page Up in a page-at-a-time view: the next / previous page (or
        pair). Returns False at the ends (and when the view scrolls)."""
        if not self._layout.paged or not self._items or self._shown_row is None:
            return False
        row = self._shown_row + step
        if not 0 <= row < len(self._row_tops):
            return False
        self._remember(self._row_first_page[row])
        self._show_row(row, at_bottom=step < 0 and self._turned_by_wheel)
        self._current_page = self._row_first_page[row]
        self.currentPageChanged.emit(self._current_page)
        if self._zoom_mode in (ZoomMode.FIT_PAGE, ZoomMode.FIT_HEIGHT, ZoomMode.FIT_VISIBLE):
            self._apply_zoom_mode()  # (pages of different sizes)
        return True

    # --- Automatically Scroll (Shift+Ctrl+H, Acrobat; user 2026-10-02) ------------------

    @property
    def auto_scrolling(self) -> bool:
        return self._auto_timer.isActive()

    def toggle_auto_scroll(self) -> bool:
        """Start (forward, or backward from the last page) or stop scrolling by itself."""
        if self.auto_scrolling:
            self.stop_auto_scroll()
            return False
        if not self._items:
            return False
        self._auto_direction = -1 if self._current_page >= len(self._items) - 1 else 1
        self._auto_carry = 0.0
        self._auto_dwell = 0
        self._auto_last = time.monotonic()
        self._auto_timer.start()
        self.autoScrollChanged.emit(True)
        return True

    def stop_auto_scroll(self) -> None:
        if self._auto_timer.isActive():
            self._auto_timer.stop()
            self.autoScrollChanged.emit(False)

    def auto_scroll_speed(self, step: int) -> float:
        """+ / -: the next speed up / down; returns it (screen pixels per second)."""
        self._auto_speed = max(0, min(len(AUTO_SCROLL_SPEEDS) - 1, self._auto_speed + step))
        return AUTO_SCROLL_SPEEDS[self._auto_speed]

    def set_auto_direction(self, direction: int) -> None:
        """Up / Down while scrolling by itself: which way it goes."""
        self._auto_direction = -1 if direction < 0 else 1

    def _auto_tick(self) -> None:
        now = time.monotonic()
        elapsed, self._auto_last = min(now - self._auto_last, 0.2), now
        self._auto_carry += AUTO_SCROLL_SPEEDS[self._auto_speed] * elapsed
        pixels = int(self._auto_carry)
        if not pixels:
            return
        self._auto_carry -= pixels
        bar = self.verticalScrollBar()
        down = self._auto_direction > 0
        at_end = bar.value() >= bar.maximum() if down else bar.value() <= bar.minimum()
        if at_end:
            if self._layout.paged:
                # stay on the page for as long as scrolling past it would take (a whole
                # window's height when it fits, else half a second), then turn it
                self._auto_dwell += pixels
                fits = bar.maximum() == bar.minimum()
                wait = self.viewport().height() if fits else AUTO_SCROLL_SPEEDS[self._auto_speed] / 2
                if self._auto_dwell < wait:
                    return
            self._auto_dwell = 0
            if self._layout.paged and self.turn_page(1 if down else -1):
                if not down:
                    bar.setValue(bar.maximum())  # (backward: start at the bottom of that page)
                return
            self.stop_auto_scroll()  # the start / end of the document
            return
        bar.setValue(bar.value() + (pixels if down else -pixels))

    def step_page(self, step: int) -> None:
        """Next / Previous Page: in a page-at-a-time view the whole page or pair turns."""
        if self._layout.paged:
            self.turn_page(step)
        else:
            self.go_to_page(self._current_page + step)

    def go_to_page(self, index: int) -> None:
        if not self._items:
            return
        index = max(0, min(index, len(self._items) - 1))
        if self._layout.paged:
            self._remember(index)
            row = self._page_row[index]
            if row != self._shown_row:
                self._show_row(row)
            if index != self._current_page:
                self._current_page = index
                self.currentPageChanged.emit(index)
            return
        self._remember(index)
        top = self._row_tops[self._page_row[index]] - PAGE_GAP / 2
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.mapFromScene(QPointF(0, top)).y())
        self._update_current_page(prefer=index)
        # Near the end of a document the view can't scroll that far; the page asked
        # for is still the current one (it's on screen).
        if self._current_page != index and bar.value() >= bar.maximum():
            self._current_page = index
            self.currentPageChanged.emit(index)

    # --- view history (Previous View / Next View, Alt+Left / Alt+Right) ----------------

    HISTORY = 100

    def _position(self) -> tuple[int, float]:
        """(page, y on that page) at the top of the window: survives zoom changes."""
        page = self._current_page
        if not 0 <= page < len(self._items):
            return (0, 0.0)
        return (page, self._items[page].mapFromScene(self.mapToScene(0, 0)).y())

    def _remember(self, destination: int) -> None:
        """Before a jump to another page: note where we were."""
        if not self._items or destination == self._current_page or getattr(self, "_jumping", False):
            return
        position = self._position()
        if not self._back or self._back[-1] != position:
            self._back.append(position)
            self._back = self._back[-self.HISTORY:]
        self._forward = []
        self.historyChanged.emit()

    def _restore(self, position: tuple[int, float]) -> None:
        page, y = position
        if not 0 <= page < len(self._items):
            return
        self._jumping = True
        try:
            top = self._items[page].mapToScene(QPointF(0, y)).y()
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self.mapFromScene(QPointF(0, top)).y())
            self._update_current_page(prefer=page)
        finally:
            self._jumping = False

    def can_go_back(self) -> bool:
        return bool(getattr(self, "_back", None))

    def can_go_forward(self) -> bool:
        return bool(getattr(self, "_forward", None))

    def go_back(self) -> None:
        if self.can_go_back():
            self._forward.append(self._position())
            self._restore(self._back.pop())
            self.historyChanged.emit()

    def go_forward(self) -> None:
        if self.can_go_forward():
            self._back.append(self._position())
            self._restore(self._forward.pop())
            self.historyChanged.emit()

    def _update_current_page(self, *_args, prefer: int | None = None) -> None:
        if not self._items:
            return
        if self._layout.paged:  # (scrolling within the shown page(s) doesn't change the page)
            if prefer is not None and self._page_row[prefer] == self._shown_row and prefer != self._current_page:
                self._current_page = prefer
                self.currentPageChanged.emit(prefer)
            return
        # The row crossing the point one third down the viewport is current; its first
        # page is the current page (or ``prefer``, if that page is in the same row).
        probe = self.mapToScene(self.viewport().rect().center().x(), self.viewport().height() // 3)
        row = max(0, bisect.bisect_right(self._row_tops, probe.y()) - 1)
        index = self._row_first_page[row]
        if prefer is not None and self._page_row[prefer] == row:
            index = prefer
        elif self._current_page >= 0 and self._page_row[self._current_page] == row:
            index = self._current_page  # stay on the right-hand page while scrolling within a row
        if index != self._current_page:
            self._current_page = index
            self.currentPageChanged.emit(index)

    # --- zoom ------------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def zoom_mode(self) -> ZoomMode:
        return self._zoom_mode

    def _points_to_pixels(self) -> float:
        return self.logicalDpiX() / 72.0

    def set_zoom(
        self, zoom: float, *, mode: ZoomMode = ZoomMode.CUSTOM, anchor_mouse: bool = False
    ) -> None:
        self._zoom_mode = mode
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        factor = zoom * self._points_to_pixels()
        if abs(self.transform().m11() - factor) < 1e-6:
            return
        self._zoom = zoom
        if anchor_mouse:
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.setTransform(QTransform.fromScale(factor, factor))
        else:
            # Keep whatever is at the top of the viewport at the top.
            top = self.mapToScene(0, 0).y()
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
            self.setTransform(QTransform.fromScale(factor, factor))
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self.mapFromScene(QPointF(0, top)).y())
        self._settle_timer.start()
        self._update_current_page()
        if getattr(self, "_brush", None):  # (the eraser circle follows the zoom)
            self._update_cursor()
        self.zoomChanged.emit(zoom)

    def zoom_in(self) -> None:
        self.set_zoom(next_zoom(self._zoom, up=True))

    def zoom_out(self) -> None:
        self.set_zoom(next_zoom(self._zoom, up=False))

    def fit_width(self) -> None:
        self._zoom_mode = ZoomMode.FIT_WIDTH
        self._apply_zoom_mode()

    def fit_page(self) -> None:
        self._zoom_mode = ZoomMode.FIT_PAGE
        self._apply_zoom_mode()

    def fit_height(self) -> None:
        self._zoom_mode = ZoomMode.FIT_HEIGHT
        self._apply_zoom_mode()

    # Fit Visible asks the window where the page has anything drawn: (page index) ->
    # QRectF in the page's displayed coordinates, or None (blank: fits the width)
    content_bounds = None

    def fit_visible(self) -> None:
        self._zoom_mode = ZoomMode.FIT_VISIBLE
        self._apply_zoom_mode()

    def zoom_to_rect(self, page_index: int, rect: QRectF) -> None:
        """Marquee Zoom: fill the window with ``rect`` (displayed page coordinates)."""
        if not 0 <= page_index < len(self._items) or rect.width() < 1 or rect.height() < 1:
            return
        viewport = self.viewport().size()
        scale = self._points_to_pixels()
        zoom = 0.95 * min(viewport.width() / (rect.width() * scale), viewport.height() / (rect.height() * scale))
        self.set_zoom(zoom)
        self.centerOn(self._items[page_index].mapToScene(rect.center()))
        self._update_current_page(prefer=page_index)

    def _apply_zoom_mode(self) -> None:
        if not self._items:
            return
        if self._zoom_mode is ZoomMode.CUSTOM:
            self.set_zoom(self._zoom)
            return
        scale = self._points_to_pixels()
        viewport = self.viewport().size()
        avail_w = max(50, viewport.width() - 2 * PAGE_GAP * scale)
        zoom = avail_w / (self._content_width * scale)
        if self._zoom_mode in (ZoomMode.FIT_PAGE, ZoomMode.FIT_HEIGHT):
            # Fit one row (or two rows for 2x2) starting at the current row.
            first = self._page_row[max(self._current_page, 0)]
            rows = self._row_heights[first : first + self._layout.rows_to_fit]
            if len(rows) < self._layout.rows_to_fit:  # at the end: use the last full set
                rows = self._row_heights[-self._layout.rows_to_fit :]
            fit_h = sum(rows) + self.page_gap * (len(rows) - 1)
            avail_h = max(50, viewport.height() - PAGE_GAP * scale)
            by_height = avail_h / (fit_h * scale)
            zoom = by_height if self._zoom_mode is ZoomMode.FIT_HEIGHT else min(zoom, by_height)
        visible = None
        if self._zoom_mode is ZoomMode.FIT_VISIBLE and self.content_bounds is not None:
            page = self._items[max(self._current_page, 0)]
            box = self.content_bounds(page.index)
            if box is not None and box.width() > 1:
                visible = page.mapRectToScene(box)
                width = visible.width() * (2 if self._layout.columns == 2 else 1) + (self.page_gap if self._layout.columns == 2 else 0)
                zoom = avail_w / (width * scale)
        self.set_zoom(zoom, mode=self._zoom_mode)
        if visible is not None and self._layout.columns == 1:  # the printed part in the middle
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() + self.mapFromScene(visible.center()).x() - self.viewport().width() // 2)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            if steps:
                self.set_zoom(self._zoom * ZOOM_STEP**steps, anchor_mouse=True)
            event.accept()
            return
        if self._layout.paged and event.angleDelta().y():
            # page at a time: the wheel scrolls within the page(s), and past the bottom /
            # top turns to the next / previous one (a short pause, so one flick turns one)
            bar = self.verticalScrollBar()
            down = event.angleDelta().y() < 0
            at_edge = bar.value() >= bar.maximum() if down else bar.value() <= bar.minimum()
            if at_edge:
                now = time.monotonic()
                if now - self._last_wheel_turn > WHEEL_TURN_PAUSE:
                    self._turned_by_wheel = True
                    try:
                        if self.turn_page(1 if down else -1):
                            self._last_wheel_turn = now
                    finally:
                        self._turned_by_wheel = False
                event.accept()
                return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_rulers()
        self._apply_zoom_mode()

    # --- rendering -------------------------------------------------------------

    def _render_scale(self) -> float:
        return self._zoom * self._points_to_pixels() * self.devicePixelRatioF()

    def pixmap_for(self, index: int) -> QPixmap | None:
        """Best available pixmap for a page; schedules a sharper render if needed."""
        entry = self._cache.get(index)
        wanted = self._render_scale()
        if entry is None or (abs(entry[0] - wanted) > 1e-3 and not self._settle_timer.isActive()):
            self._pending.add(index)
            if not self._render_timer.isActive():
                self._render_timer.start()
        if entry is None:
            return None
        self._cache.move_to_end(index)
        return entry[1]

    @staticmethod
    def _pixmap_bytes(pixmap: QPixmap) -> int:
        return pixmap.width() * pixmap.height() * max(pixmap.depth(), 8) // 8

    def cache_bytes(self) -> int:
        return sum(self._pixmap_bytes(p) for _s, p in self._cache.values())

    def set_cache_limit(self, megabytes: int) -> None:
        self.cache_limit = max(16, int(megabytes)) * 1024 * 1024
        self.trim_cache()

    def trim_cache(self, visible: QRectF | None = None) -> None:
        """Drops the least recently shown pages until the cache fits its limit. Pages on
        screen stay even when they alone are over it (dropping one would only render it
        again, over and over)."""
        if self.cache_bytes() <= self.cache_limit:
            return
        visible = self._visible_scene_rect() if visible is None else visible
        total = self.cache_bytes()
        for index in list(self._cache):  # oldest first
            if total <= self.cache_limit:
                break
            if index < len(self._items) and self._items[index].sceneBoundingRect().intersects(visible):
                continue
            total -= self._pixmap_bytes(self._cache.pop(index)[1])

    def _visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _render_next(self) -> None:
        if self._session is None or not self._pending:
            self._pending.clear()
            return
        visible = self._visible_scene_rect()
        scale = self._render_scale()
        while self._pending:
            index = self._pending.pop()
            if index >= len(self._items):
                continue
            item = self._items[index]
            if not item.sceneBoundingRect().intersects(visible):
                continue
            entry = self._cache.get(index)
            if entry is not None and abs(entry[0] - scale) <= 1e-3:
                continue
            self._cache[index] = (scale, to_pixmap(render_page(self._session, index, scale)))
            self._cache.move_to_end(index)
            self.trim_cache(visible)
            item.update()
            self._drop_lingering(index)  # the real result is on screen now
            break
        if self._pending:
            self._render_timer.start()
