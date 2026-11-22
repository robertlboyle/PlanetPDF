"""Edit mode (change the page itself: retype or delete lines of text, add text, move,
resize, replace, delete or add images) and Prepare Form mode (create, move, resize
and delete form fields; edit their properties).

Both reuse the view's markup overlay layer, with their own item ids ("txt:", "img:",
"fld:"). While one of these modes is on, the markup controller is suspended (its
overlays are hidden and it ignores the view's signals).

Undo: content edits are PageContentCommand (page snapshots); field changes are
FieldDesignCommand (specs, done once before pushing so errors show before anything
changes).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QObject, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QMenu, QToolBar

from planetpdf.core import content_edit as E
from planetpdf.core.document import PdfError
from planetpdf.core.forms import design as D
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind
from planetpdf.core.redaction.marks import from_display, to_display
from planetpdf.ui.commands import FieldDesignCommand, PageContentCommand
from planetpdf.ui.forms_controller import field_key, split_key
from planetpdf.ui.inline_editor import TEXT_FONT_FAMILIES, InlineTextEditor, grown_rect

_KIND_LABELS = {
    FieldKind.TEXT: "Text field", FieldKind.DATE: "Date field", FieldKind.CHECKBOX: "Check box",
    FieldKind.RADIO: "Radio button", FieldKind.COMBO: "Dropdown", FieldKind.LIST: "List box",
    FieldKind.SIGNATURE: "Signature field", FieldKind.BUTTON: "Button",
}

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)"
SERIF_FAMILIES = ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman", "Noto Serif"]
MONO_FAMILIES = ["Courier New", "Courier", "Liberation Mono", "Nimbus Mono PS"]
GHOST_ZOOM = 2.0

# Field tool -> (label, default size when clicked, name base), in Acrobat's order (user
# screenshot 2026-10-03). "image_field" is a button whose picture the filler chooses;
# "barcode_field" a button PlanetPDF draws as a barcode of the form's values (2026-10-23).
IMAGE_FIELD = "image_field"
BARCODE_FIELD = "barcode_field"
FIELD_TOOLS = {
    FieldKind.TEXT: ("Text Field", QSizeF(160, 20), "Text"),
    FieldKind.CHECKBOX: ("Checkbox", QSizeF(14, 14), "Check"),
    FieldKind.RADIO: ("Radio Buttons", QSizeF(180, 16), "Choice"),  # (a click: room for its text)
    FieldKind.LIST: ("List Box", QSizeF(140, 60), "List"),
    FieldKind.COMBO: ("Dropdown", QSizeF(140, 20), "Dropdown"),
    FieldKind.BUTTON: ("Button", QSizeF(90, 24), "Button"),
    IMAGE_FIELD: ("Image Field", QSizeF(144, 108), "Image"),
    BARCODE_FIELD: ("Barcode", QSizeF(144, 144), "Barcode"),
    FieldKind.DATE: ("Date", QSizeF(100, 20), "Date"),
    FieldKind.SIGNATURE: ("Signature Field", QSizeF(180, 45), "Signature"),
}
CLICK = 6.0  # smaller than this (points) was a click, not a drag


def _qrect(r) -> QRectF:
    r = pymupdf.Rect(r)
    return QRectF(r.x0, r.y0, r.width, r.height)


def _tuple(r: QRectF) -> tuple:
    return (r.left(), r.top(), r.right(), r.bottom())


def _shape_overlay(page, shape) -> QRectF:
    """A shape's click area as displayed: thin lines get something to click (5 pt)."""
    shown = _qrect(to_display(page, shape.rect))
    grow_x, grow_y = max(0.0, 5 - shown.width()) / 2, max(0.0, 5 - shown.height()) / 2
    return shown.adjusted(-grow_x, -grow_y, grow_x, grow_y)


def _families(line: E.TextLine) -> list[str]:
    return {"serif": SERIF_FAMILIES, "mono": MONO_FAMILIES}.get(line.family, TEXT_FONT_FAMILIES)


def _run_font(line: E.TextLine) -> QFont:
    """How a run of page text looks in the on-page editor (its family, size, bold, italic)."""
    font = QFont()
    font.setFamilies(_families(line))
    font.setPixelSize(max(4, round(line.size)))  # (scene units are points)
    font.setBold(line.bold)
    font.setItalic(line.italic)
    return font


# The Eraser's sizes in points; the + and - keys step through them
ERASER_SIZES = (2, 4, 6, 8, 12, 16, 24, 32, 48, 64)


