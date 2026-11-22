"""Markup and comments: shapes, lines, ink, text markup, notes, text boxes, and
appearance items (stamps, seals, signatures, initials).

Every item we create is a standard PDF annotation (visible in other viewers) with
``/NM ppdf-mk-<id>`` and its description (``MarkupSpec``, as JSON) in the private key
``/PPDFSpec``, so it can be re-created exactly: undo/redo, move, resize and restyle are
"delete + create from a changed spec". Geometry is in *unrotated* page coordinates
(PyMuPDF's annotation space; see CLAUDE.md).

Annotations made by other software are listed too; they can be deleted (and the
deletion undone) and their note text edited, but not moved or restyled.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from enum import Enum

import pymupdf

from planetpdf.core.appearance import add_appearance_annot, flatten_source, read_source
from planetpdf.core.document import PdfError, PdfSession

NM_PREFIX = "ppdf-mk-"
SPEC_KEY = "PPDFSpec"
Point = tuple[float, float]
Color = tuple[float, float, float]


class Kind(Enum):
    RECT = "rect"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    INK = "ink"
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    NOTE = "note"
    TEXTBOX = "textbox"
    STAMP = "stamp"
    SEAL = "seal"
    SIGNATURE = "signature"
    INITIALS = "initials"
    TEXT = "text"  # placed text (Fill & Sign 'Add Text'): an appearance item until flattened
    SYMBOL = "symbol"  # Fill & Sign cross / check / circle / line / dot (`text` = which)
    IMAGE = "image"  # a pasted picture (Edit ▸ Paste): an appearance item until flattened
    POLYGON = "polygon"  # closed shape, clicked point by point
    POLYLINE = "polyline"  # open line through several points
    CALLOUT = "callout"  # text box with a leader line: points = (box top-left, box bottom-right, tip)
    CARET = "caret"  # Insert text at cursor: a caret mark; `text` = the words to insert
    # Comment tools added 2026-10-02 (Acrobat's, were grayed out):
    REPLACE = "replace"  # Replace text: the words crossed out (quads) + a caret with `text` = the new words
    CLOUD = "cloud"  # a polygon with a cloudy border
    ATTACH = "attach"  # a file attached at a point: `source` = the file, `text` = its name
    INK_ERASER = "ink_eraser"  # (a tool, not an item: erases parts of pencil drawings)

    @property
    def uses_appearance(self) -> bool:
        return self in (Kind.STAMP, Kind.SEAL, Kind.SIGNATURE, Kind.INITIALS, Kind.TEXT, Kind.IMAGE, Kind.SYMBOL)

    @property
    def placed(self) -> bool:
        """Filled in on the page (Fill & Sign): selectable in every mode."""
        return self in (Kind.SIGNATURE, Kind.INITIALS, Kind.TEXT, Kind.IMAGE, Kind.SYMBOL)

    @property
    def text_markup(self) -> bool:
        return self in (Kind.HIGHLIGHT, Kind.UNDERLINE, Kind.STRIKEOUT, Kind.REPLACE)

    @property
    def movable(self) -> bool:
        return not self.text_markup  # text markup belongs to the text under it

    @property
    def keeps_aspect(self) -> bool:
        return self.uses_appearance

    @property
    def stretchable(self) -> bool:
        """Shift + a corner changes its proportions (user 2026-10-10: stamps; the picture
        stretches to the box in every viewer and when flattened)."""
        return self in (Kind.STAMP, Kind.SEAL)

    @property
    def label(self) -> str:
        return {
            Kind.RECT: "Rectangle", Kind.ELLIPSE: "Ellipse", Kind.LINE: "Line", Kind.ARROW: "Arrow",
            Kind.INK: "Freehand", Kind.HIGHLIGHT: "Highlight", Kind.UNDERLINE: "Underline",
            Kind.STRIKEOUT: "Strike-out", Kind.NOTE: "Note", Kind.TEXTBOX: "Text box", Kind.STAMP: "Stamp",
            Kind.SEAL: "Seal", Kind.SIGNATURE: "Signature", Kind.INITIALS: "Initials", Kind.TEXT: "Text", Kind.IMAGE: "Image", Kind.SYMBOL: "Symbol",
            Kind.POLYGON: "Polygon", Kind.POLYLINE: "Polyline", Kind.CALLOUT: "Callout", Kind.CARET: "Inserted text",
            Kind.REPLACE: "Replaced text", Kind.CLOUD: "Cloud", Kind.ATTACH: "Attached file", Kind.INK_ERASER: "Eraser",
        }[self]


@dataclass(frozen=True)
class Style:
    color: Color = (0.85, 0.1, 0.1)
    fill: Color | None = None
    width: float = 2.0
    opacity: float = 1.0
    font_size: float = 12.0
    # placed text (user 2026-10-03: font, weight, italic, underline…; `core.fonts`)
    font: str = "Helvetica"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    # words in another format (placed text, text boxes): (start, end, bold, italic,
    # underline, strikeout) for characters [start, end) (placed_text.char_formats)
    runs: tuple = ()

    @property
    def base_format(self) -> tuple[bool, bool, bool, bool]:
        return (self.bold, self.italic, self.underline, self.strikeout)

    @property
    def plain_text(self) -> bool:
        """Helvetica with nothing bold, italic, underlined or struck out: a text box MuPDF
        can draw by itself."""
        return self.font == "Helvetica" and not any(self.base_format) and not self.runs

    def with_format(self, text: str, **changes) -> "Style":
        """The style with new font / size / color / bold… (the text format bar). Bold,
        italic, underline or strike-out set for the whole text apply to its words too."""
        from planetpdf.core import placed_text as PT

        new = replace(self, **{k: v for k, v in changes.items() if k != "runs"})
        if "runs" in changes:
            return replace(new, runs=tuple(tuple(r) for r in changes["runs"]))
        changed = tuple(k for k in PT.STYLE_KEYS if k in changes)
        if self.runs and (changed or new.base_format != self.base_format):
            new = replace(new, runs=PT.normalize_runs(text, self.base_format, new.base_format, self.runs,
                                                      changed=changed))
        return new

    @property
    def text_format(self) -> dict:
        return {"font": self.font, "bold": self.bold, "italic": self.italic, "underline": self.underline,
                "strikeout": self.strikeout}


@dataclass(frozen=True)
class MarkupSpec:
    mark_id: str
    page: int
    kind: Kind
    rect: tuple[float, float, float, float]  # bounding box (unrotated)
    points: tuple[Point, ...] = ()  # line/arrow: 2 points; ink: the stroke(s) flattened; text markup: quad corners
    strokes: tuple[int, ...] = ()  # ink: number of points in each stroke
    style: Style = field(default_factory=Style)
    text: str = ""
    author: str = ""
    source: bytes | None = None  # appearance kinds: the upright source PDF
    # its note's pop-up box (PDF /Popup: user 2026-10-04): where (unrotated) and whether
    # it's open; None = no box (yet)
    popup: tuple[float, float, float, float] | None = None
    popup_open: bool = False

    def to_json(self) -> str:
        data = asdict(self)
        data["kind"] = self.kind.value
        data.pop("source", None)
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str, source: bytes | None = None) -> MarkupSpec:
        data = json.loads(text)
        known = Style.__dataclass_fields__
        raw = data.pop("style")
        style = Style(**{k: (tuple(tuple(r) for r in v) if k == "runs" else tuple(v) if isinstance(v, list) else v)
                         for k, v in raw.items() if k in known})
        return cls(
            mark_id=data["mark_id"],
            page=int(data["page"]),
            kind=Kind(data["kind"]),
            rect=tuple(data["rect"]),
            points=tuple(tuple(p) for p in data.get("points", [])),
            strokes=tuple(data.get("strokes", [])),
            style=style,
            text=data.get("text", ""),
            author=data.get("author", ""),
            source=source,
            popup=tuple(data["popup"]) if data.get("popup") else None,
            popup_open=bool(data.get("popup_open", False)),
        )


@dataclass(frozen=True)
class MarkupItem:
    """An annotation as listed for the UI."""

    page: int
    xref: int
    rect: tuple[float, float, float, float]  # unrotated
    kind: Kind | None  # None for types we don't create (listed as "Comment")
    type_name: str
    author: str
    text: str
    spec: MarkupSpec | None  # ours
    item_id: str  # our mark id, or "x<xref>" for foreign annotations
    popup: tuple[float, float, float, float] | None = None  # its note's pop-up box (unrotated)
    popup_open: bool = False
    modified: str = ""  # PDF date ("D:2026…")
    replies: tuple = ()  # its thread (``Reply``), oldest first

    @property
    def ours(self) -> bool:
        return self.spec is not None

    @property
    def has_note(self) -> bool:
        """A comment you can open in a pop-up box: a sticky note, or text on a highlight, a
        shape… (not text boxes, whose text is on the page)."""
        if self.kind is not None:
            return self.kind in POPUP_KINDS and (self.kind is Kind.NOTE or bool(self.text.strip()))
        return self.popup is not None or bool(self.text.strip()) and self.type_name in FOREIGN_NOTE_TYPES


def new_id() -> str:
    return uuid.uuid4().hex[:16]


# --- notes in pop-up boxes (user 2026-10-04) ---------------------------------------------------------
#
# A comment's text shows in a pop-up box joined to it by a line (the viewer draws the box and
# the line; the PDF keeps its place and whether it's open: a /Popup annotation with /Rect and
# /Open, which Acrobat uses too). Click the item to open it, "–" collapses it.

POPUP_KINDS = {Kind.NOTE, Kind.HIGHLIGHT, Kind.UNDERLINE, Kind.STRIKEOUT, Kind.REPLACE, Kind.CARET, Kind.RECT,
               Kind.ELLIPSE, Kind.LINE, Kind.ARROW, Kind.INK, Kind.POLYGON, Kind.POLYLINE, Kind.CLOUD}
FOREIGN_NOTE_TYPES = {"Text", "Highlight", "Underline", "StrikeOut", "Squiggly", "Caret", "Square", "Circle", "Line",
                      "Ink", "Polygon", "PolyLine"}
POPUP_SIZE = (200.0, 110.0)  # points: a new note's box


def default_popup(page: pymupdf.Page, anchor) -> tuple[float, float, float, float]:
    """Where a new note's box goes (unrotated): at the right edge of the page as displayed,
    level with the item (like Acrobat's notes in the margin, so it covers as little of the
    text as it can); at the left edge when the item is there; under the item's line when
    the item spans both."""
    from planetpdf.core.redaction.marks import from_display, to_display

    shown = to_display(page, tuple(anchor))
    width, height = POPUP_SIZE
    page_w, page_h = page.rect.width, page.rect.height
    y = max(6.0, min(shown.y0 - 4, page_h - height - 6))
    right, left = page_w - width - 6, 6.0
    if shown.x1 + 12 <= right:
        x = right
    elif shown.x0 - 12 >= left + width:
        x = left
    else:  # (no room beside it: below its line)
        x = max(6.0, min(shown.x0, right))
        y = min(shown.y1 + 14, page_h - height - 6)
    return from_display(page, (x, y, x + width, y + height))


def bounds(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


TEXTBOX_PADDING = 2.0
TEXTBOX_LEADING = 1.25  # line height / font size, a little generous so nothing is clipped


def textbox_height(text: str, width: float, font_size: float, style: "Style | None" = None, doc=None) -> float:
    """Height a free-text box needs to show ``text`` at ``width``, wrapped the way the
    PDF text box wraps it, so the saved box never clips a line. ``style``: a text box in
    another font / with bold words… is drawn by PlanetPDF (``_draw_textbox_text``)."""
    if style is not None and not style.plain_text:
        from planetpdf.core import placed_text as PT

        border = max(style.width, 0.0)
        wrap = max(1.0, width - 2 * border - 2 * PT.PADDING)
        _w, height = PT.layout_size(text, font_size, style.font, style.bold, style.italic, doc, runs=style.runs,
                                    wrap_width=wrap)
        return height + 2 * border + 2
    usable = max(1.0, width - 2 * TEXTBOX_PADDING)
    lines = 0
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        count = 1
        for word in words:
            trial = f"{current} {word}" if current else word
            if current and pymupdf.get_text_length(trial, fontname="helv", fontsize=font_size) > usable:
                count += 1
                current = word
            else:
                current = trial
        lines += count
    return lines * font_size * TEXTBOX_LEADING + 2 * TEXTBOX_PADDING + 2


# --- creation --------------------------------------------------------------------------------


def callout_line(box: pymupdf.Rect, tip: pymupdf.Point) -> list[pymupdf.Point]:
    """Leader line for a callout: from the tip to the middle of the box side facing it,
    with a short level "knee" next to the box (as Acrobat draws it)."""
    if tip.x < box.x0 or tip.x > box.x1:  # beside the box: attach to the left / right side
        side = box.x0 if tip.x < box.x0 else box.x1
        attach = pymupdf.Point(side, (box.y0 + box.y1) / 2)
        knee = pymupdf.Point(side - 12 if tip.x < box.x0 else side + 12, attach.y)
    else:  # above or below: the top / bottom side
        side = box.y0 if tip.y < box.y0 else box.y1
        attach = pymupdf.Point((box.x0 + box.x1) / 2, side)
        knee = pymupdf.Point(attach.x, side - 12 if tip.y < box.y0 else side + 12)
    return [tip, knee, attach]


def callout_spec_rect(box: tuple, tip: Point) -> tuple[float, float, float, float]:
    """The bounding box stored for a callout (box and tip)."""
    return bounds([(box[0], box[1]), (box[2], box[3]), tip])


def _quads(points: tuple[Point, ...]) -> list[pymupdf.Quad]:
    return [pymupdf.Quad(points[i : i + 4]) for i in range(0, len(points), 4)]


def _ink_strokes(spec: MarkupSpec) -> list[list[Point]]:
    strokes, i = [], 0
    for n in spec.strokes or (len(spec.points),):
        strokes.append([tuple(p) for p in spec.points[i : i + n]])
        i += n
    return strokes


def add_markup(session: PdfSession, spec: MarkupSpec) -> int:
    """Create the annotation for ``spec``. Returns its xref."""
    doc = session.doc
    if not 0 <= spec.page < doc.page_count:
        raise PdfError("Page out of range.")
    rect = pymupdf.Rect(spec.rect)
    s = spec.style
    name = f"{NM_PREFIX}{spec.mark_id}"
    if spec.kind.uses_appearance:
        if not spec.source:
            raise PdfError("This item has no appearance to draw.")
        xref = add_appearance_annot(
            doc, spec.page, spec.rect, spec.source, name=name, title=spec.author, contents=spec.text or spec.kind.label
        )
        doc.xref_set_key(xref, SPEC_KEY, pymupdf.get_pdf_str(spec.to_json()))
        session.mark_modified()
        return xref

    page = doc[spec.page]
    k = spec.kind
    if k is Kind.RECT:
        annot = page.add_rect_annot(rect)
    elif k is Kind.ELLIPSE:
        annot = page.add_circle_annot(rect)
    elif k in (Kind.LINE, Kind.ARROW):
        annot = page.add_line_annot(pymupdf.Point(spec.points[0]), pymupdf.Point(spec.points[1]))
        if k is Kind.ARROW:
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
    elif k is Kind.INK:
        annot = page.add_ink_annot(_ink_strokes(spec))
    elif k is Kind.HIGHLIGHT:
        annot = page.add_highlight_annot(quads=_quads(spec.points))
    elif k is Kind.UNDERLINE:
        annot = page.add_underline_annot(quads=_quads(spec.points))
    elif k is Kind.STRIKEOUT:
        annot = page.add_strikeout_annot(quads=_quads(spec.points))
    elif k is Kind.NOTE:
        annot = page.add_text_annot(pymupdf.Point(rect.x0, rect.y0), spec.text or "", icon="Note")
    elif k in (Kind.POLYGON, Kind.CLOUD):
        annot = page.add_polygon_annot([pymupdf.Point(p) for p in spec.points])
    elif k is Kind.REPLACE:
        annot = page.add_strikeout_annot(quads=_quads(spec.points))
    elif k is Kind.ATTACH:
        if not spec.source:
            raise PdfError("There is no file to attach.")
        annot = page.add_file_annot(pymupdf.Point(rect.x0, rect.y0), spec.source, spec.text or "attachment",
                                    ufilename=spec.text or "attachment", desc=spec.text, icon="PushPin")
    elif k is Kind.POLYLINE:
        annot = page.add_polyline_annot([pymupdf.Point(p) for p in spec.points])
    elif k is Kind.CALLOUT:
        box = pymupdf.Rect(spec.points[0], spec.points[1])
        tip = pymupdf.Point(spec.points[2])
        annot = page.add_freetext_annot(
            box,
            spec.text or "",
            fontsize=s.font_size,
            fontname="helv",
            text_color=s.color,
            fill_color=s.fill or (1.0, 1.0, 1.0),
            border_width=max(s.width / 2, 1.0),
            callout=callout_line(box, tip),
            line_end=pymupdf.PDF_ANNOT_LE_OPEN_ARROW,
            rotate=page.rotation,
        )
    elif k is Kind.CARET:
        # the caret's point sits under the gap between two characters (rect = its mark)
        annot = page.add_caret_annot(pymupdf.Point(rect.x0, rect.y0))
    elif k is Kind.TEXTBOX:
        annot = page.add_freetext_annot(
            rect,
            spec.text or "",
            fontsize=s.font_size,
            fontname="helv",
            text_color=s.color,
            fill_color=s.fill,
            # border_color needs rich text in PyMuPDF; a plain text box's border
            # takes the text color.
            border_width=s.width if s.width > 0 else 0,
            rotate=page.rotation,
        )
    else:
        raise PdfError(f"Can't create {k.value}")

    if k in (Kind.RECT, Kind.ELLIPSE, Kind.LINE, Kind.ARROW, Kind.INK, Kind.POLYGON, Kind.POLYLINE, Kind.CLOUD):
        annot.set_colors(stroke=s.color, fill=s.fill if k in (Kind.RECT, Kind.ELLIPSE, Kind.ARROW, Kind.POLYGON, Kind.CLOUD) else None)
        if k is Kind.CLOUD:
            annot.set_border(width=s.width, clouds=1)  # (the cloudy border effect, /BE /S /C)
        else:
            annot.set_border(width=s.width)
    elif k.text_markup or k in (Kind.NOTE, Kind.CARET, Kind.ATTACH):
        annot.set_colors(stroke=s.color)
    if k not in (Kind.TEXTBOX, Kind.CALLOUT):
        annot.set_opacity(s.opacity)
    annot.set_info(title=spec.author, content=spec.text)
    annot.update()
    xref = annot.xref
    if k in (Kind.TEXTBOX, Kind.CALLOUT) and not s.plain_text:
        annot = None
        box = spec.rect if k is Kind.TEXTBOX else tuple(pymupdf.Rect(spec.points[0], spec.points[1]))
        _draw_textbox_text(doc, spec.page, xref, box, spec.text, s)
        page = doc[spec.page]  # (drawing the text added and deleted a scratch page)
    doc.xref_set_key(xref, "M", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))  # (when: shown on its note)
    if k in POPUP_KINDS and (spec.popup is not None or k is Kind.NOTE):
        # its note's pop-up box (a sticky note always has one: MuPDF adds it, somewhere odd)
        spec = replace(spec, popup=spec.popup or default_popup(page, spec.rect))
        annot.set_popup(pymupdf.Rect(spec.popup))
        annot.set_open(spec.popup_open)
    doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(name))
    doc.xref_set_key(xref, SPEC_KEY, pymupdf.get_pdf_str(spec.to_json()))
    if k is Kind.REPLACE:
        _add_replace_caret(page, spec, xref)
    session.mark_modified()
    return xref


_TEXT_BLOCK = re.compile(rb"q\n(?P<cm>[-+\d. ]+) cm\nBT\n.*?ET\nQ\n", re.S)


def _draw_textbox_text(doc: pymupdf.Document, page_index: int, xref: int, box, text: str, s: Style) -> None:
    """A text box / callout in another font, or with bold / italic / underlined words
    (user 2026-10-03): MuPDF draws the box, its border and callout line (Helvetica only),
    and its text block is swapped for ours (``placed_text``, wrapped to the box). /DS and
    /RC say the same, so Acrobat keeps the font when the text is edited there."""
    from planetpdf.core import placed_text as PT
    from planetpdf.core.appearance import _embed_form

    page = doc[page_index]
    to_pdf = ~page.transformation_matrix  # (PyMuPDF's top-down page space -> PDF space)
    ap = int(doc.xref_get_key(xref, "AP/N")[1].split()[0])
    stream = doc.xref_stream(ap) or b""
    match = _TEXT_BLOCK.search(stream)
    if match is None:
        return  # (an empty box: nothing to draw)
    a, b, c, d, e, f = (float(v) for v in match.group("cm").split())
    frame = ~pymupdf.Matrix(a, b, c, d, e, f)  # PDF space -> the upright text frame
    corners = [pymupdf.Point(x, y) * to_pdf * frame for x in (box[0], box[2]) for y in (box[1], box[3])]
    x0, x1 = min(p.x for p in corners), max(p.x for p in corners)
    y1 = max(p.y for p in corners)
    border = max(s.width, 0.0)
    wrap = max(1.0, (x1 - x0) - 2 * border - 2 * PT.PADDING)
    source = PT.render(text, s.font_size, s.color, **s.text_format, doc=doc, runs=s.runs, wrap_width=wrap)
    form, _w, h = _embed_form(doc, source)
    placed = f"q\n1 0 0 1 {x0 + border:.3f} {y1 - border - h:.3f} cm\n/PPDFText Do\nQ\n".encode()
    text_block = b"q\n" + match.group("cm") + b" cm\n" + placed + b"Q\n"
    doc.update_stream(ap, stream[:match.start()] + text_block + stream[match.end():])
    kind, value = doc.xref_get_key(ap, "Resources")
    if kind == "xref":
        holder, prefix = int(value.split()[0]), ""
    else:
        if kind == "null":
            doc.xref_set_key(ap, "Resources", "<<>>")
        holder, prefix = ap, "Resources/"
    if doc.xref_get_key(holder, f"{prefix}XObject")[0] == "null":
        doc.xref_set_key(holder, f"{prefix}XObject", f"<< /PPDFText {form} 0 R >>")
    else:
        doc.xref_set_key(holder, f"{prefix}XObject/PPDFText", f"{form} 0 R")
    color = "#%02X%02X%02X" % tuple(round(v * 255) for v in s.color)
    doc.xref_set_key(xref, "DS", pymupdf.get_pdf_str(
        f"font: {'bold ' if s.bold else ''}{'italic ' if s.italic else ''}{s.font_size:g}pt '{s.font}'; "
        f"text-align:left; color:{color}"))
    doc.xref_set_key(xref, "RC", pymupdf.get_pdf_str(_rich_text(text, s, color)))


def _rich_text(text: str, s: Style, color: str) -> str:
    """The text as XHTML rich text (PDF /RC, Acrobat's form), runs as styled spans."""
    from html import escape

    from planetpdf.core import placed_text as PT

    def css(fmt) -> str:
        bold, italic, underline, strikeout = fmt
        deco = " ".join(d for d, on in (("underline", underline), ("line-through", strikeout)) if on) or "none"
        return (f"font-weight:{'bold' if bold else 'normal'};font-style:{'italic' if italic else 'normal'};"
                f"text-decoration:{deco}")

    formats = PT.char_formats(text, s.base_format, s.runs)
    paragraphs = []
    start = 0
    for paragraph in text.split("\n"):
        spans, i = [], start
        while i < start + len(paragraph):
            j = i
            while j < start + len(paragraph) and formats[j] == formats[i]:
                j += 1
            spans.append(f'<span style="{css(formats[i])}">{escape(text[i:j])}</span>')
            i = j
        paragraphs.append(f'<p dir="ltr">{"".join(spans)}</p>')
        start += len(paragraph) + 1
    return ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" xfa:APIVersion="Acrobat:11.0.0" xfa:spec="2.0.2" '
            f'style="font-size:{s.font_size:g}pt;text-align:left;color:{color};font-family:\'{escape(s.font)}\'">'
            + "".join(paragraphs) + "</body>")


