"""Form fields: list them in tab order, read and write values.

A *field* is identified by its fully qualified name; it may have several widgets
(a radio group, or a text field shown on two pages). Values:

* text / date: ``str``
* checkbox: ``bool``
* radio group: the chosen option's on-state name, or ``None`` for nothing chosen
* dropdown: ``str``; list box: ``list[str]`` (one item unless multi-select)

PyMuPDF quirks handled here (see CLAUDE.md): widgets only weakly reference their page;
empty text values are ignored on ``update()``; radio/checkbox values must be written
as PDF names (PyMuPDF writes a string for radio groups).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

import pymupdf

from planetpdf.core.document import PdfError, PdfSession

RectTuple = tuple[float, float, float, float]

# Field flags (PDF 32000-1, 12.7.3 and 12.7.4)
FF_READ_ONLY = 1 << 0
FF_REQUIRED = 1 << 1
FF_MULTILINE = 1 << 12
FF_PASSWORD = 1 << 13
FF_COMBO_EDIT = 1 << 18
FF_MULTISELECT = 1 << 21
FF_COMB = 1 << 24


class FieldKind(Enum):
    TEXT = "text"
    DATE = "date"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    COMBO = "combo"
    LIST = "list"
    SIGNATURE = "signature"
    BUTTON = "button"  # push buttons: no value

    @property
    def fillable(self) -> bool:
        return self not in (FieldKind.SIGNATURE, FieldKind.BUTTON)


@dataclass(frozen=True)
class WidgetRef:
    page: int
    xref: int
    rect: RectTuple  # unrotated page coordinates
    on_state: str | None = None  # checkbox / radio: this widget's "on" name
    label: str | None = None  # radio made by PlanetPDF: the text shown next to its circle (None: other software's)


@dataclass
class FormField:
    name: str
    kind: FieldKind
    widgets: list[WidgetRef]
    value: object
    options: list[str] = field(default_factory=list)  # combo / list choices (export values)
    labels: list[str] = field(default_factory=list)  # choice display texts
    read_only: bool = False
    required: bool = False
    multiline: bool = False
    multiselect: bool = False
    editable_combo: bool = False
    max_length: int = 0
    date_format: str = ""  # AFDate format, e.g. "mm/dd/yyyy"
    password: bool = False  # a text field that shows dots

    @property
    def first_page(self) -> int:
        return self.widgets[0].page


_AFDATE = re.compile(r"AFDate_(?:Format|Keystroke)(?:Ex)?\s*\(\s*[\"']([^\"']+)[\"']")
_KIND = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: FieldKind.TEXT,
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: FieldKind.CHECKBOX,
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: FieldKind.RADIO,
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: FieldKind.COMBO,
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: FieldKind.LIST,
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: FieldKind.SIGNATURE,
    pymupdf.PDF_WIDGET_TYPE_BUTTON: FieldKind.BUTTON,
}


def date_format_of(widget: pymupdf.Widget) -> str:
    """The AFDate format from a text field's format/keystroke script ('' if none)."""
    for script in (widget.script_format, widget.script_stroke):
        if script:
            match = _AFDATE.search(script)
            if match:
                return match.group(1)
    return ""


# --- AFDate format <-> Python --------------------------------------------------------------

_AF_TOKENS = [
    ("yyyy", "%Y"), ("yy", "%y"),
    ("mmmm", "%B"), ("mmm", "%b"), ("mm", "%m"), ("m", "%#m"),
    ("dddd", "%A"), ("ddd", "%a"), ("dd", "%d"), ("d", "%#d"),
]


def _strftime_pattern(af: str) -> str:
    out, i = [], 0
    while i < len(af):
        for token, code in _AF_TOKENS:
            if af.startswith(token, i):
                out.append(code)
                i += len(token)
                break
        else:
            out.append(af[i].replace("%", "%%"))
            i += 1
    return "".join(out)


def format_date(value: date, af_format: str) -> str:
    """Format like Acrobat's AFDate (``m``/``d`` without leading zeros)."""
    pattern = _strftime_pattern(af_format or "mm/dd/yyyy")
    # %#m / %#d aren't portable: expand them by hand.
    pattern = pattern.replace("%#m", str(value.month)).replace("%#d", str(value.day))
    return value.strftime(pattern)


