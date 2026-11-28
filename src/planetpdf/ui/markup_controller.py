"""Markup mode, stamps and placed signatures, as WYSIWYG as possible.

Two working modes (user decision): **Fill Form** (click fields to fill them; placed
signatures/initials can be selected and moved) and **Markup** (draw shapes, lines,
freehand, text boxes, notes, highlights; place stamps; select/move/resize/delete any
markup; fields are not clickable). Placing a signature/initials or a stamp works in
either mode: it's a one-shot tool that returns to selection afterwards.

WYSIWYG (user request): shapes preview in their real style while drawn; text markup
selects words in reading order; stamps/signatures follow the cursor before placing;
a moved item is lifted off the page and dragged as itself; text boxes are typed on
the page in their final font, and notes get an on-page note pad.

Everything is an undo command (ui/commands.py). Geometry arrives from the view in
displayed page coordinates and is converted to unrotated page coordinates here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox

from planetpdf.core import markup as K
from planetpdf.core.appearance import source_size
from planetpdf.core.document import PdfError
from planetpdf.core.markup import Kind, MarkupSpec, Style
from planetpdf.core.redaction.marks import from_display, to_display
from planetpdf.core.signing.visual import signature_source_pdf
from planetpdf.ui.commands import (
    AddMarkupCommand,
    EditForeignTextCommand,
    EraseInkCommand,
    FlattenMarkupCommand,
    PopupCommand,
    ReplyCommand,
    RemoveMarkupCommand,
    ReplaceMarkupCommand,
)
from planetpdf.ui.inline_editor import InlineTextEditor, grown_rect
from planetpdf.ui.viewer.page_view import DrawStyle
from planetpdf.ui.viewer.qimage import pdf_preview

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

# Tool -> how the view draws its live preview
DRAW_KIND = {
    Kind.RECT: "rect", Kind.ELLIPSE: "ellipse", Kind.TEXTBOX: "textbox",
    Kind.HIGHLIGHT: "text", Kind.UNDERLINE: "text", Kind.STRIKEOUT: "text",
    Kind.LINE: "line", Kind.ARROW: "arrow", Kind.INK: "ink", Kind.NOTE: "text",  # (Note: like the highlighter)
    Kind.STAMP: "place", Kind.SEAL: "place", Kind.SIGNATURE: "place", Kind.INITIALS: "place",
    Kind.TEXT: "point", Kind.SYMBOL: "point",
    Kind.POLYGON: "polygon", Kind.POLYLINE: "polyline", Kind.CALLOUT: "callout", Kind.CARET: "point",
    Kind.REPLACE: "text", Kind.CLOUD: "polygon", Kind.ATTACH: "point", Kind.INK_ERASER: "ink",
}
TEXT_TOOLS = (Kind.TEXT, Kind.TEXTBOX, Kind.CARET, Kind.NOTE)
MIN_SYMBOL = 4.0  # points: a smaller drag is a click (the circle the size of the last one)


def _resized(old: K.MarkupSpec, new: K.MarkupSpec) -> bool:
    a, b = pymupdf.Rect(old.rect), pymupdf.Rect(new.rect)
    return abs(a.width - b.width) > 0.01 or abs(a.height - b.height) > 0.01


# tools Enter on the page places with (accessibility, user 2026-10-25): in the middle of what shows
KEYBOARD_PLACED = (Kind.STAMP, Kind.SEAL, Kind.SIGNATURE, Kind.INITIALS, Kind.TEXT, Kind.SYMBOL, Kind.NOTE,
                   Kind.TEXTBOX)  # I-beam cursor (Note: to select the words)
PICK_CURSORS = {"zoom": "zoom_in"}  # box-picking tools with a cursor of their own (Marquee Zoom: user 2026-10-24)
DEFAULT_COLORS = {
    Kind.HIGHLIGHT: "#ffe600",
    Kind.UNDERLINE: "#1f5fd6",
    Kind.STRIKEOUT: "#d11a1a",
    Kind.NOTE: "#f2c200",
    Kind.TEXTBOX: "#1a1a1a",
    Kind.TEXT: "#000000",
    Kind.CARET: "#1f5fd6",
    Kind.CALLOUT: "#1a1a1a",
    Kind.REPLACE: "#1f5fd6",  # (Acrobat's Replace Text is blue)
}
SIGNATURE_WIDTH = 144.0  # default placement width in points (2 in)
INITIALS_WIDTH = 54.0
CLICK = 3.0  # a box smaller than this was a click
TEXTBOX_DEFAULT_WIDTH = 200.0
NOTE_PAD = QSizeF(200.0, 90.0)
GHOST_ZOOM = 3.0  # render ghosts sharply enough for zoomed-in views


def _rgb(value: str) -> tuple[float, float, float]:
    c = QColor(value)
    return (c.redF(), c.greenF(), c.blueF()) if c.isValid() else (0.85, 0.1, 0.1)


def _qrect(r) -> QRectF:
    r = pymupdf.Rect(r)
    return QRectF(r.x0, r.y0, r.width, r.height)


def _tuple(r: QRectF) -> tuple:
    return (r.left(), r.top(), r.right(), r.bottom())


def _inside_page(rect: QRectF, page: pymupdf.Page) -> QRectF:
    """``rect`` (displayed coordinates) moved, not resized, so it lies on the page."""
    shown = page.rect  # (displayed size)
    dx = max(0.0, shown.x0 - rect.left()) - max(0.0, rect.right() - shown.x1)
    dy = max(0.0, shown.y0 - rect.top()) - max(0.0, rect.bottom() - shown.y1)
    if rect.width() > shown.width:
        dx = shown.x0 - rect.left()
    if rect.height() > shown.height:
        dy = shown.y0 - rect.top()
    return rect.translated(dx, dy)


def _fit_text(rect: QRectF, text: str, font_size: float, style: Style | None = None, doc=None) -> QRectF:
    """Grow a text box (as displayed) so the PDF's own line wrapping fits in it."""
    needed = K.textbox_height(text, rect.width(), font_size, style, doc)
    return QRectF(rect.left(), rect.top(), rect.width(), max(rect.height(), needed))


TEXT_KINDS = (Kind.TEXT, Kind.TEXTBOX, Kind.CALLOUT)  # items whose text has a font, bold words…


def deselect(view) -> None:
    """Nothing selected on the page (a tool was chosen: user 2026-10-22, the handles of the text
    added last stayed on when the signature tool was picked)."""
    if view.selected_group:
        view.set_group([])
        view.set_selected_markup(None)
NOTE_TEXT_SIZE = 9.0  # = the note box's text (note_popups.TEXT_SIZE)


def _pdf_date(value: str) -> str:
    """"D:20261004141500-05'00'" as "10/4/2026 2:15 PM" ("" when it can't be read)."""
    import re

    match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?", value or "")
    if not match:
        return ""
    year, month, day, hour, minute = (int(g) if g else 0 for g in match.groups())
    suffix = "AM" if hour < 12 else "PM"
    return f"{month}/{day}/{year} {(hour % 12) or 12}:{minute:02d} {suffix}"


# Tools that place one thing and then go back to the arrow (user 2026-10-17: "Some things like
# attach file should be a one and done … Do what makes sense for the tool"); every other tool
# stays on until another is chosen.
ONE_AND_DONE = {Kind.STAMP, Kind.SEAL, Kind.SIGNATURE, Kind.INITIALS, Kind.ATTACH}