COMPANION_PREFIX = "ppdf-mkc-"  # a second annotation that belongs to one of our items


def _add_replace_caret(page: pymupdf.Page, spec: MarkupSpec, strikeout_xref: int) -> None:
    """Replace Text (Acrobat): a caret after the crossed-out words holds the new words; it
    is grouped with the strike-out (``/IRT`` + ``/RT /Group``), as Acrobat writes it."""
    doc = page.parent
    last = _quads(spec.points)[-1].rect
    caret = page.add_caret_annot(pymupdf.Point(last.x1, last.y1))
    caret.set_colors(stroke=spec.style.color)
    caret.set_info(title=spec.author, content=spec.text)
    caret.update()
    doc.xref_set_key(caret.xref, "NM", pymupdf.get_pdf_str(f"{COMPANION_PREFIX}{spec.mark_id}"))
    doc.xref_set_key(caret.xref, "IRT", f"{strikeout_xref} 0 R")
    doc.xref_set_key(caret.xref, "RT", "/Group")


def erase_from_ink(spec: MarkupSpec, path: list[Point], radius: float) -> MarkupSpec | None | bool:
    """Comment ▸ Eraser on a pencil drawing: the stroke points within ``radius`` of the
    eraser ``path`` go, splitting strokes where they're cut. Returns the changed spec,
    None when nothing is left, or False when the eraser didn't touch it."""

    def near(p: Point) -> bool:
        px, py = p
        for (ax, ay), (bx, by) in zip(path, path[1:] or path):
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = 0.0 if not length else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
            if (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2 <= radius * radius:
                return True
        return False

    kept: list[list[Point]] = []
    touched = False
    for stroke in _ink_strokes(spec):
        run: list[Point] = []
        for p in stroke:
            if near(p):
                touched = True
                if len(run) >= 2:
                    kept.append(run)
                run = []
            else:
                run.append(p)
        if len(run) >= 2:
            kept.append(run)
    if not touched:
        return False
    if not kept:
        return None
    points = tuple(p for stroke in kept for p in stroke)
    return replace(spec, points=points, strokes=tuple(len(s) for s in kept), rect=bounds(points))


# --- listing ---------------------------------------------------------------------------------

_SKIP_TYPES = {pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_POPUP, pymupdf.PDF_ANNOT_LINK}


def _nm(doc: pymupdf.Document, xref: int) -> str:
    kind, value = doc.xref_get_key(xref, "NM")
    return value if kind == "string" else ""


def _reply_to(doc: pymupdf.Document, xref: int) -> int | None:
    """The annotation this one replies to (/IRT; Replace Text's grouped caret, /RT /Group,
    isn't a reply)."""
    kind, value = doc.xref_get_key(xref, "IRT")
    if kind != "xref" or doc.xref_get_key(xref, "RT")[1] == "/Group":
        return None
    return int(value.split()[0])


def list_markup(session: PdfSession, pages=None) -> list[MarkupItem]:
    items = _list_markup(session, pages)
    # replies (Acrobat's comment threads: user 2026-10-04) go with the comment they answer,
    # also a reply to a reply; one whose comment is gone stays an item of its own
    doc = session.doc
    by_xref = {i.xref: i for i in items}
    parents = {i.xref: _reply_to(doc, i.xref) for i in items}

    def root(xref: int) -> int | None:
        seen = set()
        while parents.get(xref) is not None and parents[xref] in by_xref and xref not in seen:
            seen.add(xref)
            xref = parents[xref]
        return xref if xref in by_xref and parents.get(xref) is None else None

    threads: dict[int, list[Reply]] = {}
    for item in items:
        if parents[item.xref] is None:
            continue
        top = root(item.xref)
        if top is None or top == item.xref:
            continue
        threads.setdefault(top, []).append(Reply(item.item_id, item.xref, item.author, item.text, item.modified))
    out = []
    for item in items:
        if parents[item.xref] is not None and root(item.xref) not in (None, item.xref):
            continue  # (shown in its comment's thread)
        replies = threads.get(item.xref)
        out.append(replace(item, replies=tuple(sorted(replies, key=lambda r: r.modified))) if replies else item)
    return out


@dataclass(frozen=True)
class Reply:
    """A reply to a comment (a Text annotation with /IRT: how Acrobat keeps its threads)."""

    item_id: str  # "ppdf-reply-<id>" (ours) or "x<xref>"
    xref: int
    author: str
    text: str
    modified: str


REPLY_PREFIX = "ppdf-reply-"


def _reply_ids(session: PdfSession, parent_xref: int) -> list[int]:
    doc = session.doc
    return [i.xref for i in _list_markup(session) if _reply_to(doc, i.xref) == parent_xref]


def add_reply(session: PdfSession, parent_id: str, text: str, author: str = "", *, reply_id: str | None = None) -> str:
    """Reply to a comment (user 2026-10-04, as in Acrobat): a Text annotation /IRT the
    comment (/RT /R), drawn as nothing (Acrobat shows it in the comment's thread, other
    viewers don't put a note icon on the page for it). Returns its id."""
    item = find_item(session, parent_id)
    doc = session.doc
    page = doc[item.page]
    rect = pymupdf.Rect(item.spec.rect if item.spec else item.rect)
    annot = page.add_text_annot(rect.tl, text, icon="Comment")
    annot.set_info(title=author, content=text)
    annot.update()
    xref = annot.xref
    annot = None
    reply_id = reply_id or REPLY_PREFIX + new_id()
    doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(reply_id))
    doc.xref_set_key(xref, "IRT", f"{item.xref} 0 R")
    doc.xref_set_key(xref, "RT", "/R")
    doc.xref_set_key(xref, "M", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))
    empty = doc.get_new_xref()
    doc.update_object(empty, "<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << >> >>")
    doc.update_stream(empty, b"")
    doc.xref_set_key(xref, "AP", f"<< /N {empty} 0 R >>")
    session.mark_modified()
    return reply_id


