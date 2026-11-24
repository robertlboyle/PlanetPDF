from __future__ import annotations

import pymupdf
import pypdf
import pytest
from PIL import Image

from planetpdf.core import content_edit as E
from planetpdf.core import snapshots as S
from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import marks as M
from tests.fixtures import generate as g


@pytest.fixture
def text(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


def _line(session, contains, page=0):
    return next(l for l in E.text_lines(session, page) if contains in l.text)


def test_lines_found(text):
    line = _line(text, "SSN")
    assert line.text == f"SSN: {g.SSN}" and line.size == pytest.approx(12)
    assert line.family == "sans"


def test_invisible_ocr_text_is_not_offered_for_editing(text):
    text.doc[0].insert_text((72, 600), "Invisible OCR words", render_mode=3)
    assert not any("Invisible" in l.text for l in E.text_lines(text, 0))


def test_replace_line_really_replaces(text):
    line = _line(text, "Phone")
    E.replace_line(text, line, "Phone: (555) 000-1111")
    page_text = text.doc[0].get_text()
    assert "(555) 000-1111" in page_text and g.PHONE not in page_text
    assert f"SSN: {g.SSN}" in page_text and f"Email: {g.EMAIL}" in page_text  # neighbors intact
    new = _line(text, "000-1111")
    assert new.origin[1] == pytest.approx(line.origin[1], abs=0.5) and new.size == pytest.approx(line.size)
    text.save()
    assert g.PHONE not in pypdf.PdfReader(text.path).pages[0].extract_text()  # gone for other readers too


def test_replacement_uses_original_embedded_font(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Invoice total 125", fontsize=14,
                               fontfile="src/planetpdf/resources/fonts/NotoSerif.ttf", fontname="F1")
    path = tmp_path / "f.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    E.replace_line(s, _line(s, "Invoice"), "Invoice total 150")
    fonts = {f[3] for f in s.doc[0].get_fonts()}
    assert any("Noto Serif" in f for f in fonts)
    assert _line(s, "150").font.startswith("NotoSerif")
    s.close()


def _mixed_line(tmp_path):
    """ "Please read the terms carefully." with "terms" in bold, written as three runs."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    x = 72.0
    for text, font in (("Please read the ", "helv"), ("terms", "hebo"), (" carefully.", "helv")):
        page.insert_text((x, 100), text, fontname=font, fontsize=12)
        x += pymupdf.Font(font).text_length(text, 12)
    page.insert_text((72, 140), "Another line", fontname="helv", fontsize=12)
    page.insert_text((400, 100), "Far away", fontname="tibo", fontsize=12)  # (same baseline, not the same line)
    doc.save(tmp_path / "mixed.pdf")
    return PdfSession.open(tmp_path / "mixed.pdf")


def _words_and_fonts(session):
    out = []
    for block in session.doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["origin"][1] == pytest.approx(100, abs=1) and span["bbox"][0] < 390:
                    out += [(word, span["font"]) for word in span["text"].split()]
    return out


def test_a_line_in_several_fonts_is_edited_as_one(tmp_path):
    """User 2026-10-23: a line whose words use several fonts is edited as one line, each word
    keeping its own font; words typed next to a run take on its font."""
    s = _mixed_line(tmp_path)
    try:
        lines = E.text_lines(s, 0)
        bold = next(l for l in lines if l.text == "terms")
        group = E.line_group(lines, bold)
        assert [l.text for l in group] == ["Please read the", "terms", "carefully."]
        assert E.can_edit_together(s, group)
        pieces = E.group_text(group)
        assert "".join(t for t, _n in pieces) == "Please read the terms carefully."
        # "the" -> "these", "terms" -> "new terms" (still bold), "carefully" unchanged
        E.replace_runs(s, group, [("Please read these ", 0), ("new terms", 1), (" carefully.", 2)])
        assert _words_and_fonts(s) == [("Please", "Helvetica"), ("read", "Helvetica"), ("these", "Helvetica"),
                                       ("new", "Helvetica-Bold"), ("terms", "Helvetica-Bold"),
                                       ("carefully.", "Helvetica")]
        text = " ".join(s.doc[0].get_text().split())
        assert "Another line" in text and "Far away" in text  # (the rest stays)
        after = next(l for l in E.text_lines(s, 0) if l.text == "carefully.")
        bolds = next(l for l in E.text_lines(s, 0) if l.text == "new terms")
        assert after.rect[0] >= bolds.rect[2] - 0.5  # (laid out after it, not on top of it)
    finally:
        s.close()


def test_unchanged_mixed_line_stays_exactly_as_it_was(tmp_path):
    s = _mixed_line(tmp_path)
    try:
        lines = E.text_lines(s, 0)
        group = E.line_group(lines, lines[0])
        before = s.doc[0].read_contents()
        E.replace_runs(s, group, E.group_text(group))
        assert s.doc[0].read_contents() == before
        far = next(l for l in lines if l.text == "Far away")
        assert E.line_group(lines, far) == [far]
    finally:
        s.close()


def test_bold_serif_falls_back_to_matching_standard_font(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Heading", fontsize=18, fontname="tibo")
    path = tmp_path / "b.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    E.replace_line(s, _line(s, "Heading"), "New heading")
    new = _line(s, "New heading")
    assert new.bold and new.family == "serif"
    s.close()


def test_delete_line_and_add_text(text):
    E.delete_line(text, _line(text, "Email"))
    assert g.EMAIL not in text.doc[0].get_text()
    E.add_text(text, 0, (72, 500), "Added line one\nAdded line two", size=11)
    t = text.doc[0].get_text()
    assert "Added line one" in t and "Added line two" in t


def test_edit_on_rotated_page(text):
    text.doc[0].set_rotation(90)
    line = _line(text, "SSN")
    E.replace_line(text, line, "SSN: withheld")
    new = _line(text, "withheld")
    assert new.direction == pytest.approx(line.direction)  # same orientation as before
    E.add_text(text, 0, (72, 72), "Upright note")
    page = text.doc[0]
    word = next(w for w in page.get_text("words") if w[4] == "Upright")
    shown = (pymupdf.Rect(word[:4]) * page.rotation_matrix).normalize()
    assert shown.width > shown.height


def test_editing_keeps_the_users_redaction_marks_unapplied(text):
    ssn = text.doc[0].search_for(g.SSN)[0]
    M.add_mark(text, M.make_spec(0, [tuple(ssn)], "SSN"))
    E.replace_line(text, _line(text, "Phone"), "Phone: n/a")
    assert len(M.list_marks(text)) == 1
    assert g.SSN in text.doc[0].get_text()  # still there: the mark wasn't applied


def test_snapshot_undo_of_text_edit(text):
    before = S.take(text, [0])
    E.replace_line(text, _line(text, "Account"), "Account: closed")
    S.restore(text, before)
    assert f"Account: {g.ACCOUNT}" in text.doc[0].get_text() and "closed" not in text.doc[0].get_text()


# --- images ------------------------------------------------------------------------------------------


@pytest.fixture
def imaged(pdf_copy):
    s = PdfSession.open(pdf_copy("image.pdf"))
    yield s
    s.close()


def test_images_listed(imaged):
    [image] = E.page_images(imaged, 0)
    assert pymupdf.Rect(image.rect) == g.IMAGE_RECT


def test_move_and_resize_image(imaged):
    [image] = E.page_images(imaged, 0)
    E.move_image(imaged, image, (300, 400, 400, 500))
    [moved] = E.page_images(imaged, 0)
    assert moved.rect == pytest.approx((300, 400, 400, 500), abs=0.5)
    pix = imaged.doc[0].get_pixmap(clip=pymupdf.Rect(340, 440, 360, 460), dpi=72)
    assert pix.pixel(5, 5)[:3] == g.IMAGE_MARK_RGB  # the red center moved with it
    assert "Caption under the image" in imaged.doc[0].get_text()


def test_delete_and_replace_image(imaged, tmp_path):
    [image] = E.page_images(imaged, 0)
    new = tmp_path / "new.png"
    Image.new("RGB", (100, 50), (0, 0, 255)).save(new)
    E.replace_image(imaged, image, new.read_bytes())
    [replaced] = E.page_images(imaged, 0)
    r = pymupdf.Rect(replaced.rect)
    assert r.width == pytest.approx(200, abs=1) and r.height == pytest.approx(100, abs=1)  # fitted, proportions kept
    E.delete_image(imaged, replaced)
    assert E.page_images(imaged, 0) == []


def test_word_runs_lay_out_words_without_space_glyphs():
    """Subset fonts (Word / Print to PDF) map the space to the no-break space glyph:
    lines are then written word by word, spaced like the font's space."""
    import pymupdf

    font = pymupdf.Font("helv")
    runs = E._word_runs(font, "ab  cd e", 10, (100, 200), (1.0, 0.0))
    assert [w for w, _ in runs] == ["ab", "cd", "e"]
    assert runs[0][1] == (100, 200)
    gap = font.glyph_advance(0xA0) * 10
    assert runs[1][1][0] == pytest.approx(100 + font.text_length("ab", 10) + 2 * gap)
    down = E._word_runs(font, "ab cd", 10, (100, 200), (0.0, 1.0))  # a line running down the page
    assert down[1][1][0] == 100 and down[1][1][1] > 200


def test_move_line_keeps_text_font_and_spacing(text):
    line = next(l for l in E.text_lines(text, 0) if "SSN" in l.text)
    E.move_line(text, line, 30, 120)
    moved = next(l for l in E.text_lines(text, 0) if "SSN" in l.text)
    assert moved.text == line.text and moved.size == pytest.approx(line.size)
    assert moved.origin[0] == pytest.approx(line.origin[0] + 30, abs=0.5)
    assert moved.origin[1] == pytest.approx(line.origin[1] + 120, abs=0.5)

# --- user bug 2026-10-01: a moved paragraph landing on other text ------------------------------


def _page_with(tmp_path, draw):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    draw(page)
    path = tmp_path / "drawn.pdf"
    doc.save(path)
    return PdfSession.open(path)


def test_text_drawn_on_top_of_other_text_is_its_own_line(tmp_path):
    def draw(page):
        page.insert_text((72, 100), "Ethics and Law in Data", fontname="helv", fontsize=11)
        page.insert_text((140, 100), "specializing in AI", fontname="tiro", fontsize=11)  # same baseline, on top
        page.insert_text((72, 140), "Left part", fontname="helv", fontsize=11)
        page.insert_text((400, 140), "far right", fontname="helv", fontsize=11)  # same baseline, far away
    s = _page_with(tmp_path, draw)
    texts = sorted(l.text for l in E.text_lines(s, 0))
    assert texts == sorted(["Ethics and Law in Data", "specializing in AI", "Left part", "far right"])
    s.close()


def test_removing_a_line_keeps_the_text_it_lies_on(tmp_path):
    def draw(page):
        page.insert_text((72, 100), "Ethics and Law in Data", fontname="helv", fontsize=11)
        page.insert_text((120, 100), "Moved here by mistake", fontname="tiro", fontsize=11)
    s = _page_with(tmp_path, draw)
    moved = next(l for l in E.text_lines(s, 0) if l.text.startswith("Moved"))
    E.move_line(s, moved, 0, 200)
    texts = [l.text for l in E.text_lines(s, 0)]
    assert "Ethics and Law in Data" in texts and "Moved here by mistake" in texts
    s.close()


def test_a_line_moved_again_keeps_its_embedded_font(tmp_path):
    from planetpdf.core.resources import font_path

    def draw(page):
        page.insert_font(fontname="noto", fontfile=str(font_path("sans")))
        page.insert_text((72, 100), "Experienced engineer", fontname="noto", fontsize=11)
    s = _page_with(tmp_path, draw)
    for step in range(3):
        line = next(l for l in E.text_lines(s, 0) if l.text == "Experienced engineer")
        E.move_line(s, line, 20, 15)
        again = next(l for l in E.text_lines(s, 0) if l.text == "Experienced engineer")
        assert "Noto" in again.font and again.size == pytest.approx(11), step  # never Helvetica
    assert again.origin == pytest.approx((72 + 60, 100 + 45), abs=0.5)
    s.close()


def test_shared_glyphs_read_back_as_plain_characters(tmp_path):
    """Word's subset fonts use one glyph for '-' and U+2010, ' ' and U+00A0."""
    import io

    from fontTools.ttLib import TTFont

    from planetpdf.core.resources import font_path

    font = TTFont(str(font_path("sans")))
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap[0x2010] = table.cmap[0x2D]
            table.cmap[0xA0] = table.cmap[0x20]
    out = io.BytesIO()
    font.save(out)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    E._insert(page, (72, 100), "a 37-year record", "shared", out.getvalue(), 11, (0, 0, 0), 0)
    assert page.get_text().strip() == "a 37-year record"

def _split_font_pdf(path, second: str = ""):
    """A line drawn with two partial copies of one font, each holding only some letters'
    shapes (like the user's "BE Filing Acknowledgement", 2026-10-02); optionally a second
    line of those letters just under it."""
    import io

    from fontTools import subset

    from planetpdf.core.resources import font_path

    def partial(letters: str) -> bytes:
        """Every letter in the character map, but only ``letters`` with an outline: like a
        PDF's font subset, it claims letters it can't draw."""
        from fontTools.ttLib.tables._g_l_y_f import Glyph

        options = subset.Options()
        font = subset.load_font(str(font_path("sans")), options)
        subsetter = subset.Subsetter(options)
        subsetter.populate(text="HELlop")
        subsetter.subset(font)
        cmap = font.getBestCmap()
        for c in "HELlop":
            if c not in letters:
                font["glyf"][cmap[ord(c)]] = Glyph()  # (an empty outline)
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_font(fontname="F1", fontbuffer=partial("HEL"))
    page.insert_font(fontname="F2", fontbuffer=partial("lop"))
    x, size = 72.0, 30.0
    for c, name in zip("HELlop", ("F1", "F1", "F1", "F2", "F2", "F2")):
        page.insert_text((x, 200), c, fontname=name, fontsize=size)
        x += pymupdf.Font(fontfile=str(font_path("sans"))).text_length(c, fontsize=size)
    x = 72.0
    for c in second:
        page.insert_text((x, 236), c, fontname="F1" if c in "HEL" else "F2", fontsize=size)
        x += pymupdf.Font(fontfile=str(font_path("sans"))).text_length(c, fontsize=size)
    doc.save(path)
    return path


def _ink_in(page, rect):
    pix = page.get_pixmap(dpi=72, clip=rect)
    s = pix.samples
    return sum(1 for i in range(0, len(s), pix.n) if s[i] < 128)


def test_moving_a_line_drawn_from_partial_font_copies_keeps_every_letter(tmp_path):
    """User bug 2026-10-02: a moved heading lost its weight (the wrong copy of a same-named
    font), then letters (no single copy has them all)."""
    s = PdfSession.open(_split_font_pdf(tmp_path / "split.pdf"))
    try:
        [line] = [l for l in E.text_lines(s, 0) if "HEL" in l.text.replace(" ", "")]
        before = [_ink_in(s.doc[0], pymupdf.Rect(r)) for r in _letter_boxes(s, line)]
        assert all(before)
        E.move_line(s, line, 0, 100)
        [moved] = [l for l in E.text_lines(s, 0) if "HEL" in l.text.replace(" ", "")]
        after = [_ink_in(s.doc[0], pymupdf.Rect(r)) for r in _letter_boxes(s, moved)]
        assert len(after) == 6 and all(after)  # (every letter still drawn)
        fonts = {sp["font"] for b in s.doc[0].get_text("dict")["blocks"] for l in b.get("lines", []) for sp in l["spans"]}
        assert not any("Helvetica" in f for f in fonts)  # (in the document's own font, not a substitute)
    finally:
        s.close()


def test_moving_lines_over_each_other_again_and_again_changes_nothing_else(tmp_path):
    """User bug 2026-10-03: "the display got more and more wonky the more I moved boxes
    around. Some letters were bold and others were not. Some letters vanished." Lines in a
    font split into partial copies, moved half onto each other and back, many times: every
    letter stays, each line stays one line, and the page ends up as it started."""
    s = PdfSession.open(_split_font_pdf(tmp_path / "split.pdf", second="pHoL"))
    try:
        start = s.doc[0].get_pixmap(dpi=72).samples

        def line(text):
            [found] = [l for l in E.text_lines(s, 0) if l.text.replace(" ", "") == text]
            return found

        for _round in range(4):
            E.move_line(s, line("HELlop"), 12, 18)  # (half onto the line under it)
            E.move_line(s, line("HELlop"), -12, -18)
            E.move_line(s, line("pHoL"), -8, -18)
            E.move_line(s, line("pHoL"), 8, 18)
        end = s.doc[0].get_pixmap(dpi=72).samples
        changed = sum(1 for a, b in zip(start, end) if abs(a - b) > 60)
        assert changed < len(start) * 0.002  # (edge smoothing at most)
        fonts = {sp["font"] for b in s.doc[0].get_text("dict")["blocks"] for l in b.get("lines", []) for sp in l["spans"]}
        assert not any("Helvetica" in f or "Times" in f for f in fonts)
    finally:
        s.close()


def _letter_boxes(s, line):
    return [ch["bbox"] for b in s.doc[0].get_text("rawdict", clip=pymupdf.Rect(line.rect) + (-2, -2, 2, 2))["blocks"]
            for l in b.get("lines", []) for sp in l["spans"] for ch in sp["chars"] if ch["c"].strip()]


def test_several_spaces_in_a_row_are_kept(text):
    """User 2026-11-24: "If I edit a line of text with multiple spaces, when I close it, it loses all
    the spaces except for one": the line was read with its spaces squeezed, and an edit that only
    added spaces counted as no change."""
    text.doc[0].insert_text((72, 640), "Total:    $25.00", fontsize=12, fontname="helv")
    line = _line(text, "Total")
    assert line.text == "Total:    $25.00"  # (offered for editing as it is)
    E.replace_line(text, line, "Total:    $30.00")
    assert "Total:    $30.00" in text.doc[0].get_text()
    line = _line(text, "Total")
    E.replace_line(text, line, "Total:        $30.00")  # (only spaces added: still a change)
    assert "Total:        $30.00" in text.doc[0].get_text()
