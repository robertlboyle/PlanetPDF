"""A file taken out of a PDF carries the PDF's "came from the internet" mark (security review
2026-11-27: a saved attachment had none, so Windows opened even a .cmd without warning)."""

from __future__ import annotations

import sys

import pytest

from planetpdf.core.zone_mark import carry_mark, mark_of

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' Zone.Identifier stream")

MARK = "[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://example.com/\n"


def test_an_attachment_gets_the_pdfs_mark(tmp_path):
    pdf = tmp_path / "from-the-web.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    with open(f"{pdf}:Zone.Identifier", "w", encoding="utf-8") as stream:
        stream.write(MARK)
    saved = tmp_path / "note.cmd"
    saved.write_bytes(b"echo hi")
    assert carry_mark(pdf, saved)
    assert "ZoneId=3" in mark_of(saved)


def test_a_pdf_of_ones_own_gives_no_mark(tmp_path):
    pdf = tmp_path / "mine.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    saved = tmp_path / "note.txt"
    saved.write_bytes(b"x")
    assert not carry_mark(pdf, saved) and mark_of(saved) is None
    assert not carry_mark(None, saved)