def _find_reply(session: PdfSession, reply_id: str) -> tuple[MarkupItem, Reply]:
    for item in list_markup(session):
        for reply in item.replies:
            if reply.item_id == reply_id:
                return item, reply
    raise PdfError("That reply no longer exists.")


def set_reply_text(session: PdfSession, reply_id: str, text: str, author: str | None = None) -> str:
    """Change a reply's words; returns the old ones. Changed: signed with ``author``
    (whoever edited them)."""
    _item, reply = _find_reply(session, reply_id)
    doc = session.doc
    old = reply.text
    page = doc[_item.page]
    annot = page.load_annot(reply.xref)
    annot.set_info(content=text)
    if author is not None and text != old:
        annot.set_info(title=author)
    doc.xref_set_key(reply.xref, "M", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))
    session.mark_modified()
    return old


def remove_reply(session: PdfSession, reply_id: str) -> tuple[str, Reply]:
    """Delete a reply; returns (its comment's id, the reply) to put it back."""
    item, reply = _find_reply(session, reply_id)
    page = session.doc[item.page]
    annot = page.load_annot(reply.xref)
    if annot is not None:
        page.delete_annot(annot)
    session.mark_modified()
    return item.item_id, reply


def _list_markup(session: PdfSession, pages=None) -> list[MarkupItem]:
    doc = session.doc
    items: list[MarkupItem] = []
    for index in range(doc.page_count) if pages is None else pages:
        page = doc[index]
        for annot in page.annots():
            if annot.type[0] in _SKIP_TYPES:
                continue
            spec = None
            nm = _nm(doc, annot.xref)
            if nm.startswith(COMPANION_PREFIX):
                continue  # (part of one of our items, e.g. Replace Text's caret)
            if nm.startswith(NM_PREFIX):
                kind_, raw = doc.xref_get_key(annot.xref, SPEC_KEY)
                if kind_ == "string":
                    try:
                        # Keep the stored rect: annot.rect is padded (line ends, widths).
                        spec = replace(MarkupSpec.from_json(raw, read_source(doc, annot.xref)), page=index)
                        if spec.kind is Kind.ATTACH:  # (the file lives in the annotation)
                            spec = replace(spec, source=annot.get_file())
                    except (ValueError, KeyError, TypeError, RuntimeError):
                        spec = None
            info = annot.info
            popup = _popup_rect(doc, page, annot)
            items.append(
                MarkupItem(
                    page=index,
                    xref=annot.xref,
                    rect=tuple(annot.rect),
                    kind=spec.kind if spec else None,
                    type_name=spec.kind.label if spec else annot.type[1],
                    author=info.get("title", ""),
                    text=info.get("content", ""),
                    spec=spec,
                    item_id=spec.mark_id if spec else (nm if nm.startswith(REPLY_PREFIX) else f"x{annot.xref}"),
                    popup=popup if popup and pymupdf.Rect(popup).width > 1 else None,
                    popup_open=bool(annot.is_open),
                    modified=info.get("modDate", "") or info.get("creationDate", ""),
                )
            )
    return items


