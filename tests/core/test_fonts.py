"""Fonts for text PlanetPDF writes (`core.fonts`), placed text in any font, and Edit PDF
reading only the page's own text (user 2026-10-03)."""

from __future__ import annotations

import sys

import pymupdf
import pytest

from planetpdf.core import content_edit as E
from planetpdf.core import fonts
from planetpdf.core import markup as K
from planetpdf.core import placed_text as PT
from planetpdf.core.document import PdfSession


def test_the_list_starts_with_the_standard_and_bundled_fonts():
    names = fonts.families()
    assert names[:8] == ["Helvetica", "Times Roman", "Courier", "Noto Sans", "Noto Serif", "Caveat",
                         "Dancing Script", "Great Vibes"]
    assert len(names) == len(set(names))


def test_standard_fonts_have_exact_styles_and_are_never_embedded():
    face = fonts.face("Times Roman", bold=True, italic=True)
    assert (face.fontname, face.buffer, face.fake_bold, face.fake_italic) == ("tibi", None, False, False)
    assert "Times New Roman" in face.editor_families


def test_a_font_without_a_bold_file_is_drawn_bold_by_planetpdf():
    face = fonts.face("Caveat", bold=True)
    assert face.buffer is not None and face.fake_bold and not face.fake_italic
    assert face.fontname.startswith("PPDF")


def test_an_unknown_font_falls_back_to_helvetica():
    assert fonts.face("No Such Font 123").fontname == "helv"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows font registry")
def test_windows_font_names_are_split_into_family_and_style():
    from planetpdf.core.windows_fonts import split_name

    assert split_name("Arial Bold Italic (TrueType)") == ("Arial", True, True)
    assert split_name("Segoe UI Semibold (TrueType)") == ("Segoe UI Semibold", False, False)
    assert split_name("Calibri Light Italic (OpenType)") == ("Calibri Light", False, True)


def test_subsetting_keeps_only_the_letters_used():
    from planetpdf.core.resources import font_path

    whole = font_path("serif").read_bytes()
    small = fonts.subset(whole, "Hello")
    assert len(small) < len(whole) / 10
    font = pymupdf.Font(fontbuffer=small)
    assert font.has_glyph(ord("H")) and not font.has_glyph(ord("Z"))


@pytest.mark.parametrize("font, kw", [("Helvetica", {"bold": True, "underline": True}),
                                      ("Noto Serif", {"italic": True, "strikeout": True}),
                                      ("Caveat", {"bold": True})])
def test_placed_text_is_drawn_in_its_font_and_style(font, kw):
    pdf = PT.render("Better than Acrobat", 18, (0.8, 0.1, 0.1), font=font, **kw)
    with pymupdf.open(stream=pdf) as doc:
        page = doc[0]
        names = [f[3] for f in page.get_fonts()]
        assert any(font.split()[0].lower() in n.lower() for n in names), names
        assert page.get_text().strip() == "Better than Acrobat"
        lines = [d for d in page.get_drawings() if d["items"] and d["items"][0][0] == "l"]
        assert len(lines) == (1 if kw.get("underline") or kw.get("strikeout") else 0)


def test_moving_placed_text_keeps_its_picture(tmp_path):
    """Only moved: the same drawing at the new place (so text in a font this computer
    doesn't have keeps its look)."""
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "a.pdf")
    session = PdfSession.open(tmp_path / "a.pdf")
    try:
        spec = K.placed_text(session, 0, (100, 100), "Hello", K.Style(font="Noto Serif", font_size=14))
        moved = K.restyled_text(session, spec, top_left=(200, 300))
        assert moved.source == spec.source
        assert moved.rect[0] == pytest.approx(200) and moved.rect[1] == pytest.approx(300)
        bigger = K.restyled_text(session, spec, font="Courier")
        assert bigger.source != spec.source and bigger.style.font == "Courier"
    finally:
        session.close()


def test_old_specs_without_font_fields_still_load():
    spec = K.MarkupSpec.from_json('{"mark_id":"a","page":0,"kind":"text","rect":[0,0,1,1],'
                                  '"style":{"color":[0,0,0],"font_size":12,"unknown_key":1},"text":"x"}')
    assert spec.style.font == "Helvetica" and not spec.style.bold


def test_edit_pdf_reads_only_the_page_s_own_text(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 40), "page text", fontsize=12)
    page.add_freetext_annot((20, 80, 200, 110), "a comment", fontsize=12)
    page.set_rotation(90)
    doc.save(tmp_path / "a.pdf")
    session = PdfSession.open(tmp_path / "a.pdf")
    try:
        assert [line.text for line in E.text_lines(session, 0)] == ["page text"]
        page = session.doc[0]
        ours = E.page_text(page, "dict")["blocks"][0]["lines"][0]["spans"][0]["origin"]
        theirs = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["origin"]
        assert ours == pytest.approx(theirs)  # (same coordinates as get_text, rotated page too)
    finally:
        session.close()