class EditController(QObject):
    modeChanged = Signal(object)  # None | "edit" | "design"
    toolChanged = Signal(object)
    selectionChanged = Signal(object)
    eraserSizeChanged = Signal(float)  # overlay id or None (the Edit PDF panel follows it)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.mode: str | None = None
        self.tool: object = None  # "text", "image" or a FieldKind
        self.editor: InlineTextEditor | None = None
        self._lines: dict[int, list[E.TextLine]] = {}
        self._images: dict[int, list[E.PageImage]] = {}
        self._drawings: dict[int, list] = {}  # drawn shapes (core.drawings.PageDrawing)
        self.eraser_size = 12.0  # points
        self._place_png: bytes | None = None
        self._place_ghost: tuple | None = None  # (picture, size) following the mouse while placing an image
        view = window.view
        view.markupClicked.connect(self._guard(self._on_clicked))
        view.markupDoubleClicked.connect(self._guard(self._on_double_clicked))
        view.markupMoved.connect(self._guard(self._on_moved))
        view.markupContextMenu.connect(self._guard(self._context_menu))
        view.markupDragStarted.connect(self._guard(self._on_drag_started))
        view.shapeDrawn.connect(self._guard(self._on_shape))
        view.drawToolCanceled.connect(self._guard(self._on_escape))
        view.markupDragCanceled.connect(self._guard(self._on_drag_canceled))
        view.markupMovedToPage.connect(self._guard(self._on_moved_to_page))
        view.markupClickReleased.connect(self._guard(self._on_click_released))
        view.brushStep.connect(self.step_eraser)
        view.markupGroupMoved.connect(self._guard(lambda ids, delta: self.move_group(ids, delta.x(), delta.y())))
        view.markupGroupMovedToPage.connect(self._guard(self.move_group_to_page))
        view.groupChanged.connect(lambda _ids: self.selectionChanged.emit(view.selected_markup))
        self._edit_on_release: str | None = None  # a click on the selected line: edit it

    def _guard(self, slot):
        def call(*args):
            if self.mode is not None and self.session is not None:
                slot(*args)
        return call

    @property
    def session(self):
        return self._window.session

    # --- modes and tools ------------------------------------------------------------------------

    def set_mode(self, mode: str | None) -> None:
        if mode == self.mode:
            return
        self.finish_editing()
        self.set_tool(None)
        markup = self._window.markup
        if mode is not None:
            markup.set_tool(None)
            if markup.markup_mode:
                markup.set_markup_mode(False)
            self._window.forms.deactivate()
        self.mode = mode
        markup.suspended = mode is not None
        self._window.view.set_group_select(mode in ("edit", "design"))  # box / Shift+click several objects
        if mode is None:
            markup.apply_mode()
            markup.refresh()
        else:
            self._window.view.set_interaction(fields=False, markup=True)
            self.refresh()
        self.modeChanged.emit(mode)

    def _on_escape(self) -> None:
        """Esc: the tool stays on (user 2026-10-17: until another tool is chosen or the tool
        is closed, every tool everywhere); Esc only drops what was being drawn."""
        if self.tool == "image":
            self.set_tool(None)  # (one and done: Esc cancels it)
            return
        if self.tool is not None or self._window.markup.picking:
            self._window.statusBar().showMessage(
                "The tool stays on: choose the arrow or another tool to stop.", 5000)
            return
        self.set_tool(None)

    def set_tool(self, tool) -> None:
        self.finish_editing()
        if self._window.markup.picking:  # (another tool: Link / Crop Pages… stops)
            self._window.markup.cancel_pick_box()
        self.tool = tool
        self._window.view.set_brush(self.eraser_size if tool == "erase" else None)  # (a circle the brush's size)
        self._place_png = None
        self._place_ghost = None
        view = self._window.view
        if tool is not None:
            from planetpdf.ui.markup_controller import deselect

            deselect(view)  # (another tool: the last item's handles go, user 2026-10-22)
        if tool is None:
            view.set_draw_tool(None)
        elif tool == "text":
            view.set_draw_tool("point", text_cursor=True)
            self._window.statusBar().showMessage("Click where the text should go, then type. Esc cancels.", 8000)
        elif tool == "erase":
            from planetpdf.ui.viewer.page_view import DrawStyle

            view.set_draw_tool("ink")
            view.set_group([])  # (nothing stays selected: the eraser works on whatever it passes over)
            view.set_selected_markup(None)
            view.set_draw_style(DrawStyle(color=QColor(255, 80, 120), width=self.eraser_size, opacity=0.35))
            self._window.statusBar().showMessage(
                "Eraser: drag over anything to erase it (text, pictures, lines, and boxes it touches). "
                "The + and - keys change its size. Esc stops.", 8000)
        elif tool in FIELD_TOOLS:
            view.set_draw_tool("box")
            self._window.statusBar().showMessage(
                f"Drag a box for the {FIELD_TOOLS[tool][0].lower()} (or click for the standard size). Esc cancels.", 8000)
        if tool is not None:
            self._window.act_redact_area.setChecked(False)
        self.toolChanged.emit(tool)

    def add_image(self, path: Path | None = None) -> None:
        """Pick an image file, then place it on the page like a stamp."""
        if path is None:
            name, _ = QFileDialog.getOpenFileName(self._window, "Add Image", self._window.settings.last_directory, IMAGE_FILTER)
            if not name:
                return
            path = Path(name)
        try:
            pix = pymupdf.Pixmap(str(path))
            if pix.alpha:
                png = pix.tobytes("png")
            else:
                png = pymupdf.Pixmap(pymupdf.csRGB, pix).tobytes("png") if pix.n > 3 else pix.tobytes("png")
        except (RuntimeError, ValueError, OSError) as exc:
            self._window.show_message("Add Image", f"That image couldn't be read: {exc}")
            return
        self.set_tool("image")
        self._place_png = png
        width = min(pix.width * 72 / max(pix.xres or 96, 1), 300.0)
        self._place_ghost = (QPixmap.fromImage(QImage.fromData(png)), QSizeF(width, width * pix.height / pix.width))
        self._arm_image()
        self._window.statusBar().showMessage("Click on the page to place the image (or drag to size it). Esc cancels.",
                                             8000)

    def _arm_image(self) -> None:
        """The Add Image tool ready to place (again): the picture follows the mouse."""
        view = self._window.view
        view.set_draw_tool("place")
        view.set_placement_ghost(*self._place_ghost)

    # --- overlays --------------------------------------------------------------------------------

    def _signature(self, index: int) -> tuple:
        """Changes whenever the page's content does (edits, OCR, redaction, page moves),
        so the cached lines/images are never stale."""
        page = self.session.doc[index]
        return (id(self.session), page.xref, page.rotation, hash(page.read_contents()))

    def _cached(self, cache: dict, index: int, compute):
        key = self._signature(index)
        hit = cache.get(index)
        if hit is None or hit[0] != key:
            hit = cache[index] = (key, compute(self.session, index))
        return hit[1]

    def _page_lines(self, index: int) -> list[E.TextLine]:
        return self._cached(self._lines, index, E.text_lines)

    def _page_images(self, index: int) -> list[E.PageImage]:
        return self._cached(self._images, index, E.page_images)

    def _page_drawings(self, index: int) -> list:
        from planetpdf.core import drawings as DR

        return self._cached(self._drawings, index, DR.page_drawings)

    def _drawing(self, item_id: str):
        kind, page, n = item_id.split(":")
        shapes = self._page_drawings(int(page))
        return shapes[int(n)] if kind == "vec" and int(n) < len(shapes) else None

    def refresh(self) -> None:
        if self.mode is None or self.session is None:
            return
        view = self._window.view
        overlays: dict[int, list] = {}
        doc = self.session.doc
        if self.mode == "edit":
            for index in range(doc.page_count):
                page = doc[index]
                # Shapes first, so a line of text drawn over its frame / underline wins the click.
                for n, shape in enumerate(self._page_drawings(index)):
                    overlays.setdefault(index, []).append((f"vec:{index}:{n}", _shape_overlay(page, shape), True, False, False))
                for n, image in enumerate(self._page_images(index)):
                    overlays.setdefault(index, []).append((f"img:{index}:{n}", _qrect(to_display(page, image.rect)), True, True))
                for n, line in enumerate(self._page_lines(index)):
                    # movable (drag / arrow keys), not resizable
                    overlays.setdefault(index, []).append((f"txt:{index}:{n}", _qrect(to_display(page, line.rect)), True, False, False))
            # Placed text / signatures stay movable here too (until the page is flattened).
            for item in self._window.markup.items:
                if item.spec is not None and item.spec.kind.placed:
                    shown = _qrect(to_display(doc[item.page], item.spec.rect))
                    overlays.setdefault(item.page, []).append(("mk:" + item.item_id, shown, True, True))
        labels = {}
        if self.mode != "edit":
            # Prepare Form (Acrobat): every field a light blue box with its name in it, so even
            # an empty dropdown or an unchecked radio button shows where it is
            self._radio_groups: dict[str, tuple[str, int]] = {}
            for f in F.list_fields(self.session):
                kind = _KIND_LABELS.get(f.kind, "Field")
                if f.kind is FieldKind.RADIO:
                    # one object per page: all its buttons (user 2026-10-02: "should be grouped
                    # together and should not be separate objects"); dragging moves them all,
                    # the corners scale the group
                    for page_index in sorted({r.page for r in f.widgets}):
                        refs = [r for r in f.widgets if r.page == page_index]
                        union = QRectF()
                        for ref in refs:
                            union = union.united(_qrect(to_display(doc[page_index], ref.rect)))
                        item_id = "fld:" + field_key(f.name, refs[0].xref)
                        overlays.setdefault(page_index, []).append((item_id, union, True, False))
                        self._radio_groups[item_id] = (f.name, page_index)
                        labels[item_id] = (f.name, f"{kind} group: {f.name}, {len(refs)} buttons "
                                                   "(double-click for its properties)")
                    continue
                for ref in f.widgets:
                    shown = to_display(doc[ref.page], ref.rect)
                    item_id = "fld:" + field_key(f.name, ref.xref)
                    overlays.setdefault(ref.page, []).append((item_id, _qrect(shown), True, False))
                    labels[item_id] = (f.name, f"{kind}: {f.name} (double-click for its properties)")
        selected = view.selected_markup
        view.set_markup_overlays(overlays, labels)
        if selected and view.markup_rect(selected) is not None:
            view.set_selected_markup(selected)
            if selected.startswith("mk:"):
                self._window.markup.show_item_bar(selected, selected[3:])

    def _line(self, item_id: str) -> E.TextLine | None:
        kind, page, n = item_id.split(":")
        lines = self._page_lines(int(page))
        return lines[int(n)] if kind == "txt" and int(n) < len(lines) else None

    def _image(self, item_id: str) -> E.PageImage | None:
        kind, page, n = item_id.split(":")
        images = self._page_images(int(page))
        return images[int(n)] if kind == "img" and int(n) < len(images) else None

    # --- view signals ----------------------------------------------------------------------------

    def _on_clicked(self, item_id: str) -> None:
        """A line of text works like an object (user 2026-10-01): the first click selects
        it (drag or arrow keys move it), a click on the selected line edits it."""
        already = bool(item_id) and self._window.view.selected_markup == item_id
        self._edit_on_release = item_id if already and item_id.startswith("txt:") else None
        self._window.view.set_selected_markup(item_id or None)
        self.selectionChanged.emit(item_id or None)
        if item_id.startswith("mk:"):
            self._window.markup.show_item_bar(item_id, item_id[3:])
        if item_id.startswith("txt:") and not already:
            self._window.statusBar().showMessage(
                "Drag the text, or use the arrow keys (Shift: 10 pt), to move it. Click it again to edit it.", 6000)

    def _on_click_released(self, item_id: str) -> None:
        if item_id and item_id == self._edit_on_release:
            self._edit_on_release = None
            self.edit_line(item_id)

    def _on_double_clicked(self, item_id: str) -> None:
        if item_id.startswith("txt:"):
            self._edit_on_release = None
            self.edit_line(item_id)
            return
        if item_id.startswith("mk:"):
            self._window.markup.edit_text(item_id[3:])
        if item_id.startswith("fld:"):
            self.edit_properties(split_key(item_id[4:])[0])

    def _on_drag_started(self, item_id: str) -> None:
        """Moving an image: its own picture follows the mouse."""
        if item_id.startswith("mk:"):
            self._window.markup._lift(item_id[3:])
            return
        self._edit_on_release = None  # it's a drag, not a click
        if item_id.startswith("txt:"):
            line = self._line(item_id)
            if line is None:
                return
            page = self.session.doc[line.page]
            rect = line.rect
        elif item_id.startswith("img:"):
            image = self._image(item_id)
            if image is None:
                return
            page = self.session.doc[image.page]
            rect = image.rect
        else:
            return
        clip = pymupdf.Rect(to_display(page, rect)) * page.derotation_matrix
        pix = page.get_pixmap(matrix=pymupdf.Matrix(GHOST_ZOOM, GHOST_ZOOM), clip=clip.normalize(), alpha=False)
        qimage = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self._window.view.set_drag_ghost(QPixmap.fromImage(qimage.copy()))

    def _on_drag_canceled(self, item_id: str) -> None:
        if item_id.startswith("mk:"):
            self._window.markup._drop_back(item_id[3:])

    def _on_moved_to_page(self, item_id: str, page_index: int, rect: QRectF) -> None:
        """Dragged onto another page (images, placed items, form fields)."""
        if item_id.startswith("mk:"):
            self._window.markup.move_to_page(item_id[3:], page_index, rect)
            return
        target = from_display(self.session.doc[page_index], _tuple(rect))
        if item_id.startswith(("txt:", "vec:")):  # (a line brings its underline, frame and links)
            self.move_objects([(item_id, page_index, rect)], to_page=page_index)
            return
        if item_id.startswith("img:"):
            image = self._image(item_id)
            if image is None:
                return self.refresh()

            def move(s):
                png = E.image_png(s, image)
                E.delete_image(s, image)
                E.place_image(s, page_index, target, png)

            try:
                command = PageContentCommand.applied(self._window, sorted({image.page, page_index}), move,
                                                     f"Move Image to Page {page_index + 1}")
            except PdfError as exc:
                self._window.show_message("Move Image", str(exc))
                return self.refresh()
            self._window.push_command(command)
            return
        if item_id.startswith("fld:"):
            name, _xref = split_key(item_id[4:])
            spec = D.field_spec(self.session, name)
            if spec.kind is FieldKind.RADIO or spec.extra_widgets:
                self._window.show_message("Move Field", "Radio groups and fields shown in several places can only be moved within their page.")
                return self.refresh()
            new = replace(spec, page=page_index, rect=target)
            self._design(f"Move Field to Page {page_index + 1}", lambda s: D.replace_field(s, name, new),
                         lambda s: D.replace_field(s, name, spec), {spec.page, page_index})
            return
        self.refresh()

    def _on_moved(self, item_id: str, rect: QRectF) -> None:
        if item_id.startswith("mk:"):
            self._window.markup._on_moved(item_id[3:], rect)
            return
        if item_id.startswith("img:"):
            image = self._image(item_id)
            if image is not None:
                page = self.session.doc[image.page]
                new = from_display(page, _tuple(rect))
                self._content(image.page, lambda s: E.move_image(s, image, new), "Move Image")
                return
        elif item_id.startswith(("txt:", "vec:")):
            where = self._model_place(item_id)
            if where is not None:  # (a line brings its underline, frame and links)
                self.move_objects([(item_id, where[0], rect)])
                return
        elif item_id in getattr(self, "_radio_groups", {}):
            name, page_index = self._radio_groups[item_id]
            self.move_radio_group(name, page_index, rect)
            return
        elif item_id.startswith("fld:"):
            name, xref = split_key(item_id[4:])
            self.move_field(name, xref, rect)
            return
        self.refresh()

    def move_radio_group(self, name: str, page_index: int, rect: QRectF) -> None:
        """A radio group dragged (or resized by a corner): every button on that page moves
        with it, keeping its place in the group (scaled when the group was resized)."""
        page = self.session.doc[page_index]
        refs = [r for r in F.get_field(self.session, name).widgets if r.page == page_index]
        shown = [_qrect(to_display(page, r.rect)) for r in refs]
        union = QRectF()
        for box in shown:
            union = union.united(box)
        old = [r.rect for r in refs]
        resized = abs(rect.width() - union.width()) > 0.5 or abs(rect.height() - union.height()) > 0.5
        spec = D.field_spec(self.session, name)
        if resized and spec.labels and len(refs) == len(spec.option_rects):
            # wider or narrower: the text wraps again and every button gets just the height
            # it needs (user 2026-10-02: "everything has to fit"; the drag's height is ignored)
            first = shown[0]
            fitted = D.fit_radio(self.session, spec, top_left=(rect.left(), rect.top()),
                                 width=max(1.0, rect.width() - (union.width() - first.width())))
            new = list(fitted.option_rects)
        else:
            sx = rect.width() / union.width() if union.width() else 1.0
            sy = rect.height() / union.height() if union.height() else 1.0
            new = []
            for box in shown:
                moved = QRectF(rect.left() + (box.left() - union.left()) * sx, rect.top() + (box.top() - union.top()) * sy,
                               box.width() * sx, box.height() * sy)
                new.append(from_display(page, _tuple(moved)))

        def move(s, frm: list, to: list) -> None:
            # (found by position: xrefs change when undo recreates a field)
            widgets = [w for w in F.get_field(s, name).widgets if w.page == page_index]
            chosen = [min(widgets, key=lambda w, r=r: sum(abs(a - b) for a, b in zip(w.rect, r))).xref for r in frm]
            for xref, target in zip(chosen, to):
                D.move_widget(s, page_index, xref, target)

        self._design("Move Radio Buttons", lambda s: move(s, old, new), lambda s: move(s, new, old), {page_index})

    def move_group(self, ids: list[str], dx: float, dy: float) -> bool:
        """Move several selected objects (lines of text, images, placed items) by (dx, dy)
        as displayed, on their own pages: one undo step. They stay selected."""
        if self.mode == "design":  # (Prepare Form: several fields)
            places = [(i, self.field_place(i)) for i in ids]
            return self.place_fields([(i, p[1].translated(dx, dy)) for i, p in places if p is not None],
                                     "Move Fields" if len(ids) > 1 else "Move Field")
        placements = []
        for item_id in ids:
            where = self._model_place(item_id)
            if where is not None:
                page_index, shown = where
                placements.append((item_id, page_index, shown.translated(dx, dy)))
        return self.move_objects(placements)

    def move_group_to_page(self, ids: list[str], page_index: int, rects: list[QRectF]) -> bool:
        """Dropped on another page (in the view, or on its thumbnail in the Pages panel):
        each object goes to ``rects[i]`` (displayed coordinates of that page)."""
        return self.move_objects([(i, page_index, r) for i, r in zip(ids, rects)], to_page=page_index)

    def _model_place(self, item_id: str) -> tuple[int, QRectF] | None:
        """Where an object is in the document: (page, displayed rect)."""
        from planetpdf.core import markup as K

        doc = self.session.doc
        if item_id.startswith("txt:"):
            obj = self._line(item_id)
            page, rect = (obj.page, obj.rect) if obj else (None, None)
        elif item_id.startswith("img:"):
            obj = self._image(item_id)
            page, rect = (obj.page, obj.rect) if obj else (None, None)
        elif item_id.startswith("vec:"):  # (by its click area, like its overlay: see move_objects)
            obj = self._drawing(item_id)
            return (obj.page, _shape_overlay(doc[obj.page], obj)) if obj else None
        elif item_id.startswith("mk:"):
            try:
                spec = K.find_item(self.session, item_id[3:]).spec
            except PdfError:
                spec = None
            page, rect = (spec.page, spec.rect) if spec else (None, None)
        else:
            return None
        if page is None:
            return None
        return page, _qrect(to_display(doc[page], rect))

    def move_objects(self, placements: list[tuple[str, int, QRectF]], *, to_page: int | None = None) -> bool:
        """Put each object at (page, displayed rect): one undo step, then select them all
        again. Lines are moved with `content_edit.move_group` (all lifted, then put down)."""
        from planetpdf.core import markup as K
        from planetpdf.ui.commands import DocumentSnapshotCommand

        from planetpdf.core import drawings as DR

        doc = self.session.doc
        lines, images, placed, shapes, links, after = [], [], [], [], [], []
        chosen = {(d.page, d.index) for d in (self._drawing(i) for i, _p, _r in placements if i.startswith("vec:")) if d}
        taken: set[tuple[int, int]] = set()  # shapes already moving (selected, or another line's)
        taken_links: set[tuple[int, int]] = set()
        kinds = []
        for item_id, page_index, rect in placements:
            new = from_display(doc[page_index], _tuple(rect))
            kinds.append(item_id.split(":")[0])
            if item_id.startswith("txt:"):
                line = self._line(item_id)
                if line is not None:
                    dx, dy = new[0] - line.rect[0], new[1] - line.rect[1]
                    lines.append((line, dx, dy, page_index))
                    after.append(("txt", page_index, (line.origin[0] + dx, line.origin[1] + dy)))
                    # its underline / word frame and its links go with it (user bug 2026-10-01)
                    for d in DR.decorations(self._page_drawings(line.page), line.rect, line.size):
                        key = (d.page, d.index)
                        if key not in chosen and key not in taken:
                            taken.add(key)
                            shapes.append((d, dx, dy, page_index))
                    for link in DR.links_over(doc[line.page], line.rect):
                        key = (line.page, link.get("xref", id(link)))
                        if key not in taken_links:
                            taken_links.add(key)
                            links.append((line.page, link, dx, dy, page_index))
            elif item_id.startswith("vec:"):
                d = self._drawing(item_id)
                if d is not None and (d.page, d.index) not in taken:
                    taken.add((d.page, d.index))
                    # ``rect`` is the (enlarged) click area moved: move the shape by as much
                    area = _shape_overlay(doc[d.page], d)
                    shape_now = _qrect(to_display(doc[d.page], d.rect)).translated(rect.topLeft() - area.topLeft())
                    new = from_display(doc[page_index], _tuple(shape_now))
                    dx, dy = new[0] - d.rect[0], new[1] - d.rect[1]
                    shapes.append((d, dx, dy, page_index))
                    after.append(("vec", page_index, (d.rect[0] + dx, d.rect[1] + dy, d.rect[2] + dx, d.rect[3] + dy)))
            elif item_id.startswith("img:"):
                image = self._image(item_id)
                if image is not None:
                    r = image.rect
                    dx, dy = new[0] - r[0], new[1] - r[1]
                    images.append((image, dx, dy, page_index))
                    after.append(("img", page_index, (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)))
            elif item_id.startswith("mk:"):
                spec = K.find_item(self.session, item_id[3:]).spec
                if spec is not None and spec.kind.movable:
                    moved = replace(spec, page=page_index)
                    if spec.kind is K.Kind.TEXT:
                        moved = K.restyled_text(self.session, moved, top_left=(rect.left(), rect.top()))
                    else:
                        r = spec.rect
                        moved = K.transformed(moved, (new[0], new[1], new[0] + r[2] - r[0], new[1] + r[3] - r[1]))
                    placed.append((spec, moved))
                    after.append(("mk", page_index, item_id))
        if not (lines or images or placed or shapes):
            self.refresh()
            return False

        def apply(session) -> None:
            E.move_group(session, lines, images, shapes, links)
            for old, new in placed:
                K.replace_markup(session, old, new)

        count = len(after)
        single = {"txt": "Move Text", "img": "Move Image", "vec": "Move Shape", "mk": "Move Object"}
        label = f"Move {count} Objects" if count > 1 else single.get(kinds[0] if kinds else "", "Move Object")
        if to_page is not None:
            label += f" to Page {to_page + 1}"
        try:
            command = DocumentSnapshotCommand.applied(self._window, apply, label)
        except Exception as exc:  # nothing changed (the snapshot was put back)
            self._window.report_error("Move Objects", exc)
            self.refresh()
            return False
        self._window.push_command(command)
        self.refresh()
        new_ids = []
        for kind, page_index, where in after:
            if kind == "txt":
                found = self._line_at(page_index, where)
                if found is not None:
                    new_ids.append(found[0])
            elif kind == "img":
                found = self._find_image_id(page_index, where)
                if found is not None:
                    new_ids.append(found)
            elif kind == "vec":
                found = self._find_drawing_id(page_index, where)
                if found is not None:
                    new_ids.append(found)
            else:
                new_ids.append(where)
        self._window.view.set_group(new_ids)
        if to_page is not None:
            self._window.view.go_to_page(to_page)
        self.selectionChanged.emit(self._window.view.selected_markup)
        return True
    def erase(self, page_index: int, points) -> bool:
        """Free-form eraser stroke (points as displayed): one undo step; the tool stays on."""
        from planetpdf.core import drawings as DR

        page = self.session.doc[page_index]
        unrotated = []
        for p in points:
            q = pymupdf.Point(p.x(), p.y()) * page.derotation_matrix
            unrotated.append((q.x, q.y))
        size = self.eraser_size
        return self._content(page_index, lambda s: DR.erase(s, page_index, unrotated, size), "Eraser")  # ("Undo Eraser")

    def step_eraser(self, direction: int) -> None:
        """The + and - keys while the Eraser is on: the next size up or down."""
        if self.tool != "erase":
            return
        if direction > 0:
            bigger = [s for s in ERASER_SIZES if s > self.eraser_size + 0.01]
            size = bigger[0] if bigger else ERASER_SIZES[-1]
        else:
            smaller = [s for s in ERASER_SIZES if s < self.eraser_size - 0.01]
            size = smaller[-1] if smaller else ERASER_SIZES[0]
        self.set_eraser_size(size)
        self._window.statusBar().showMessage(f"Eraser: {size:g} pt   (+ bigger, - smaller)", 3000)

    def set_eraser_size(self, size: float) -> None:
        from planetpdf.ui.viewer.page_view import DrawStyle

        self.eraser_size = float(size)
        self.eraserSizeChanged.emit(self.eraser_size)
        if self.tool == "erase":
            self._window.view.set_brush(self.eraser_size)
            self._window.view.set_draw_style(DrawStyle(color=QColor(255, 80, 120), width=self.eraser_size, opacity=0.35))

    def _find_drawing_id(self, page_index: int, rect) -> str | None:
        """The overlay id of the shape now at ``rect`` (unrotated) on that page."""
        target = pymupdf.Rect(rect)
        best, best_distance = None, None
        for n, d in enumerate(self._page_drawings(page_index)):
            distance = sum(abs(a - b) for a, b in zip(d.rect, target))
            if best_distance is None or distance < best_distance:
                best, best_distance = f"vec:{page_index}:{n}", distance
        return best if best_distance is not None and best_distance < 2 else None

    def _delete_group(self, ids: list[str]) -> bool:
        """Delete the selected objects (a line with its underline, frame and links): one
        undo step."""
        from planetpdf.core import drawings as DR
        from planetpdf.core import markup as K
        from planetpdf.ui.commands import DocumentSnapshotCommand

        doc = self.session.doc
        lines = [line for line in (self._line(i) for i in ids if i.startswith("txt:")) if line is not None]
        images = [image for image in (self._image(i) for i in ids if i.startswith("img:")) if image is not None]
        placed = [i[3:] for i in ids if i.startswith("mk:")]
        shapes = {(d.page, d.index): d for d in (self._drawing(i) for i in ids if i.startswith("vec:")) if d}
        links: dict[tuple, tuple[int, dict]] = {}
        for line in lines:
            for d in DR.decorations(self._page_drawings(line.page), line.rect, line.size):
                shapes.setdefault((d.page, d.index), d)
            for link in DR.links_over(doc[line.page], line.rect):
                links.setdefault((line.page, link.get("xref", id(link))), (line.page, link))

        def apply(session) -> None:
            E.delete_group(session, lines, images, list(shapes.values()), list(links.values()))
            for mark_id in placed:
                K.remove_markup(session, mark_id)

        single = {"txt": "Delete Text", "img": "Delete Image", "vec": "Delete Shape", "mk": "Delete Object"}
        label = f"Delete {len(ids)} Objects" if len(ids) > 1 else single.get(ids[0].split(":")[0], "Delete Object")
        try:
            command = DocumentSnapshotCommand.applied(self._window, apply, label)
        except Exception as exc:
            self._window.report_error("Delete Objects", exc)
            self.refresh()
            return False
        self._window.push_command(command)
        self._window.view.set_group([])
        self.refresh()
        self.selectionChanged.emit(None)
        return True

    def _reselect_line(self, page_index: int, origin: tuple[float, float]) -> None:
        """After a move the line has a new id: keep it selected (so it can be moved on)."""
        self.refresh()
        found = self._line_at(page_index, origin)
        if found is not None:
            self._window.view.set_selected_markup(found[0])
        self.selectionChanged.emit(self._window.view.selected_markup)

    def _on_shape(self, draw: str, page_index: int, data) -> None:
        tool = self.tool
        page = self.session.doc[page_index]
        if tool == "erase" and draw == "ink":
            self.erase(page_index, data)
            return
        if tool == "text" and draw == "point":
            # (the tool stays on until another is chosen: user 2026-10-17)
            self._window.markup.new_placed_text(page_index, data.center())
        elif tool == "image" and draw == "place" and self._place_png is not None:
            png = self._place_png
            self.set_tool(None)  # (one and done; first: whatever happens next, the tool never stays stuck)
            try:
                rect = from_display(page, _tuple(data))
                before = len(self._page_images(page_index))
                placed = self._content(page_index, lambda s: E.place_image(s, page_index, rect, png), "Add Image")
            except Exception as exc:  # (a PyMuPDF error on an unusual file: say so, don't hang)
                self._window.report_error("Add Image", exc)
                self.refresh()
                return
            if placed and len(self._page_images(page_index)) > before:
                # Select it right away: handles to resize, drag to move (user report).
                new_id = self._find_image_id(page_index, rect)
                if new_id:
                    self._window.view.set_selected_markup(new_id)
                    self._window.statusBar().showMessage(
                        "Image added: drag it to move it (even to another page), or its corners to resize it.", 6000)
        elif tool in FIELD_TOOLS and draw == "box":
            self.create_field(tool, page_index, data)

    def _find_image_id(self, page_index: int, rect) -> str | None:
        """The overlay id of the image placed at ``rect`` (unrotated) on that page."""
        target = pymupdf.Rect(rect)
        best, best_distance = None, None
        for n, image in enumerate(self._page_images(page_index)):
            distance = sum(abs(a - b) for a, b in zip(image.rect, target))
            if best_distance is None or distance < best_distance:
                best, best_distance = f"img:{page_index}:{n}", distance
        return best

    def _context_menu(self, item_id: str, pos) -> None:
        if item_id.startswith("mk:"):
            self._window.markup._context_menu(item_id[3:], pos)
            return
        menu = QMenu(self._window)
        if item_id.startswith("txt:"):
            menu.addAction("Edit Text", lambda: self.edit_line(item_id))
            menu.addAction("Delete Text", lambda: self.delete(item_id))
        elif item_id.startswith("img:"):
            menu.addAction("Replace Image…", lambda: self.replace_image(item_id))
            menu.addAction("Delete Image", lambda: self.delete(item_id))
        elif item_id.startswith("fld:"):
            name = split_key(item_id[4:])[0]
            menu.addAction("Properties…", lambda: self.edit_properties(name))
            menu.addAction("Delete Field", lambda: self.delete(item_id))
        else:
            return
        menu.exec(pos)

    # --- content edits ---------------------------------------------------------------------------

    def _content(self, page_index: int, apply, text: str) -> bool:
        try:
            command = PageContentCommand.applied(self._window, [page_index], apply, text)
        except PdfError as exc:
            self._window.show_message(text, str(exc))
            self.refresh()
            return False
        except Exception as exc:  # unexpected (e.g. PyMuPDF on an unusual file): nothing changed
            self._window.report_error(text, exc)
            self.refresh()
            return False
        self._window.push_command(command)
        return True

    def edit_line(self, item_id: str) -> None:
        line = self._line(item_id)
        if line is None:
            return
        page = self.session.doc[line.page]
        group = E.line_group(self._page_lines(line.page), line)
        if not E.can_edit_together(self.session, group):
            group = [line]  # (alone, or a font in partial copies: one run at a time)
        box = pymupdf.Rect(group[0].rect)
        for run in group[1:]:
            box |= pymupdf.Rect(run.rect)
        shown = _qrect(to_display(page, tuple(box)))
        from planetpdf.ui.inline_editor import BOX_PADDING

        rect = QRectF(shown.left() - BOX_PADDING, shown.top() - BOX_PADDING, shown.width() + 2 * BOX_PADDING, shown.height() + 2 * BOX_PADDING)

        def commit(text: str, _rect: QRectF, sources=None) -> None:
            if len(group) > 1:  # (a line in several fonts: each piece in its run's; user 2026-10-23)
                pieces: list[tuple[str, int]] = []
                for ch, source in zip(text, sources or [0] * len(text)):
                    if pieces and pieces[-1][1] == source:
                        pieces[-1] = (pieces[-1][0] + ch, source)
                    else:
                        pieces.append((ch, source))
                label = "Delete Text" if not text.strip() else "Edit Text"
                self._content(line.page, lambda s: E.replace_runs(s, group, pieces), label)
                return
            if text == line.text:
                return
            label = "Delete Text" if not text.strip() else "Edit Text"
            self._content(line.page, lambda s: E.replace_line(s, line, text), label)

        self._open_editor(line.page, rect, line.text, commit, font_size=line.size, color=QColor.fromRgbF(*line.color),
                          families=_families(line), bold=line.bold, italic=line.italic, single_line=True, opaque=True,
                          sources=[(text, n, _run_font(group[n]), QColor.fromRgbF(*group[n].color))
                                   for text, n in E.group_text(group)] if len(group) > 1 else None)

    def nudge(self, item_id: str, shown: QRectF, dx: float, dy: float) -> bool:
        """Arrow keys: move the selected image, field or placed item a little."""
        if item_id.startswith("mk:"):
            return self._window.markup.nudge(item_id[3:], shown, dx, dy)
        if item_id.startswith(("img:", "fld:", "txt:", "vec:")):
            self._on_moved(item_id, shown.translated(dx, dy))
            return True
        return False

    def _open_editor(self, page_index: int, rect: QRectF, text: str, on_commit, *, font_size: float, color: QColor,
                     sources=None, **kw) -> None:
        """``sources``: a line of page text in several fonts (``InlineTextEditor.set_sources``);
        ``on_commit`` then also gets each character's run."""
        view = self._window.view
        self.finish_editing()
        holder: dict = {}
        min_height = rect.height()

        def resize(content_height: float) -> None:
            proxy, editor = holder.get("proxy"), holder.get("editor")
            if proxy is None or editor is None:
                return
            if editor.single_line:  # one line: grows sideways only
                proxy.setGeometry(QRectF(rect.left(), rect.top(), max(rect.width(), editor.natural_width()), rect.height()))
            else:
                proxy.setGeometry(QRectF(rect.left(), rect.top(), rect.width(), max(min_height, content_height)))

        def commit(value: str, content_height: float) -> None:
            editor = holder.get("editor")
            runs = editor.char_sources() if sources is not None and editor is not None else None
            self._close_editor()
            if sources is not None:
                on_commit(value, grown_rect(rect, content_height, min_height), runs)
            else:
                on_commit(value, grown_rect(rect, content_height, min_height))

        editor = InlineTextEditor(text, font_size=font_size, color=color, note=False, on_commit=commit,
                                  on_cancel=self._close_editor, on_resize=resize, **kw)
        if sources is not None:
            editor.set_sources(sources)
        holder["editor"] = editor
        self.editor = editor
        holder["proxy"] = view.show_editor_at(page_index, rect, editor)
        editor.selectAll() if kw.get("single_line") else None
        editor.setFocus()

    def _close_editor(self) -> None:
        self.editor = None
        self._window.view.remove_editor()

    def finish_editing(self) -> None:
        import shiboken6

        if self.editor is not None and not shiboken6.isValid(self.editor):
            self.editor = None  # (the view removed it, e.g. a page refresh: user bug 2026-10-03)
        if self.editor is not None:
            self.editor.finish()

    def replace_image(self, item_id: str, path: Path | None = None) -> None:
        image = self._image(item_id)
        if image is None:
            return
        if path is None:
            name, _ = QFileDialog.getOpenFileName(self._window, "Replace Image", self._window.settings.last_directory, IMAGE_FILTER)
            if not name:
                return
            path = Path(name)
        try:
            pix = pymupdf.Pixmap(str(path))
            png = (pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.n - pix.alpha > 3 else pix).tobytes("png")
        except (RuntimeError, ValueError, OSError) as exc:
            self._window.show_message("Replace Image", f"That image couldn't be read: {exc}")
            return
        self._content(image.page, lambda s: E.replace_image(s, image, png), "Replace Image")

    # --- Edit PDF panel (FORMAT / OBJECTS) ------------------------------------------------------------

    def selected_line(self) -> E.TextLine | None:
        item_id = self._window.view.selected_markup or ""
        return self._line(item_id) if item_id.startswith("txt:") else None

    def selected_image(self) -> E.PageImage | None:
        item_id = self._window.view.selected_markup or ""
        return self._image(item_id) if item_id.startswith("img:") else None

    def _line_at(self, page_index: int, origin: tuple[float, float]) -> tuple[str, E.TextLine] | None:
        for n, line in enumerate(self._page_lines(page_index)):
            r = pymupdf.Rect(line.rect)
            if abs(line.origin[1] - origin[1]) < 1.0 and r.x0 - 1 <= origin[0] <= r.x1:
                return f"txt:{page_index}:{n}", line
        return None

    def selected_objects(self) -> tuple[list, list, list]:
        """The selected (lines of text, images, shapes): the Edit PDF panel works on all
        of them (one object or a group)."""
        ids = self._window.view.selected_group
        lines = [x for x in (self._line(i) for i in ids if i.startswith("txt:")) if x is not None]
        images = [x for x in (self._image(i) for i in ids if i.startswith("img:")) if x is not None]
        shapes = [x for x in (self._drawing(i) for i in ids if i.startswith("vec:")) if x is not None]
        return lines, images, shapes

    def _change_selection(self, apply, label: str, lines, images, shapes, *, image_rects=None, shape_rects=None) -> bool:
        """Run ``apply(session)`` as one undo step, then select the same objects again
        (found at their new places: ``image_rects`` / ``shape_rects`` = unrotated rects)."""
        from planetpdf.ui.commands import DocumentSnapshotCommand

        self.finish_editing()
        try:
            command = DocumentSnapshotCommand.applied(self._window, apply, label)
        except Exception as exc:  # nothing changed (the snapshot was put back)
            self._window.report_error(label, exc)
            self.refresh()
            return False
        self._window.push_command(command)
        self.refresh()
        ids = []
        for line in lines:
            found = self._line_at(line.page, line.origin)
            if found is not None:
                ids.append(found[0])
        for image, rect in zip(images, image_rects or [i.rect for i in images]):
            found = self._find_image_id(image.page, rect)
            if found:
                ids.append(found)
        for shape, rect in zip(shapes, shape_rects or [d.rect for d in shapes]):
            found = self._find_drawing_id(shape.page, rect)
            if found:
                ids.append(found)
        self._window.view.set_group(ids)
        self.selectionChanged.emit(self._window.view.selected_markup)
        return True

    def restyle_selected(self, **style) -> bool:
        """FORMAT: new size / color / family / bold / italic for every selected line of
        text (whatever is being typed in one is kept first). They stay selected."""
        lines, _images, _shapes = self.selected_objects()
        if not lines:
            return False

        def apply(session) -> None:
            for line in lines:
                E.restyle_line(session, line, **style)

        return self._change_selection(apply, "Format Text" if len(lines) == 1 else f"Format {len(lines)} Lines",
                                      lines, [], [])

    def recolor_selected(self, color) -> bool:
        """The color button: text color of the selected lines, outline (or fill) color of
        the selected shapes."""
        from planetpdf.core import drawings as DR

        lines, _images, shapes = self.selected_objects()
        if not (lines or shapes):
            return False

        def apply(session) -> None:
            for line in lines:
                E.restyle_line(session, line, color=color)
            for page_index in sorted({d.page for d in shapes}):
                DR.recolor_drawings(session, page_index, [d for d in shapes if d.page == page_index], color)

        return self._change_selection(apply, "Change Color", lines, [], shapes)

    def underlined(self, line) -> list:
        """The underline shapes under a line of text (thin, below its baseline)."""
        from planetpdf.core import drawings as DR

        return [d for d in DR.decorations(self._page_drawings(line.page), line.rect, line.size)
                if pymupdf.Rect(d.rect).height <= max(2.5, 0.25 * line.size) and d.rect[1] >= line.origin[1] - 0.5]

    def toggle_underline(self) -> bool:
        """FORMAT ▸ Underline: underline the selected lines in their own color, or take the
        underline away when they all have one."""
        from planetpdf.core import drawings as DR

        lines, _images, _shapes = self.selected_objects()
        if not lines:
            return False
        existing = {(d.page, d.index): d for line in lines for d in self.underlined(line)}
        remove = len(existing) > 0 and all(self.underlined(line) for line in lines)

        def apply(session) -> None:
            if remove:
                for page_index in sorted({d.page for d in existing.values()}):
                    DR.remove_drawings(session, page_index, [d for d in existing.values() if d.page == page_index])
                return
            for line in lines:
                if not self.underlined(line):
                    DR.add_underline(session, line.page, line.rect, line.origin, line.size, line.color)

        return self._change_selection(apply, "Remove Underline" if remove else "Underline", lines, [], [])

    def transform_selected(self, operation: str) -> bool:
        """OBJECTS: rotate or flip the selected images and shapes, each about its own
        center (one undo step; they stay selected)."""
        from planetpdf.core import drawings as DR

        _lines, images, shapes = self.selected_objects()
        if not (images or shapes):
            return False
        image_rects = []
        for image in images:
            box = pymupdf.Rect(image.rect)
            if operation.startswith("rotate"):
                c = box.tl + (box.br - box.tl) * 0.5
                box = pymupdf.Rect(c.x - box.height / 2, c.y - box.width / 2, c.x + box.height / 2, c.y + box.width / 2)
            image_rects.append(tuple(box))
        shape_rects = []
        for d in shapes:
            new = DR._transformed_path(self.session.doc[d.page], d.path, operation)
            shape_rects.append(tuple(new["rect"]))

        def apply(session) -> None:
            for image in images:
                E.transform_image(session, image, operation)
            for page_index in sorted({d.page for d in shapes}):
                DR.transform_drawings(session, page_index, [d for d in shapes if d.page == page_index], operation)

        verb = "Rotate" if operation.startswith("rotate") else "Flip"
        count = len(images) + len(shapes)
        noun = ("Image" if images else "Shape") if count == 1 else f"{count} Objects"
        return self._change_selection(apply, f"{verb} {noun}", [], images, shapes,
                                      image_rects=image_rects, shape_rects=shape_rects)
    def delete(self, item_id: str | None = None) -> bool:
        group = self._window.view.selected_group
        if item_id is None and len(group) > 1 and self.mode == "edit":
            return self._delete_group(group)
        if item_id is None and len(group) > 1 and self.mode == "design":
            return self.delete_fields([split_key(i[4:])[0] for i in group if i.startswith("fld:")])
        item_id = item_id or self._window.view.selected_markup
        if not item_id or self.mode is None:
            return False
        if item_id.startswith("mk:"):
            return self._window.markup.delete(item_id[3:])
        if item_id.startswith(("txt:", "vec:")):  # (a line takes its underline, frame and links)
            obj = self._line(item_id) if item_id.startswith("txt:") else self._drawing(item_id)
            return obj is not None and self._delete_group([item_id])
        if item_id.startswith("img:"):
            image = self._image(item_id)
            return image is not None and self._content(image.page, lambda s: E.delete_image(s, image), "Delete Image")
        if item_id.startswith("fld:"):
            self.delete_field(split_key(item_id[4:])[0])
            return True
        return False

    # --- form fields -----------------------------------------------------------------------------

    def _design(self, text: str, do, undo, pages: set[int]) -> bool:
        try:
            do(self.session)
        except PdfError as exc:
            self._window.show_message(text, str(exc))
            self.refresh()
            return False
        self._window.push_command(FieldDesignCommand(self._window, text, do, undo, pages))
        return True

    def create_field(self, kind, page_index: int, box: QRectF) -> str | None:
        label, size, base = FIELD_TOOLS[kind]
        image, barcode = kind == IMAGE_FIELD, kind == BARCODE_FIELD
        if image or barcode:
            kind = FieldKind.BUTTON
        if box.width() < CLICK or box.height() < CLICK:
            box = QRectF(box.left(), box.top(), size.width(), size.height())
        page = self.session.doc[page_index]
        rect = from_display(page, _tuple(box))
        name = D.unique_name(self.session, base)
        extra: dict = {}
        if kind is FieldKind.RADIO:
            step = box.height() + 8
            second = from_display(page, _tuple(box.translated(0, step)))
            # each button shows its text next to its circle (typed in the properties at once)
            extra = dict(options=("Choice1", "Choice2"), option_rects=(rect, second), labels=("Choice 1", "Choice 2"))
        elif kind in (FieldKind.COMBO, FieldKind.LIST):
            extra = dict(options=("Choice 1", "Choice 2", "Choice 3"))
        elif kind is FieldKind.DATE:
            extra = dict(date_format="mm/dd/yyyy")
        elif kind is FieldKind.BUTTON:
            extra = dict(action="image") if image else dict(action="barcode") if barcode else dict(caption="Button")
        elif kind is FieldKind.CHECKBOX:
            extra = dict(labels=("",))  # (ours: its text is typed in its properties, shown beside the box)
        spec = D.fit_radio(self.session, D.FieldSpec(name, kind, page_index, rect, **extra))  # (radio: fits its text)
        if not self._design(f"Add {label}", lambda s: D.create_field(s, spec), lambda s: D.delete_field(s, spec.name), {page_index}):
            return None
        self._window.view.set_selected_markup(self._overlay_id(name))
        if kind in (FieldKind.RADIO, FieldKind.COMBO, FieldKind.LIST) or (kind is FieldKind.BUTTON and not image):
            self.edit_properties(name)  # their choices (a button: its caption and action) need typing in
        return name

    def _overlay_id(self, name: str) -> str | None:
        try:
            f = F.get_field(self.session, name)
        except PdfError:
            return None
        return "fld:" + field_key(name, f.widgets[0].xref)

    def move_field(self, name: str, xref: int, rect: QRectF) -> None:
        f = F.get_field(self.session, name)
        ref = next((w for w in f.widgets if w.xref == xref), None)
        if ref is None:
            return
        page = self.session.doc[ref.page]
        old, new = ref.rect, from_display(page, _tuple(rect))

        def move(s, frm, to):
            # Found by name and position: xrefs change when a field is recreated by undo.
            field_ = F.get_field(s, name)
            widget = min(field_.widgets, key=lambda w: sum(abs(a - b) for a, b in zip(w.rect, frm)))
            D.move_widget(s, widget.page, widget.xref, to)

        self._design("Move Field", lambda s: move(s, old, new), lambda s: move(s, new, old), {ref.page})

    def field_place(self, item_id: str) -> tuple[int, QRectF] | None:
        """A field's (or radio group's) page and box as displayed."""
        return self._window.view.markup_rect(item_id) if item_id.startswith("fld:") else None

    def place_fields(self, placements: list[tuple[str, QRectF]], text: str) -> bool:
        """Several fields to new boxes (as displayed, on their own pages): Align, Match Size,
        Distribute, Center on Page and moving a selection of fields. One undo step; they
        stay selected. A radio group's buttons keep their places in the group."""
        doc = self.session.doc
        moves = []  # (name, page, old, new): unrotated rects
        for item_id, rect in placements:
            if item_id in getattr(self, "_radio_groups", {}):
                name, page_index = self._radio_groups[item_id]
                page = doc[page_index]
                refs = [r for r in F.get_field(self.session, name).widgets if r.page == page_index]
                shown = [_qrect(to_display(page, r.rect)) for r in refs]
                union = QRectF()
                for box in shown:
                    union = union.united(box)
                sx = rect.width() / union.width() if union.width() else 1.0
                sy = rect.height() / union.height() if union.height() else 1.0
                for ref, box in zip(refs, shown):
                    moved = QRectF(rect.left() + (box.left() - union.left()) * sx,
                                   rect.top() + (box.top() - union.top()) * sy, box.width() * sx, box.height() * sy)
                    moves.append((name, page_index, ref.rect, from_display(page, _tuple(moved))))
            elif item_id.startswith("fld:"):
                name, xref = split_key(item_id[4:])
                ref = next((w for w in F.get_field(self.session, name).widgets if w.xref == xref), None)
                if ref is not None:
                    moves.append((name, ref.page, ref.rect, from_display(doc[ref.page], _tuple(rect))))
        moves = [m for m in moves if any(abs(a - b) > 0.01 for a, b in zip(m[2], m[3]))]
        if not moves:
            return False

        def apply(s, forward: bool) -> None:
            # every widget found first (by name and position: xrefs change when undo
            # recreates a field), then moved, so one button's new place can't be mistaken
            # for another's old one
            chosen = []
            for name, page_index, old, new in moves:
                frm, to = (old, new) if forward else (new, old)
                widgets = [w for w in F.get_field(s, name).widgets if w.page == page_index]
                widget = min(widgets, key=lambda w, frm=frm: sum(abs(a - b) for a, b in zip(w.rect, frm)))
                chosen.append((page_index, widget.xref, to))
            for page_index, xref, to in chosen:
                D.move_widget(s, page_index, xref, to)

        selected = self._window.view.selected_group
        if not self._design(text, lambda s: apply(s, True), lambda s: apply(s, False), {m[1] for m in moves}):
            return False
        self.refresh()
        self._window.view.set_group(selected)
        self.selectionChanged.emit(self._window.view.selected_markup)
        return True

    def delete_field(self, name: str) -> None:
        spec = D.field_spec(self.session, name)
        pages = {spec.page, *(p for p, _r in spec.extra_widgets)}
        self._design("Delete Field", lambda s: D.delete_field(s, name), lambda s: D.create_field(s, spec), pages)

    def delete_fields(self, names: list[str]) -> bool:
        """Several selected fields: one "Delete N Fields" step."""
        names = list(dict.fromkeys(names))
        if not names:
            return False
        specs = [D.field_spec(self.session, n) for n in names]
        pages = {p for s in specs for p in (s.page, *(q for q, _r in s.extra_widgets))}

        def do(s) -> None:
            for n in names:
                D.delete_field(s, n)

        def undo(s) -> None:
            for spec in specs:
                D.create_field(s, spec)

        return self._design(f"Delete {len(names)} Fields" if len(names) > 1 else "Delete Field", do, undo, pages)

    def edit_properties(self, name: str) -> None:
        from planetpdf.ui.dialogs.field_properties import FieldPropertiesDialog

        old = D.field_spec(self.session, name)
        others = [f.name for f in F.list_fields(self.session) if f.kind.fillable and f.name != name]
        dialog = FieldPropertiesDialog(old, self._window, others)
        if dialog.exec() != FieldPropertiesDialog.DialogCode.Accepted:
            return
        self.change_field(name, dialog.spec())

    def change_field(self, name: str, new: D.FieldSpec) -> None:
        old = D.field_spec(self.session, name)
        if old.kind is FieldKind.RADIO and (new.labels, new.font_size, len(new.options)) != (
                old.labels, old.font_size, len(old.options)):
            new = D.fit_radio(self.session, new)  # new text or size: the buttons fit it again
        checkbox_text = old.kind is FieldKind.CHECKBOX and (new.labels, new.font_size) != (old.labels, old.font_size)
        if checkbox_text:
            new = D.fit_checkbox(self.session, new)  # (its text: the box fits it, like a radio button)
        if new == old:
            return
        pages = {old.page, *(p for p, _r in old.extra_widgets)}
        if checkbox_text or old.kind is FieldKind.RADIO and (new.options, new.labels, new.font_size, new.option_rects) != (
                old.options, old.labels, old.font_size, old.option_rects):  # (drawn again)
            do = lambda s: D.replace_field(s, name, new)  # noqa: E731
            undo = lambda s: D.replace_field(s, new.name, old)  # noqa: E731
        else:
            do = lambda s: D.update_field(s, name, new)  # noqa: E731
            undo = lambda s: D.update_field(s, new.name, old)  # noqa: E731
        if self._design("Field Properties", do, undo, pages):
            self._window.view.set_selected_markup(self._overlay_id(new.name))