def _popup_rect(doc: pymupdf.Document, page: pymupdf.Page, annot) -> tuple | None:
    """An annotation's pop-up box, unrotated like every annotation rect. (PyMuPDF 1.28's
    ``set_popup`` takes unrotated coordinates but ``popup_rect`` reads them back rotated, so
    the /Rect is read here.)"""
    if not annot.has_popup:
        return None
    kind, value = doc.xref_get_key(annot.popup_xref, "Rect")
    if kind != "array":
        return None
    try:
        rect = pymupdf.Rect([float(v) for v in value.strip("[]").split()]) * page.transformation_matrix
    except ValueError:
        return None
    return tuple(rect.normalize())


def find_item(session: PdfSession, item_id: str) -> MarkupItem:
    for item in list_markup(session):
        if item.item_id == item_id:
            return item
    raise PdfError("That markup no longer exists.")


def set_popup(session: PdfSession, item_id: str, rect=None, open_: bool | None = None, *,
              modified: bool = True) -> tuple[tuple | None, bool]:
    """Move / resize (``rect``, unrotated) and / or open or collapse (``open_``) an item's
    note box in place, for ours and other software's alike. An item without a box gets one
    beside it. Returns the previous (rect, open) for undo. ``modified=False``: opening or
    collapsing a note isn't an edit (it's saved with the next save, as Acrobat does)."""
    item = find_item(session, item_id)
    doc = session.doc
    page = doc[item.page]  # (keep the page alive: annots only weakly reference it)
    annot = page.load_annot(item.xref)
    if annot is None:
        raise PdfError("That markup no longer exists.")
    old = (item.popup, item.popup_open)
    if rect is None and item.popup is None:
        rect = default_popup(page, item.spec.rect if item.spec else item.rect)
    if rect is not None:
        annot.set_popup(pymupdf.Rect(rect))
    if open_ is not None:
        annot.set_open(bool(open_))
    if item.spec is not None:  # (ours: the description says the same)
        spec = replace(item.spec, popup=tuple(rect) if rect is not None else item.popup,
                       popup_open=item.popup_open if open_ is None else bool(open_))
        doc.xref_set_key(item.xref, SPEC_KEY, pymupdf.get_pdf_str(spec.to_json()))
    if modified:
        session.mark_modified()
    return old


