"""Export To formats added in backlog Phase C (2026-10-02)."""

from __future__ import annotations

import os

import zipfile

import pymupdf
import pytest

from planetpdf.core import ghostscript
from planetpdf.core import windows_office as office
from planetpdf.core.export import ExportFormat, ExportOptions, export

needs_gs = pytest.mark.skipif(ghostscript.find() is None, reason="Ghostscript not installed")
needs_word = pytest.mark.skipif(
    not office.available("word") or not os.environ.get("PLANETPDF_TEST_OFFICE"),
    # (opt-in, user 2026-10-03: Office's save-as-PDF goes through the default printer's
    # driver, which put a print dialog on the user's screen during a test run, and Excel
    # once hung waiting on something hidden)
    reason="drives the real Microsoft Word: set PLANETPDF_TEST_OFFICE=1 (and have Word installed)")


@pytest.fixture
def doc(tmp_path):
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    page.insert_text((72, 80), "Quarterly Report", fontname="hebo", fontsize=18)
    page.insert_text((72, 120), "Revenue grew by twelve percent this quar-", fontname="helv", fontsize=11)
    page.insert_text((72, 134), "ter, led by fiber sales.", fontname="helv", fontsize=11)
    # a small ruled table: Item / Amount
    y = 200
    for row in [("Item", "Amount"), ("Fiber", "1,200"), ("Voice", "300")]:
        page.draw_rect(pymupdf.Rect(72, y, 272, y + 20), color=(0, 0, 0))
        page.draw_line((172, y), (172, y + 20), color=(0, 0, 0))
        page.insert_text((76, y + 14), row[0], fontname="helv", fontsize=10)
        page.insert_text((176, y + 14), row[1], fontname="helv", fontsize=10)
        y += 20
    d.new_page(width=612, height=792).insert_text((72, 80), "Second page", fontname="helv", fontsize=12)
    yield d
    d.close()


def _one(doc, fmt, path, pages=(0, 1), notes=None):
    [out] = export(doc, list(pages), path, fmt, ExportOptions(dpi=60), None, notes)
    assert out.exists() and out.stat().st_size > 0
    return out


def test_html(doc, tmp_path):
    text = _one(doc, ExportFormat.HTML, tmp_path / "r.html").read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>") and "Quarterly Report" in text and "Second page" in text
    assert text.count('class="page"') == 2


def test_xml(doc, tmp_path):
    import xml.etree.ElementTree as ET

    root = ET.parse(_one(doc, ExportFormat.XML, tmp_path / "r.xml")).getroot()
    assert root.tag == "document" and len(root.findall("page")) == 2
    spans = [s for s in root.iter("span")]
    assert any(s.text == "Quarterly Report" and s.get("bold") == "true" for s in spans)


def test_accessible_text_joins_lines_into_paragraphs(doc, tmp_path):
    text = _one(doc, ExportFormat.TXT_ACCESSIBLE, tmp_path / "r.txt").read_text(encoding="utf-8")
    assert text.startswith("Page 1") and "this quarter, led by fiber sales." in text  # (hyphen rejoined)


def test_excel_tables_become_cells(doc, tmp_path):
    from openpyxl import load_workbook

    book = load_workbook(_one(doc, ExportFormat.XLSX, tmp_path / "r.xlsx"))
    assert book.sheetnames == ["Page 1", "Page 2"]
    rows = [[c for c in row if c is not None] for row in book["Page 1"].iter_rows(values_only=True)]
    assert ["Fiber", 1200] in rows and ["Voice", 300] in rows  # (numbers as numbers)


def test_xml_spreadsheet_2003(doc, tmp_path):
    text = _one(doc, ExportFormat.XML_SPREADSHEET, tmp_path / "r.xml").read_text(encoding="utf-8")
    assert 'progid="Excel.Sheet"' in text and '<Data ss:Type="Number">1200</Data>' in text