def parse_date(text: str, af_format: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    candidates = [_strftime_pattern(af_format or "mm/dd/yyyy").replace("%#m", "%m").replace("%#d", "%d")]
    candidates += ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%B %d, %Y"]
    for pattern in candidates:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


# --- listing ---------------------------------------------------------------------------------


def _tab_order(page: pymupdf.Page, widgets: list[pymupdf.Widget]) -> list[pymupdf.Widget]:
    """Order widgets as the page's /Tabs entry says (row, column, structure/annotation)."""
    kind, tabs = page.parent.xref_get_key(page.xref, "Tabs")
    shown = [(w, pymupdf.Rect(w.rect) * page.rotation_matrix) for w in widgets]
    if tabs == "/R":  # rows: top to bottom, then left to right
        shown.sort(key=lambda item: (round(item[1].y0), item[1].x0))
    elif tabs == "/C":  # columns: left to right, then top to bottom
        shown.sort(key=lambda item: (round(item[1].x0), item[1].y0))
    # /S (structure) and no entry: keep the /Annots order, which widgets() follows.
    return [w for w, _ in shown]


def _choices(widget: pymupdf.Widget) -> tuple[list[str], list[str]]:
    values, labels = [], []
    for choice in widget.choice_values or []:
        if isinstance(choice, (list, tuple)):
            values.append(str(choice[0]))
            labels.append(str(choice[1]) if len(choice) > 1 else str(choice[0]))
        else:
            values.append(str(choice))
            labels.append(str(choice))
    return values, labels


def list_fields(session: PdfSession) -> list[FormField]:
    """All fields, in tab order (page by page)."""
    doc = session.doc
    fields: dict[str, FormField] = {}
    order: list[str] = []
    for index in range(doc.page_count):
        page = doc[index]
        widgets = list(page.widgets())
        for widget in _tab_order(page, widgets):
            kind = _KIND.get(widget.field_type)
            if kind is None:
                continue
            name = widget.field_name or f"(unnamed {widget.xref})"
            on = _name_text(widget.on_state()) if kind in (FieldKind.CHECKBOX, FieldKind.RADIO) else None
            label = radio_label(doc, widget.xref) if kind in (FieldKind.RADIO, FieldKind.CHECKBOX) else None
            # ours with text: the whole area (circle + its text) is what PlanetPDF works with
            rect = (label_box(doc, widget.xref) or tuple(widget.rect)) if label is not None else tuple(widget.rect)
            ref = WidgetRef(index, widget.xref, rect, on if isinstance(on, str) else None, label)
            if name in fields:
                fields[name].widgets.append(ref)
                if kind is FieldKind.RADIO and _radio_is_on(widget):
                    fields[name].value = ref.on_state
                continue
            flags = widget.field_flags or 0
            if kind is FieldKind.TEXT:
                fmt = date_format_of(widget)
                if fmt:
                    kind = FieldKind.DATE
            else:
                fmt = ""
            values, labels = _choices(widget)
            if kind is FieldKind.LIST and flags & FF_MULTISELECT:
                value = _read_multi_list(doc, widget.xref)  # PyMuPDF can't read these
            else:
                value = _read_value(kind, widget, flags)
            fields[name] = FormField(
                name=name,
                kind=kind,
                widgets=[ref],
                value=value,
                options=values,
                labels=labels,
                read_only=bool(flags & FF_READ_ONLY),
                required=bool(flags & FF_REQUIRED),
                multiline=bool(flags & FF_MULTILINE) and kind is FieldKind.TEXT,
                multiselect=bool(flags & FF_MULTISELECT) and kind is FieldKind.LIST,
                editable_combo=bool(flags & FF_COMBO_EDIT) and kind is FieldKind.COMBO,
                max_length=widget.text_maxlen or 0,
                date_format=fmt,
                password=bool(flags & FF_PASSWORD) and kind is FieldKind.TEXT,
            )
            order.append(name)
    return [fields[n] for n in order]


LABEL_KEY = "PPDFLabel"  # a radio button's / checkbox's visible text (our own key, on its widget)
# The standard way, as Acrobat makes them (user 2026-10-04, Acrobat redrew ours from the
# field's own properties and the text vanished): the widget is just the small circle / box,
# and its text is ordinary page text beside it (a tagged mark, `page_marks`, kind
# LABEL_KIND, spec = {"field", "button"}). BOX_KEY keeps the whole area, circle and text,
# (unrotated, PyMuPDF's top-down page coordinates): what PlanetPDF moves, resizes and fits.
BOX_KEY = "PPDFBox"
LABEL_KIND = "fieldlabel"


def radio_label(doc: pymupdf.Document, xref: int) -> str | None:
    kind, value = doc.xref_get_key(xref, LABEL_KEY)
    return value if kind == "string" else None


def label_box(doc: pymupdf.Document, xref: int) -> tuple[float, float, float, float] | None:
    kind, value = doc.xref_get_key(xref, BOX_KEY)
    if kind != "array":
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value.strip("[]").split())
    except ValueError:
        return None
    return (x0, y0, x1, y1)


def set_label_box(doc: pymupdf.Document, xref: int, box) -> None:
    doc.xref_set_key(xref, BOX_KEY, "[" + " ".join(f"{v:.3f}" for v in box) + "]")


def _radio_is_on(widget: pymupdf.Widget) -> bool:
    value = widget.field_value
    return value not in (None, "", "Off", False)