# --- removal (undoable, including other software's annotations) ------------------------------------


@dataclass(frozen=True)
class RemovedForeign:
    page: int
    source: str  # the annotation's object source
    popup: str | None


def remove_markup(session: PdfSession, item_id: str) -> MarkupSpec | RemovedForeign:
    """Delete an item; returns what's needed to put it back (``restore_markup``)."""
    item = find_item(session, item_id)
    doc = session.doc
    page = doc[item.page]
    if item.spec is not None:
        restore: MarkupSpec | RemovedForeign = item.spec
    else:
        source = doc.xref_object(item.xref, compressed=True)
        kind, value = doc.xref_get_key(item.xref, "Popup")
        popup = doc.xref_object(int(value.split()[0]), compressed=True) if kind == "xref" else None
        restore = RemovedForeign(item.page, source, popup)
    annot = page.load_annot(item.xref)
    if annot is not None:
        page.delete_annot(annot)
    if item.spec is not None:  # its companions (Replace Text's caret)
        page = doc[item.page]
        for other in [a for a in page.annots() if _nm(doc, a.xref) == f"{COMPANION_PREFIX}{item.spec.mark_id}"]:
            page.delete_annot(other)
    session.mark_modified()
    return restore


def restore_markup(session: PdfSession, removed: MarkupSpec | RemovedForeign) -> str:
    """Undo ``remove_markup``. Returns the item id (foreign items get a new one)."""
    if isinstance(removed, MarkupSpec):
        add_markup(session, removed)
        return removed.mark_id
    doc = session.doc
    page = doc[removed.page]
    source = re.sub(r"/P\s+\d+\s+0\s+R", f"/P {page.xref} 0 R", removed.source)
    source = re.sub(r"/Popup\s+\d+\s+0\s+R", "", source)
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    refs = [xref]
    if removed.popup:
        popup = doc.get_new_xref()
        doc.update_object(popup, re.sub(r"/Parent\s+\d+\s+0\s+R", f"/Parent {xref} 0 R", removed.popup))
        doc.xref_set_key(xref, "Popup", f"{popup} 0 R")
        refs.append(popup)
    kind, value = doc.xref_get_key(page.xref, "Annots")
    existing = value.strip("[]") if kind == "array" else ""
    doc.xref_set_key(page.xref, "Annots", "[" + existing + " " + " ".join(f"{r} 0 R" for r in refs) + "]")
    session.mark_modified()
    return f"x{xref}"


