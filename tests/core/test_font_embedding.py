"""Fonts a PDF only names are embedded on save from this computer; the 14 built-in fonts
are left alone (user 2026-10-03)."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import fonts
from planetpdf.core.document import PdfSession
from planetpdf.core.font_embedding import embed_fonts, is_built_in

needs_arial = pytest.mark.skipif("Arial" not in fonts.installed(), reason="needs Arial installed")


def _named_only(path, base: str = "Arial", text: str = "Hello world"):
    """A page whose text uses a font the file only names (like the BE Filing letter's
    "Arial", which Word left out)."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((20, 50), text, fontsize=14, fontname="helv")
    [(xref, *_rest)] = page.get_fonts()
    doc.xref_set_key(xref, "BaseFont", f"/{base.replace(',', '#2C')}")
    doc.xref_set_key(xref, "Subtype", "/TrueType")
    doc.save(path)
    return path


def _spans(page):
    return [(s["text"], tuple(round(v, 1) for v in s["bbox"][:1]), s["font"])
            for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]


def test_the_built_in_fonts_are_left_alone():
    doc = pymupdf.open()
    page = doc.new_page()
    for n, name in enumerate(("helv", "hebo", "tiro", "cour")):
        page.insert_text((20, 40 + 20 * n), "built in", fontname=name)
    report = embed_fonts(doc)
    assert report.embedded == {} and report.missing == []
    assert all(f[1] == "n/a" for f in page.get_fonts())  # (still not embedded)
    assert is_built_in("Helvetica,Bold") and is_built_in("ABCDEF+Times-Roman") and not is_built_in("TimesNewRomanPSMT")


@needs_arial
def test_a_named_font_is_embedded_with_just_its_letters(tmp_path):
    doc = pymupdf.open(_named_only(tmp_path / "a.pdf"))
    before = _spans(doc[0])
    report = embed_fonts(doc)
    assert report.embedded == {"Arial": "Arial"}
    doc = pymupdf.open(stream=doc.tobytes(garbage=3, deflate=True))
    [(xref, ext, *_rest)] = doc[0].get_fonts()
    assert ext == "ttf"
    program = pymupdf.Font(fontbuffer=doc.extract_font(xref)[3])
    assert program.has_glyph(ord("H")) and program.has_glyph(ord("w")) and not program.has_glyph(ord("Z"))
    after = _spans(doc[0])
    assert [s[0] for s in after] == [s[0] for s in before]  # (same text, same place)
    assert [s[1] for s in after] == [s[1] for s in before]


@needs_arial
def test_bold_is_embedded_from_the_bold_file(tmp_path):
    doc = pymupdf.open(_named_only(tmp_path / "a.pdf", "Arial,Bold"))
    embed_fonts(doc)
    [(xref, *_rest)] = doc[0].get_fonts()
    name = pymupdf.Font(fontbuffer=doc.extract_font(xref)[3]).name
    assert "Bold" in name


def test_a_font_not_on_this_computer_is_reported(tmp_path):
    doc = pymupdf.open(_named_only(tmp_path / "a.pdf", "NoSuchFont123"))
    report = embed_fonts(doc)
    assert report.missing == ["NoSuchFont123"] and "NoSuchFont123" in report.message()
    assert doc[0].get_fonts()[0][1] == "n/a"


@needs_arial
def test_form_field_fonts_are_left_alone(tmp_path):
    """Viewers type new letters with the form's default font: it must stay whole."""
    doc = pymupdf.open(_named_only(tmp_path / "a.pdf"))
    [(xref, *_rest)] = doc[0].get_fonts()
    form = doc.get_new_xref()
    doc.update_object(form, f"<< /Fields [] /DR << /Font << /Arial {xref} 0 R >> >> >>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{form} 0 R")
    assert embed_fonts(doc).embedded == {}


@needs_arial
def test_saving_embeds_them(tmp_path):
    session = PdfSession.open(_named_only(tmp_path / "a.pdf"))
    try:
        session.mark_modified()
        session.save()
        assert session.font_report.embedded == {"Arial": "Arial"}
        assert session.doc[0].get_fonts()[0][1] == "ttf"
        assert session.doc[0].get_text().strip() == "Hello world"
    finally:
        session.close()


@needs_arial
def test_a_save_reports_each_step(tmp_path):
    """User 2026-10-23 (no progress for Save): the steps, counted where they can be."""
    from planetpdf.core.progress import stage_text

    session = PdfSession.open(_named_only(tmp_path / "a.pdf"))
    steps = []
    try:
        session.mark_modified()
        session.save(progress=lambda done, total, stage="": steps.append(stage_text(stage, done, total)))
    finally:
        session.close()
    assert steps == ["Step 1 of 3: finding the letters used on page 1 of 1", "Step 1 of 3: embedding font 1 of 1",
                     "Step 2 of 3: writing 1 page…", "Step 3 of 3: opening the saved file…"]