def _read_value(kind: FieldKind, widget: pymupdf.Widget, flags: int):
    value = widget.field_value
    if kind in (FieldKind.TEXT, FieldKind.DATE, FieldKind.COMBO):
        return "" if value in (None, False) else str(value)
    if kind is FieldKind.CHECKBOX:
        return value not in (None, "", "Off", False)
    if kind is FieldKind.RADIO:
        return _name_text(widget.on_state()) if _radio_is_on(widget) else None
    if kind is FieldKind.LIST:
        if value in (None, "", False):
            return []
        return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    return None


def get_field(session: PdfSession, name: str) -> FormField:
    for f in list_fields(session):
        if f.name == name:
            return f
    raise PdfError(f"No form field named {name!r}.")


# --- writing ---------------------------------------------------------------------------------


def _load_widget(page: pymupdf.Page, xref: int) -> pymupdf.Widget | None:
    for widget in page.widgets():
        if widget.xref == xref:
            return widget
    return None


def _parent_xref(doc: pymupdf.Document, xref: int) -> int | None:
    kind, value = doc.xref_get_key(xref, "Parent")
    return int(value.split()[0]) if kind == "xref" else None


def _pdf_name(value: str) -> str:
    return "/" + "".join(c if c.isalnum() or c in "-_." else f"#{ord(c):02X}" for c in value)


def _name_text(raw: str | None) -> str | None:
    """A PDF name as text: PyMuPDF's ``on_state()`` returns it still escaped ("Choice#201"),
    and escaping that again made "Choice#2320 1" on every save, the chosen radio button
    drawn invisible (user bug 2026-10-02)."""
    if not isinstance(raw, str) or "#" not in raw:
        return raw
    data = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw.encode("latin-1", "replace"))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def set_field_value(session: PdfSession, name: str, value) -> object:
    """Set a field's value (all its widgets). Returns the previous value."""
    f = get_field(session, name)
    if not f.kind.fillable:
        raise PdfError(f"{name} can't be filled in.")
    old = f.value
    doc = session.doc
    value = _normalize(f, value)
    for ref in f.widgets:
        page = doc[ref.page]  # keep the page alive while using its widget
        widget = _load_widget(page, ref.xref)
        if widget is None:
            continue
        if f.kind is FieldKind.RADIO:
            on = value is not None and ref.on_state == value
            if ref.label is None:  # (ours keep their own drawing: update() would draw a box-filling circle)
                widget.field_value = True if on else False
                widget.update()
            doc.xref_set_key(ref.xref, "AS", _pdf_name(ref.on_state) if on and ref.on_state else "/Off")
        elif f.kind is FieldKind.CHECKBOX and ref.label is not None:
            # ours (box + its text): update() would draw MuPDF's own box over the whole field
            set_checkbox_state(doc, ref.xref, ref.on_state, bool(value))
        elif f.kind is FieldKind.CHECKBOX:
            widget.field_value = bool(value)
            widget.update()
        elif f.kind is FieldKind.LIST and f.multiselect:
            _write_multi_list(doc, ref.xref, f, value)
        elif f.kind is FieldKind.LIST:
            widget.field_value = value[0] if value else " "
            widget.update()
            if not value:
                doc.xref_set_key(ref.xref, "V", "null")
            # (PyMuPDF's appearance doesn't show which item is chosen)
            _draw_list_appearance(doc, ref.xref, f.labels or f.options,
                                  {f.options.index(value[0])} if value else set())
        else:
            text = str(value)
            # PyMuPDF ignores an empty value on update(); write a space, then clear /V.
            widget.field_value = text if text else " "
            widget.update()
            if not text:
                doc.xref_set_key(ref.xref, "V", "()")
    # Button values are PDF names on the field (the radio group's parent, if any).
    if f.kind in (FieldKind.RADIO, FieldKind.CHECKBOX):
        first = f.widgets[0]
        holder = _parent_xref(doc, first.xref) if f.kind is FieldKind.RADIO else None
        if f.kind is FieldKind.CHECKBOX:
            on_name = first.on_state or "Yes"
            name_value = _pdf_name(on_name) if value else "/Off"
            for ref in f.widgets:
                target = _parent_xref(doc, ref.xref) or ref.xref
                doc.xref_set_key(target, "V", name_value)
        else:
            name_value = _pdf_name(value) if value else "/Off"
            doc.xref_set_key(holder or first.xref, "V", name_value)
    session.mark_modified()
    from planetpdf.core.forms import barcodes

    barcodes.redraw_all(session)  # (barcode fields show the form's values: user 2026-10-23)
    return old


# --- multi-select list boxes (PyMuPDF 1.28 can neither read nor write them) ---------------------

_PDF_STRING = re.compile(rb"\(((?:\\.|[^\\)])*)\)|<([0-9A-Fa-f\s]*)>")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _field_holder(doc: pymupdf.Document, xref: int) -> int:
    """The object that holds the field's /V: the widget itself, or its parent field."""
    if doc.xref_get_key(xref, "T")[0] != "null":
        return xref
    return _parent_xref(doc, xref) or xref


