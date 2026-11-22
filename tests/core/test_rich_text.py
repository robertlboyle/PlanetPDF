"""Words in another format and text boxes in any font (user 2026-10-03): runs in
`placed_text`, `Style.with_format`, and text boxes / callouts drawn by PlanetPDF."""

from __future__ import annotations

from dataclasses import replace

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core import placed_text as PT
from planetpdf.core.document import PdfSession

PLAIN = (False, False, False, False)


def test_runs_round_trip():
    text = "one two three"
    formats = PT.char_formats(text, PLAIN, ((4, 7, True, False, False, False),))
    assert formats[4] == (True, False, False, False) and formats[3] == PLAIN
    assert PT.make_runs(formats, PLAIN) == ((4, 7, True, False, False, False),)
    assert PT.make_runs(formats, (True, False, False, False)) == ((0, 4, False, False, False, False),
                                                                  (7, 13, False, False, False, False))


def test_whole_text_format_reaches_the_words():
    style = K.Style(runs=((4, 7, True, False, False, False),))
    # bold for the whole text: the bold word has nothing special left
    assert style.with_format("one two three", bold=True).runs == ()
    # italic for the whole text: the bold word stays bold, and is italic too
    italic = style.with_format("one two three", italic=True)
    assert italic.italic and italic.runs == ((4, 7, True, True, False, False),)


def test_runs_follow_a_change_of_words():
    runs = ((4, 7, True, False, False, False),)
    assert PT.edit_runs("one two three", "one two four", PLAIN, runs) == runs
    assert PT.edit_runs("one two three", "zero one two three", PLAIN, runs) == ((9, 12, True, False, False, False),)


def test_wrapping_breaks_between_words():
    text = "aaaa bbbb cccc dddd"
    one = PT.layout_size(text, 12)
    wrapped = PT.layout_size(text, 12, wrap_width=60)
    assert wrapped[0] == 60 + 2 * PT.PADDING and wrapped[1] > one[1] * 1.5


def test_a_bold_word_is_drawn_bold():
    pdf = PT.render("plain bold plain", 14, runs=((6, 10, True, False, False, False),))
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        spans = [s for b in doc[0].get_text("dict")["blocks"] for l in b["lines"] for s in l["spans"]]
    bold = [s["text"].strip() for s in spans if "Bold" in s["font"] or s["flags"] & 16]
    assert bold == ["bold"]
    assert "plain bold plain" in " ".join(s["text"] for s in spans).replace("  ", " ")


@pytest.fixture(params=[0, 90])
def session(tmp_path, request):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).set_rotation(request.param)
    doc.save(tmp_path / "blank.pdf")
    s = PdfSession.open(tmp_path / "blank.pdf")
    yield s
    s.close()


def _textbox(session, style, text="A text box in Times with a bold word"):
    page = session.doc[0]
    from planetpdf.core.redaction.marks import from_display

    shown = (100, 100, 300, 100 + K.textbox_height(text, 200, style.font_size, style, session.doc))
    spec = K.MarkupSpec(K.new_id(), 0, K.Kind.TEXTBOX, from_display(page, shown), style=style, text=text)
    K.add_markup(session, spec)
    return spec


def _ap_fonts(session) -> str:
    page = session.doc[0]
    annot = next(page.annots())
    return " ".join(str(f) for f in session.doc.get_page_fonts(0)) + str(annot.xref)


def test_text_box_in_another_font_with_a_bold_word(session):
    style = K.Style(color=(0.8, 0, 0), font="Times Roman", font_size=14, width=1,
                    runs=((32, 36, True, False, False, False),))
    spec = _textbox(session, style)
    [item] = K.list_markup(session)
    assert item.spec.style == style and item.spec.text == spec.text  # (read back from the PDF)
    xref = item.xref
    doc = session.doc
    assert "Times" in doc.xref_get_key(xref, "RC")[1] and "font-weight:bold" in doc.xref_get_key(xref, "RC")[1]
    words = session.doc[0].get_text("words")  # (annotation text: drawn by us, upright)
    assert [w[4] for w in words][:3] == ["A", "text", "box"]
    # every word inside the box as displayed
    box = pymupdf.Rect(item.rect) * session.doc[0].rotation_matrix
    assert all(box.contains(pymupdf.Rect(w[:4]) * session.doc[0].rotation_matrix) for w in words)
    # moved: still drawn by us
    K.replace_markup(session, spec, K.transformed(spec, pymupdf.Rect(spec.rect) + (50, 50, 50, 50)))
    assert doc.xref_get_key(K.list_markup(session)[0].xref, "RC")[0] != "null"


def test_plain_text_boxes_stay_as_mupdf_draws_them(session):
    _textbox(session, K.Style(font_size=12))
    xref = K.list_markup(session)[0].xref
    assert session.doc.xref_get_key(xref, "RC")[0] == "null"


def test_callout_in_another_font(session):
    page = session.doc[0]
    style = K.Style(font="Courier", font_size=12, width=1, bold=True)
    box, tip = (200, 200, 360, 240), (120, 320)
    spec = K.MarkupSpec(K.new_id(), 0, K.Kind.CALLOUT, K.callout_spec_rect(box, tip),
                        points=((box[0], box[1]), (box[2], box[3]), tip), style=style, text="Look here")
    K.add_markup(session, spec)
    page = session.doc[0]  # (drawing the text added and deleted a scratch page)
    fonts = {f[3] for f in page.get_fonts(full=True)} | {s["font"] for b in page.get_text("dict")["blocks"]
                                                         for l in b["lines"] for s in l["spans"]}
    assert any("Courier" in f for f in fonts)


def test_placed_text_keeps_its_bold_word(session):
    style = K.Style(font_size=12, runs=((5, 9, True, False, False, False),))
    spec = K.placed_text(session, 0, (100, 100), "with bold word", style)
    K.add_markup(session, spec)
    [item] = K.list_markup(session)
    assert item.spec.style.runs == style.runs
    wider = K.restyled_text(session, item.spec, text="with bold words")
    assert wider.style.runs == style.runs
    italic = K.restyled_text(session, item.spec, italic=True)  # (the bold word stays bold, italic too)
    assert italic.style.runs == ((5, 9, True, True, False, False),) and italic.style.italic
    assert K.restyled_text(session, item.spec, bold=False).style.runs == ()  # (bold off for all: that word too)
    assert replace(italic.style, runs=()) != italic.style
