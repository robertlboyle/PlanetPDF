"""Barcode fields (Prepare Form ▸ Barcode; user 2026-10-23, "Yes, QR + PDF417").

Acrobat draws its barcode fields with its own paid plug-in. PlanetPDF's is a push button
(like an image field, ``buttons``) marked ``/PPDFBarcode`` with its settings as JSON: the
kind of barcode (``qr``: a QR code, segno; ``pdf417``: PDF417, pdf417gen), the fields it
encodes (none listed = every field that can be filled in, in tab order) and whether the
field names come first. Its picture is drawn by PlanetPDF, in vector modules (sharp at any
zoom and when printed), every time a field's value changes (``fields.set_field_value``) and
after a move; other PDF viewers show the last picture drawn but don't redraw it.

What it holds (Acrobat's tab-delimited format): the field names, then their values, each
separated by tabs, on two lines (just the values without the names). A tab or a new line
inside a value becomes a space.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pymupdf

from planetpdf.core.document import PdfError, PdfSession

BARCODE_KEY = "PPDFBarcode"
SYMBOLOGIES = {"qr": "QR code", "pdf417": "PDF417"}
PDF417_COLUMNS = 6
PDF417_ROW_HEIGHT = 3  # (each PDF417 row is this many modules tall: the usual 1:3)
QUIET = {"qr": 2, "pdf417": 2}  # (white modules around the code, so scanners find its edges)


class BarcodeError(PdfError):
    pass


@dataclass(frozen=True)
class BarcodeSettings:
    symbology: str = "qr"
    fields: tuple[str, ...] = field(default=())  # () = every fillable field
    names: bool = True  # (a first line of field names)

    def to_json(self) -> str:
        return json.dumps({"symbology": self.symbology, "fields": list(self.fields), "names": self.names})

    @classmethod
    def from_json(cls, text: str) -> BarcodeSettings:
        data = json.loads(text)
        symbology = data.get("symbology", "qr")
        return cls(symbology if symbology in SYMBOLOGIES else "qr", tuple(data.get("fields") or ()),
                   bool(data.get("names", True)))


def settings(doc: pymupdf.Document, xref: int) -> BarcodeSettings | None:
    """A barcode field's settings (None: not one of ours)."""
    kind, value = doc.xref_get_key(xref, BARCODE_KEY)
    if kind != "string":
        return None
    try:
        return BarcodeSettings.from_json(value)
    except (ValueError, TypeError):
        return BarcodeSettings()


def set_settings(doc: pymupdf.Document, xref: int, value: BarcodeSettings | None) -> None:
    doc.xref_set_key(xref, BARCODE_KEY, "null" if value is None else pymupdf.get_pdf_str(value.to_json()))


# --- what it holds -------------------------------------------------------------------------------------------


def _value_text(f) -> str:
    from planetpdf.core.forms.fields import FieldKind

    value = f.value
    if f.kind is FieldKind.CHECKBOX:
        return "Yes" if value else "Off"
    if isinstance(value, (list, tuple)):
        value = ";".join(str(v) for v in value)
    return "" if value in (None, False) else str(value)


def _cell(text: str) -> str:
    return " ".join(str(text).replace("\t", " ").splitlines())


def data_text(session: PdfSession, value: BarcodeSettings) -> str:
    """What the barcode holds: names, then values, tab-separated (see the module doc)."""
    from planetpdf.core.forms.fields import list_fields

    fillable = {f.name: f for f in list_fields(session) if f.kind.fillable}
    chosen = [fillable[n] for n in value.fields if n in fillable] if value.fields else list(fillable.values())
    values = "\t".join(_cell(_value_text(f)) for f in chosen)
    return ("\t".join(_cell(f.name) for f in chosen) + "\n" + values) if value.names else values