def test_powerpoint_has_editable_text(doc, tmp_path):
    from pptx import Presentation

    deck = Presentation(str(_one(doc, ExportFormat.PPTX, tmp_path / "r.pptx")))
    assert len(deck.slides) == 2
    texts = [shape.text_frame.text for shape in deck.slides[0].shapes if shape.has_text_frame]
    assert any("Quarterly Report" in t for t in texts)


def test_jpeg2000(doc, tmp_path):
    from PIL import Image

    files = export(doc, [0, 1], tmp_path / "r.jp2", ExportFormat.JPX, ExportOptions(dpi=40))
    assert len(files) == 2
    with Image.open(files[0]) as image:
        assert image.format == "JPEG2000" and image.size[0] > 100


def test_raster_postscript_without_ghostscript(doc, tmp_path, monkeypatch):
    monkeypatch.setattr(ghostscript, "find", lambda: None)
    notes = []
    data = _one(doc, ExportFormat.PS, tmp_path / "r.ps", notes=notes).read_bytes()
    assert data.startswith(b"%!PS-Adobe-3.0") and data.count(b"showpage") == 2
    assert notes and "pictures" in notes[0]
    eps = _one(doc, ExportFormat.EPS, tmp_path / "r.eps", pages=(0,)).read_bytes()
    assert b"EPSF-3.0" in eps.splitlines()[0] and b"%%BoundingBox: 0 0 612 792" in eps


@needs_gs
def test_vector_postscript_with_ghostscript(doc, tmp_path):
    data = _one(doc, ExportFormat.PS, tmp_path / "r.ps").read_bytes()
    assert data.startswith(b"%!PS") and b"Quarterly" in data or b"/Font" in data or b"findfont" in data
    eps = _one(doc, ExportFormat.EPS, tmp_path / "r.eps", pages=(0,)).read_bytes()
    assert b"%%BoundingBox" in eps


@needs_gs
@pytest.mark.parametrize("standard", ["pdfa", "pdfx", "pdfe"])
def test_standards_through_ghostscript(doc, tmp_path, standard):
    seen = []
    target = tmp_path / f"{standard}.pdf"
    ghostscript.convert_standard(doc, target, standard, lambda d, t, s="": seen.append((d, t, s)) or True)
    assert any(s == "Converting page" and t == 2 for _d, t, s in seen)
    with pymupdf.open(target) as out:
        assert "Quarterly Report" in out[0].get_text()
        catalog = out.xref_object(out.pdf_catalog())
        xmp = out.get_xml_metadata()
        if standard == "pdfa":
            assert "/OutputIntents" in catalog and "pdfaid:part" in xmp.replace(' ', '') or "pdfaid" in xmp
        elif standard == "pdfx":
            assert "/OutputIntents" in catalog
        else:
            assert "PDF/E-1" in xmp and out.metadata["format"].endswith("1.6")


@needs_word
def test_word_97_2003_through_word(doc, tmp_path):
    """Through Microsoft Word. Where the organization's Office policy blocks the format
    (Planet Networks' Microsoft 365 policy does), the error says so plainly."""
    if office.word_97_blocked_by_policy():
        with pytest.raises(office.OfficeBlocked, match="policy"):
            export(doc, [0, 1], tmp_path / "r.doc", ExportFormat.DOC, ExportOptions(dpi=60))
        assert not (tmp_path / "r.doc").exists()
        return
    out = _one(doc, ExportFormat.DOC, tmp_path / "r.doc")
    assert out.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1")  # (the old binary Word format)
    assert not zipfile.is_zipfile(out)


@needs_word
def test_office_files_to_pdf(tmp_path):
    """Create Multiple PDF Files: Word (and Excel / PowerPoint) files through Office."""
    from docx import Document

    source = tmp_path / "letter.docx"
    d = Document()
    d.add_paragraph("Dear customer, your fiber is live.")
    d.save(source)
    office.to_pdf(source, tmp_path / "letter.pdf")
    with pymupdf.open(tmp_path / "letter.pdf") as out:
        assert "your fiber is live" in out[0].get_text()
