"""Create Multiple PDF Files, PDF Portfolio, Recognize Text in Multiple Files (Phase C)."""

from __future__ import annotations

import os

import pymupdf
import pytest
from PIL import Image

from planetpdf.core import batch as B
from planetpdf.core import ocr
from planetpdf.core import windows_office as office


def _pdf(path, text):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), text, fontname="helv")
    doc.save(path)
    return path


def test_create_multiple_pdfs_converts_each_and_reports_failures(tmp_path):
    Image.new("RGB", (200, 100), "red").save(tmp_path / "photo.png")
    (tmp_path / "notes.txt").write_text("Plain text notes", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    _pdf(tmp_path / "already.pdf", "Already a PDF")
    seen = []
    results = B.create_multiple([tmp_path / n for n in ("photo.png", "notes.txt", "data.bin", "already.pdf")],
                                tmp_path / "out", lambda d, t, s="": seen.append(f"{s} {d + 1} of {t}") or True)
    by_name = {r.source.name: r for r in results}
    assert by_name["photo.png"].output.name == "photo.pdf"
    assert "Plain text notes" in pymupdf.open(by_name["notes.txt"].output)[0].get_text()
    assert by_name["data.bin"].output is None and "can't turn" in by_name["data.bin"].error
    assert by_name["already.pdf"].output.exists()
    assert "Converting file 2 of 4" in seen
    again = B.create_multiple([tmp_path / "photo.png"], tmp_path / "out")
    assert again[0].output.name == "photo (2).pdf"  # (never over an existing file)


@pytest.mark.skipif(not os.environ.get("PLANETPDF_TEST_OFFICE"),
                    reason="drives the real Microsoft Office (and its printer driver): set PLANETPDF_TEST_OFFICE=1")
@pytest.mark.skipif(not office.available("excel"), reason="Microsoft Excel not installed")
def test_office_files_through_office(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    book.active["A1"] = "Invoice total"
    book.save(tmp_path / "sheet.xlsx")
    [result] = B.create_multiple([tmp_path / "sheet.xlsx"], tmp_path / "out")
    assert result.output and "Invoice total" in pymupdf.open(result.output)[0].get_text()


def test_portfolio_keeps_the_files_unchanged(tmp_path):
    a = _pdf(tmp_path / "a.pdf", "First")
    Image.new("RGB", (50, 50), "blue").save(tmp_path / "b.png")
    target = B.make_portfolio([a, tmp_path / "b.png", a], tmp_path / "bundle.pdf", title="Project files")
    with pymupdf.open(target) as doc:
        assert B.is_portfolio(doc)
        names = [n for n, _size in B.attachments(doc)]
        assert names == ["a.pdf", "b.png", "a (2).pdf"]
        assert doc.embfile_get("b.png") == (tmp_path / "b.png").read_bytes()
        assert "Project files" in doc[0].get_text() and "b.png" in doc[0].get_text()


@pytest.mark.skipif(ocr.find_tessdata() is None, reason="Tesseract language data not installed")
def test_ocr_in_multiple_files(tmp_path):
    # a "scan": a picture of text, no text layer
    page_doc = pymupdf.open()
    page = page_doc.new_page(width=612, height=792)
    page.insert_text((72, 200), "INVOICE NUMBER 4471", fontname="helv", fontsize=28)
    pix = page.get_pixmap(dpi=150)
    scan = pymupdf.open()
    scan.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), pixmap=pix)
    scan.save(tmp_path / "scan.pdf")
    _pdf(tmp_path / "text.pdf", "Has text already")
    seen = []
    results = B.ocr_files([tmp_path / "scan.pdf", tmp_path / "text.pdf"], tmp_path / "ocr",
                          progress=lambda d, t, s="": seen.append(s) or True)
    scanned, plain = results
    assert "INVOICE" in pymupdf.open(scanned.output)[0].get_text().upper()
    assert plain.error.startswith("no scanned pages")
    assert any(s.startswith("File 1 of 2 (scan.pdf): recognizing text on page") for s in seen)