def modules(symbology: str, text: str) -> tuple[list[list[bool]], int]:
    """The barcode's dark modules, row by row, and how many modules tall each row is drawn."""
    try:
        if symbology == "pdf417":
            import pdf417gen

            image = pdf417gen.render_image(pdf417gen.encode(text, columns=PDF417_COLUMNS), scale=1, ratio=1, padding=0)
            gray = image.convert("L")
            width, height = gray.size
            pixels = gray.load()
            return [[pixels[x, y] < 128 for x in range(width)] for y in range(height)], PDF417_ROW_HEIGHT
        import segno

        code = segno.make(text, error="m", micro=False)
        return [[bool(v) for v in row] for row in code.matrix], 1
    except Exception as exc:  # (segno's DataOverflowError, pdf417gen's ValueError: too much data)
        raise BarcodeError(f"Too much data for a {SYMBOLOGIES.get(symbology, symbology)}: choose fewer fields"
                           + (", or PDF417" if symbology == "qr" else "") + ".") from exc


# --- drawing --------------------------------------------------------------------------------------------------


def _draw_modules(page: pymupdf.Page, area: pymupdf.Rect, rows: list[list[bool]], row_height: int, quiet: int) -> None:
    columns = len(rows[0]) + 2 * quiet
    tall = len(rows) * row_height + 2 * quiet
    size = min(area.width / columns, area.height / tall)  # (one module, square; kept in proportion)
    x0 = area.x0 + (area.width - columns * size) / 2 + quiet * size
    y0 = area.y0 + (area.height - tall * size) / 2 + quiet * size
    shape = page.new_shape()
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            top = y0 + r * row_height * size
            shape.draw_rect(pymupdf.Rect(x0 + start * size, top, x0 + c * size, top + row_height * size))
    shape.finish(color=None, fill=(0, 0, 0), width=0)
    shape.commit()


def draw(session: PdfSession, page_index: int, xref: int) -> None:
    """Draw a barcode field's picture from the form's values now (upright on turned pages)."""
    from planetpdf.core.appearance import _embed_form, rotate_pdf

    doc = session.doc
    value = settings(doc, xref)
    if value is None:
        return
    page = doc[page_index]
    rect = next((w.rect for w in page.widgets() if w.xref == xref), None)
    if rect is None:
        return
    shown = (rect * page.rotation_matrix).normalize()
    src = pymupdf.open()
    sheet = src.new_page(width=max(shown.width, 1), height=max(shown.height, 1))
    sheet.draw_rect(sheet.rect, color=None, fill=(1, 1, 1))
    try:
        rows, row_height = modules(value.symbology, data_text(session, value))
        _draw_modules(sheet, sheet.rect + (1, 1, -1, -1), rows, row_height, QUIET[value.symbology])
    except BarcodeError as exc:
        sheet.insert_textbox(sheet.rect + (3, 3, -3, -3), str(exc), fontsize=min(8, max(5, shown.height / 6)),
                             color=(0.6, 0.1, 0.1), align=1)
        sheet.draw_rect(sheet.rect, color=(0.6, 0.1, 0.1), width=1)
    data = src.tobytes(deflate=True)
    src.close()
    form, _w, _h = _embed_form(doc, rotate_pdf(data, page.rotation))
    doc.xref_set_key(xref, "AP", f"<< /N {form} 0 R >>")
    doc.xref_set_key(xref, "MK/I", "null")
    session.mark_modified()


def barcode_widgets(doc: pymupdf.Document) -> list[tuple[int, int]]:
    """(page, xref) of every barcode field's widget (a quick look: no field list)."""
    out = []
    for index in range(doc.page_count):
        for xref in doc[index].annot_xrefs():
            if xref[1] == pymupdf.PDF_ANNOT_WIDGET and doc.xref_get_key(xref[0], BARCODE_KEY)[0] == "string":
                out.append((index, xref[0]))
    return out


def redraw_all(session: PdfSession) -> None:
    """Every barcode drawn again (after a value changed)."""
    for index, xref in barcode_widgets(session.doc):
        draw(session, index, xref)
