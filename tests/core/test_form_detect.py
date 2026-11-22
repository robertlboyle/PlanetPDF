from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core.document import PdfSession
from planetpdf.core.form_detect import add_detected_fields, detect_fields
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind


def _form_page(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Name: ____________________________", fontsize=11)
    page.insert_text((72, 140), "Date:", fontsize=11)
    page.draw_line((110, 142), (260, 142))
    page.insert_text((72, 180), "Signature:", fontsize=11)
    page.draw_line((140, 182), (320, 182))
    page.draw_rect((72, 210, 82, 220))
    page.insert_text((88, 219), "I agree", fontsize=11)
    page.insert_text((72, 260), "Unit Types:", fontsize=11)
    for y in (290, 320, 350):
        page.draw_line((72, y), (400, y))
    page.insert_text((72, 400), "Underlined words", fontsize=11)
    page.draw_line((72, 402), (170, 402))
    page.insert_text((72, 440), "Comments:", fontsize=11)
    page.draw_rect((72, 450, 400, 530))
    doc.save(path)
    return path


@pytest.fixture
def session(tmp_path):
    s = PdfSession.open(_form_page(tmp_path / "form.pdf"))
    yield s
    s.close()


def test_finds_blanks_and_names_them(session):
    found = {spec.name: spec for spec in detect_fields(session.doc, [0], signatures=True)}
    assert found["Name"].kind is FieldKind.TEXT
    assert found["Date"].kind is FieldKind.DATE
    assert found["Signature"].kind is FieldKind.SIGNATURE
    assert found["I agree"].kind is FieldKind.CHECKBOX
    assert {"Unit Types 1", "Unit Types 2", "Unit Types 3"} <= set(found)
    assert found["Comments"].multiline
    assert not any("Underlined" in name for name in found)  # (a line with words on it)


def test_without_signatures_signature_lines_are_text(session):
    found = {spec.name: spec for spec in detect_fields(session.doc, [0], signatures=False)}
    assert found["Signature"].kind is FieldKind.TEXT


def test_fields_are_made_once(session):
    found = detect_fields(session.doc, [0], signatures=True)
    assert add_detected_fields(session, found) == len(found)
    names = {f.name for f in F.list_fields(session)}
    assert {"Name", "Date", "I agree"} <= names
    assert detect_fields(session.doc, [0], signatures=True) == []  # (already fields)


def test_progress_counts_pages(session):
    calls = []
    detect_fields(session.doc, [0], progress=lambda *a: calls.append(a) or True)
    assert calls[0][:2] == (0, 1)


def test_a_resume_finds_nothing(tmp_path):
    """User 2026-10-04: a resume's section rules, link underline and shaded strips became
    fields ("it's a resume so it shouldn't find anything")."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "Daryl Schmoldt", fontsize=20)
    page.insert_text((72, 104), "Columbia, NJ | (908) 432-5492 | daryl@example.com", fontsize=10)
    page.draw_line((290, 106), (380, 106), color=(0, 0, 1))  # (the link's underline)
    page.draw_rect((72, 128, 540, 129), color=None, fill=(0.6, 0.6, 0.6))  # (a section rule, as a thin box)
    page.insert_text((72, 150), "Summary", fontsize=13)
    page.draw_line((72, 156), (540, 156), color=(0.6, 0.6, 0.6))  # (a rule under a heading)
    page.insert_text((72, 172), "Senior developer with twenty years of experience.", fontsize=10)
    page.draw_rect((72, 180, 540, 200), color=None, fill=(0.85, 0.85, 0.9))  # (a shaded strip)
    page.insert_text((72, 230), "Skills:", fontsize=11)
    page.insert_text((72, 246), "Python, C#, SQL, Azure", fontsize=10)
    page.draw_rect((72, 270, 80, 278), color=(0, 0, 0))  # (a square bullet, no words beside it)
    doc.save(tmp_path / "resume.pdf")
    with pymupdf.open(tmp_path / "resume.pdf") as d:
        assert detect_fields(d, [0]) == []


def test_colon_and_question_with_space_after_them(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Phone:", fontsize=11)
    page.insert_text((72, 140), "Why do you want this job?", fontsize=11)
    page.insert_text((72, 180), "Employer: Planet Networks", fontsize=11)  # (already answered)
    page.insert_text((540, 220), ".", fontsize=11)  # (the text's right edge)
    doc.save(tmp_path / "f.pdf")
    with pymupdf.open(tmp_path / "f.pdf") as d:
        names = {s.name for s in detect_fields(d, [0])}
    assert "Why do you want this job" in names and "Employer" not in " ".join(names)
    assert "Phone" in names  # ("Phone:" with empty space to the margin)


def _detect(tmp_path, draw) -> dict:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    draw(page)
    page.insert_text((540, 760), ".", fontsize=11)  # (the text's right edge)
    doc.save(tmp_path / "f.pdf")
    with pymupdf.open(tmp_path / "f.pdf") as d:
        return {s.name: s for s in detect_fields(d, [0])}


def _grid(page, x0, y0, cols, rows, cells, w=100, h=20):
    for r in range(rows + 1):
        page.draw_line((x0, y0 + r * h), (x0 + cols * w, y0 + r * h))
    for c in range(cols + 1):
        page.draw_line((x0 + c * w, y0), (x0 + c * w, y0 + rows * h))
    for (r, c), text in cells.items():
        page.insert_text((x0 + c * w + 4, y0 + r * h + 14), text, fontsize=10)


def test_empty_cells_of_a_form_table_become_fields(tmp_path):
    """User 2026-10-23: a table with a header row and rows to fill in; a price list (every
    cell filled) stays as it is, and the table's rules aren't answer lines."""
    def draw(page):
        _grid(page, 72, 100, 3, 4, {(0, 0): "Item", (0, 1): "Qty", (0, 2): "Date",
                                    (1, 0): "Widget", (1, 1): "2", (1, 2): "10/01/2026", (2, 0): "Gadget"})
        _grid(page, 72, 300, 2, 3, {(0, 0): "Product", (0, 1): "Price", (1, 0): "A", (1, 1): "$1",
                                    (2, 0): "B", (2, 1): "$2"})

    found = _detect(tmp_path, draw)
    assert {"Qty Gadget", "Date Gadget", "Item 3", "Qty 3", "Date 3"} <= set(found)
    assert found["Date Gadget"].kind is FieldKind.DATE
    assert len(found) == 5  # (only those: no price-list cells, no fields on the table's rules)
    qty = found["Qty Gadget"].rect
    assert 172 < qty[0] < 175 and 140 < qty[1] < 143  # (inside its cell)


def test_known_form_labels_need_no_colon(tmp_path):
    """User 2026-10-23: "Date of Birth" with room after it is a blank, like "Phone:"; but only
    the usual form labels, and never a heading with its content under it."""
    def draw(page):
        page.insert_text((72, 100), "Date of Birth", fontsize=11)
        page.insert_text((72, 140), "Email Address", fontsize=11)
        page.insert_text((72, 180), "Favorite Color", fontsize=11)  # (not a form label we know)
        page.insert_text((72, 220), "Address", fontsize=13)  # (a heading: its content under it)
        page.insert_text((72, 238), "12 Main Street, Columbia NJ", fontsize=10)
        page.insert_text((72, 280), "Name", fontsize=11)
        page.insert_text((300, 280), "Amount", fontsize=11)  # (a table's headings: side by side)

    found = _detect(tmp_path, draw)
    assert found["Date of Birth"].kind is FieldKind.DATE and "Email Address" in found
    assert not {"Favorite Color", "Address", "Name", "Amount"} & set(found)


def test_placeholders_become_fields(tmp_path):
    def draw(page):
        page.insert_text((72, 100), "Start date  MM/DD/YYYY", fontsize=11)
        page.insert_text((72, 140), "Cell  (___) ___-____", fontsize=11)
        page.insert_text((72, 180), "SSN  XXX-XX-XXXX", fontsize=11)
        page.insert_text((72, 220), "Deposit  $______", fontsize=11)
        page.insert_text((72, 260), "Date of Birth (MM/DD/YYYY): ____________________", fontsize=11)
        page.insert_text((72, 300), "Office  (   )    -", fontsize=11)

    found = _detect(tmp_path, draw)
    assert found["Start date"].kind is FieldKind.DATE and found["Start date"].rect[2] - found["Start date"].rect[0] >= 72
    assert found["Cell"].kind is FieldKind.TEXT and "SSN" in found and "Deposit" in found
    assert found["Date of Birth"].kind is FieldKind.DATE  # (the hint named nothing; the blank after it did)
    assert not any("MM" in name for name in found)
    assert not any("Office" in name and s.kind is FieldKind.CHECKBOX for name, s in found.items())