class MarkupController(QObject):
    toolChanged = Signal(object)  # Kind | None
    modeChanged = Signal(bool)  # True = Markup mode
    textColorChanged = Signal()  # the color for new placed text (toolbar swatch)
    editorChanged = Signal()  # an on-page editor opened / closed, or its text format changed

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.markup_mode = False
        self.tool: Kind | None = None
        self._pending_source: bytes | None = None  # stamp / signature being placed
        self._pending_label = ""
        self._pending_size = QSizeF()
        self._one_shot_box: Callable[[int, tuple], None] | None = None  # e.g. digital signature box
        self._pick_keep = False  # the box picker is a tool that stays on (Link, Crop Pages…)
        self._pick_name = ""
        self._words: dict[int, list[tuple[tuple, tuple, QRectF]]] = {}  # page -> words (cached)
        self._hidden: str | None = None  # item lifted off the page while dragged / edited
        self._editing_note: str | None = None  # a note being typed in (its box steps aside)
        self.editor: InlineTextEditor | None = None
        self.typing_format: dict | None = None  # placed text being typed: its font, size, color…
        self.typing_kind = Kind.TEXT
        self.textbox_borderless = False  # (the text box tool as Acrobat's "Add text comment")
        self.items: list[K.MarkupItem] = []
        self.symbol_name = "check"  # which Fill & Sign symbol the Symbol tool places
        self.suspended = False  # Edit / Prepare Form mode owns the overlays and view signals
        view = window.view
        view.keyboard_placing = lambda: (not self.suspended and self._one_shot_box is None
                                         and self.tool in KEYBOARD_PLACED)
        view.grab_with_tool = self._grabbed_with_tool
        # (a one-shot box, e.g. Marquee Zoom or Edit PDF ▸ Link, works in every mode)
        view.shapeDrawn.connect(lambda draw, page, data: self._on_shape(draw, page, data)
                                if not self.suspended or (draw == "box" and self._one_shot_box is not None) else None)
        # (Esc only drops what was being drawn: the tool stays on, user 2026-10-17; a tool that
        # places one thing is put away)
        view.drawToolCanceled.connect(self._cancel_one_shot_or(self._unless_suspended(self._escape_tool)))
        view.markupClicked.connect(self._unless_suspended(self.select))
        view.pageClicked.connect(self._unless_suspended(self._click_adds_text))
        view.markupMoved.connect(self._unless_suspended(self._on_moved))
        view.markupMovedToPage.connect(self._unless_suspended(self.move_to_page))
        view.markupDoubleClicked.connect(self._unless_suspended(self.edit_text))
        view.markupContextMenu.connect(self._unless_suspended(self._context_menu))
        view.markupDragStarted.connect(self._unless_suspended(self._lift))
        view.markupDragCanceled.connect(self._unless_suspended(self._drop_back))
        view.popupMoved.connect(self._unless_suspended(self.move_popup))
        view.popupToggled.connect(self._unless_suspended(self.open_popup))
        view.popupEditRequested.connect(self._unless_suspended(self.edit_text))
        view.popupReplyRequested.connect(self._unless_suspended(self.reply_to))
        view.set_word_provider(self._display_words)
        view.brushStep.connect(self.step_ink_eraser)
        self.apply_mode()

    def _cancel_one_shot_or(self, slot):
        def call(*args):
            if self._one_shot_box is not None:  # (Esc while picking a box)
                if self._pick_keep:
                    return  # (a tool that picks boxes, like Link or Crop: it stays on, user 2026-10-17)
                self._one_shot_box = None
                self._window.view.set_draw_tool(None)
                self._window.statusBar().clearMessage()
                self.toolChanged.emit(self.tool)  # (its button shows it's off)
                return
            slot(*args)
        return call

    def _unless_suspended(self, slot):
        def call(*args):
            if not self.suspended:
                slot(*args)
        return call

    @property
    def session(self):
        return self._window.session

    # --- mode and tools ---------------------------------------------------------------------

    def set_markup_mode(self, on: bool) -> None:
        edit = getattr(self._window, "edit", None)
        if edit is not None and edit.mode is not None:
            edit.set_mode(None)  # leaving Edit / Prepare Form
        if on == self.markup_mode:
            self.modeChanged.emit(on)
            return
        self.finish_editing()
        self.markup_mode = on
        if not on and self.tool not in (None, Kind.SIGNATURE, Kind.INITIALS, Kind.TEXT):
            self.set_tool(None)
        self._window.forms.deactivate()
        self.apply_mode()
        self.refresh()
        self.modeChanged.emit(on)

    def apply_mode(self) -> None:
        # Fill Form: fields clickable, and placed signatures can still be moved/deleted.
        self._window.view.set_interaction(fields=not self.markup_mode, markup=True)

    def set_tool(self, tool: Kind | None, *, borderless: bool | None = None) -> None:
        """``borderless`` (text box): Acrobat's "Add text comment", a text box with no box
        around it (left as it was when not given)."""
        self.finish_editing()
        self.tool = tool
        if borderless is not None:
            self.textbox_borderless = borderless
        if tool not in (Kind.STAMP, Kind.SEAL, Kind.SIGNATURE, Kind.INITIALS):
            self._pending_source = None
        self._one_shot_box = None
        self._pick_keep = False
        view = self._window.view
        if tool is not None:
            deselect(view)  # (another tool: the last item's handles go, user 2026-10-22)
        draw = DRAW_KIND.get(tool) if tool else None
        if tool is Kind.SYMBOL and self.symbol_name == "circle":
            draw = "ellipse"  # (dragged from corner to corner: user 2026-10-26)
        view.set_draw_tool(draw, text_cursor=tool in TEXT_TOOLS,
                           text_hover=tool is not None and (tool.text_markup or tool is Kind.CALLOUT))
        # the pencil eraser: a circle as wide as the brush (the Edit PDF Eraser's size)
        view.set_brush(self._window.edit.eraser_size if tool is Kind.INK_ERASER else None)
        if tool is Kind.TEXT:
            self._window.statusBar().showMessage("Click where the text should go, then type. Esc cancels.", 8000)
        elif tool is Kind.INK_ERASER:
            self._window.statusBar().showMessage(
                "Eraser: drag over pencil drawings to erase parts of them. The + and - keys change its size. "
                "Esc stops.", 8000)
        elif tool is Kind.ATTACH:
            self._window.statusBar().showMessage("Click where the attachment's paperclip goes. Esc cancels.", 8000)
        elif tool is Kind.REPLACE:
            self._window.statusBar().showMessage("Drag across the words to replace, then type the new words.", 8000)
        elif tool is Kind.NOTE:
            self._window.statusBar().showMessage("Note: drag over words to highlight them with a note, or click anywhere for a sticky "
                                                 "note. Then type in its box.", 8000)
        if tool is not None:
            self._window.act_redact_area.setChecked(False)
            self.update_draw_style()
        self.toolChanged.emit(tool)

    def update_draw_style(self) -> None:
        """Tell the view how the live preview should look (same as the result)."""
        tool = self.tool or Kind.RECT
        style = self.style_for(tool)
        if tool is Kind.SYMBOL:  # (the circle's preview: its color, line and fill)
            s = self._window.settings
            color = self.style_for(Kind.TEXT).color
            style = replace(style, color=color, width=s.circle_line, opacity=1.0,
                            fill=color if s.circle_filled else None)
        self._window.view.set_draw_style(
            DrawStyle(
                color=QColor.fromRgbF(*style.color),
                width=style.width,
                fill=QColor.fromRgbF(*style.fill) if style.fill else None,
                opacity=style.opacity,
                text_markup=("strikeout" if tool is Kind.REPLACE else tool.value) if tool.text_markup else "highlight",
            )
        )

    def style_for(self, kind: Kind) -> Style:
        s = self._window.settings
        colors = s.markup_colors or {}
        color = _rgb(colors.get(kind.value, DEFAULT_COLORS.get(kind, s.markup_color)))
        fill = _rgb(s.markup_fill) if s.markup_fill_enabled and kind in (Kind.RECT, Kind.ELLIPSE, Kind.TEXTBOX, Kind.POLYGON, Kind.CLOUD, Kind.CALLOUT) else None
        style = Style(color=color, fill=fill, width=s.markup_width, opacity=s.markup_opacity, font_size=s.markup_font_size)
        # the font chosen for the next placed text / text box (Acrobat keeps each tool's own)
        chosen = s.text_format if kind is Kind.TEXT else s.textbox_format if kind in (Kind.TEXTBOX, Kind.CALLOUT) else None
        if chosen:
            known = set(style.text_format)
            style = replace(style, **{k: v for k, v in chosen.items() if k in known})
        return style

    def set_color(self, kind: Kind | None, color: QColor) -> None:
        s = self._window.settings
        if kind is None:
            s.markup_color = color.name()
        else:
            colors = dict(s.markup_colors or {})
            colors[kind.value] = color.name()
            s.markup_colors = colors
        self.update_draw_style()

    # --- words for text markup ---------------------------------------------------------------

    def _page_words(self, index: int) -> list[tuple[tuple, tuple, QRectF]]:
        """(unrotated word rect, (block, line), displayed rect) in reading order."""
        if index not in self._words:
            page = self.session.doc[index]
            words = []
            for w in page.get_text("words", sort=True):
                shown = to_display(page, w[:4])
                words.append((tuple(w[:4]), (w[5], w[6]), QRectF(shown.x0, shown.y0, shown.width, shown.height)))
            self._words[index] = words
        return self._words[index]

    def _display_words(self, index: int) -> list[tuple[QRectF, object]]:
        if self.session is None:
            return []
        return [(shown, line) for _rect, line, shown in self._page_words(index)]

    # --- placing stamps and signatures ------------------------------------------------------

    def place_source(self, kind: Kind, source: bytes, label: str, size: QSizeF | None = None) -> None:
        """Arm a one-shot tool: the source follows the cursor; click (or drag) to place it."""
        self.set_tool(kind)
        self._pending_source = source
        self._pending_label = label
        if size is None:
            width, height = source_size(source)
            size = QSizeF(width, height)
        self._pending_size = size
        ghost = pdf_preview(source, int(size.width() * GHOST_ZOOM), int(size.height() * GHOST_ZOOM))
        self._window.view.set_placement_ghost(ghost, size)
        self._window.view.setFocus()  # (so Enter places it there and then: no mouse needed)
        self._window.statusBar().showMessage(
            f"Click on the page to place the {label.lower()} (or drag to size it), or press Enter. Esc cancels.", 8000
        )

    def place_stamp(self, stamp_id: str) -> None:
        library = self._window.stamp_library
        try:
            spec = library.get(stamp_id)
            s = self._window.settings
            source = library.render(spec, user=s.user_name, company=s.company_name, now=datetime.now(), date_format=s.date_format, time_format=s.time_format)
        except (PdfError, OSError) as exc:
            self._window.show_message("Stamp", str(exc))
            return
        s.last_stamp = spec.stamp_id  # Stamp ▾ offers it first next time
        self.place_source(Kind.SEAL if spec.kind == "image" else Kind.STAMP, source, spec.name)

    def place_signature(self, kind: str = "signature", sig_id: str | None = None) -> bool:
        """Place the saved signature/initials (asks to create one if none is saved)."""
        library = self._window.signature_library
        item = library.get(sig_id) if sig_id else library.default(kind)
        if item is None:
            if not self._window.create_signature(kind):
                return False
            item = library.default(kind)
            if item is None:
                return False
        try:
            source = signature_source_pdf(library.png(item))
        except (PdfError, OSError, RuntimeError) as exc:
            self._window.show_message("Signature", f"The saved {kind} couldn't be loaded: {exc}")
            return False
        width, height = source_size(source)
        target = SIGNATURE_WIDTH if kind == "signature" else INITIALS_WIDTH
        size = QSizeF(target, target * height / width)
        self.place_source(Kind.SIGNATURE if kind == "signature" else Kind.INITIALS, source, kind.title(), size)
        return True

    def _escape_tool(self) -> None:
        if self.tool in ONE_AND_DONE:
            self.set_tool(None)

    def _click_adds_text(self, page_index: int, point: QPointF) -> None:
        """A plain click on the page with no tool on (the arrow, also right after a
        signature or stamp is placed) adds text there, as Add Text does (user 2026-11-16:
        "clicking anywhere when no other tool is selected or after a signature is dropped
        should default to adding text"). Preferences ▸ Documents can switch it off."""
        if (self.session is None or not self._window.settings.click_adds_text or self.tool is not None
                or self._one_shot_box is not None or self._window.text_tools.active):
            return
        # (a click on an item the arrow can't select, such as one that keeps only its look or another
        # program's comment, isn't a click on empty paper: no new text on top of it; review 2026-11-27)
        page = self.session.doc[page_index]
        if any(item.page == page_index and _qrect(to_display(page, item.rect)).contains(point) for item in self.items):
            self._window.statusBar().showMessage(
                "That's an item on the page: open the Comment tool to select it, or click empty space to add text.",
                8000)
            return
        deselect(self._window.view)
        self.new_placed_text(page_index, point)

    # --- remembered text: suggested while typing (user 2026-11-16) ----------------------------------

    def _suggest_text(self, typed: str) -> list[str]:
        if not self._window.settings.remember_text:
            return []
        return self._window.text_history.suggestions(typed)

    def _remember_text(self, text: str) -> None:
        s = self._window.settings
        if s.remember_text:
            self._window.text_history.remember(text, allow_private=s.remember_private_numbers)

    @property
    def picking(self) -> bool:
        """A tool that picks boxes (Link, Crop Pages, Snapshot, Marquee Zoom) is on."""
        return self._one_shot_box is not None and self._pick_keep

    @property
    def pick_name(self) -> str:
        """Which box-picking tool is on ("link", "crop", …; "" = none)."""
        return self._pick_name if self._one_shot_box is not None else ""

    def cancel_pick_box(self) -> None:
        was = self.picking
        self._pick_keep = False
        if self._one_shot_box is not None:
            self._one_shot_box = None
            self._window.view.set_draw_tool(None)
        if was:
            self.toolChanged.emit(self.tool)  # (the tool buttons show it's off)

    def pick_box(self, callback: Callable[[int, tuple], None], message: str, *, keep: bool = False,
                 name: str = "") -> None:
        """Let the user drag a box on a page; ``callback(page, unrotated_rect)``. ``keep``: a
        tool (Link, Crop Pages…) that stays on for the next box until another tool is chosen
        (user 2026-10-17); else once, a step of something else (the signature's box)."""
        self.set_tool(None)
        deselect(self._window.view)
        self._one_shot_box = callback
        self._pick_keep = keep
        self._pick_name = name
        self.toolChanged.emit(None)  # (the tool buttons show it's on)
        self._window.view.set_draw_tool("box", tool_cursor=PICK_CURSORS.get(name))
        self._window.statusBar().showMessage(message, 10000)

    # --- drawing → specs ---------------------------------------------------------------------

    def _signed(self, old: MarkupSpec, new: MarkupSpec) -> MarkupSpec:
        """Its words edited: signed by whoever edited them (user 2026-10-04). Anything else
        (moved, resized, restyled, replied to) keeps its author, even a blank one."""
        return replace(new, author=self._author()) if new.text != old.text else new

    def _author(self) -> str:
        return self._window.settings.author()  # (Preferences' name, else the login name: as Acrobat)

    def _on_shape(self, draw: str, page_index: int, data) -> None:
        session = self.session
        if session is None:
            return
        page = session.doc[page_index]
        if draw == "box" and self._one_shot_box is not None:
            callback, self._one_shot_box = self._one_shot_box, None
            keep = self._pick_keep
            if not keep:
                self._window.view.set_draw_tool(None)
            rect = data if isinstance(data, QRectF) else QRectF()
            if rect.width() < 20 or rect.height() < 10:
                rect = QRectF(rect.center().x() - 90, rect.center().y() - 25, 180, 50)
            callback(page_index, from_display(page, _tuple(rect)))
            if keep and self._pick_keep and self._one_shot_box is None:  # (still on: ready for the next box)
                self._one_shot_box = callback
                self._window.view.set_draw_tool("box", tool_cursor=PICK_CURSORS.get(self._pick_name))
            elif not keep:
                self.toolChanged.emit(self.tool)  # (one and done: its button shows it's off)
            return
        tool = self.tool
        if tool is None:
            return
        if tool is Kind.TEXTBOX:
            self._new_textbox(page_index, data)
            return
        if tool is Kind.TEXT:  # (every tool stays on until another is chosen: user 2026-10-17)
            self.new_placed_text(page_index, data.center())
            return
        if tool is Kind.SYMBOL:
            self._new_symbol(page_index, draw, data)
            return
        if tool is Kind.NOTE:
            if draw == "text" and data is not None:  # words highlighted: the note goes with them
                self._new_highlight_note(page_index, page, data)
            elif draw == "point":  # a click away from text: a sticky note there
                self._new_note(page_index, data.center())
            return
        if draw == "point" and tool.text_markup:
            return  # (a click with the highlighter caught no words)
        if tool is Kind.CALLOUT:
            self._new_callout(page_index, data)
            return
        if tool is Kind.CARET:
            self._new_caret(page_index, data.center())
            return
        if tool is Kind.ATTACH:
            self._new_attachment(page_index, data.center())
            return
        if tool is Kind.INK_ERASER:
            self._erase_ink(page_index, page, data)
            return
        try:
            spec = self._spec_from_shape(tool, page, page_index, data)
        except PdfError as exc:
            self._window.statusBar().showMessage(str(exc), 5000)
            return
        if spec is None:
            return
        if tool is Kind.REPLACE:
            self._ask_replacement(page_index, spec)
            return
        if tool in ONE_AND_DONE:
            self.set_tool(None)  # (it places one thing: the arrow, and it's selected to adjust)
        self.add(spec)

    def _ask_replacement(self, page_index: int, spec: MarkupSpec) -> None:
        """Replace Text: the crossed-out words are chosen; a note pad for the new words."""
        page = self.session.doc[page_index]
        last = pymupdf.Rect(spec.points[-4], spec.points[-1]) * page.rotation_matrix  # (as displayed)
        anchor = QPointF(last.x1, last.y1)

        def commit(text: str, _final: QRectF) -> None:
            if text.strip():
                self.add(replace(spec, text=text))

        self._open_editor(page_index, self._note_pad_rect(page_index, anchor), "", note=True, font_size=11,
                          color=QColor("black"), on_commit=commit, on_cancel=lambda: None)

    def _new_attachment(self, page_index: int, point: QPointF) -> None:
        """Attach File: a paperclip where clicked, holding the chosen file."""
        from pathlib import Path

        path = self._window.choose_attachment()
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self._window.show_message("Attach File", f"{Path(path).name} could not be read:\n\n{exc}")
            return
        page = self.session.doc[page_index]
        q = pymupdf.Point(point.x(), point.y()) * page.derotation_matrix
        self.set_tool(None)  # (one and done: a file is chosen each time)
        self.add(self._new(Kind.ATTACH, page_index, rect=(q.x, q.y, q.x + 20, q.y + 20), source=data,
                           text=Path(path).name))

    def _erase_ink(self, page_index: int, page: pymupdf.Page, data) -> None:
        """The pencil eraser: cut what it passed over out of the pencil drawings (ours) on
        the page; one undo step for the whole stroke."""
        path = [(q.x, q.y) for q in (pymupdf.Point(p.x(), p.y()) * page.derotation_matrix for p in data)]
        radius = self._window.edit.eraser_size / 2
        changes = []
        for item in K.list_markup(self.session, [page_index]):
            if item.spec is None or item.kind is not Kind.INK:
                continue
            result = K.erase_from_ink(item.spec, path, radius)
            if result is not False:
                changes.append((item.spec, result))
        if not changes:
            return
        self._window.push_command(EraseInkCommand(self, changes))

    def step_ink_eraser(self, direction: int) -> None:
        """+ / - while the pencil eraser is on (the same sizes as Edit PDF's Eraser)."""
        if self.tool is not Kind.INK_ERASER:
            return
        from planetpdf.ui.edit_controller import ERASER_SIZES

        edit = self._window.edit
        sizes = [s for s in ERASER_SIZES if (s > edit.eraser_size + 0.01 if direction > 0 else s < edit.eraser_size - 0.01)]
        size = (sizes[0] if direction > 0 else sizes[-1]) if sizes else edit.eraser_size
        edit.set_eraser_size(size)
        self._window.view.set_brush(size)
        self._window.statusBar().showMessage(f"Eraser: {size:g} pt   (+ bigger, - smaller)", 3000)

    def _new(self, tool: Kind, index: int, style: Style | None = None, **kw) -> MarkupSpec:
        return MarkupSpec(mark_id=K.new_id(), page=index, kind=tool, style=style or self.style_for(tool),
                          author=self._author(), **kw)

    def _spec_from_shape(self, tool: Kind, page: pymupdf.Page, index: int, data) -> MarkupSpec | None:
        def unrot_point(p: QPointF) -> tuple:
            q = pymupdf.Point(p.x(), p.y()) * page.derotation_matrix
            return (q.x, q.y)

        if tool.uses_appearance:
            if not self._pending_source:
                return None
            rect = data  # the view already sized it (click = default size, drag = fitted)
            if rect.width() < CLICK or rect.height() < CLICK:
                size = self._pending_size
                rect = QRectF(rect.center().x() - size.width() / 2, rect.center().y() - size.height() / 2, size.width(), size.height())
            rect = _inside_page(rect, page)  # never (partly) off the page, out of sight
            return self._new(tool, index, rect=from_display(page, _tuple(rect)), source=self._pending_source, text=self._pending_label)
        if tool in (Kind.RECT, Kind.ELLIPSE):
            if data.width() < CLICK or data.height() < CLICK:
                return None
            return self._new(tool, index, rect=from_display(page, _tuple(data)))
        if tool in (Kind.LINE, Kind.ARROW):
            points = tuple(unrot_point(p) for p in data)
            return self._new(tool, index, rect=K.bounds(points), points=points)
        if tool is Kind.INK:
            points = tuple(unrot_point(p) for p in data)
            return self._new(tool, index, rect=K.bounds(points), points=points, strokes=(len(points),))
        if tool in (Kind.POLYGON, Kind.POLYLINE, Kind.CLOUD):
            points = tuple(unrot_point(p) for p in data)
            return self._new(tool, index, rect=K.bounds(points), points=points)
        if tool.text_markup:
            all_words = self._page_words(index)
            if isinstance(data, list):  # (a box drawn off the text: the words it touches)
                words = [all_words[i] for i in data if 0 <= i < len(all_words)]
            else:
                lo, hi = data
                words = all_words[lo : hi + 1]
            if not words:
                raise PdfError("No text there. Drag across the words to mark.")
            # One quad per line (no gaps between words), in reading order.
            lines: dict[tuple, pymupdf.Rect] = {}
            for rect, line, _shown in words:
                lines[line] = lines[line] | pymupdf.Rect(rect) if line in lines else pymupdf.Rect(rect)
            points = []
            for r in lines.values():
                q = r.quad
                points += [tuple(q.ul), tuple(q.ur), tuple(q.ll), tuple(q.lr)]
            return self._new(tool, index, rect=K.bounds(points), points=tuple(points))
        return None

    # --- on-page text editing -------------------------------------------------------------------

    def _open_editor(self, page_index: int, rect: QRectF, text: str, *, note: bool, font_size: float, color: QColor,
                     on_commit: Callable[[str, QRectF], None], on_cancel: Callable[[], None], wrap: bool = True,
                     text_format: dict | None = None, runs=(), kind: Kind = Kind.TEXT,
                     suggest: bool = False) -> None:
        """``text_format`` (placed text, text boxes): the font, bold… the text is typed in;
        the text format bar can change it while typing (``typing_format``,
        ``set_typing_format``), for selected words only (``runs``) or the whole text."""
        view = self._window.view
        self.finish_editing()
        min_height = rect.height()
        holder: dict = {}
        self.typing_kind = kind  # (placed text, a text box or a callout: whose format the bar remembers)
        self.typing_format = None if text_format is None else {
            **text_format, "font_size": font_size, "color": (color.redF(), color.greenF(), color.blueF())}

        def geometry(content_height: float) -> QRectF:
            grown = grown_rect(rect, content_height, min_height)
            editor_ = holder.get("editor")
            if not wrap and editor_ is not None:  # placed text: one line per line typed, grows sideways
                grown.setWidth(max(rect.width(), editor_.natural_width()))
            return grown

        def resize(content_height: float) -> None:
            proxy = holder.get("proxy")
            if proxy is not None:
                proxy.setGeometry(geometry(content_height))

        def commit(value: str, content_height: float) -> None:
            final = geometry(content_height)
            if self.typing_format is not None and holder.get("editor") is not None:
                self.typing_format["runs"] = holder["editor"].runs(self._typing_base())
            self._close_editor()
            on_commit(value, final)

        def cancel() -> None:
            self._close_editor()
            on_cancel()

        editor = InlineTextEditor(text, font_size=font_size, color=color, note=note, on_commit=commit, on_cancel=cancel,
                                  on_resize=resize, wrap=wrap,
                                  enter_finishes=kind is Kind.TEXT and not note and not wrap,  # (Add Text)
                                  suggest=self._suggest_text if suggest else None,
                                  forget=self._window.text_history.forget if suggest else None)
        holder["editor"] = editor
        self.editor = editor
        if self.typing_format is not None:
            from planetpdf.ui.text_format import editor_font

            editor.set_text_format(editor_font(self.typing_format, self.session.doc, text), color)
            if runs:
                editor.set_runs(self._typing_base(), runs)
            editor.selectionChanged.connect(self.editorChanged.emit)  # (the bar shows the selected words' B I U S)
        holder["proxy"] = view.show_editor_at(page_index, rect, editor)
        editor.setFocus()
        self.editorChanged.emit()

    def _typing_base(self) -> tuple[bool, bool, bool, bool]:
        from planetpdf.core.placed_text import STYLE_KEYS

        fmt = self.typing_format or {}
        return tuple(bool(fmt.get(k)) for k in STYLE_KEYS)

    def selected_words_format(self) -> dict | None:
        """B / I / U / S of the words selected in the open editor (None: nothing selected)."""
        import shiboken6

        if self.typing_format is None or self.editor is None or not shiboken6.isValid(self.editor):
            return None
        return self.editor.selection_format()

    def set_typing_format(self, **changes) -> bool:
        """The text format bar while text is being typed: the editor shows it at once, and
        the text is placed with it. Bold / italic / underline / strike-out with words
        selected change just those words (returns True then); otherwise the whole text."""
        import shiboken6

        from planetpdf.core.placed_text import STYLE_KEYS
        from planetpdf.ui.text_format import editor_font

        if self.typing_format is None or self.editor is None or not shiboken6.isValid(self.editor):
            return False
        styles = {k: v for k, v in changes.items() if k in STYLE_KEYS}
        if styles and len(styles) == len(changes) and self.editor.format_selection(**styles):
            self.editor.setFocus()
            self.editorChanged.emit()
            return True
        self.typing_format.update(changes)
        self.editor.set_text_format(editor_font(self.typing_format, self.session.doc, self.editor.toPlainText()),
                                    QColor.fromRgbF(*self.typing_format["color"]))
        if styles:
            self.editor.format_all(**styles)
        self.editor.setFocus()
        self.editorChanged.emit()
        return False

    def _note_missing_letters(self, style: Style, text: str) -> None:
        """Typed in a font only in this document: name the letters it lacks (drawn in its
        closest font)."""
        from planetpdf.core import fonts

        face = fonts.face(style.font, style.bold, style.italic, doc=self.session.doc, text=text)
        missing = face.missing(text) if face.in_document else []
        if missing:
            self._window.statusBar().showMessage(
                f"“{face.family}” in this document has no {', '.join(missing[:12])}: "
                f"{'that letter is' if len(missing) == 1 else 'those letters are'} drawn in {face.fallback}.", 10000)

    def _typed_style(self, style: Style) -> Style:
        fmt = self.typing_format or {}
        known = set(style.text_format) | {"font_size", "color", "runs"}
        return replace(style, **{k: (tuple(v) if k in ("color", "runs") else v) for k, v in fmt.items() if k in known})

    def _close_editor(self) -> None:
        self.editor = None
        self._window.view.remove_editor()
        self.editorChanged.emit()

    def finish_editing(self) -> None:
        """Commit an open on-page editor (e.g. before switching tools or saving)."""
        import shiboken6

        if self.editor is not None and not shiboken6.isValid(self.editor):
            self.editor = None  # (already removed by the view)
        if self.editor is not None:
            self.editor.finish()

    def _new_textbox(self, page_index: int, rect: QRectF) -> None:
        style = self.style_for(Kind.TEXTBOX)
        if self.textbox_borderless:  # (Acrobat's "Add text comment": no box around the text)
            style = replace(style, width=0, fill=None)
        if rect.width() < 24:  # a click: a default-width box that grows as you type
            rect = QRectF(rect.left(), rect.top(), TEXTBOX_DEFAULT_WIDTH, style.font_size * 1.6)
        rect = QRectF(rect.left(), rect.top(), rect.width(), max(rect.height(), style.font_size * 1.6))
        page = self.session.doc[page_index]

        def commit(text: str, final: QRectF) -> None:
            if text.strip():
                typed = self._typed_style(style)
                final = _fit_text(final, text, typed.font_size, typed, self.session.doc)
                self.add(self._new(Kind.TEXTBOX, page_index, style=typed, rect=from_display(page, _tuple(final)),
                                   text=text))

        self._open_editor(page_index, rect, "", note=False, font_size=style.font_size,
                          color=QColor.fromRgbF(*style.color), on_commit=commit, on_cancel=lambda: None,
                          text_format=style.text_format, kind=Kind.TEXTBOX)

    def new_placed_text(self, page_index: int, point: QPointF) -> None:
        """Fill & Sign "Add Text": type at the click (the I-beam marks the middle of the
        first line); the result is an item you can move and resize until flattened."""
        from planetpdf.core import placed_text as PT

        style = self.style_for(Kind.TEXT)
        size = PT.clamp_size(style.font_size)
        line = size * PT.LEADING
        rect = QRectF(point.x() - PT.PADDING, point.y() - PT.PADDING - line / 2, size * 4, line + 2 * PT.PADDING)
        session = self.session

        def commit(text: str, final: QRectF) -> None:
            typed = self._typed_style(style)
            if text.strip():
                self.add(K.placed_text(session, page_index, (final.left(), final.top()), text, typed, author=self._author()))
                self._note_missing_letters(typed, text)
                self._remember_text(text)

        self._open_editor(page_index, rect, "", note=False, font_size=size, color=QColor.fromRgbF(*style.color),
                          on_commit=commit, on_cancel=lambda: None, wrap=False, text_format=style.text_format,
                          suggest=True)

    def _edit_placed_text(self, item: K.MarkupItem) -> None:
        spec = item.spec
        page = self.session.doc[item.page]
        shown = to_display(page, spec.rect)
        self._hide(item.item_id, item.page)  # the editor takes its place

        def commit(text: str, _final: QRectF) -> None:
            self._hidden = None
            typed = self._typed_style(spec.style)
            if not text.strip():
                self._push_or_show(RemoveMarkupCommand(self._window, spec.mark_id, "Text"), item.item_id, item.page)
            elif text != spec.text or typed != spec.style:
                new = K.restyled_text(self.session, spec, text=text, font_size=typed.font_size, color=typed.color,
                                      runs=typed.runs, **typed.text_format)
                if not self._push_or_show(ReplaceMarkupCommand(self._window, spec, self._signed(spec, new), "Edit Text"),
                                          item.item_id, item.page):
                    return
                self._note_missing_letters(typed, text)
                if text != spec.text:
                    self._remember_text(text)
            else:
                self._show(item.item_id, item.page)

        def cancel() -> None:
            self._show(item.item_id, item.page)

        self._open_editor(item.page, _qrect(shown), spec.text, note=False, font_size=spec.style.font_size,
                          color=QColor.fromRgbF(*spec.style.color), on_commit=commit,
                          on_cancel=cancel, wrap=False, text_format=spec.style.text_format, runs=spec.style.runs,
                          suggest=True)

    def _note_pad_rect(self, page_index: int, anchor: QPointF) -> QRectF:
        page_rect = self._window.view._items[page_index].boundingRect()
        x = min(anchor.x() + 24, page_rect.right() - NOTE_PAD.width() - 4)
        y = min(anchor.y(), page_rect.bottom() - NOTE_PAD.height() - 4)
        return QRectF(max(4, x), max(4, y), NOTE_PAD.width(), NOTE_PAD.height())

    def _new_callout(self, page_index: int, data) -> None:
        """Callout: the drag went from the thing called out (the arrow tip) to where the
        text goes; type in the box on the page."""
        from planetpdf.ui.viewer.page_view import callout_box

        tip, end = data
        style = self.style_for(Kind.CALLOUT)
        page = self.session.doc[page_index]
        box = callout_box(tip, end)

        def commit(text: str, final: QRectF) -> None:
            if not text.strip():
                return
            typed = self._typed_style(style)
            final = _fit_text(final, text, typed.font_size, typed, self.session.doc)
            b = from_display(page, _tuple(final))
            t = pymupdf.Point(tip.x(), tip.y()) * page.derotation_matrix
            points = ((b[0], b[1]), (b[2], b[3]), (t.x, t.y))
            self.add(self._new(Kind.CALLOUT, page_index, style=typed, rect=K.callout_spec_rect(b, (t.x, t.y)),
                               points=points, text=text))

        self._open_editor(page_index, box, "", note=False, font_size=style.font_size,
                          color=QColor.fromRgbF(*style.color), on_commit=commit, on_cancel=lambda: None,
                          text_format=style.text_format, kind=Kind.CALLOUT)

    def _new_caret(self, page_index: int, point: QPointF) -> None:
        """Insert Text at Cursor: a caret at the gap between the characters clicked, and a
        note pad for the words to insert (Acrobat)."""
        page = self.session.doc[page_index]
        q = pymupdf.Point(point.x(), point.y()) * page.derotation_matrix
        x, baseline = q.x, q.y + 4
        best = None
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for ch in span["chars"]:
                        r = pymupdf.Rect(ch["bbox"])
                        if r.y0 - 2 <= q.y <= r.y1 + 2 and r.x0 - 3 <= q.x <= r.x1 + 3:
                            gap = r.x0 if q.x < (r.x0 + r.x1) / 2 else r.x1
                            distance = abs(gap - q.x)
                            if best is None or distance < best[0]:
                                best = (distance, gap, ch["origin"][1])
        if best is not None:
            x, baseline = best[1], best[2]
        rect = (x - 10, baseline - 2, x + 10, baseline + 12)  # the caret mark, its point at the gap

        def commit(text: str, _final: QRectF) -> None:
            if text.strip():
                self.add(self._new(Kind.CARET, page_index, rect=rect, text=text))

        self._open_editor(page_index, self._note_pad_rect(page_index, point), "", note=True, font_size=11,
                          color=QColor("black"), on_commit=commit, on_cancel=lambda: None)

    def _new_note(self, page_index: int, point: QPointF) -> None:
        """A sticky note where clicked: its icon, and its box beside it to type in (it stays
        open, joined to the icon by a line; user 2026-10-04)."""
        page = self.session.doc[page_index]
        x, y = (pymupdf.Point(point.x(), point.y()) * page.derotation_matrix)
        rect = (x, y, x + 20, y + 20)
        popup = K.default_popup(page, rect)

        def commit(text: str, _final: QRectF) -> None:
            if text.strip():
                self.add(self._new(Kind.NOTE, page_index, rect=rect, text=text,
                                   popup=self._fit_popup(page, popup, text, shrink=True), popup_open=True))

        self._open_editor(page_index, _qrect(to_display(page, popup)), "", note=True, font_size=NOTE_TEXT_SIZE,
                          color=QColor("black"), on_commit=commit, on_cancel=lambda: None)

    def _new_highlight_note(self, page_index: int, page: pymupdf.Page, words) -> None:
        """The Note tool on text (user 2026-10-04: "the cursor should be like a highlighter"):
        the words are highlighted and the note's box opens beside them, joined by a line."""
        spec = self._spec_from_shape(Kind.HIGHLIGHT, page, page_index, words)
        if spec is None:
            return
        popup = K.default_popup(page, spec.rect)

        def commit(text: str, _final: QRectF) -> None:
            if text.strip():
                self.add(replace(spec, text=text, popup=self._fit_popup(page, popup, text, shrink=True), popup_open=True))

        self._open_editor(page_index, _qrect(to_display(page, popup)), "", note=True, font_size=NOTE_TEXT_SIZE,
                          color=QColor("black"), on_commit=commit, on_cancel=lambda: None)

    # --- notes in pop-up boxes (user 2026-10-04) ----------------------------------------------

    def _fit_popup(self, page: pymupdf.Page, popup, text: str, *, shrink: bool = False):
        """A note's box as tall as its text needs (user 2026-10-04: it auto-resizes with its
        text): `shrink` for a new note (its exact height), else only taller; on the page."""
        from planetpdf.ui.viewer.note_popups import fitted_height

        shown = _qrect(to_display(page, popup))
        need = fitted_height(shown.width(), text)
        height = need if shrink else max(shown.height(), need)
        top = min(shown.top(), max(0.0, page.rect.height - height - 4))
        return from_display(page, (shown.left(), top, shown.right(), top + height))

    def move_popup(self, item_id: str, rect: QRectF) -> None:
        """A note's box dragged by its header (or resized by its corner): one undo step."""
        item = self._item(item_id)
        if item is None:
            return
        page = self.session.doc[item.page]
        new = from_display(page, _tuple(rect))
        old = item.popup
        resized = old is not None and (abs(pymupdf.Rect(old).width - pymupdf.Rect(new).width) > 0.5
                                       or abs(pymupdf.Rect(old).height - pymupdf.Rect(new).height) > 0.5)
        self._window.push_command(PopupCommand(self._window, item_id, old, new, "Resize Note" if resized else "Move Note"))

    def open_popup(self, item_id: str, open_: bool) -> None:
        """Click on a note's item: its box opens; "–": it collapses. Not an edit (no undo
        step), but saved with the file, as Acrobat does."""
        item = self._item(item_id)
        if open_ and item is not None and self._window.settings.comments_in_panel:
            self.show_in_panel(item_id)  # (comments in the panel: its card, not a box)
            return
        if item is None or not item.has_note or (item.popup is not None and item.popup_open == open_):
            return
        try:
            K.set_popup(self.session, item_id, open_=open_, modified=False)
        except PdfError:
            return
        # (only the boxes: this can happen during the click that starts dragging the item,
        # whose selection frame must stay)
        self.items = K.list_markup(self.session)
        self._show_note_popups()

    def _show_note_popups(self) -> None:
        """Every open note's box on its page, joined to its item; outside Comment mode a
        click on a note's item opens it (in Comment mode, selecting it does)."""
        from planetpdf.ui.viewer.note_popups import NotePopup

        popups: dict[int, list] = {}
        anchors: dict[int, list] = {}
        doc = self.session.doc
        for item in self.items:
            if not item.has_note or item.item_id in (self._hidden, self._editing_note):
                continue
            page = doc[item.page]
            anchor = _qrect(to_display(page, item.spec.rect if item.spec else item.rect))
            if not self.markup_mode:
                anchors.setdefault(item.page, []).append((item.item_id, anchor))
            if item.popup is None or not item.popup_open or self._window.settings.comments_in_panel:
                continue  # (collapsed, or comments shown in the panel instead)
            color = item.spec.style.color if item.spec else (1.0, 0.85, 0.2)
            replies = tuple((r.item_id, "  ".join(p for p in (r.author or "Reply", _pdf_date(r.modified)) if p), r.text)
                            for r in item.replies if r.item_id != self._editing_note)
            popups.setdefault(item.page, []).append(NotePopup(
                item.item_id, _qrect(to_display(page, item.popup)), anchor, self._popup_title(item), item.text,
                QColor.fromRgbF(*color), replies))
        self._window.view.set_note_popups(popups, anchors)

    # --- replies (Acrobat's comment threads: user 2026-10-04) ------------------------------

    def reply_parent(self, reply_id: str) -> K.MarkupItem | None:
        return next((i for i in self.items if any(r.item_id == reply_id for r in i.replies)), None)

    def reply_to(self, item_id: str) -> None:
        """"Reply" in a note's box: type the reply just under the box; it joins the thread."""
        item = self._item(item_id)
        if item is None or self.session is None:
            return
        if item.popup is None or not item.popup_open:
            self.open_popup(item_id, True)
        box = self._window.view.popup_rect(item_id)
        if box is None:
            return
        page = self.session.doc[item.page]
        bounds = QRectF(page.rect.x0, page.rect.y0, page.rect.width, page.rect.height)
        rect = QRectF(box.left(), box.bottom() + 3, box.width(), 44)
        if rect.bottom() > bounds.bottom():  # (no room under it: over its lower part)
            rect.moveBottom(box.bottom() - 2)

        def commit(text: str, _final: QRectF) -> None:
            if text.strip():
                self._window.push_command(ReplyCommand(self._window, "Reply", parent_id=item_id, new_text=text,
                                                       author=self._author()))

        self._open_editor(item.page, rect, "", note=True, font_size=NOTE_TEXT_SIZE, color=QColor("black"),
                          on_commit=commit, on_cancel=lambda: None)
        self.editor.setPlaceholderText("Type your reply…")

    def _edit_reply(self, reply_id: str) -> None:
        """A click on a reply's words: edit them in place (emptied: the reply is deleted)."""
        parent = self.reply_parent(reply_id)
        popup = self._window.view._popup_items.get(parent.item_id) if parent else None
        if parent is None or popup is None:
            return
        reply = next(r for r in parent.replies if r.item_id == reply_id)
        block = next((r for i, kind, r in popup.blocks() if i == reply_id and kind == "text"), None)
        rect = (block or popup.body_rect()).adjusted(-2, -2, 2, 22)

        def commit(text: str, _final: QRectF) -> None:
            self._editing_note = None
            if not text.strip():
                self._window.push_command(ReplyCommand(self._window, "Delete Reply", reply_id=reply_id))
            elif text != reply.text:
                self._window.push_command(ReplyCommand(self._window, "Edit Reply", reply_id=reply_id, new_text=text))
            else:
                self._show_note_popups()

        def cancel() -> None:
            self._editing_note = None
            self._show_note_popups()

        self._open_editor(parent.page, rect, reply.text, note=True, font_size=NOTE_TEXT_SIZE, color=QColor("black"),
                          on_commit=commit, on_cancel=cancel)
        self._editing_note = reply_id  # (its words step aside while typing)
        self._show_note_popups()

    def _popup_title(self, item: K.MarkupItem) -> str:
        when = _pdf_date(item.modified)
        return "  ".join(part for part in (item.author or item.type_name, when) if part)

    def edit_text(self, item_id: str) -> None:
        """Edit an item's text in place: a text box directly on the page; anything else
        (notes, comments on shapes, other software's notes) in a note pad beside it."""
        if self.reply_parent(item_id) is not None:
            self._edit_reply(item_id)
            return
        item = self._item(item_id)
        if item is not None and item.kind is Kind.TEXT and item.spec is not None:
            self._edit_placed_text(item)
            return
        if item is not None and item.kind is Kind.ATTACH and item.spec is not None:
            self._window.save_attachment(item.spec.text, item.spec.source or b"")  # (double-click: save it)
            return
        if item is None or (item.kind and item.kind.uses_appearance):
            return
        page = self.session.doc[item.page]
        shown = to_display(page, item.spec.rect if item.spec else item.rect)
        if item.kind in (Kind.TEXTBOX, Kind.CALLOUT) and item.spec is not None:
            spec = item.spec
            self._hide(item_id, item.page)  # the editor takes its place
            box = shown if item.kind is Kind.TEXTBOX else to_display(page, tuple(pymupdf.Rect(spec.points[0],
                                                                                              spec.points[1])))

            def commit(text: str, final: QRectF) -> None:
                self._hidden = None
                if not text.strip():
                    self._push_or_show(RemoveMarkupCommand(self._window, spec.mark_id, item.type_name), item_id, item.page)
                    return
                typed = self._typed_style(spec.style)
                new = self._refit_box(spec, typed, text, final)
                if new == spec:
                    self._show(item_id, item.page)
                    return
                self._push_or_show(ReplaceMarkupCommand(self._window, spec, self._signed(spec, new), "Edit Text"),
                                   item_id, item.page)

            self._open_editor(item.page, _qrect(box), spec.text, note=False, font_size=spec.style.font_size,
                              color=QColor.fromRgbF(*spec.style.color), on_commit=commit,
                              on_cancel=lambda: self._show(item_id, item.page), text_format=spec.style.text_format,
                              runs=spec.style.runs, kind=item.kind)
            return

        def commit_note(text: str, _final: QRectF) -> None:
            self._editing_note = None
            if text != item.text:
                self.set_text(item_id, text)
            else:
                self._show_note_popups()

        def cancel_note() -> None:
            self._editing_note = None
            self._show_note_popups()

        # typed in its box (where it is, or where it will go), which steps aside meanwhile
        box = item.popup if item.popup is not None else K.default_popup(page, item.spec.rect if item.spec else item.rect)
        self._open_editor(item.page, _qrect(to_display(page, box)), item.text, note=True, font_size=NOTE_TEXT_SIZE,
                          color=QColor("black"), on_commit=commit_note, on_cancel=cancel_note)
        self._editing_note = item_id
        self._show_note_popups()

    # --- lifting an item while it's dragged ------------------------------------------------------

    def _hide(self, item_id: str, page: int) -> None:
        K.set_hidden(self.session, item_id, True)
        self._hidden = item_id
        self._window.view.refresh_page(page)

    def _show(self, item_id: str, page: int) -> None:
        K.set_hidden(self.session, item_id, False)
        self._hidden = None
        self._window.view.refresh_page(page)

    def _push_or_show(self, command, item_id: str, page: int) -> bool:
        """Pushes the change to an item that was hidden while it was retyped. Canceled (a signed
        document's question): the item is shown again as it was (security review 2026-11-27,
        finding 12: it stayed hidden until the file was reopened)."""
        if self._window.push_command(command):
            return True
        self._show(item_id, page)
        self.refresh()
        return False

    def _lift(self, item_id: str) -> None:
        """A drag started: show the item itself under the mouse, and take it off the page."""
        item = self._item(item_id)
        if item is None:
            return
        image = K.render_item(self.session, item_id, GHOST_ZOOM)
        if image is not None:
            qimage = QImage(image.samples, image.width, image.height, image.stride, QImage.Format.Format_RGBA8888)
            self._window.view.set_drag_ghost(QPixmap.fromImage(qimage.copy()))
        self._hide(item_id, item.page)

    def _drop_back(self, item_id: str) -> None:
        item = self._item(item_id)
        if item is not None and self._hidden == item_id:
            self._show(item_id, item.page)

    # --- commands -------------------------------------------------------------------------------

    def add(self, spec: MarkupSpec) -> None:
        self._window.push_command(AddMarkupCommand(self._window, spec))

    def _item(self, item_id: str) -> K.MarkupItem | None:
        return next((i for i in self.items if i.item_id == item_id), None)

    def delete(self, item_id: str | None = None) -> bool:
        item_id = item_id or self._window.view.selected_markup
        if item_id and self.reply_parent(item_id) is not None:
            self._window.push_command(ReplyCommand(self._window, "Delete Reply", reply_id=item_id))
            return True
        item = self._item(item_id) if item_id else None
        if item is None:
            return False
        self._window.push_command(RemoveMarkupCommand(self._window, item.item_id, item.type_name))
        return True

    def set_text(self, item_id: str, text: str) -> None:
        item = self._item(item_id)
        if item is None:
            return
        if item.spec is None:
            self._window.push_command(EditForeignTextCommand(self._window, item_id, item.page, text))
        else:
            new = replace(item.spec, text=text)
            page = self.session.doc[item.page]
            if new.kind in K.POPUP_KINDS and text.strip() and item.popup is None:  # (a note now: its box, open)
                new = replace(new, popup=self._fit_popup(page, K.default_popup(page, item.spec.rect), text, shrink=True),
                              popup_open=True)
            elif item.popup is not None:  # (as it is on the page, taller if the text needs it)
                new = replace(new, popup=self._fit_popup(page, item.popup, text), popup_open=item.popup_open)
            self._window.push_command(ReplaceMarkupCommand(self._window, item.spec, self._signed(item.spec, new),
                                                           "Edit Text"))

    def restyle_selected(self, **changes) -> bool:
        """The Comment bar's color / line thickness / opacity on the selected comment (one
        undo step); placed items with their own appearance aren't restyled this way."""
        item = self._item(self._window.view.selected_markup or "")
        if item is None or item.spec is None or item.spec.kind.uses_appearance or item.spec.kind is Kind.ATTACH:
            return False
        style = replace(item.spec.style, **changes)
        if style == item.spec.style:
            return False
        self._window.push_command(ReplaceMarkupCommand(self._window, item.spec, replace(item.spec, style=style),
                                                       "Change Style"))
        return True

    def restyle(self, item_id: str, style: Style) -> None:
        item = self._item(item_id)
        if item is None or item.spec is None or item.spec.kind.uses_appearance:
            return
        self._window.push_command(ReplaceMarkupCommand(self._window, item.spec, replace(item.spec, style=style), "Change Style"))

    def flatten(self, item_id: str | None = None) -> None:
        item = self._item(item_id or self._window.view.selected_markup or "")
        if item is None or item.spec is None or not item.spec.kind.uses_appearance:
            return
        self._window.push_command(FlattenMarkupCommand(self._window, item.item_id, item.type_name))

    def _new_symbol(self, page_index: int, draw: str, data) -> None:
        """Fill & Sign: a symbol at a click, the size of the last one placed or resized; a circle
        can also be dragged from corner to corner (any oval), and that becomes its size."""
        s = self._window.settings
        name = self.symbol_name
        color = self.style_for(Kind.TEXT).color  # Fill & Sign: one color for text and symbols
        center = data.center()
        box = None
        if draw == "ellipse" and data.width() >= MIN_SYMBOL and data.height() >= MIN_SYMBOL:
            box = (data.left(), data.top(), data.right(), data.bottom())
        size = tuple(s.symbol_sizes.get(name, ())) or None
        spec = K.symbol(self.session, page_index, (center.x(), center.y()), name, color, author=self._author(),
                        size=size, box=box, line=s.circle_line, filled=s.circle_filled)
        self.add(spec)
        if box is not None:
            self.remember_symbol_size(spec)

    def remember_symbol_size(self, spec: K.MarkupSpec) -> None:
        """The next symbol of this kind gets this one's size (user 2026-10-26: "most checkboxes
        on a form will be the same size")."""
        w, h = K.symbol_size(self.session, spec)
        self._window.settings.symbol_sizes = {**self._window.settings.symbol_sizes,
                                              spec.text: [round(w, 2), round(h, 2)]}

    def set_circle_style(self, *, line: float | None = None, filled: bool | None = None) -> None:
        """The circle ▾ menu: outline or solid, and the line width, for the next circles and the
        selected one."""
        s = self._window.settings
        if line is not None:
            s.circle_line = line
        if filled is not None:
            s.circle_filled = filled
        self.update_draw_style()
        item = self._item(self._window.view.selected_markup or "")
        if item is not None and item.spec is not None and item.spec.kind is Kind.SYMBOL and item.spec.text == "circle":
            new = K.restyled_circle(self.session, item.spec, line=line, filled=filled)
            if new != item.spec:
                self._window.push_command(ReplaceMarkupCommand(self._window, item.spec, new, "Circle Style"))

    def _on_moved(self, item_id: str, rect: QRectF, verb: str = "Move") -> None:
        item = self._item(item_id)
        if item is None or item.spec is None or not item.spec.kind.movable:
            self._drop_back(item_id)
            self.refresh()  # snap the overlay back
            return
        if not self._redrawable(item):
            return
        page = self.session.doc[item.page]
        target = from_display(page, _tuple(rect))
        if item.spec.kind is Kind.TEXT:
            new = K.fitted_text(self.session, item.spec, target)  # resizing scales the text
        else:
            new = K.transformed(item.spec, target)
        if new.kind is Kind.SYMBOL and _resized(item.spec, new):
            new = K.redrawn_symbol(self.session, new)  # (a circle's line keeps its width)
            self.remember_symbol_size(new)  # the next one is this size
        self._replace(item, new, f"{verb} {item.type_name}")

    def _redrawable(self, item: K.MarkupItem) -> bool:
        """An item drawn from a picture PlanetPDF keeps with it (a signature, a stamp) can only be
        moved or resized while that picture is there; a file saved by another program may have
        kept just its look (security review 2026-11-25: moving one deleted it)."""
        if not item.spec.kind.uses_appearance or item.spec.source:
            return True
        self._drop_back(item.item_id)
        self.refresh()  # (the overlay goes back)
        self._window.statusBar().showMessage(
            f"This {item.type_name.lower()} can't be moved or resized: the file keeps only how it looks, not what "
            "PlanetPDF needs to draw it again. You can still delete it.", 10000)
        return False

    def _replace(self, item: K.MarkupItem, new: MarkupSpec, text: str) -> bool:
        """The move / resize as one undo step. A signed document's question canceled: the item
        stays where it was, shown again (it was hidden while dragged; security review 2026-11-25)."""
        self._hidden = None  # the command replaces the hidden item with the moved one
        if self._window.push_command(ReplaceMarkupCommand(self._window, item.spec, new, text)):
            return True
        K.set_hidden(self.session, item.item_id, False)
        self._window.view.refresh_page(item.page)
        self.refresh()
        return False

    def move_to_page(self, item_id: str, page_index: int, rect: QRectF) -> bool:
        """Dropped on another page (Acrobat can't do this): the item moves there, at
        ``rect`` (displayed coordinates of that page), as one undo step."""
        item = self._item(item_id)
        if item is None or item.spec is None or not item.spec.kind.movable:
            self._drop_back(item_id)
            self.refresh()
            return False
        if not self._redrawable(item):
            return False
        spec = item.spec
        page = self.session.doc[page_index]
        target = from_display(page, _tuple(rect))
        if spec.kind is Kind.TEXT:
            new = K.restyled_text(self.session, replace(spec, page=page_index), top_left=(rect.left(), rect.top()))
        else:
            new = K.transformed(replace(spec, page=page_index), target)
        return self._replace(item, new, f"Move {item.type_name} to Page {page_index + 1}")

    def nudge(self, item_id: str, shown: QRectF, dx: float, dy: float) -> bool:
        """Arrow keys: move the selected item a little (``shown`` = its displayed rect)."""
        item = self._item(item_id)
        if item is None or item.spec is None or not item.spec.kind.movable:
            return False
        moved = shown.translated(dx, dy)
        self._on_moved(item_id, moved, verb="Nudge")
        from planetpdf.ui import accessibility

        accessibility.announce(self._window.view, f"{moved.left() / 72:.1f} inches from the left, "
                                                  f"{moved.top() / 72:.1f} from the top")
        return True

    def change_text_size(self, item_id: str | None, step: int) -> bool:
        """A− / A+ (Ctrl+[ / Ctrl+]) for placed text and text boxes."""
        from planetpdf.core import placed_text as PT

        item = self._item(item_id or self._window.view.selected_markup or "")
        if item is not None and item.spec is not None and item.spec.kind is Kind.SYMBOL:
            return self._resize_symbol(item.spec, step)
        if item is None or item.spec is None or item.spec.kind not in TEXT_KINDS:
            return False
        spec = item.spec
        size = PT.size_steps(spec.style.font_size, step)
        if size == spec.style.font_size:
            return False
        if spec.kind is Kind.TEXT:
            new = K.restyled_text(self.session, spec, font_size=size)
        else:
            new = self._refit_box(spec, replace(spec.style, font_size=size))
        self._window.push_command(ReplaceMarkupCommand(self._window, spec, new, "Text Size"))
        return True

    def _refit_box(self, spec: MarkupSpec, style: Style, text: str | None = None,
                   shown: QRectF | None = None) -> MarkupSpec:
        """A text box / callout with new words and/or format, its box as tall as the text
        needs (``shown``: the box as displayed after typing; else its current one)."""
        text = spec.text if text is None else text
        page = self.session.doc[spec.page]
        doc = self.session.doc
        if spec.kind is Kind.CALLOUT:
            box = tuple(pymupdf.Rect(spec.points[0], spec.points[1]))
            current = _qrect(to_display(page, box)) if shown is None else shown
            fitted = QRectF(current.left(), current.top(), current.width(),
                            K.textbox_height(text, current.width(), style.font_size, style, doc))
            b = from_display(page, _tuple(fitted))
            tip = spec.points[2]
            return replace(spec, text=text, style=style, points=((b[0], b[1]), (b[2], b[3]), tip),
                           rect=K.callout_spec_rect(b, tip))
        current = _qrect(to_display(page, spec.rect)) if shown is None else shown
        if shown is not None:  # (typed: grows to fit, as before)
            fitted = _fit_text(current, text, style.font_size, style, doc)
        else:
            fitted = QRectF(current.left(), current.top(), current.width(),
                            K.textbox_height(text, current.width(), style.font_size, style, doc))
        new_rect = from_display(page, _tuple(fitted))
        if text == spec.text and style == spec.style and pymupdf.Rect(new_rect) == pymupdf.Rect(spec.rect):
            return spec
        return replace(spec, text=text, style=style, rect=new_rect)

    SYMBOL_STEP = 1.25  # − / + grow or shrink a symbol by this much, around its center
    SYMBOL_MIN, SYMBOL_MAX = 3.0, 144.0  # points (the shorter side)

    def _resize_symbol(self, spec: K.MarkupSpec, step: int) -> bool:
        rect = pymupdf.Rect(spec.rect)
        factor = self.SYMBOL_STEP if step > 0 else 1 / self.SYMBOL_STEP
        short = min(rect.width, rect.height)
        factor = max(self.SYMBOL_MIN / short, min(self.SYMBOL_MAX / short, factor))
        if abs(factor - 1) < 1e-3:
            return False
        center = (rect.tl + rect.br) / 2
        w, h = rect.width * factor / 2, rect.height * factor / 2
        new = K.redrawn_symbol(self.session, K.transformed(spec, (center.x - w, center.y - h, center.x + w, center.y + h)))
        self.remember_symbol_size(new)  # the next one is this size
        self._window.push_command(ReplaceMarkupCommand(self._window, spec, new, "Symbol Size"))
        return True

    def change_text_color(self, item_id: str | None, color: QColor) -> bool:
        """Recolor placed text / a text box; the color also becomes the default for new ones."""
        item = self._item(item_id or "")
        if item is None or item.spec is None or item.spec.kind not in (Kind.TEXT, Kind.TEXTBOX, Kind.SYMBOL):
            return False
        self.set_color(Kind.TEXT if item.spec.kind is Kind.SYMBOL else item.spec.kind, color)  # the next one matches
        self.textColorChanged.emit()
        spec = item.spec
        rgb = (color.redF(), color.greenF(), color.blueF())
        if spec.kind is Kind.TEXT:
            new = K.restyled_text(self.session, spec, color=rgb)
        elif spec.kind is Kind.SYMBOL:
            new = K.recolored_symbol(spec, rgb)
        else:
            new = replace(spec, style=replace(spec.style, color=rgb))
        if new != spec:
            self._window.push_command(ReplaceMarkupCommand(self._window, spec, new, "Text Color"))
        return True

    def restyle_texts(self, item_ids: list[str], **changes) -> bool:
        """The text format bar on selected placed text: new font, size, bold, italic,
        underline, strike-out and/or color for each one (one undo step), and the same for
        the next text you add (Acrobat keeps the last format)."""
        from planetpdf.core import fonts

        specs = []
        for item_id in item_ids:
            item = self._item(item_id)
            if item is not None and item.spec is not None and item.spec.kind in TEXT_KINDS:
                specs.append(item.spec)
        if not specs:
            return False

        def changed(spec: MarkupSpec) -> MarkupSpec:
            if spec.kind is Kind.TEXT:
                return K.restyled_text(self.session, spec, **changes)
            style = spec.style.with_format(spec.text, **{k: (tuple(v) if k == "color" else v)
                                                         for k, v in changes.items()})
            if style.plain_text != spec.style.plain_text or style.font != spec.style.font:
                fonts.face(style.font, style.bold, style.italic, doc=self.session.doc, text=spec.text)  # (license)
            return self._refit_box(spec, style)

        try:
            news = [changed(spec) for spec in specs]
        except fonts.FontError as exc:
            QMessageBox.warning(self._window, "Font", str(exc))
            return False
        pairs = [(old, new) for old, new in zip(specs, news) if new != old]
        self.remember_text_format(kind=specs[0].kind, **changes)
        if not pairs:
            return False
        label = "Format Text" if len(pairs) == 1 else f"Format {len(pairs)} Texts"
        stack = self._window.undo_stack
        if len(pairs) > 1:
            stack.beginMacro(label)
        try:
            for old, new in pairs:
                self._window.push_command(ReplaceMarkupCommand(self._window, old, new, label))
        finally:
            if len(pairs) > 1:
                stack.endMacro()
        return True

    def remember_text_format(self, kind: Kind = Kind.TEXT, **changes) -> None:
        """The next placed text (or text box / callout: ``kind``) gets this format (font,
        size, color…)."""
        s = self._window.settings
        box = kind in (Kind.TEXTBOX, Kind.CALLOUT)
        fmt = dict((s.textbox_format if box else s.text_format) or {})
        for key in ("font", "bold", "italic", "underline", "strikeout"):
            if key in changes:
                fmt[key] = changes[key]
        if box:
            s.textbox_format = fmt
        else:
            s.text_format = fmt
        if "font_size" in changes:
            s.markup_font_size = float(changes["font_size"])
        if "color" in changes:
            for k in ((Kind.TEXTBOX, Kind.CALLOUT) if box else (Kind.TEXT,)):
                self.set_color(k, QColor.fromRgbF(*changes["color"]))
            self.textColorChanged.emit()

    def pick_text_color(self, item_id: str | None = None) -> None:
        """Text Color…: recolor the selected text, or set the color for new placed text."""
        item = self._item(item_id or "")
        if item is not None and item.spec is not None and item.spec.kind in (Kind.TEXT, Kind.TEXTBOX, Kind.SYMBOL):
            current = QColor.fromRgbF(*item.spec.style.color)
        else:
            item = None
            current = QColor.fromRgbF(*self.style_for(Kind.TEXT).color)
        color = self._window.pick_color(current, "Text Color")
        if color is None:
            return
        if item is not None:
            self.change_text_color(item.item_id, color)
        else:
            self.set_color(Kind.TEXT, color)
            self.textColorChanged.emit()

    def flatten_page(self, pages: list[int]) -> None:
        """Fill & Sign: make placed text, signatures and stamps part of the page(s)."""
        from planetpdf.ui.commands import FlattenPagesCommand

        items = [i for i in K.list_markup(self.session, pages) if i.spec and i.spec.kind.uses_appearance]
        if not items:
            self._window.statusBar().showMessage("Nothing to flatten: no placed text, signatures, or stamps there.", 5000)
            return
        self._window.push_command(FlattenPagesCommand(self._window, pages))

    GRAB_TOOLS = (Kind.TEXT, Kind.SYMBOL)  # (Fill & Sign's tools that stay on)

    def _grabbed_with_tool(self, item_id: str) -> bool:
        """With Add Text or a symbol tool on, a press on anything added (text, signatures,
        initials, symbols, pictures, stamps) selects it: then it moves, stretches, gets bigger
        or smaller (A− / A+) and a double-click retypes it, as in Edit PDF (user 2026-11-24).
        A press anywhere else still adds text or a symbol. Only items this user's PlanetPDF made
        (``made_here``): a file's own "PlanetPDF" item could be page-sized and take every click
        (security review 2026-11-25)."""
        if self.suspended or self._one_shot_box is not None or self.tool not in self.GRAB_TOOLS:
            return False
        item = self._item(item_id)
        return (item is not None and item.kind is not None and item.made_here
                and (item.kind.placed or item.kind in (Kind.STAMP, Kind.SEAL)))

    def select(self, item_id: str) -> None:
        self._window.view.set_selected_markup(item_id or None)
        self._window.comments_panel.select_item(item_id or None)
        self.show_item_bar(item_id or None)
        item = self._item(item_id) if item_id else None
        if item is not None and self._window.settings.comments_in_panel:
            self.show_in_panel(item_id)  # (comments in the panel: its card)
        elif item is not None and item.has_note and not (item.popup is not None and item.popup_open):
            self.open_popup(item_id, True)  # (Acrobat: clicking a comment opens its note)

    # --- comments in the panel or on the page (user 2026-10-04, Acrobat screenshots) --------

    def set_comments_in_panel(self, on: bool) -> None:
        """Comments in the Comments panel on the right, or in boxes on the page."""
        self._window.settings.comments_in_panel = on
        self.refresh()
        if on:
            self._window.show_tool_panel(self._window._comments_dock)
        self._window.statusBar().showMessage(
            "Comments show in the Comments panel: click one there to go to it on the page." if on else
            "Comments show in boxes on the page.", 6000)

    def show_in_panel(self, item_id: str) -> None:
        """A comment clicked on the page: the Comments panel flies out on its card."""
        self._window.show_tool_panel(self._window._comments_dock)
        self._window.comments_panel.select_item(item_id)

    def jump_to(self, item_id: str) -> None:
        """A comment clicked in the panel: the document scrolls to what it's about, centers
        it and marks it for a moment (on the page, its box opens too)."""
        item = self._item(item_id) or self.reply_parent(item_id)
        if item is None or self.session is None:
            return
        page = self.session.doc[item.page]
        shown = _qrect(to_display(page, item.spec.rect if item.spec else item.rect))
        self._window.view.show_area(item.page, shown)
        if self._window.view.markup_rect(item.item_id) is not None:
            self._window.view.set_selected_markup(item.item_id)
        if not self._window.settings.comments_in_panel and item.has_note:
            self.open_popup(item.item_id, True)

    def post_reply(self, item_id: str, text: str) -> None:
        """The panel's Post: a reply to the comment."""
        if text.strip() and self._item(item_id) is not None:
            self._window.push_command(ReplyCommand(self._window, "Reply", parent_id=item_id, new_text=text.strip(),
                                                   author=self._author()))

    def show_item_bar(self, overlay_id: str | None, item_id: str | None = None) -> None:
        """The A− A+ ✕ bar over a selected placed item (``overlay_id`` is the view's id
        for it; Edit PDF mode prefixes ours with "mk:")."""
        from planetpdf.ui.item_bar import ItemBar

        view = self._window.view
        item = self._item(item_id or overlay_id or "")
        if overlay_id is None or item is None or item.spec is None or not (item.spec.kind.placed or item.spec.kind is Kind.TEXTBOX):
            view.set_item_bar(None)
            return
        sized = item.spec.kind in (Kind.TEXT, Kind.TEXTBOX, Kind.SYMBOL)
        bar = ItemBar(
            symbol=item.spec.kind is Kind.SYMBOL,
            on_smaller=(lambda: self.change_text_size(item.item_id, -1)) if sized else None,
            on_larger=(lambda: self.change_text_size(item.item_id, 1)) if sized else None,
            on_delete=lambda: self.delete(item.item_id),
            on_color=(lambda: self.pick_text_color(item.item_id)) if sized or item.spec.kind is Kind.SYMBOL else None,
            color=QColor.fromRgbF(*item.spec.style.color),
        )
        view.set_item_bar(overlay_id, bar)

    def _context_menu(self, item_id: str, pos) -> None:
        item = self._item(item_id)
        if item is None:
            return
        menu = QMenu(self._window)
        if item.kind is Kind.TEXT or not (item.kind and item.kind.uses_appearance):
            menu.addAction("Edit Text…", lambda: self.edit_text(item_id))
        if item.kind in (Kind.TEXT, Kind.TEXTBOX):
            menu.addAction("Larger Text", lambda: self.change_text_size(item_id, 1))
            menu.addAction("Smaller Text", lambda: self.change_text_size(item_id, -1))
        if item.kind and item.kind.uses_appearance:
            menu.addAction("Flatten (can't be moved afterwards)", lambda: self.flatten(item_id))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete(item_id))
        menu.exec(pos)

    # --- refresh ---------------------------------------------------------------------------------

    def changed(self, pages: set[int], select: str | None = None) -> None:
        """Called by markup commands (do/undo)."""
        for page in pages:
            if self.session is not None and 0 <= page < self.session.page_count:
                self._window.view.refresh_page(page)
                self._window.thumbnails.refresh_page(page)
        self.refresh(select=select)

    def refresh(self, select: str | None = None) -> None:
        self._words = {}  # text can change (OCR, redaction, page edits)
        session = self.session
        if session is None:
            self.items = []
            self._window.view.set_markup_overlays({})
            self._window.view.set_note_popups({})
            self._window.comments_panel.populate([])
            return
        self.items = K.list_markup(session)
        if self.suspended:  # Edit / Prepare Form: their overlays are showing
            self._window.view.set_note_popups({})
            self._window.comments_panel.populate(self.items, None)
            self._window.edit.refresh()
            return
        self._show_note_popups()
        overlays: dict[int, list[tuple[str, QRectF, bool, bool, bool, bool]]] = {}
        for item in self.items:
            if not self.selectable(item):
                continue
            page = session.doc[item.page]
            shown = to_display(page, item.spec.rect if item.spec else item.rect)
            movable = item.spec is not None and item.spec.kind.movable
            keep = item.spec is not None and item.spec.kind.keeps_aspect
            stretch = item.spec is not None and (item.spec.kind.stretchable  # (Shift + a corner)
                                                 or (item.spec.kind is Kind.SYMBOL and item.spec.text == "circle"))
            overlays.setdefault(item.page, []).append((item.item_id, _qrect(shown), movable, keep, True, stretch))
        self._window.view.set_markup_overlays(overlays)
        if select is not None:
            self._window.view.set_selected_markup(select)
        self._window.comments_panel.populate(self.items, self._window.view.selected_markup)
        self.show_item_bar(self._window.view.selected_markup)

    def selectable(self, item: K.MarkupItem) -> bool:
        """Comment mode: everything. Otherwise placed text, signatures and initials, plus stamps
        and seals while the Stamp tool is open (user 2026-10-10: move and stretch a stamp just
        placed without leaving the tool)."""
        if self.markup_mode or (item.kind and item.kind.placed):
            return True
        panes = getattr(self._window, "tool_panes", None)  # (made after this controller)
        return item.kind in (Kind.STAMP, Kind.SEAL) and panes is not None and panes.current == "stamp"

    def place_symbol(self, name: str) -> None:
        """Fill & Sign: click to put a cross / check / circle / line / dot on the page."""
        self.symbol_name = name
        self.set_tool(Kind.SYMBOL)
        self._window.statusBar().showMessage(
            "Drag from one corner to the other for the circle (Shift: a perfect circle), or click for one "
            "the size of the last. Esc cancels." if name == "circle" else
            "Click where the symbol should go. Esc cancels.", 8000)
