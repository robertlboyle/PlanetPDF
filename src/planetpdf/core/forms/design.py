"""Prepare Form: create, change, move and delete form fields.

A field is described by a ``FieldSpec`` (everything needed to recreate it), so undo
is "delete and recreate from the spec". Moving/resizing a widget and changing a
field's properties are done in place, so fields made by other software keep their
scripts and appearance settings.

PyMuPDF quirks handled here (see CLAUDE.md): radio buttons can't be created with
``add_widget`` (we create checkboxes and turn them into kids of one radio field);
deleting kids leaves the parent in ``/AcroForm/Fields``; widgets on rotated pages
need ``/MK/R`` so their text reads upright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import pymupdf

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind

RectTuple = tuple[float, float, float, float]

_WIDGET_TYPE = {
    FieldKind.TEXT: pymupdf.PDF_WIDGET_TYPE_TEXT,
    FieldKind.DATE: pymupdf.PDF_WIDGET_TYPE_TEXT,
    FieldKind.CHECKBOX: pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    FieldKind.COMBO: pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    FieldKind.LIST: pymupdf.PDF_WIDGET_TYPE_LISTBOX,
    FieldKind.SIGNATURE: pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
    FieldKind.BUTTON: pymupdf.PDF_WIDGET_TYPE_BUTTON,
}
CREATABLE = [FieldKind.TEXT, FieldKind.DATE, FieldKind.CHECKBOX, FieldKind.RADIO, FieldKind.COMBO, FieldKind.LIST, FieldKind.SIGNATURE,
             FieldKind.BUTTON]
BUTTON_FILL = (0.86, 0.86, 0.86)  # buttons: light gray, like Acrobat's
DRAWN_BUTTONS = ("image", "barcode")  # (buttons whose picture PlanetPDF draws: no caption, white)
_RADIO_FLAGS = (1 << 15) | (1 << 14)  # Radio | NoToggleToOff
FIELD_BORDER = (0.35, 0.35, 0.35)  # new fields: dark gray border…
FIELD_FILL = (1.0, 1.0, 1.0)  # …on white
_NAME_OK = re.compile(r"^[^.()\\]+$")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    page: int
    rect: RectTuple  # unrotated; for radio groups the first button
    options: tuple[str, ...] = ()  # dropdown/list choices; radio: each button's value
    option_rects: tuple[RectTuple, ...] = ()  # radio: one rect per option (same page)
    value: object = None
    tooltip: str = ""
    required: bool = False
    read_only: bool = False
    multiline: bool = False
    password: bool = False  # (a copy of a password field stays one: security review 2026-11-25)
    multiselect: bool = False
    editable_combo: bool = False
    max_length: int = 0
    date_format: str = ""
    font_size: float = 0.0  # 0 = auto
    extra_widgets: tuple[tuple[int, RectTuple], ...] = field(default=())  # a field shown in several places
    # radio: each button's visible text, drawn next to its circle (may be paragraphs; "" =
    # none). () = a group made by other software (keeps its own look).
    labels: tuple[str, ...] = ()
    # buttons (core.forms.buttons): the caption, and what a click does: "", "reset", "print",
    # "url:<address>", "image" (an image field: the filler picks its picture) or "other"
    caption: str = ""
    action: str = ""
    # a barcode field (action "barcode"): its ``barcodes.BarcodeSettings`` (None = the defaults)
    barcode: object = None

    def validate(self) -> None:
        if not self.name.strip() or not _NAME_OK.match(self.name):
            raise PdfError("A field needs a name without dots, brackets, or backslashes.")
        if self.kind in (FieldKind.COMBO, FieldKind.LIST) and not self.options:
            raise PdfError("Add at least one choice.")
        if self.kind is FieldKind.RADIO:
            if len(self.options) < 2 or len(self.options) != len(self.option_rects):
                raise PdfError("A radio group needs at least two buttons.")
            if len(set(self.options)) != len(self.options) or not all(o.strip() for o in self.options):
                raise PdfError("Each radio button needs its own value.")
            if self.labels and len(self.labels) != len(self.options):
                raise PdfError("Each radio button needs its own text (it may be empty).")
        if self.kind is FieldKind.DATE and not self.date_format:
            raise PdfError("Choose a date format.")
        if self.kind is FieldKind.BUTTON and self.action.startswith("url:") and not self.action[4:].strip():
            raise PdfError("Type the web address the button opens.")


def _flags(spec: FieldSpec) -> int:
    flags = 0
    if spec.read_only:
        flags |= F.FF_READ_ONLY
    if spec.required:
        flags |= F.FF_REQUIRED
    if spec.multiline and spec.kind is FieldKind.TEXT:
        flags |= F.FF_MULTILINE
    if spec.password and spec.kind is FieldKind.TEXT:
        flags |= F.FF_PASSWORD
    if spec.multiselect and spec.kind is FieldKind.LIST:
        flags |= F.FF_MULTISELECT
    if spec.editable_combo and spec.kind is FieldKind.COMBO:
        flags |= F.FF_COMBO_EDIT
    return flags


def field_names(session: PdfSession) -> set[str]:
    return {f.name for f in F.list_fields(session)}


def unique_name(session: PdfSession, base: str) -> str:
    names = field_names(session)
    base = re.sub(r"[.()\\]", "_", base) or "Field"
    n = 1
    while f"{base}{n}" in names:
        n += 1
    return f"{base}{n}"


# --- creating ------------------------------------------------------------------------------------


def _upright(doc: pymupdf.Document, page: pymupdf.Page, xref: int) -> None:
    if page.rotation:
        doc.xref_set_key(xref, "MK/R", str(page.rotation))


def _add_widget(session: PdfSession, spec: FieldSpec, index: int, rect: RectTuple, kind: FieldKind, name: str) -> int:
    doc = session.doc
    page = doc[index]
    w = pymupdf.Widget()
    w.field_type = _WIDGET_TYPE[kind]
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.field_flags = _flags(spec)
    w.text_fontsize = spec.font_size
    # A visible box (user bug 2026-10-02: an empty dropdown or unchecked radio button
    # drew nothing at all): thin dark border on white, like Acrobat's fields with a border.
    w.border_color = FIELD_BORDER
    w.border_width = 1
    drawn = kind is FieldKind.BUTTON and spec.action in DRAWN_BUTTONS  # (its picture is PlanetPDF's)
    w.fill_color = BUTTON_FILL if kind is FieldKind.BUTTON and not drawn else FIELD_FILL
    if kind is FieldKind.BUTTON and not drawn:
        w.button_caption = spec.caption or "Button"
    if spec.tooltip:
        w.field_label = spec.tooltip
    if kind in (FieldKind.TEXT, FieldKind.DATE):
        w.text_maxlen = spec.max_length
    if kind is FieldKind.DATE:
        w.script_format = f'AFDate_FormatEx("{spec.date_format}");'
        w.script_stroke = f'AFDate_KeystrokeEx("{spec.date_format}");'
    if kind in (FieldKind.COMBO, FieldKind.LIST):
        w.choice_values = list(spec.options)
    if kind is FieldKind.CHECKBOX:
        w.field_value = bool(spec.value)
    elif kind in (FieldKind.TEXT, FieldKind.DATE, FieldKind.COMBO) and spec.value:
        w.field_value = str(spec.value)
    widget = page.add_widget(w)
    xref = widget.xref
    if kind is FieldKind.BUTTON and spec.action == "image":
        from planetpdf.core.forms import buttons

        del widget
        turned = page.rotation
        buttons.set_button_action(doc, xref, spec.action)
        buttons.set_image(session, index, xref, None)  # (adds and deletes a page: `page` is stale)
        if turned:
            _upright(doc, doc[index], xref)
        return xref
    if kind is FieldKind.BUTTON and spec.action == "barcode":
        from planetpdf.core.forms import barcodes, buttons

        del widget
        turned = page.rotation
        buttons.set_button_action(doc, xref, "barcode")
        barcodes.set_settings(doc, xref, spec.barcode or barcodes.BarcodeSettings())
        barcodes.draw(session, index, xref)  # (adds and deletes a page: `page` is stale)
        if turned:
            _upright(doc, doc[index], xref)
        return xref
    if page.rotation:
        _upright(doc, page, xref)
        page = doc[index]
        widget = F._load_widget(page, xref)
        widget.update()
    if kind is FieldKind.BUTTON:  # (after update(), which drops the action)
        from planetpdf.core.forms import buttons

        buttons.set_button_action(doc, xref, spec.action)
    if spec.kind is FieldKind.CHECKBOX and spec.labels:  # ours: its box and its text (user 2026-10-04)
        # (spec.kind: radio buttons start out as checkboxes, with the group's labels)
        doc.xref_set_key(xref, F.LABEL_KEY, pymupdf.get_pdf_str(spec.labels[0]))
        F.set_label_box(doc, xref, rect)  # (the area: create_field's redraw puts the box and its text in it)
    return xref


def _acro_fields(doc: pymupdf.Document) -> list[int]:
    _kind, value = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
    if value.startswith("[") or _kind == "array":
        return [int(x) for x in re.findall(r"(\d+) 0 R", value)]
    if _kind == "xref":  # indirect array
        return [int(x) for x in re.findall(r"(\d+) 0 R", doc.xref_object(int(value.split()[0]), compressed=True))]
    return []


def _set_acro_fields(doc: pymupdf.Document, refs: list[int]) -> None:
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")


def _drop_keys(doc: pymupdf.Document, xref: int, keys: list[str]) -> None:
    """Really remove simple dictionary keys (``xref_set_key(.., "null")`` leaves ``/k null``)."""
    source = doc.xref_object(xref, compressed=True)
    for key in keys:
        source = re.sub(rf"/{key}(?=[\s/(\[<])\s*(\((?:\\.|[^\\)])*\)|/[^\s/<>\[\]()]+|-?[\d.]+(?:\s+0\s+R)?|null|true|false)", "", source)
    doc.update_object(xref, source)


def _create_radio(session: PdfSession, spec: FieldSpec) -> None:
    doc = session.doc
    kids = []
    for n, (option, rect) in enumerate(zip(spec.options, spec.option_rects)):
        temp = replace(spec, value=None)
        kids.append((_add_widget(session, temp, spec.page, rect, FieldKind.CHECKBOX, f"{spec.name}__{n}"), option))
    parent = doc.get_new_xref()
    value = F._pdf_name(spec.value) if spec.value in spec.options else "/Off"
    tooltip = f" /TU {pymupdf.get_pdf_str(spec.tooltip)}" if spec.tooltip else ""
    refs = " ".join(f"{x} 0 R" for x, _ in kids)
    doc.update_object(parent, f"<< /FT /Btn /Ff {_RADIO_FLAGS | _flags(spec)} /T {pymupdf.get_pdf_str(spec.name)} /V {value}{tooltip} /Kids [{refs}] >>")
    for xref, option in kids:
        on = F._pdf_name(option)
        for key in ("AP/N", "AP/D"):
            kind, ap = doc.xref_get_key(xref, key)
            if kind == "dict":
                doc.xref_set_key(xref, key, ap.replace("/Yes", on))
        doc.xref_set_key(xref, "AS", on if value == on else "/Off")
        _drop_keys(doc, xref, ["T", "FT", "Ff", "V", "TU"])
        doc.xref_set_key(xref, "Parent", f"{parent} 0 R")
    kid_xrefs = {x for x, _ in kids}
    _set_acro_fields(doc, [r for r in _acro_fields(doc) if r not in kid_xrefs] + [parent])
    # The kids were made as checkboxes (square): draw them as radio buttons, each with its
    # label beside a text-sized circle (user 2026-10-02: the circle grew with the box and
    # the text you typed never showed), with the group's value.
    labels = spec.labels or ("",) * len(kids)
    for (xref, _option), label, rect in zip(kids, labels, spec.option_rects):
        doc.xref_set_key(xref, F.LABEL_KEY, pymupdf.get_pdf_str(label))
        F.set_label_box(doc, xref, rect)  # (the area: redraw puts the circle and its text in it)
        doc.xref_set_key(xref, "MK/CA", "(l)")  # (ZapfDingbats circle: other editors redraw it round)
    F.set_field_value(session, spec.name, spec.value if spec.value in spec.options else None)
    F.redraw(session, spec.name)


def fit_radio(session: PdfSession, spec: FieldSpec, *, top_left: tuple[float, float] | None = None,
              width: float | None = None) -> FieldSpec:
    """A radio group made by PlanetPDF, sized to its text (user 2026-10-02: "there is no
    reason to stretch it out way bigger than the text and font need"): every button as
    wide as the first (or ``width``), as tall as its wrapped text needs, stacked from the
    first button's top-left (or ``top_left``) with a gap that follows the text size. All
    in the page as displayed (``top_left`` / ``width`` too), so turned pages work the same.
    Groups from other software (no labels) and groups across pages are left as they are."""
    if spec.kind is not FieldKind.RADIO or not spec.labels or not spec.option_rects:
        return spec
    page = session.doc[spec.page]
    shown = [(pymupdf.Rect(r) * page.rotation_matrix).normalize() for r in spec.option_rects]
    size = spec.font_size or F.RADIO_TEXT_SIZE
    x0, y0 = top_left if top_left is not None else (shown[0].x0, shown[0].y0)
    w = max(width if width is not None else shown[0].width, F.radio_min_width(size))
    gap = size * 0.6
    rects, y = [], y0
    for label in spec.labels:
        h = F.radio_fit_height(w, label, size)
        rects.append(tuple((pymupdf.Rect(x0, y, x0 + w, y + h) * page.derotation_matrix).normalize()))
        y += h + gap
    return replace(spec, rect=rects[0], option_rects=tuple(rects))


CHECKBOX_TEXT_WIDTH = 180.0  # points: a checkbox given text gets at least this wide (like a new radio button)


def fit_checkbox(session: PdfSession, spec: FieldSpec) -> FieldSpec:
    """A checkbox of ours with its text (user 2026-10-04), sized to that text like our radio
    buttons: as wide as it is (wider when it was just a small box and now has words), as
    tall as the wrapped text needs; its top-left corner stays. Without text: a square box
    the size of the text. Others' checkboxes are left as they are."""
    if spec.kind is not FieldKind.CHECKBOX or not spec.labels:
        return spec
    page = session.doc[spec.page]
    shown = (pymupdf.Rect(spec.rect) * page.rotation_matrix).normalize()
    size = spec.font_size or F.RADIO_TEXT_SIZE
    label = spec.labels[0]
    x0, y0 = shown.x0, shown.y0
    if label.strip():
        w = max(shown.width if shown.width > size * 3 else CHECKBOX_TEXT_WIDTH, F.radio_min_width(size))
        room = page.rect.width - x0 - 4  # (kept on the page: narrower, or moved left)
        if w > room:
            w, x0 = (room, x0) if room >= max(F.radio_min_width(size), 100) else (w, max(4.0, page.rect.width - w - 4))
        h = F.radio_fit_height(w, label, size)
    else:
        w = h = size * 1.15 + 1
    y0 = max(4.0, min(y0, page.rect.height - h - 4))
    rect = (pymupdf.Rect(x0, y0, x0 + w, y0 + h) * page.derotation_matrix).normalize()
    return replace(spec, rect=tuple(rect))


def create_field(session: PdfSession, spec: FieldSpec) -> None:
    spec.validate()
    if spec.name in field_names(session):
        raise PdfError(f"There is already a field named {spec.name!r}.")
    if spec.kind is FieldKind.RADIO:
        _create_radio(session, spec)
    elif spec.kind in _WIDGET_TYPE:
        _add_widget(session, spec, spec.page, spec.rect, spec.kind, spec.name)
        for index, rect in spec.extra_widgets:
            _add_widget(session, spec, index, rect, spec.kind, spec.name)
    else:
        raise PdfError(f"Can't create {spec.kind.value} fields.")
    if spec.kind is FieldKind.LIST and spec.value:
        F.set_field_value(session, spec.name, spec.value)
    F.redraw(session, spec.name)  # (list boxes, signature fields: our own appearance)
    session.mark_modified()


# --- reading back / deleting ----------------------------------------------------------------------


def field_spec(session: PdfSession, name: str) -> FieldSpec:
    f = F.get_field(session, name)
    doc = session.doc
    first = f.widgets[0]
    widget = F._load_widget(doc[first.page], first.xref)
    tooltip = (widget.field_label or "") if widget else ""
    size = (widget.text_fontsize or 0.0) if widget else 0.0
    if f.kind is FieldKind.BUTTON:
        from planetpdf.core.forms import buttons

        from planetpdf.core.forms import barcodes

        action = buttons.button_action(doc, first.xref)
        caption = (widget.button_caption or "") if widget else ""
        return FieldSpec(f.name, f.kind, first.page, first.rect, tooltip=tooltip, read_only=f.read_only, font_size=size,
                         caption=caption if action not in DRAWN_BUTTONS else "", action=action,
                         barcode=barcodes.settings(doc, first.xref) if action == "barcode" else None,
                         extra_widgets=tuple((w.page, w.rect) for w in f.widgets[1:]))
    common = dict(name=f.name, kind=f.kind, page=first.page, rect=first.rect, value=f.value, tooltip=tooltip,
                  required=f.required, read_only=f.read_only, multiline=f.multiline, password=f.password,
                  multiselect=f.multiselect,
                  editable_combo=f.editable_combo, max_length=f.max_length, date_format=f.date_format, font_size=size)
    if f.kind is FieldKind.RADIO:
        ours = all(w.label is not None for w in f.widgets)
        return FieldSpec(options=tuple(w.on_state or f"Choice{n + 1}" for n, w in enumerate(f.widgets)),
                         option_rects=tuple(w.rect for w in f.widgets),
                         labels=tuple(w.label or "" for w in f.widgets) if ours else (), **common)
    labels = (first.label,) if f.kind is FieldKind.CHECKBOX and first.label is not None else ()
    if labels:  # (ours: the text size is the text's, as drawn)
        common["font_size"] = F.da_size(doc, first.xref)
    return FieldSpec(options=tuple(f.options), extra_widgets=tuple((w.page, w.rect) for w in f.widgets[1:]),
                     labels=labels, **common)


def delete_field(session: PdfSession, name: str) -> FieldSpec:
    """Delete a field (all its widgets); returns its spec so it can be recreated."""
    spec = field_spec(session, name)
    f = F.get_field(session, name)
    doc = session.doc
    parents = {p for w in f.widgets if (p := F._parent_xref(doc, w.xref)) is not None}
    if any(ref.label is not None for ref in f.widgets):
        F.remove_labels(session, name)  # (its text on the page goes with it)
    for ref in f.widgets:
        page = doc[ref.page]
        widget = F._load_widget(page, ref.xref)
        if widget is not None:
            page.delete_widget(widget)
    if parents:  # PyMuPDF leaves the parent field behind
        _set_acro_fields(doc, [r for r in _acro_fields(doc) if r not in parents])
    session.mark_modified()
    return spec


def replace_field(session: PdfSession, old_name: str, spec: FieldSpec) -> FieldSpec:
    """Delete ``old_name`` and create ``spec`` (used when the kind or buttons change)."""
    old = delete_field(session, old_name)
    try:
        create_field(session, spec)
    except PdfError:
        create_field(session, old)
        raise
    return old


# --- in place -------------------------------------------------------------------------------------


def move_widget(session: PdfSession, page_index: int, xref: int, rect: RectTuple) -> RectTuple:
    """Move/resize one widget; returns its previous rect."""
    doc = session.doc
    page = doc[page_index]
    widget = F._load_widget(page, xref)
    if widget is None:
        raise PdfError("That field is no longer on the page.")
    old = tuple(widget.rect)
    name = widget.field_name
    if F.radio_label(doc, xref) is not None:  # ours with text: the area moves; redraw lays it out
        old = F.label_box(doc, xref) or old
        F.set_label_box(doc, xref, rect)
        widget = None
        F.redraw(session, name)
        session.mark_modified()
        return old
    _, state = doc.xref_get_key(xref, "AS")
    action = doc.xref_get_key(xref, "A")
    widget.rect = pymupdf.Rect(rect)
    widget.update()
    if action[0] != "null" and doc.xref_get_key(xref, "A")[0] == "null":
        doc.xref_set_key(xref, "A", action[1])  # (update() drops a button's action)
    if widget.field_type in (pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON, pymupdf.PDF_WIDGET_TYPE_CHECKBOX) and state.startswith("/"):
        # update() switched a moved radio button on (user bug 2026-10-02: two buttons on)
        doc.xref_set_key(xref, "AS", state)
    F.redraw(session, name)  # (lists, signature fields and our radio buttons: our own look)
    session.mark_modified()
    return old


def update_field(session: PdfSession, name: str, spec: FieldSpec) -> None:
    """Change a field's name and properties in place (same kind and buttons)."""
    spec.validate()
    f = F.get_field(session, name)
    if spec.kind is not f.kind:
        raise PdfError("Changing the kind of field means recreating it.")
    if spec.name != name and spec.name in field_names(session):
        raise PdfError(f"There is already a field named {spec.name!r}.")
    doc = session.doc
    relabel = spec.name != name and any(ref.label is not None for ref in f.widgets)
    if relabel:  # (its text on the page is tagged with its name)
        F.remove_labels(session, name)
    if f.kind is FieldKind.RADIO and relabel:
        update_field(session, name, replace(spec, name=name))  # (the rest first, under the old name…)
        parent = F._parent_xref(doc, f.widgets[0].xref)
        doc.xref_set_key(parent if parent is not None else f.widgets[0].xref, "T", pymupdf.get_pdf_str(spec.name))
        F.redraw(session, spec.name)  # (…then renamed, and its text placed again)
        return
    if f.kind is FieldKind.RADIO:
        parent = F._parent_xref(doc, f.widgets[0].xref)
        holder = parent if parent is not None else f.widgets[0].xref
        doc.xref_set_key(holder, "T", pymupdf.get_pdf_str(spec.name))
        doc.xref_set_key(holder, "Ff", str(_RADIO_FLAGS | _flags(spec)))
        if spec.tooltip:
            doc.xref_set_key(holder, "TU", pymupdf.get_pdf_str(spec.tooltip))
        else:
            _drop_keys(doc, holder, ["TU"])
        session.mark_modified()
        return
    if f.kind is FieldKind.BUTTON:
        from planetpdf.core.forms import buttons

        was = buttons.button_action(doc, f.widgets[0].xref)
        if was != spec.action and (was in DRAWN_BUTTONS or spec.action in DRAWN_BUTTONS):
            delete_field(session, name)  # (a button became an image field or a barcode, or back)
            create_field(session, spec)
            return
    keep = (F.FF_READ_ONLY | F.FF_REQUIRED | F.FF_MULTILINE | F.FF_MULTISELECT | F.FF_COMBO_EDIT)
    for ref in f.widgets:
        page = doc[ref.page]
        widget = F._load_widget(page, ref.xref)
        if widget is None:
            continue
        widget.field_name = spec.name
        widget.field_flags = ((widget.field_flags or 0) & ~keep) | _flags(spec)
        widget.field_label = spec.tooltip or None
        widget.text_fontsize = spec.font_size
        if f.kind in (FieldKind.TEXT, FieldKind.DATE):
            widget.text_maxlen = spec.max_length
        if f.kind is FieldKind.DATE and spec.date_format != f.date_format:
            widget.script_format = f'AFDate_FormatEx("{spec.date_format}");'
            widget.script_stroke = f'AFDate_KeystrokeEx("{spec.date_format}");'
        if f.kind in (FieldKind.COMBO, FieldKind.LIST):
            widget.choice_values = list(spec.options)
        if f.kind is FieldKind.BUTTON and spec.action not in DRAWN_BUTTONS:
            widget.button_caption = spec.caption or "Button"
        widget.update()
        if f.kind is FieldKind.BUTTON:
            from planetpdf.core.forms import barcodes, buttons

            buttons.set_button_action(doc, ref.xref, spec.action)
            if spec.action == "barcode":  # (F.redraw below draws it with these)
                barcodes.set_settings(doc, ref.xref, spec.barcode or barcodes.BarcodeSettings())
    F.redraw(session, spec.name)
    session.mark_modified()