class EditUi:
    """Mode actions, the Edit toolbar and the Prepare Form toolbar."""

    def __init__(self, window: MainWindow) -> None:
        self.window = window
        controller = window.edit
        self.act_mode_edit = QAction("Edit PDF", window, checkable=True)
        self.act_mode_edit.setToolTip("Edit the page: retype text, add text, move, resize, replace, or delete images")
        self.act_mode_design = QAction("Prepare Form", window, checkable=True)
        self.act_mode_design.setToolTip("Create form fields and change their properties")

        self.edit_toolbar = QToolBar("Edit", window)
        self.edit_toolbar.setObjectName("editToolbar")
        self.edit_toolbar.setMovable(False)
        self.act_add_text = QAction("Add Text", window)
        self.act_add_text.setToolTip("Click on the page and type new text")
        self.act_add_text.triggered.connect(lambda: controller.set_tool("text"))
        self.act_add_image = QAction("Add Image…", window)
        self.act_add_image.triggered.connect(lambda: controller.add_image())
        # Free-form eraser (user request 2026-10-01): wipes whatever the brush passes over
        self.act_erase = QAction("Eraser", window, checkable=True)
        self.act_erase.setToolTip("Eraser: drag over text, pictures, lines, or boxes to erase them. "
                                  "The + and - keys change its size; Esc stops")
        self.act_erase.triggered.connect(lambda on: controller.set_tool("erase" if on else None))
        self.eraser_menu = QMenu("Eraser size", window)
        sizes = QActionGroup(window)
        for label, size in (("Fine (4 pt)", 4), ("Small (8 pt)", 8), ("Medium (12 pt)", 12), ("Large (24 pt)", 24),
                            ("Huge (48 pt)", 48)):
            action = self.eraser_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(size == controller.eraser_size)
            action.triggered.connect(lambda _c=False, s=size: (controller.set_eraser_size(s), controller.set_tool("erase")))
            action.setData(size)
            sizes.addAction(action)
        # (the + and - keys reach sizes between these too: only an exact match is ticked)
        controller.eraserSizeChanged.connect(
            lambda size: [a.setChecked(a.data() == size) for a in self.eraser_menu.actions()])
        self.edit_toolbar.addActions([self.act_add_text, self.act_add_image])
        self.edit_toolbar.addSeparator()
        self.edit_toolbar.addWidget(_hint("Click a line of text to retype it (Enter to finish). Drag an image to move it, "
                                          "its corners to resize it; right-click for more.", window))
        self.edit_toolbar.setVisible(False)

        self.design_toolbar = QToolBar("Prepare Form", window)
        self.design_toolbar.setObjectName("designToolbar")
        self.design_toolbar.setMovable(False)
        self.field_group = QActionGroup(window)
        # Acrobat's bar (user screenshot 2026-10-03): Select, the field tools, the pin, Preview
        self.act_select_field = QAction("Select", window, checkable=True)
        self.act_select_field.setToolTip("Select, move, and resize fields (a box or Shift+click selects several)")
        self.act_select_field.triggered.connect(lambda: (self._fields_again(), controller.set_tool(None)))
        self.act_select_field.setChecked(True)
        self.field_group.addAction(self.act_select_field)
        self.design_toolbar.addAction(self.act_select_field)
        # Edit text & images (user screenshot 2026-10-03): Edit PDF on the page without leaving
        # Prepare Form (its FORMAT panel flies out); a field tool or the arrow comes back
        self.act_edit_form_content = QAction("Edit text && images", window, checkable=True)
        self.act_edit_form_content.setToolTip("Edit text & images: change the page itself, then pick a field tool "
                                              "or the arrow to go on with the fields")
        self.act_edit_form_content.toggled.connect(lambda on: window.tool_panes.edit_form_content(on))
        self.design_toolbar.addAction(self.act_edit_form_content)
        self.design_toolbar.addAction(self.act_add_text)  # (T: Add Text, as on the Edit PDF bar)
        self.field_actions: dict = {}
        for kind, (label, _size, _base) in FIELD_TOOLS.items():
            action = QAction(label, window, checkable=True)
            action.setToolTip(f"Add a {label.lower()}: drag a box on the page, or click for the standard size")
            action.triggered.connect(lambda _c=False, k=kind: (self._fields_again(), controller.set_tool(k)))
            self.field_group.addAction(action)
            self.field_actions[kind] = action
            self.design_toolbar.addAction(action)
        self.field_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.design_toolbar.addSeparator()
        self.act_detect_fields = QAction("Detect Fields", window)
        self.act_detect_fields.setToolTip("Look for blanks, boxes, and lines to fill in on every page and make "
                                          "fields of them (one undo step)")
        self.act_detect_fields.triggered.connect(lambda: window.detect_form_fields())
        self.design_toolbar.addAction(self.act_detect_fields)
        self.act_form_help = QAction("Help", window)
        self.act_form_help.setToolTip("How Prepare Form works")
        self.act_form_help.triggered.connect(lambda: window.show_message("Prepare Form", FORM_HELP))
        self.design_toolbar.addAction(self.act_form_help)
        self.act_preview_form = QAction("Preview", window, checkable=True)
        self.act_preview_form.setToolTip("Try the form: fill it in as people will (click Preview again to go on editing)")
        self.act_preview_form.toggled.connect(lambda on: window.tool_panes.preview_form(on))
        # (on the bar as a blue pill beside Close: ui/toolbars.py)
        self.design_toolbar.setVisible(False)
        controller.toolChanged.connect(self._on_tool)
        controller.modeChanged.connect(self._on_mode)

    def _fields_again(self) -> None:
        """A field tool or the arrow after Edit text & images / Preview: back to the fields."""
        panes = getattr(self.window, "tool_panes", None)
        if panes is not None and panes.form_side_mode is not None:
            panes.form_side(None)

    def _on_mode(self, mode) -> None:
        pass  # (which tool bar shows is up to ui/toolbars.ToolPanes)

    def _on_tool(self, tool) -> None:
        for kind, action in self.field_actions.items():
            action.setChecked(kind == tool)
        self.act_select_field.setChecked(tool is None)
        self.act_erase.setChecked(tool == "erase")


FORM_HELP = (
    "Prepare Form makes fillable forms.\n\n"
    "• Pick a field tool (text field, checkbox, radio buttons, list box, dropdown, button, image, date, "
    "signature), then drag a box on the page, or click for the standard size.\n"
    "• Double-click a field for its name, choices, and other properties. Drag it to move it, its corners to "
    "resize it.\n"
    "• Select several with a box around them or Shift+click, then use Align, Center, Match Size, and "
    "Distribute in the panel on the right.\n"
    "• Detect Fields turns blank lines, boxes, and check boxes on the pages into fields.\n"
    "• Edit text & images changes the page itself; T adds text to it.\n"
    "• Preview lets you fill in the form as people will. Click Edit to go back."
)


def _hint(text: str, parent):
    from PySide6.QtWidgets import QLabel

    label = QLabel("  " + text, parent)
    label.setStyleSheet("color: palette(mid);")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label