# --- editing -----------------------------------------------------------------------------------


def transformed(spec: MarkupSpec, new_rect) -> MarkupSpec:
    """The same item fitted into ``new_rect`` (move and/or resize)."""
    old = pymupdf.Rect(spec.rect)
    new = pymupdf.Rect(new_rect)
    sx = new.width / old.width if old.width else 1.0
    sy = new.height / old.height if old.height else 1.0
    points = tuple((new.x0 + (x - old.x0) * sx, new.y0 + (y - old.y0) * sy) for x, y in spec.points)
    return replace(spec, rect=tuple(new), points=points)


def replace_markup(session: PdfSession, old: MarkupSpec, new: MarkupSpec) -> None:
    """Swap an item for a changed version of itself (same id). Its replies stay its own:
    they're pointed at the new annotation."""
    old_xref = find_item(session, old.mark_id).xref
    replies = _reply_ids(session, old_xref)
    for reply in replies:  # (MuPDF deletes an annotation's replies with it: let go of them first)
        session.doc.xref_set_key(reply, "IRT", "null")
    remove_markup(session, old.mark_id)
    xref = add_markup(session, new)
    for reply in replies:
        session.doc.xref_set_key(reply, "IRT", f"{xref} 0 R")


def set_foreign_text(session: PdfSession, item_id: str, text: str, author: str | None = None) -> str:
    """Edit the note text of an annotation made elsewhere. Returns the old text. Its words
    changed: signed with ``author`` (whoever edited them)."""
    item = find_item(session, item_id)
    page = session.doc[item.page]
    annot = page.load_annot(item.xref)
    old = annot.info.get("content", "")
    annot.set_info(content=text)
    if author is not None and text != old:
        annot.set_info(title=author)
    annot.update()
    session.doc.xref_set_key(item.xref, "M", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))
    session.mark_modified()
    return old