# --- fonts only in the document (user 2026-10-03) ----------------------------------------------------


def _document_font_pdf(path, *, wide_copy: bool = False):
    """Page 1: "HELLO pool" drawn with two partial copies of a font that isn't installed
    ("PlanetTest": each copy claims every letter but has outlines for only some, like
    the BE Filing letter). ``wide_copy``: a third copy of another weight (other widths)."""
    import io

    from fontTools import subset
    from fontTools.ttLib.tables._g_l_y_f import Glyph

    from planetpdf.core.resources import font_path

    def copy(inked: str, widen: int = 0) -> bytes:
        options = subset.Options()
        options.name_IDs = ["*"]
        font = subset.load_font(str(font_path("sans")), options)
        sub = subset.Subsetter(options)
        sub.populate(text="HELOpol ")
        sub.subset(font)
        for record in font["name"].names:
            if record.nameID in (1, 4, 16):
                record.string = "PlanetTest"
            elif record.nameID == 6:
                record.string = "PlanetTest-Regular"
        cmap = font.getBestCmap()
        for c in "HELOpol":
            if c not in inked:
                font["glyf"][cmap[ord(c)]] = Glyph()
            elif widen:
                width, lsb = font["hmtx"][cmap[ord(c)]]
                font["hmtx"][cmap[ord(c)]] = (width + widen, lsb)
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_font(fontname="F1", fontbuffer=copy("HELO"))
    page.insert_font(fontname="F2", fontbuffer=copy("pol"))
    x = 72.0
    for c in "HELLO pool":
        if c != " ":
            page.insert_text((x, 200), c, fontname="F1" if c in "HELO" else "F2", fontsize=30)
        x += 20
    if wide_copy:
        page.insert_font(fontname="F3", fontbuffer=copy("HELOpol", widen=80))
        page.insert_text((72, 300), "Hello", fontname="F3", fontsize=30)
    doc.save(path)
    return path


def test_a_document_s_own_fonts_are_listed_with_their_letters_merged(tmp_path):
    doc = pymupdf.open(_document_font_pdf(tmp_path / "a.pdf"))
    own = fonts.document_fonts(doc)
    assert list(own) == ["PlanetTest"]
    face = fonts.face("PlanetTest", doc=doc, text="HELLO pool")
    assert face.in_document and face.fallback == "Helvetica"
    assert face.missing("HELLO pool") == []  # (both copies' letters, merged into one program)
    assert face.missing("Hello quiz") == ["e", "q", "u", "i", "z"]
    assert fonts.face("PlanetTest").fontname == "helv"  # (another document: not there)


def test_two_weights_stay_apart_and_bold_takes_the_heavier(tmp_path):
    doc = pymupdf.open(_document_font_pdf(tmp_path / "a.pdf", wide_copy=True))
    [font] = fonts.document_fonts(doc).values()
    assert len(font.instances) == 2
    regular, bold = font.face(False, False, "pool"), font.face(True, False, "pool")
    assert regular.fontname != bold.fontname and not bold.fake_bold


def test_text_in_a_document_font_draws_missing_letters_in_the_closest_font(tmp_path):
    session = PdfSession.open(_document_font_pdf(tmp_path / "a.pdf"))
    try:
        spec = K.placed_text(session, 0, (100, 400), "Hello pool", K.Style(font="PlanetTest", font_size=20))
        with pymupdf.open(stream=spec.source) as src:
            used = {s["font"]: s["text"] for b in src[0].get_text("dict")["blocks"] for l in b["lines"] for s in l["spans"]}
            assert src[0].get_text().replace("\n", "").replace(" ", "") == "Hellopool"
        assert any("PlanetTest" in name for name in used) and "Helvetica" in used  # ("e" isn't in the document)
        assert "e" in used["Helvetica"]
    finally:
        session.close()


def test_page_text_can_use_the_document_s_own_font(tmp_path):
    path = _document_font_pdf(tmp_path / "a.pdf")
    doc = pymupdf.open(path)
    doc[0].insert_text((72, 400), "Pool hello", fontsize=14)
    doc.save(tmp_path / "b.pdf")
    session = PdfSession.open(tmp_path / "b.pdf")
    try:
        line = next(l for l in E.text_lines(session, 0) if l.text == "Pool hello")
        assert E.line_font(next(l for l in E.text_lines(session, 0) if "pool" in l.text)) == "PlanetTest"
        E.restyle_line(session, line, font="PlanetTest")  # (lacks "P" and "e": the closest font instead)
        line = next(l for l in E.text_lines(session, 0) if l.text == "Pool hello")
        assert line.font == "Helvetica"
        short = next(l for l in E.text_lines(session, 0) if l.text == "HELLO pool")
        E.restyle_line(session, short, size=20, font="PlanetTest")
        assert any(l.text == "HELLO pool" and "PlanetTest" in l.font for l in E.text_lines(session, 0))
    finally:
        session.close()