def _unescape(raw: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(raw):
        if raw[i : i + 1] == b"\\" and i + 1 < len(raw):
            out += _ESCAPES.get(raw[i + 1 : i + 2], raw[i + 1 : i + 2])
            i += 2
        else:
            out += raw[i : i + 1]
            i += 1
    data = bytes(out)
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    return data.decode("latin-1")


def _read_multi_list(doc: pymupdf.Document, xref: int) -> list[str]:
    holder = _field_holder(doc, xref)
    kind, value = doc.xref_get_key(holder, "V")
    if kind == "null" or not value:
        return []
    if kind == "string":
        return [value]
    items = []
    for literal, hexa in _PDF_STRING.findall(value.encode("latin-1", "replace")):
        items.append(_unescape(literal) if literal or not hexa else bytes.fromhex(hexa.decode()).decode("latin-1"))
    return items


def _helv_font_xref(doc: pymupdf.Document) -> int:
    kind, value = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/DR/Font/Helv")
    if kind == "xref":
        return int(value.split()[0])
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    return xref


def _write_multi_list(doc: pymupdf.Document, xref: int, f: FormField, values: list[str]) -> None:
    holder = _field_holder(doc, xref)
    if values:
        doc.xref_set_key(holder, "V", "[" + " ".join(pymupdf.get_pdf_str(v) for v in values) + "]")
        indices = sorted(f.options.index(v) for v in values)
        doc.xref_set_key(holder, "I", "[" + " ".join(map(str, indices)) + "]")
    else:
        doc.xref_set_key(holder, "V", "null")
        doc.xref_set_key(holder, "I", "null")
    _draw_list_appearance(doc, xref, f.labels or f.options, {f.options.index(v) for v in values}, multiselect=True)


_ROTATION_MATRIX = {0: "1 0 0 1 0 0", 90: "0 1 -1 0 0 0", 180: "-1 0 0 -1 0 0", 270: "0 -1 1 0 0 0"}


def _mk_color(doc: pymupdf.Document, xref: int, key: str) -> str | None:
    """A widget's /MK background (BG) or border (BC) color as fill operands, if any."""
    kind, value = doc.xref_get_key(xref, f"MK/{key}")
    numbers = re.findall(r"-?[\d.]+", value) if kind == "array" else []
    if len(numbers) == 3:
        return " ".join(numbers)
    if len(numbers) == 1:
        return f"{numbers[0]} {numbers[0]} {numbers[0]}"
    return None


def _form_xobject(doc: pymupdf.Document, xref: int, inner, *, box: bool = True) -> int:
    """A widget appearance (Form XObject), the way MuPDF draws them: the widget's
    background and border (/MK; ``box`` False: neither), upright for the page's rotation
    (/MK/R: drawn in the turned frame + /Matrix). ``inner(width, height) -> list of
    operators`` draws the content. Returns its xref."""
    x0, y0, x1, y1 = (float(v) for v in re.findall(r"-?[\d.]+", doc.xref_get_key(xref, "Rect")[1])[:4])
    width, height = abs(x1 - x0), abs(y1 - y0)
    kind, rot = doc.xref_get_key(xref, "MK/R")
    rotation = int(float(rot)) % 360 if kind in ("int", "float") and rot.strip("-").replace(".", "").isdigit() else 0
    if rotation in (90, 270):
        width, height = height, width
    ops = []
    background, border = _mk_color(doc, xref, "BG"), _mk_color(doc, xref, "BC")
    if box and background:
        ops.append(f"q {background} rg 0 0 {width:.2f} {height:.2f} re f Q")
    if box and border:
        ops.append(f"q 1 w {border} RG 0.5 0.5 {width - 1:.2f} {height - 1:.2f} re s Q")
    ops += inner(width, height)
    ap = doc.get_new_xref()
    doc.update_object(
        ap,
        f"<< /Type /XObject /Subtype /Form /BBox [0 0 {width:.2f} {height:.2f}] "
        f"/Matrix [{_ROTATION_MATRIX.get(rotation, _ROTATION_MATRIX[0])}] "
        f"/Resources << /Font << /Helv {_helv_font_xref(doc)} 0 R >> >> >>",
    )
    doc.update_stream(ap, "\n".join(ops).encode("latin-1", "replace"))
    return ap


def _write_widget_appearance(doc: pymupdf.Document, xref: int, inner) -> None:
    """Our own /AP /N for a widget (``_form_xobject``)."""
    doc.xref_set_key(xref, "AP", f"<< /N {_form_xobject(doc, xref, inner)} 0 R >>")


RADIO_TEXT_SIZE = 10.0  # a radio button's label when no size is set; the circle follows it


def _win_ansi(text: str) -> str:
    """Text for a Helvetica (WinAnsi) string in a content stream: PDF-escaped, with
    characters Helvetica can't show as "?" (the stream is written as Latin-1 bytes)."""
    raw = text.encode("cp1252", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_label(text: str, width: float, size: float) -> list[str]:
    """The label's lines: its own line breaks kept (pasted paragraphs), long lines
    wrapped at spaces to ``width`` points of Helvetica at ``size``."""
    lines: list[str] = []
    for paragraph in re.split(r"\r\n|\r|\n", text):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            trial = word if not line else f"{line} {word}"
            if line and pymupdf.get_text_length(trial, fontname="helv", fontsize=size) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def _circle(cx: float, cy: float, r: float) -> str:
    k = 0.5523 * r  # (Bezier control distance for a quarter circle)
    return (f"{cx + r:.2f} {cy:.2f} m {cx + r:.2f} {cy + k:.2f} {cx + k:.2f} {cy + r:.2f} {cx:.2f} {cy + r:.2f} c "
            f"{cx - k:.2f} {cy + r:.2f} {cx - r:.2f} {cy + k:.2f} {cx - r:.2f} {cy:.2f} c "
            f"{cx - r:.2f} {cy - k:.2f} {cx - k:.2f} {cy - r:.2f} {cx:.2f} {cy - r:.2f} c "
            f"{cx + k:.2f} {cy - r:.2f} {cx + r:.2f} {cy - k:.2f} {cx + r:.2f} {cy:.2f} c h")


def radio_layout(width: float, height: float, label: str, size: float):
    """Where a radio button's circle and label lines go in its box (PDF coordinates, y up):
    (circle center x, center y, radius, [(text x, baseline y, line), …]). The circle stays
    the size of the text (user 2026-10-02: it shouldn't grow with a big box), at the left
    of the first line; the label is to its right, never over it."""
    size = size or RADIO_TEXT_SIZE
    diameter = min(size * 1.15, width, height)
    r = diameter / 2
    if not label:
        return width / 2, height / 2, r, []
    text_x = diameter + max(3.0, size * 0.4) + 1
    lines = wrap_label(label, max(width - text_x - 2, size), size)
    leading = size * 1.2
    first = height - 1 - size * 0.8  # baseline of the first line (top-aligned)
    if len(lines) == 1 and height < leading * 2:  # a one-line box: centered up and down
        first = (height - size * 0.7) / 2
    cy = min(first + size * 0.33, height - r - 0.5)
    placed = []
    for n, line in enumerate(lines):
        baseline = first - n * leading
        if baseline < -size * 0.2:
            break  # (the rest doesn't fit in the box)
        placed.append((text_x, baseline, line))
    return r + 0.5, cy, r, placed


def radio_fit_height(width: float, label: str, size: float) -> float:
    """The height a radio button needs for its text at this width and size (as
    ``radio_layout`` draws it: first baseline 0.8 size below the top, a line every 1.2
    size, room for the last line's descenders), never less than its circle."""
    size = size or RADIO_TEXT_SIZE
    diameter = size * 1.15
    if not label:
        return diameter + 1
    text_x = diameter + max(3.0, size * 0.4) + 1
    lines = wrap_label(label, max(width - text_x - 2, size), size)
    return max(diameter + 1, (len(lines) - 1) * size * 1.2 + size * 1.05 + 2)


def radio_min_width(size: float) -> float:
    """Narrower than this, a radio button's text has no room next to its circle."""
    size = size or RADIO_TEXT_SIZE
    return size * 1.15 + max(3.0, size * 0.4) + 1 + size * 3


def draw_radio_button(doc: pymupdf.Document, xref: int, label: str, size: float, on_name: str | None) -> None:
    """A radio button made by PlanetPDF: a small circle and its label text, both inside the
    widget (so it shows in every viewer and prints, and clicking the text picks it), in an
    Off and an On look."""
    size = size or RADIO_TEXT_SIZE

    def look(on: bool):
        def inner(width: float, height: float) -> list[str]:
            cx, cy, r, lines = radio_layout(width, height, label, size)
            ops = [f"q 0 0 {width:.2f} {height:.2f} re W n",
                   f"q 1 g 0.35 G 1 w {_circle(cx, cy, max(r - 0.5, 0.5))} B Q"]
            if on:
                ops.append(f"q 0.15 g {_circle(cx, cy, r * 0.45)} f Q")
            for x, y, line in lines:
                if line:
                    ops.append(f"BT 0 g /Helv {size:.2f} Tf {x:.2f} {y:.2f} Td ({_win_ansi(line)}) Tj ET")
            ops.append("Q")
            return ops

        return _form_xobject(doc, xref, inner, box=False)

    off, on = look(False), look(True)
    name = _pdf_name(on_name or "Yes")
    doc.xref_set_key(xref, "AP", f"<< /N << {name} {on} 0 R /Off {off} 0 R >> >>")


def draw_checkbox(doc: pymupdf.Document, xref: int, label: str, size: float, on_name: str | None) -> None:
    """A checkbox made by PlanetPDF (user 2026-10-04: "the checkbox doesn't allow me to edit
    the text that is shown"): a small square and its text, both inside the widget like our
    radio buttons (``radio_layout``), so it shows and prints everywhere and clicking the
    text ticks it; an Off look and an On look with a check mark."""
    size = size or RADIO_TEXT_SIZE

    def look(on: bool):
        def inner(width: float, height: float) -> list[str]:
            cx, cy, r, lines = radio_layout(width, height, label, size)
            side = max(2 * r - 1, 1.0)
            x0, y0 = cx - side / 2, cy - side / 2
            ops = [f"q 0 0 {width:.2f} {height:.2f} re W n",
                   f"q 1 g 0.35 G 1 w {x0:.2f} {y0:.2f} {side:.2f} {side:.2f} re B Q"]
            if on:  # a check mark
                ops.append(f"q 0.15 G {max(side * 0.12, 0.8):.2f} w 1 J 1 j {x0 + side * 0.2:.2f} {y0 + side * 0.5:.2f} m "
                           f"{x0 + side * 0.42:.2f} {y0 + side * 0.22:.2f} l {x0 + side * 0.82:.2f} {y0 + side * 0.8:.2f} l "
                           "S Q")
            for x, y, line in lines:
                if line:
                    ops.append(f"BT 0 g /Helv {size:.2f} Tf {x:.2f} {y:.2f} Td ({_win_ansi(line)}) Tj ET")
            ops.append("Q")
            return ops

        return _form_xobject(doc, xref, inner, box=False)

    off, on = look(False), look(True)
    name = _pdf_name(on_name or "Yes")
    doc.xref_set_key(xref, "AP", f"<< /N << {name} {on} 0 R /Off {off} 0 R >> >>")
    doc.xref_set_key(xref, "MK/CA", "(4)")  # (other editors draw it again with a check mark: ZapfDingbats "4")


def set_checkbox_state(doc: pymupdf.Document, xref: int, on_name: str | None, on: bool) -> None:
    """Tick / untick one of our checkboxes: /AS on the widget, /V (a name) on its field."""
    name = _pdf_name(on_name or "Yes") if on else "/Off"
    doc.xref_set_key(xref, "AS", name)
    holder = _parent_xref(doc, xref) or xref
    if doc.xref_get_key(holder, "T")[0] == "null":  # (a kid without its own name: the field is the parent)
        holder = xref
    doc.xref_set_key(holder, "V", name)


LIST_HIGHLIGHT = "0.16 0.40 0.78"  # a chosen list item (white text on it): clearly visible


def _draw_list_appearance(doc: pymupdf.Document, xref: int, labels: list[str], selected: set[int],
                          multiselect: bool = False) -> None:
    """Our own /AP for a list box: items top to bottom, chosen ones on a strong blue with
    white text (PyMuPDF shows no selection at all, and can't do multiple selections). A
    list that allows several choices shows a check box in front of each (user 2026-10-02:
    "it isn't super obvious how to select multiple items")."""
    _, da = doc.xref_get_key(xref, "DA")
    size_match = re.search(r"([\d.]+)\s+Tf", da or "")
    font_size = float(size_match.group(1)) if size_match and float(size_match.group(1)) > 0 else 11.0
    line = font_size * 1.15

    def inner(width: float, height: float) -> list[str]:
        ops = [f"/Tx BMC q 1 1 {width - 2:.2f} {height - 2:.2f} re W n"]
        box = font_size * 0.75 if multiselect else 0.0
        text_x = 2 + (box + 4 if multiselect else 0)
        for i, label in enumerate(labels):
            top = height - 1 - i * line
            if top - line < 0:
                break
            chosen = i in selected
            if chosen:
                ops.append(f"{LIST_HIGHLIGHT} rg 1 {top - line:.2f} {width - 2:.2f} {line:.2f} re f")
            if multiselect:
                y = top - line + (line - box) / 2
                ops.append(f"q 1 g {'1 G' if chosen else '0.35 G'} 0.8 w 3 {y:.2f} {box:.2f} {box:.2f} re B Q")
                if chosen:  # a check mark in the box
                    ops.append(f"q {LIST_HIGHLIGHT} RG 1.2 w 1 J 1 j {3 + box * 0.2:.2f} {y + box * 0.5:.2f} m "
                               f"{3 + box * 0.42:.2f} {y + box * 0.22:.2f} l {3 + box * 0.82:.2f} {y + box * 0.8:.2f} l S Q")
            text = pymupdf.get_pdf_str(label)
            ops.append(f"BT {'1 g' if chosen else '0 g'} /Helv {font_size:.2f} Tf {text_x:.2f} "
                       f"{top - font_size:.2f} Td {text} Tj ET")
        ops.append("Q EMC")
        return ops

    _write_widget_appearance(doc, xref, inner)


def draw_empty_signature(doc: pymupdf.Document, xref: int) -> None:
    """An unsigned signature field: its box with a line to sign on and "Sign here" (MuPDF's
    own "SIGN" tag isn't turned with the page and has no box)."""

    def inner(width: float, height: float) -> list[str]:
        size = max(3.0, min(8.0, height * 0.22))
        base = max(size * 1.4, height * 0.28)
        return [f"q 0.55 0.55 0.55 RG 0.6 w 4 {base:.2f} m {width - 4:.2f} {base:.2f} l S Q",
                f"BT 0.45 0.45 0.45 rg /Helv {size:.2f} Tf 4 {base - size - 1:.2f} Td (Sign here) Tj ET"]

    _write_widget_appearance(doc, xref, inner)


def button_parts(page: pymupdf.Page, box, label: str, size: float):
    """Where a labeled radio button / checkbox's parts go, for its whole area ``box``
    (unrotated): (the circle / box's rect, unrotated; the text's rect, unrotated, or None;
    the text lines as (x, top-down baseline, line) inside the text's rect; its width and
    height as displayed). Same layout as before (``radio_layout``), so nothing moves."""
    size = size or RADIO_TEXT_SIZE
    shown = (pymupdf.Rect(box) * page.rotation_matrix).normalize()
    cx, cy, r, lines = radio_layout(shown.width, shown.height, label, size)
    # (radio_layout works upright, y up from the bottom of the area)
    circle = pymupdf.Rect(shown.x0 + cx - r, shown.y1 - cy - r, shown.x0 + cx + r, shown.y1 - cy + r)
    if not lines:
        return tuple((circle * page.derotation_matrix).normalize()), None, [], 0.0, 0.0
    text_x = lines[0][0]
    width, height = max(shown.width - text_x, 1.0), shown.height
    text = pymupdf.Rect(shown.x0 + text_x, shown.y0, shown.x1, shown.y1)
    placed = [(x - text_x, height - baseline, line) for x, baseline, line in lines]
    return (tuple((circle * page.derotation_matrix).normalize()), tuple((text * page.derotation_matrix).normalize()),
            placed, width, height)


def _label_spec(name: str, button: str | None) -> str:
    import json

    return json.dumps({"field": name, "button": button or ""}, separators=(",", ":"))


def remove_labels(session: PdfSession, name: str) -> int:
    """Take a field's text off the pages (its marks; every page: it may have moved)."""
    import json

    from planetpdf.core import page_marks

    def mine(spec: str) -> bool:
        try:
            return json.loads(spec).get("field") == name
        except (ValueError, AttributeError):
            return False

    return page_marks.remove_marks(session, LABEL_KIND, where=mine)


def _place_label(session: PdfSession, page_index: int, name: str, button: str | None, rect, lines, width: float,
                 height: float, size: float) -> None:
    from planetpdf.core import page_marks

    src = pymupdf.open()
    sheet = src.new_page(width=width, height=height)
    for x, baseline, line in lines:
        if line:
            sheet.insert_text((x, baseline), line, fontname="helv", fontsize=size or RADIO_TEXT_SIZE, color=(0, 0, 0))
    data = src.tobytes(deflate=True)
    src.close()
    page_marks.place_mark(session, page_index, rect, data, LABEL_KIND, _label_spec(name, button))


def _pdf_rect(page: pymupdf.Page, rect) -> str:
    r = pymupdf.Rect(rect) * ~page.transformation_matrix  # (PyMuPDF's top-down space -> PDF space)
    r = r.normalize()
    return f"[{r.x0:.3f} {r.y0:.3f} {r.x1:.3f} {r.y1:.3f}]"


def _draw_labeled_buttons(session: PdfSession, f: FormField) -> None:
    """Our labeled radio buttons / checkbox the standard way: each widget only its circle /
    box (drawn like before, Off and On), its text page text beside it (user 2026-10-04)."""
    doc = session.doc
    remove_labels(session, f.name)
    for ref in f.widgets:
        if ref.label is None:
            continue
        page = doc[ref.page]
        size = da_size(doc, ref.xref) or RADIO_TEXT_SIZE
        box = ref.rect  # (the whole area: BOX_KEY, or the old widget rect of an older file)
        circle, text_rect, lines, width, height = button_parts(page, box, ref.label, size)
        set_label_box(doc, ref.xref, box)
        doc.xref_set_key(ref.xref, "Rect", _pdf_rect(page, circle))
        if f.kind is FieldKind.CHECKBOX:
            draw_checkbox(doc, ref.xref, "", size, ref.on_state)
            set_checkbox_state(doc, ref.xref, ref.on_state, bool(f.value))
        else:
            draw_radio_button(doc, ref.xref, "", size, ref.on_state)
            on = f.value is not None and f.value == ref.on_state
            doc.xref_set_key(ref.xref, "AS", _pdf_name(ref.on_state) if on and ref.on_state else "/Off")
        if text_rect is not None:
            _place_label(session, ref.page, f.name, ref.on_state if f.kind is FieldKind.RADIO else None, text_rect,
                         lines, width, height, size)


def old_style_buttons(session: PdfSession) -> list[str]:
    """Fields with our radio buttons / checkboxes the old way (their text inside the widget,
    from before 2026-10-04): no BOX_KEY yet."""
    doc = session.doc
    return [f.name for f in list_fields(session) if f.kind in (FieldKind.RADIO, FieldKind.CHECKBOX)
            and any(ref.label is not None and label_box(doc, ref.xref) is None for ref in f.widgets)]


def upgrade_old_buttons(session: PdfSession) -> int:
    """Store them the standard way (``_draw_labeled_buttons``): nothing moves on the page."""
    names = old_style_buttons(session)
    for name in names:
        redraw(session, name)
    if names:
        session.mark_modified()
    return len(names)


def redraw(session: PdfSession, name: str) -> None:
    """Draw a field again after PyMuPDF's ``update()`` (a move, new properties): list boxes
    keep their highlighted choices, unsigned signature fields their box."""
    f = get_field(session, name)
    doc = session.doc
    if f.kind in (FieldKind.RADIO, FieldKind.CHECKBOX) and any(ref.label is not None for ref in f.widgets):
        _draw_labeled_buttons(session, f)
    for ref in f.widgets:
        if f.kind is FieldKind.LIST:
            chosen = [] if f.value in (None, "") else ([f.value] if isinstance(f.value, str) else list(f.value))
            _draw_list_appearance(doc, ref.xref, f.labels or f.options,
                                  {f.options.index(v) for v in chosen if v in f.options}, f.multiselect)
        elif f.kind is FieldKind.SIGNATURE and doc.xref_get_key(ref.xref, "V")[0] in ("null", ""):
            draw_empty_signature(doc, ref.xref)
        elif f.kind is FieldKind.BUTTON:
            from planetpdf.core.forms import buttons

            action = buttons.button_action(doc, ref.xref)
            if action == "image":  # (its picture, not MuPDF's empty button)
                buttons.set_image(session, ref.page, ref.xref, buttons.stored_picture(doc, ref.xref))
            elif action == "barcode":
                from planetpdf.core.forms import barcodes

                barcodes.draw(session, ref.page, ref.xref)
        elif f.kind in (FieldKind.CHECKBOX, FieldKind.RADIO) and ref.label is not None:
            pass  # (ours: drawn above, the standard way)


def da_size(doc: pymupdf.Document, xref: int) -> float:
    """A widget's text size from its /DA (0 = automatic)."""
    _, da = doc.xref_get_key(xref, "DA")
    match = re.search(r"([\d.]+)\s+Tf", da or "")
    return float(match.group(1)) if match else 0.0


def _normalize(f: FormField, value):
    if f.kind in (FieldKind.TEXT, FieldKind.DATE):
        text = "" if value is None else str(value)
        if f.max_length and len(text) > f.max_length:
            text = text[: f.max_length]
        return text
    if f.kind is FieldKind.CHECKBOX:
        return bool(value)
    if f.kind is FieldKind.RADIO:
        if value in (None, "", False, "Off"):
            return None
        states = {w.on_state for w in f.widgets}
        if value not in states:
            raise PdfError(f"{value!r} isn't an option of {f.name}.")
        return value
    if f.kind is FieldKind.COMBO:
        text = "" if value is None else str(value)
        if text and not f.editable_combo and text not in f.options:
            raise PdfError(f"{text!r} isn't an option of {f.name}.")
        return text
    if f.kind is FieldKind.LIST:
        items = [] if value in (None, "") else ([value] if isinstance(value, str) else list(value))
        unknown = [v for v in items if v not in f.options]
        if unknown:
            raise PdfError(f"{unknown[0]!r} isn't an option of {f.name}.")
        return items if f.multiselect else items[:1]
    return value


def empty_value(f: FormField):
    return {
        FieldKind.CHECKBOX: False,
        FieldKind.RADIO: None,
        FieldKind.LIST: [],
    }.get(f.kind, "")


def fields_by_page(fields: Iterable[FormField]) -> dict[int, list[tuple[FormField, WidgetRef]]]:
    out: dict[int, list[tuple[FormField, WidgetRef]]] = {}
    for f in fields:
        for ref in f.widgets:
            out.setdefault(ref.page, []).append((f, ref))
    return out


def tab_stops(session: PdfSession, fields: Iterable[FormField] | None = None) -> list[tuple[str, int]]:
    """(field name, widget xref) for every fillable widget, in tab order: page by page,
    each page ordered by its /Tabs rule. Read-only fields are skipped."""
    fields = list(fields) if fields is not None else list_fields(session)
    fillable = {f.name for f in fields if f.kind.fillable and not f.read_only}
    stops = []
    doc = session.doc
    for index in range(doc.page_count):
        page = doc[index]
        for widget in _tab_order(page, list(page.widgets())):
            name = widget.field_name or f"(unnamed {widget.xref})"
            if name in fillable:
                stops.append((name, widget.xref))
    return stops
