from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from planetpdf.core import create as C
from planetpdf.core.document import PdfError


def _png(w=480, h=240, color=(0, 90, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


def test_from_image_bytes():
    doc = pymupdf.open("pdf", C.pdf_from_image(_png()))
    assert doc.page_count == 1 and doc[0].rect.width == pytest.approx(360)  # 480 px at 96 dpi
    big = pymupdf.open("pdf", C.pdf_from_image(_png(4000, 1000)))
    assert big[0].rect.width == pytest.approx(C.LETTER.width)  # shrunk to fit


def test_from_text_paginates():
    text = "\n".join(f"Line {n} with some words" for n in range(200))
    doc = pymupdf.open("pdf", C.pdf_from_text(text))
    assert doc.page_count > 1 and "Line 0 with some words" in doc[0].get_text()
    assert "Line 199" in doc[-1].get_text()


def test_from_files(tmp_path):
    png = tmp_path / "scan.png"
    png.write_bytes(_png())
    assert pymupdf.open("pdf", C.pdf_from_file(png)).page_count == 1
    tiff = tmp_path / "two.tif"
    frames = [Image.new("RGB", (200, 100), c) for c in ("red", "blue")]
    frames[0].save(tiff, save_all=True, append_images=frames[1:])
    assert pymupdf.open("pdf", C.pdf_from_file(tiff)).page_count == 2  # every frame
    txt = tmp_path / "notes.txt"
    txt.write_text("Hello from a text file", encoding="utf-8")
    assert "Hello from a text file" in pymupdf.open("pdf", C.pdf_from_file(txt))[0].get_text()
    doc = tmp_path / "letter.docx"
    doc.write_bytes(b"x")
    with pytest.raises(PdfError, match="can't convert"):
        C.pdf_from_file(doc)


def test_blank():
    doc = pymupdf.open("pdf", C.blank_pdf())
    assert doc.page_count == 1 and doc[0].rect == C.LETTER
