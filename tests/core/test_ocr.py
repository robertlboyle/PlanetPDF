from __future__ import annotations

import re

import pymupdf
import pytest

from planetpdf.core import ocr
from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionOptions, redact_to_file
from planetpdf.core.redaction.patterns import PRESETS
from planetpdf.core.redaction.sanitize import SanitizeOptions
from planetpdf.core.redaction.search import search_pages
from tests.fixtures import generate as g

pytestmark = pytest.mark.skipif(ocr.find_tessdata() is None, reason="Tesseract language data not installed")


@pytest.fixture
def scanned(pdf_copy):
    s = PdfSession.open(pdf_copy("scanned.pdf"))
    yield s
    s.close()


def _ocr_all(session):
    words = ocr.recognize_pages(session.doc.tobytes(), range(session.page_count))
    for index, page_words in words.items():
        ocr.insert_ocr_text(session, index, page_words)
    return words


def test_languages_found():
    assert "eng" in ocr.available_languages()
    assert "osd" not in ocr.available_languages()


def test_scanned_pages_need_ocr(scanned):
    assert all(ocr.page_needs_ocr(scanned.doc, i) for i in range(scanned.page_count))
    assert all(not scanned.doc[i].get_text().strip() for i in range(scanned.page_count))


@pytest.mark.parametrize("index", range(len(g.SCAN_ROTATIONS)))
def test_recognition_every_rotation(scanned, index):
    words = ocr.recognize_pages(scanned.doc.tobytes(), [index])[index]
    texts = [w.text for w in words]
    assert g.SSN in texts and g.EMAIL in texts, (scanned.doc[index].rotation, texts)


@pytest.mark.parametrize("index", range(len(g.SCAN_ROTATIONS)))
def test_inserted_text_is_aligned_and_invisible(scanned, index):
    words = ocr.recognize_pages(scanned.doc.tobytes(), [index])[index]
    before = scanned.doc[index].get_pixmap(dpi=50).samples
    ocr.insert_ocr_text(scanned, index, words)
    page = scanned.doc[index]
    hit = page.search_for(g.SSN)
    word = next(w for w in words if w.text == g.SSN)
    assert hit, "OCR text not searchable"
    found, expected = hit[0], pymupdf.Rect(word.rect)
    # Aligned along the text direction; the box covers the printed word.
    assert (found & expected).get_area() >= 0.9 * expected.get_area()
    assert page.get_pixmap(dpi=50).samples == before  # invisible: looks identical
    assert ocr.page_has_ocr(scanned.doc, index)
    assert not ocr.page_needs_ocr(scanned.doc, index)


def test_ocr_text_survives_save_and_undo_removes_it(scanned):
    _ocr_all(scanned)
    scanned.save()
    assert g.SSN in scanned.doc[0].get_text()
    assert ocr.remove_ocr_text(scanned, range(scanned.page_count)) == scanned.page_count
    assert all(not scanned.doc[i].get_text().strip() for i in range(scanned.page_count))
    assert not any(ocr.page_has_ocr(scanned.doc, i) for i in range(scanned.page_count))


def test_ocr_then_redact_scan(scanned, tmp_path):
    """The point of OCR for redaction: find the SSN on a scan and remove the pixels."""
    _ocr_all(scanned)
    for hit in search_pages(scanned, PRESETS["ssn"].pattern):
        M.add_mark(scanned, M.make_spec(hit.page, hit.rects, "SSN"))
    assert len(M.list_marks(scanned)) == len(g.SCAN_ROTATIONS)
    out = tmp_path / "scan_redacted.pdf"
    result = redact_to_file(scanned, out, RedactionOptions(), SanitizeOptions())
    assert result.report.passed, result.report.as_text()
    # Re-OCR the output: the SSN image pixels must be gone on every page.
    check = PdfSession.open(out)
    words = ocr.recognize_pages(check.doc.tobytes(), range(check.page_count))
    for index, page_words in words.items():
        texts = [w.text for w in page_words]
        assert g.SSN not in texts, f"SSN still visible in the image on page {index + 1}"
        assert g.EMAIL in texts  # the rest of the scan is intact
    check.close()


def test_progress_can_cancel(scanned):
    seen = []

    def progress(done, total):
        seen.append(done)
        return done < 1

    words = ocr.recognize_pages(scanned.doc.tobytes(), range(4), progress=progress)
    assert list(words) == [0]


def _outlined(tmp_path, real_line: str = ""):
    """A page whose lines are drawn as outlines (MuPDF's SVG with text as paths, back to PDF),
    with an optional line of real text above them."""
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    for n, line in enumerate(["Quarterly Report 2026", "Revenue grew by twelve percent",
                              "Customer accounts reached a new high"]):
        page.insert_text((72, 160 + 30 * n), line, fontsize=16)
    svg = pymupdf.open(stream=page.get_svg_image(text_as_path=True).encode(), filetype="svg")
    outlines = pymupdf.open("pdf", svg.convert_to_pdf())
    doc = pymupdf.open()
    target = doc.new_page(width=612, height=792)
    target.show_pdf_page(target.rect, outlines, 0)
    if real_line:
        target.insert_text((72, 100), real_line, fontsize=16)
    doc.save(tmp_path / "outlined.pdf")
    return PdfSession.open(tmp_path / "outlined.pdf")


def test_outlined_text_is_found_and_made_searchable(tmp_path):
    """User 2026-10-23 ("searchable only"): words drawn as outlines need text recognition like a
    scan; afterwards search finds them."""
    s = _outlined(tmp_path)
    try:
        assert not s.doc[0].get_text().strip() and ocr.page_needs_ocr(s.doc, 0)
        _ocr_all(s)
        assert not ocr.page_needs_ocr(s.doc, 0)
        pattern = re.compile("twelve", re.IGNORECASE)
        assert len(search_pages(s, pattern)) == 1
    finally:
        s.close()


def test_a_page_partly_in_outlines_finds_each_word_once(tmp_path):
    s = _outlined(tmp_path, real_line="Annual summary for members")
    try:
        assert ocr.page_needs_ocr(s.doc, 0)  # (real text above, outlines below)
        _ocr_all(s)
        assert len(search_pages(s, re.compile("summary", re.IGNORECASE))) == 1  # (not found twice)
        assert len(search_pages(s, re.compile("Revenue", re.IGNORECASE))) == 1
    finally:
        s.close()


def test_drawings_and_plain_text_pages_are_not_outlined_text():
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    chart, text = doc[0], doc[1]  # (fetched after adding pages: adding one invalidates Page objects)
    for n in range(80):  # (a scatter chart's dots: small shapes, but not lined up like text)
        x, y = 72 + (n * 37) % 460, 100 + (n * 53) % 500
        chart.draw_circle((x, y), 2, color=None, fill=(0.2, 0.4, 0.8))
    for n in range(20):
        text.insert_text((72, 80 + 20 * n), "An ordinary line of real text on the page", fontsize=11)
    assert not ocr.page_has_outlined_text(doc[0]) and not ocr.page_needs_ocr(doc, 0)
    assert not ocr.page_has_outlined_text(doc[1]) and not ocr.page_needs_ocr(doc, 1)
