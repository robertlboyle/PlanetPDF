"""Redaction gaps found by the security review of 2026-11-25 (all confirmed in 1.0.5)."""

from __future__ import annotations

import json

import pymupdf
import pytest

from planetpdf import cli
from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionOptions, redact_to_file
from planetpdf.core.redaction.sanitize import SanitizeOptions

KEEP_EVERYTHING = SanitizeOptions(**{name: False for name in SanitizeOptions.__dataclass_fields__})
BOX = (100, 150, 330, 260)


def _page(tmp_path, draw, name="page.pdf"):
    path = tmp_path / name
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 500), "Plain text elsewhere", fontsize=12)
    draw(doc, page)
    doc.save(path)
    doc.close()
    return path


def _redact(path, out, rects=(BOX,), page=0, sanitize=KEEP_EVERYTHING):
    session = PdfSession.open(path)
    try:
        for rect in rects:
            M.add_mark(session, M.make_spec(page, [rect], "Area"))
        return redact_to_file(session, out, RedactionOptions(), sanitize).report
    finally:
        session.close()


def _lines(points):
    def draw(_doc, page):
        shape = page.new_shape()
        for a, b in zip(points, points[1:]):
            shape.draw_line(a, b)
        shape.finish(color=(0, 0, 0), width=1.5)
        shape.commit()
    return draw


def test_a_signature_drawn_as_straight_segments_crossing_the_box_fails(tmp_path):
    scribble = [(80, 240), (140, 170), (200, 250), (260, 160), (350, 230)]  # (sticks out on both sides)
    report = _redact(_page(tmp_path, _lines(scribble)), tmp_path / "out.pdf")
    assert not report.passed
    assert any("curved or slanted" in c.title for c in report.failures)


def test_a_zigzag_around_the_box_is_checked_and_fails(tmp_path):
    zigzag = [(90, 140), (215, 270), (340, 140), (90, 270), (340, 270)]  # (its frame encloses the box)
    report = _redact(_page(tmp_path, _lines(zigzag)), tmp_path / "out.pdf")
    assert any("curved or slanted" in c.title for c in report.failures)


def test_table_lines_crossing_the_box_only_warn(tmp_path):
    grid = [(60, 200), (400, 200)]  # (a level rule through the box)
    report = _redact(_page(tmp_path, _lines(grid)), tmp_path / "out.pdf")
    assert report.passed
    assert any("straight line" in c.title for c in report.warnings)


def test_keep_failed_never_writes_over_the_original(tmp_path, capsys):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Secret 123-45-6789", fontsize=12)
    path = tmp_path / "in.pdf"
    doc.save(path)
    before = path.read_bytes()
    code = cli.main(["planetpdf", "redact", str(path), "--out", str(path), "--find", "123-45-6789", "--keep-failed",
                     "--quiet"])
    assert code == cli.USAGE and "never written over the original" in capsys.readouterr().err
    assert path.read_bytes() == before


def _short_term_pdf(tmp_path, subject):
    def draw(doc, page):
        page.insert_text((72, 200), "Code XQZ here", fontsize=12)
        doc.set_metadata({"subject": subject})
    return _page(tmp_path, draw)


def test_a_short_term_left_in_the_metadata_fails(tmp_path):
    path = _short_term_pdf(tmp_path, "XQZ note")
    session = PdfSession.open(path)
    rect = session.doc[0].search_for("XQZ")[0]
    session.close()
    report = _redact(path, tmp_path / "out.pdf", rects=[tuple(rect)])
    assert any("(short) is still in the file" in c.title and "subject" in c.detail for c in report.failures)


def test_a_short_term_only_on_the_page_still_passes(tmp_path):
    path = _short_term_pdf(tmp_path, "XQZW note")  # (a longer word that merely starts the same)
    session = PdfSession.open(path)
    rect = session.doc[0].search_for("XQZ")[0]
    session.close()
    assert _redact(path, tmp_path / "out.pdf", rects=[tuple(rect)]).passed


def test_page_thumbnails_go_with_the_redaction(tmp_path):
    def draw(doc, page):
        page.insert_text((72, 200), "Thumbnail secret", fontsize=12)
        thumb = page.get_pixmap(dpi=20)
        holder = pymupdf.open()
        holder.new_page(width=10, height=10).insert_image(pymupdf.Rect(0, 0, 10, 10), pixmap=thumb)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                                "/BitsPerComponent 8 >>")
        doc.update_stream(xref, b"\x00")
        doc.xref_set_key(page.xref, "Thumb", f"{xref} 0 R")
    path = _page(tmp_path, draw)
    session = PdfSession.open(path)
    rect = session.doc[0].search_for("Thumbnail secret")[0]
    session.close()
    out = tmp_path / "out.pdf"
    report = _redact(path, out, rects=[tuple(rect)])
    assert report.passed
    with pymupdf.open(out) as doc:
        assert doc.xref_get_key(doc[0].xref, "Thumb")[0] == "null"


def test_nothing_matched_on_a_scanned_page_says_why(tmp_path, capsys):
    doc = pymupdf.open()
    picture = doc.new_page()
    picture.insert_text((72, 100), "123-45-6789", fontsize=20)
    pix = picture.get_pixmap(dpi=72)
    scan = pymupdf.open()
    scan.new_page().insert_image(scan[0].rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    scan.save(path)
    code = cli.main(["planetpdf", "redact", str(path), "--out", str(tmp_path / "out.pdf"), "--find", "123-45-6789",
                     "--json", "--quiet"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["marks"] == 0
    assert result["pages_without_text"] == [1] and "no text to search" in result["note"]


def test_an_unmarked_hit_doesnt_hide_the_term_in_an_attachment(tmp_path):
    def draw(doc, page):
        page.insert_text((72, 200), "First R2TERMTWO", fontsize=12)
        page.insert_text((72, 300), "Second R2TERMTWO", fontsize=12)
        doc.embfile_add("note.txt", b"R2TERMTWO in an attachment", filename="note.txt")
    path = _page(tmp_path, draw)
    session = PdfSession.open(path)
    first = session.doc[0].search_for("R2TERMTWO")[0]
    session.close()
    report = _redact(path, tmp_path / "out.pdf", rects=[tuple(first)])  # (only the first one marked)
    assert any("still appears where it wasn't marked" in c.title for c in report.warnings)
    assert any("can still be extracted" in c.title and "attachment" in c.detail for c in report.failures)
    assert not report.passed


@pytest.mark.parametrize("name", ["ok"])
def test_short_terms_and_whole_words(name):
    from planetpdf.core.redaction.verify import _outside_pages

    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Report XQZ_2 and XQZW"})
    assert _outside_pages(doc, "XQZ", whole_word=True) == ["document information (title)"]
    doc.set_metadata({"title": "XQZW only"})
    assert _outside_pages(doc, "XQZ", whole_word=True) == []
