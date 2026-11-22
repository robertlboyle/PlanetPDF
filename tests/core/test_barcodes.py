"""Barcode fields (user 2026-10-23: QR + PDF417, drawn by PlanetPDF from the form's values)."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core.document import PdfSession
from planetpdf.core.forms import barcodes as B
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.design import FieldSpec, create_field, field_spec, move_widget, update_field
from planetpdf.core.forms.fields import FieldKind


@pytest.fixture
def session(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "form.pdf")
    s = PdfSession.open(tmp_path / "form.pdf")
    create_field(s, FieldSpec("Name", FieldKind.TEXT, 0, (72, 72, 300, 90)))
    create_field(s, FieldSpec("Member", FieldKind.CHECKBOX, 0, (72, 100, 84, 112)))
    yield s
    s.close()


def _barcode(session, symbology="qr", rect=(320, 72, 470, 222), **kw):
    create_field(session, FieldSpec("Code", FieldKind.BUTTON, 0, rect, action="barcode",
                                    barcode=B.BarcodeSettings(symbology, **kw)))
    return next(w.xref for w in F.get_field(session, "Code").widgets)


def _drawn_modules(session, rect, size):
    """The field as drawn, sampled at the centers of a size x size grid of modules (quiet zone 2)."""
    zoom = 6
    pix = session.doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(rect), annots=True)
    samples = pix.samples
    inner = min(pix.width, pix.height) - 2 * zoom  # (1 pt white edge each side)
    module = inner / (size + 4)
    x0 = (pix.width - inner) / 2 + 2 * module
    y0 = (pix.height - inner) / 2 + 2 * module
    rows = []
    for r in range(size):
        row = []
        for c in range(size):
            x, y = int(x0 + (c + 0.5) * module), int(y0 + (r + 0.5) * module)
            row.append(samples[(y * pix.width + x) * pix.n] < 128)
        rows.append(row)
    return rows


def test_a_qr_code_holds_the_names_and_values_and_follows_them(session):
    import segno

    xref = _barcode(session)
    F.set_field_value(session, "Name", "Jane Doe")
    text = B.data_text(session, B.BarcodeSettings())
    assert text == "Name\tMember\nJane Doe\tOff"
    expected = [[bool(v) for v in row] for row in segno.make(text, error="m", micro=False).matrix]
    assert _drawn_modules(session, (320, 72, 470, 222), len(expected)) == expected
    before = session.doc.xref_get_key(xref, "AP/N")
    F.set_field_value(session, "Member", True)  # (another value: drawn again)
    assert session.doc.xref_get_key(xref, "AP/N") != before
    assert B.data_text(session, B.BarcodeSettings()).endswith("Jane Doe\tYes")


def test_chosen_fields_only_and_without_names(session):
    _barcode(session, fields=("Name",), names=False)
    F.set_field_value(session, "Name", "A\tB\nC")  # (tabs and new lines inside a value: spaces)
    assert B.data_text(session, field_spec(session, "Code").barcode) == "A B C"


def test_settings_survive_a_move_a_change_and_a_save(session, tmp_path):
    xref = _barcode(session, "pdf417", rect=(72, 300, 400, 380))
    spec = field_spec(session, "Code")
    assert spec.action == "barcode" and spec.barcode == B.BarcodeSettings("pdf417") and spec.caption == ""
    move_widget(session, 0, xref, (72, 400, 400, 480))
    assert B.settings(session.doc, xref) == B.BarcodeSettings("pdf417")
    update_field(session, "Code", spec.__class__(**{**spec.__dict__, "barcode": B.BarcodeSettings("qr", ("Name",))}))
    session.save()
    session.close()
    with pymupdf.open(tmp_path / "form.pdf") as doc:
        [(page, xref)] = B.barcode_widgets(doc)
        assert B.settings(doc, xref) == B.BarcodeSettings("qr", ("Name",))
        assert doc.xref_get_key(xref, "A")[0] in ("null", "")  # (a click does nothing)


def test_pdf417_rows_are_three_modules_tall():
    rows, row_height = B.modules("pdf417", "Name\tDate\nJane\t10/23/2026")
    assert row_height == 3 and len(rows) >= 3 and all(len(r) == len(rows[0]) for r in rows)


def test_too_much_data_says_so_in_the_box(session):
    with pytest.raises(B.BarcodeError, match="Too much data for a QR code"):
        B.modules("qr", "x" * 5000)
    create_field(session, FieldSpec("Notes", FieldKind.TEXT, 0, (72, 500, 500, 520)))
    _barcode(session)
    F.set_field_value(session, "Notes", "x" * 4000)
    text = session.doc[0].get_text(clip=pymupdf.Rect(320, 72, 470, 222))
    assert "Too much data" in " ".join(text.split())