@dataclass(frozen=True)
class ItemImage:
    """An annotation rendered on its own (transparent background), as displayed."""

    width: int
    height: int
    stride: int
    samples: bytes  # RGBA


def render_item(session: PdfSession, item_id: str, scale: float) -> ItemImage | None:
    """Picture of one item alone, e.g. to drag it around while it's being moved."""
    try:
        item = find_item(session, item_id)
    except PdfError:
        return None
    page = session.doc[item.page]
    annot = page.load_annot(item.xref)
    if annot is None:
        return None
    pix = annot.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
    return ItemImage(pix.width, pix.height, pix.stride, pix.samples)


def set_hidden(session: PdfSession, item_id: str, hidden: bool) -> None:
    """Temporarily hide an item (while it's dragged or edited in place). Doesn't count
    as a change: the drag/edit then replaces the item, or un-hides it on cancel."""
    try:
        item = find_item(session, item_id)
    except PdfError:
        return
    page = session.doc[item.page]  # keep the page alive: annots only weakly reference it
    annot = page.load_annot(item.xref)
    if annot is None:
        return
    flags = annot.flags
    annot.set_flags(flags | pymupdf.PDF_ANNOT_IS_HIDDEN if hidden else flags & ~pymupdf.PDF_ANNOT_IS_HIDDEN)


def flatten_markup(session: PdfSession, item_id: str) -> tuple[MarkupSpec, list[int]]:
    """Draw an appearance item (stamp, seal, signature) into the page content and remove
    the annotation. Returns (spec, previous content stream xrefs) for undo."""
    item = find_item(session, item_id)
    spec = item.spec
    if spec is None or not spec.kind.uses_appearance or not spec.source:
        raise PdfError("Only stamps, seals, and signatures can be flattened.")
    doc = session.doc
    before = doc[spec.page].get_contents()
    remove_markup(session, item_id)
    flatten_source(doc, spec.page, spec.rect, spec.source)
    session.mark_modified()
    return spec, before


def unflatten_markup(session: PdfSession, spec: MarkupSpec, contents_before: list[int]) -> None:
    """Undo ``flatten_markup``: drop the added content stream(s) and restore the item."""
    doc = session.doc
    page = doc[spec.page]
    refs = " ".join(f"{x} 0 R" for x in contents_before)
    doc.xref_set_key(page.xref, "Contents", f"[{refs}]" if len(contents_before) != 1 else f"{contents_before[0]} 0 R")
    add_markup(session, spec)


# --- placed text ----------------------------------------------------------------------------------


def placed_text(session: PdfSession, page: int, top_left: Point, text: str, style: Style, *,
                mark_id: str | None = None, author: str = "") -> MarkupSpec:
    """A placed-text item whose top-left corner is at ``top_left`` (as displayed)."""
    from planetpdf.core import placed_text as PT
    from planetpdf.core.redaction.marks import from_display

    size = PT.clamp_size(style.font_size)
    style = replace(style, font_size=size)
    fmt = style.text_format
    runs = PT.make_runs(PT.char_formats(text, style.base_format, style.runs), style.base_format)  # (in the text)
    style = replace(style, runs=runs)
    width, height = PT.layout_size(text, size, fmt["font"], fmt["bold"], fmt["italic"], doc=session.doc, runs=runs)
    x, y = top_left
    rect = from_display(session.doc[page], (x, y, x + width, y + height))
    source = PT.render(text, size, style.color, **fmt, doc=session.doc, runs=runs)
    return MarkupSpec(mark_id=mark_id or new_id(), page=page, kind=Kind.TEXT, rect=rect, style=style,
                      text=text, author=author, source=source)


