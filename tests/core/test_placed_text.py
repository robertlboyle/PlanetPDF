from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core import placed_text as PT
from planetpdf.core.document import PdfSession
from planetpdf.core.markup import Kind, Style
from planetpdf.core.redaction.marks import to_display


@pytest.fixture
def session(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


def _words(page, word):
    return [w for w in page.get_text("words") if w[4] == word]


def test_place_move_resize_restyle_and_flatten(session):
    spec = K.placed_text(session, 0, (300, 400), "Zebedee Quill\n12 Main St", Style(color=(0, 0, 0.6), font_size=11))
    assert spec.kind is Kind.TEXT and spec.kind.placed and spec.kind.uses_appearance
    K.add_markup(session, spec)
    [item] = [i for i in K.list_markup(session) if i.kind is Kind.TEXT]
    assert item.spec.text == "Zebedee Quill\n12 Main St" and item.spec.style.font_size == 11
    # the first baseline sits where the on-page editor showed it
    width, height = PT.layout_size(spec.text, 11)
    assert pymupdf.Rect(spec.rect).width == pytest.approx(width) and pymupdf.Rect(spec.rect).height == pytest.approx(height)

    bigger = K.restyled_text(session, spec, font_size=PT.size_steps(11, 1))
    assert bigger.style.font_size == 12 and pymupdf.Rect(bigger.rect).tl == pymupdf.Rect(spec.rect).tl
    moved = K.fitted_text(session, bigger, pymupdf.Rect(bigger.rect) + (50, 20, 50, 20))
    assert moved.style.font_size == 12 and pymupdf.Rect(moved.rect).tl == pymupdf.Point(350, 420)
    r = pymupdf.Rect(moved.rect)
    scaled = K.fitted_text(session, moved, (r.x0, r.y0, r.x0 + r.width * 2, r.y0 + r.height * 2))
    assert scaled.style.font_size == pytest.approx(24)
    K.replace_markup(session, spec, scaled)

    contents_before = session.doc[0].get_contents()
    done = K.flatten_page(session, 0)
    assert len(done) == 1 and not [i for i in K.list_markup(session) if i.kind is Kind.TEXT]
    page = session.doc[0]
    assert len(_words(page, "Zebedee")) == 1  # now real page text
    span = next(s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]
                if "Zebedee" in s["text"])
    assert span["size"] == pytest.approx(24, abs=0.1) and span["origin"][0] == pytest.approx(350 + PT.PADDING, abs=0.1)
    K.unflatten_page(session, done)
    assert [i.spec.style.font_size for i in K.list_markup(session) if i.kind is Kind.TEXT] == [24]
    assert session.doc[0].get_contents() == contents_before  # (text extraction also reads annotations)


def test_rotated_page_reads_upright(session):
    session.doc[0].set_rotation(90)
    spec = K.placed_text(session, 0, (100, 100), "Upright words", Style(font_size=14))
    K.add_markup(session, spec)
    page = session.doc[0]
    shown = to_display(page, spec.rect)
    assert shown.width > shown.height  # a line of text, not a column
    assert (shown.x0, shown.y0) == pytest.approx((100, 100))
    K.flatten_page(session, 0)
    page = session.doc[0]
    word = _words(page, "Upright")[0]
    w = (pymupdf.Rect(word[:4]) * page.rotation_matrix).normalize()
    assert w.width > w.height and w.x0 == pytest.approx(100 + PT.PADDING, abs=1.5)


def test_non_latin_text_uses_bundled_font(session):
    spec = K.placed_text(session, 0, (72, 600), "Zoë Łukasz Ωmega", Style())
    K.add_markup(session, spec)
    K.flatten_page(session, 0)
    assert "Łukasz" in session.doc[0].get_text()
    assert len(spec.source) < 100_000  # subset


def test_size_steps():
    assert PT.size_steps(11, 1) == 12 and PT.size_steps(12, -1) == 11
    assert PT.size_steps(144, 1) == 144 and PT.size_steps(6, -1) == 6
