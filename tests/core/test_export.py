from __future__ import annotations

import pymupdf
import pytest
from PIL import Image

from planetpdf.core import ocr
from planetpdf.core.export import (
    ExportCanceled,
    ExportFormat,
    ExportOptions,
    export,
    image_file_names,
)
from tests.fixtures import generate as g

needs_ocr = pytest.mark.skipif(ocr.find_tessdata() is None, reason="Tesseract language data not installed")


@pytest.fixture
def text_doc(fixture_dir):
    doc = pymupdf.open(fixture_dir / "text.pdf")
    yield doc
    doc.close()


# --- images -------------------------------------------------------------------------------


def test_file_names(tmp_path):
    names = image_file_names(tmp_path / "out.png", [0, 4, 11], ExportFormat.PNG)
    assert [n.name for n in names] == ["out_p001.png", "out_p005.png", "out_p012.png"]
    assert image_file_names(tmp_path / "one.jpg", [3], ExportFormat.JPG) == [tmp_path / "one.jpg"]
    assert image_file_names(tmp_path / "all.tif", [0, 1], ExportFormat.TIFF_MULTI) == [tmp_path / "all.tif"]


@pytest.mark.parametrize("fmt", [ExportFormat.PNG, ExportFormat.JPG, ExportFormat.TIFF])
def test_image_per_page(text_doc, tmp_path, fmt):
    files = export(text_doc, [2, 0], tmp_path / f"page{fmt.extension}", fmt, ExportOptions(dpi=72))
    assert len(files) == 2 and all(f.exists() for f in files)
    with Image.open(files[0]) as img:
        assert img.size == (612, 792)  # letter at 72 dpi
    assert not list(tmp_path.glob(".~planetpdf-*"))


def test_image_dpi_scales_size(text_doc, tmp_path):
    [f] = export(text_doc, [0], tmp_path / "hi.png", ExportFormat.PNG, ExportOptions(dpi=144))
    with Image.open(f) as img:
        assert img.size == (1224, 1584)


def test_multipage_tiff(text_doc, tmp_path):
    [f] = export(text_doc, [0, 1, 2], tmp_path / "all.tif", ExportFormat.TIFF_MULTI, ExportOptions(dpi=50))
    with Image.open(f) as img:
        assert img.n_frames == 3


def test_image_only_pdf_has_no_text(text_doc, tmp_path):
    [f] = export(text_doc, [0, 2], tmp_path / "flat.pdf", ExportFormat.IMAGE_PDF, ExportOptions(dpi=100))
    with pymupdf.open(f) as doc:
        assert doc.page_count == 2
        assert all(not page.get_text().strip() for page in doc)
        assert all(page.get_images() for page in doc)
        assert doc[0].rect == pymupdf.Rect(0, 0, 612, 792)
    assert not g.bytes_contain_text(f.read_bytes(), g.SSN)


def test_cancel_leaves_no_partial_file(text_doc, tmp_path):
    target = tmp_path / "x.tif"
    with pytest.raises(ExportCanceled):
        export(text_doc, [0, 1, 2], target, ExportFormat.TIFF_MULTI, ExportOptions(dpi=50), progress=lambda d, t: d < 1)
    assert not target.exists()
    assert not list(tmp_path.glob(".~planetpdf-*"))


# --- text / RTF / Word ----------------------------------------------------------------------------


def test_text_export(text_doc, tmp_path):
    [f] = export(text_doc, [0, 1], tmp_path / "out.txt", ExportFormat.TXT, ExportOptions())
    content = f.read_text(encoding="utf-8")
    pages = content.split("\f")
    assert len(pages) == 2
    assert "Customer record" in pages[0] and g.SSN in pages[0] and "Page 2" in pages[1]


def test_rtf_export(text_doc, fixture_dir, tmp_path):
    [f] = export(text_doc, [0, 1], tmp_path / "out.rtf", ExportFormat.RTF, ExportOptions())
    rtf = f.read_text(encoding="ascii")
    assert rtf.startswith("{\\rtf1") and rtf.rstrip().endswith("}")
    assert rtf.count("{") == rtf.count("}")  # balanced groups
    assert "Customer record" in rtf and g.SSN in rtf
    assert rtf.count("\\page") == 1

    with pymupdf.open(fixture_dir / "image.pdf") as doc:
        [f] = export(doc, [0], tmp_path / "img.rtf", ExportFormat.RTF, ExportOptions())
    assert "\\pngblip" in f.read_text(encoding="ascii") or "\\jpegblip" in f.read_text(encoding="ascii")


def test_rtf_escapes_special_and_unicode(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Braces {x} back\\slash café", fontsize=12)
    [f] = export(doc, [0], tmp_path / "u.rtf", ExportFormat.RTF, ExportOptions())
    rtf = f.read_text(encoding="ascii")
    assert "\\{x\\}" in rtf and "back\\\\slash" in rtf and "caf\\u233?" in rtf


def test_docx_export(text_doc, tmp_path):
    import docx

    [f] = export(text_doc, [1, 0], tmp_path / "out.docx", ExportFormat.DOCX, ExportOptions())
    document = docx.Document(str(f))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Customer record" in text and g.SSN in text
    assert text.index("Page 2") < text.index("Page 1")  # given page order kept
    assert not list(tmp_path.glob(".~planetpdf-*"))


@needs_ocr
def test_text_export_with_ocr_for_scans(fixture_dir, tmp_path):
    with pymupdf.open(fixture_dir / "scanned.pdf") as doc:
        [plain] = export(doc, [0], tmp_path / "plain.txt", ExportFormat.TXT, ExportOptions())
        [ocrd] = export(doc, [0, 3], tmp_path / "ocr.txt", ExportFormat.TXT, ExportOptions(ocr_missing_text=True))
    assert plain.read_text(encoding="utf-8").strip() == ""
    text = ocrd.read_text(encoding="utf-8")
    assert text.count(g.SSN) == 2 and g.EMAIL in text


@needs_ocr
def test_docx_export_with_ocr_for_scans(fixture_dir, tmp_path):
    import docx

    with pymupdf.open(fixture_dir / "scanned.pdf") as doc:
        [f] = export(doc, [0], tmp_path / "scan.docx", ExportFormat.DOCX, ExportOptions(ocr_missing_text=True))
    text = "\n".join(p.text for p in docx.Document(str(f)).paragraphs)
    assert g.SSN in text or g.SSN in "\n".join(
        c.text for t in docx.Document(str(f)).tables for r in t.rows for c in r.cells
    )