def restyled_text(session: PdfSession, spec: MarkupSpec, *, text: str | None = None, font_size: float | None = None,
                  color: Color | None = None, top_left: Point | None = None, **text_format) -> MarkupSpec:
    """The same placed text with new words, size, color, font / bold / italic / underline /
    strike-out (``text_format``) and/or position (top-left as displayed). Only moved: the
    same picture at the new place (a font this computer doesn't have stays as it was)."""
    from planetpdf.core.redaction.marks import from_display, to_display

    page = session.doc[spec.page]
    shown = to_display(page, spec.rect)
    style = spec.style if font_size is None else replace(spec.style, font_size=font_size)
    if color is not None:
        style = replace(style, color=tuple(color))
    new_text = spec.text if text is None else text
    if text_format:
        style = style.with_format(new_text, **text_format)
    if new_text != spec.text and "runs" not in text_format and style.runs:
        from planetpdf.core import placed_text as PT

        style = replace(style, runs=PT.edit_runs(spec.text, new_text, style.base_format, style.runs))
    if style == spec.style and new_text == spec.text and spec.source is not None:
        from planetpdf.core.appearance import source_size

        width, height = source_size(spec.source)  # (the picture is upright: its size as displayed)
        x, y = top_left or (shown.x0, shown.y0)
        return replace(spec, rect=from_display(page, (x, y, x + width, y + height)))
    return placed_text(session, spec.page, top_left or (shown.x0, shown.y0), new_text,
                       style, mark_id=spec.mark_id, author=spec.author)


def fitted_text(session: PdfSession, spec: MarkupSpec, new_rect) -> MarkupSpec:
    """Placed text dragged to ``new_rect`` (unrotated): resizing scales the font size."""
    from planetpdf.core.redaction.marks import to_display

    page = session.doc[spec.page]
    old, new = to_display(page, spec.rect), to_display(page, new_rect)
    moved_only = abs(old.width - new.width) < 0.5 and abs(old.height - new.height) < 0.5
    size = spec.style.font_size if moved_only else spec.style.font_size * new.height / max(old.height, 0.1)
    return restyled_text(session, spec, font_size=size, top_left=(new.x0, new.y0))


def flatten_page(session: PdfSession, index: int) -> list[tuple[MarkupSpec, list[int]]]:
    """Flatten every placed text, signature, initials, stamp and seal of ours on a page.
    Returns what ``unflatten_page`` needs to undo it."""
    done = []
    for item in list_markup(session, [index]):
        if item.spec is not None and item.spec.kind.uses_appearance and item.spec.source:
            done.append(flatten_markup(session, item.item_id))
    return done


def unflatten_page(session: PdfSession, done: list[tuple[MarkupSpec, list[int]]]) -> None:
    for spec, before in reversed(done):
        unflatten_markup(session, spec, before)


# --- Fill & Sign symbols -----------------------------------------------------------------------


def symbol(session: PdfSession, page: int, center: Point, name: str, color: Color, *, author: str = "",
           size: tuple[float, float] | None = None, box: tuple | None = None, line: float | None = None,
           filled: bool = False) -> MarkupSpec:
    """A cross / check / circle / line / dot centered at ``center`` (as displayed), ``size``
    (width, height as displayed) or its default size; or filling ``box`` (as displayed: a circle
    dragged from corner to corner). A circle's ``line`` width and ``filled`` are kept in its style
    (``width``; ``fill`` = the color when filled)."""
    from planetpdf.core import symbols as S
    from planetpdf.core.redaction.marks import from_display

    if box is None:
        _label, w, h = S.SYMBOLS[name]
        w, h = size or (w, h)
        x, y = center
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    style = Style(color=tuple(color))
    if name == "circle":
        style = replace(style, width=line or S.CIRCLE_LINE, fill=tuple(color) if filled else None)
    spec = MarkupSpec(mark_id=new_id(), page=page, kind=Kind.SYMBOL, rect=from_display(session.doc[page], box),
                      style=style, text=name, author=author)
    return redrawn_symbol(session, spec)


def symbol_size(session: PdfSession, spec: MarkupSpec) -> tuple[float, float]:
    """A symbol's (width, height) as displayed (its rect is the page's unrotated one)."""
    rect = pymupdf.Rect(spec.rect)
    turned = session.doc[spec.page].rotation % 180 == 90
    return (rect.height, rect.width) if turned else (rect.width, rect.height)


def redrawn_symbol(session: PdfSession, spec: MarkupSpec) -> MarkupSpec:
    """The symbol drawn for its size now: a circle's line keeps its width at any size (the other
    symbols are drawn at their default size and scale)."""
    from planetpdf.core import symbols as S

    if spec.text != "circle":
        return replace(spec, source=S.render(spec.text, spec.style.color))
    w, h = symbol_size(session, spec)
    return replace(spec, source=S.render("circle", spec.style.color, w, h, line=spec.style.width,
                                         filled=spec.style.fill is not None))


def recolored_symbol(spec: MarkupSpec, color: Color) -> MarkupSpec:
    from planetpdf.core import symbols as S

    style = replace(spec.style, color=tuple(color), fill=tuple(color) if spec.style.fill is not None else None)
    if spec.text != "circle":
        return replace(spec, style=style, source=S.render(spec.text, color))
    rect = pymupdf.Rect(spec.rect)  # (same size: re-render at the source's own size)
    w, h = _source_size(spec) or (rect.width, rect.height)
    return replace(spec, style=style, source=S.render("circle", color, w, h, line=style.width,
                                                      filled=style.fill is not None))


def _source_size(spec: MarkupSpec) -> tuple[float, float] | None:
    if not spec.source:
        return None
    with pymupdf.open("pdf", spec.source) as doc:
        return doc[0].rect.width, doc[0].rect.height


def restyled_circle(session: PdfSession, spec: MarkupSpec, *, line: float | None = None,
                    filled: bool | None = None) -> MarkupSpec:
    """A circle with another line width and/or solid / outline."""
    style = spec.style
    if line is not None:
        style = replace(style, width=line)
    if filled is not None:
        style = replace(style, fill=tuple(style.color) if filled else None)
    return redrawn_symbol(session, replace(spec, style=style))