def test_page_text_takes_any_font_from_the_list(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=400, height=200).insert_text((20, 50), "Hello world", fontsize=14)
    doc.save(tmp_path / "a.pdf")
    session = PdfSession.open(tmp_path / "a.pdf")
    try:
        [line] = E.text_lines(session, 0)
        assert E.line_font(line) == "Helvetica"
        E.restyle_line(session, line, font="Noto Serif")
        [line] = E.text_lines(session, 0)
        assert line.text == "Hello world" and E.line_font(line) == "Noto Serif"
        assert len(session.doc.tobytes(garbage=4, deflate=True)) < 60_000  # (the font is cut down to these letters)
    finally:
        session.close()


def test_fonts_in_a_collection_are_listed_and_usable(tmp_path, monkeypatch):
    """Font collections (.ttc: macOS keeps Helvetica, Avenir, Menlo… this way, 2026-10-14): every
    face is listed, the plain style wins its slot, and a face used is lifted out into its own file."""
    from fontTools.ttLib import TTCollection, TTFont

    from planetpdf.core.resources import font_path

    folder = tmp_path / "fonts"
    folder.mkdir()
    collection = TTCollection()
    bundled = font_path("sans").parent
    collection.fonts = [TTFont(str(bundled / "BarlowCondensed-SemiBoldItalic.ttf")),
                        TTFont(str(bundled / "BarlowCondensed-BoldItalic.ttf"))]
    collection.save(str(folder / "Barlow.ttc"))
    monkeypatch.setattr(fonts, "FACE_CACHE", tmp_path / "faces")
    found = fonts._scan_font_folders([folder])
    face = found["Barlow Condensed"][(True, True)]
    assert face.suffix == ".ttf" and not face.exists()  # (listed; not written out until used)
    real = fonts.font_file(face)
    assert real.is_file() and TTFont(str(real))["name"].getDebugName(4) == "Barlow Condensed Bold Italic"

    monkeypatch.setattr(fonts, "_installed", lambda: tuple(found.items()))
    drawn = fonts.face("Barlow Condensed", True, True)
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=100)
    page.insert_font(fontname=drawn.fontname, fontbuffer=drawn.buffer)
    page.insert_text((20, 50), "Hello", fontname=drawn.fontname, fontsize=20)
    assert "Hello" in page.get_text()


def test_windows_lists_collections_without_replacing_single_files(tmp_path, monkeypatch):
    """Windows: the registry's collections (Cambria, MS Gothic… user 2026-10-23, "as on the Mac")
    fill the styles a family's single files don't (Cambria regular is in cambria.ttc, its bold and
    italic are single files); single files are kept."""
    import sys
    import types

    from fontTools.ttLib import TTCollection, TTFont

    monkeypatch.setitem(sys.modules, "winreg", sys.modules.get("winreg") or types.ModuleType("winreg"))
    from planetpdf.core import windows_fonts  # (the registry itself is replaced below)
    from planetpdf.core.resources import font_path

    folder = tmp_path / "fonts"
    folder.mkdir()
    collection = TTCollection()
    bundled = font_path("sans").parent
    collection.fonts = [TTFont(str(bundled / "BarlowCondensed-SemiBoldItalic.ttf")),
                        TTFont(str(bundled / "BarlowCondensed-BoldItalic.ttf"))]
    collection.save(str(folder / "Barlow.ttc"))
    single = tmp_path / "barlow-regular.ttf"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(fonts, "FACE_CACHE", tmp_path / "faces")
    monkeypatch.setattr(windows_fonts, "installed_fonts", lambda: {"Barlow Condensed": {(False, False): single}})
    monkeypatch.setattr(windows_fonts, "collection_files", lambda: [folder / "Barlow.ttc"])
    fonts._installed.cache_clear()
    try:
        found = fonts.installed()["Barlow Condensed"]
    finally:
        fonts._installed.cache_clear()
    assert found[(False, False)] == single  # (the single file stays)
    real = fonts.font_file(found[(True, True)])  # (the collection's bold italic, plain style first)
    assert TTFont(str(real))["name"].getDebugName(4) == "Barlow Condensed Bold Italic"
