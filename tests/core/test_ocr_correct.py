from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import ocr
from planetpdf.core.document import PdfSession
from planetpdf.core.ocr_correct import correct_words, find_suspects, looks_wrong, ocr_words, word_picture


@pytest.fixture(params=[0, 90])
def session(tmp_path, request):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Visible text", fontsize=12)
    page.set_rotation(request.param)
    ocr.insert_ocr_text_doc(doc, 0, [ocr.OcrWord((72, 200, 140, 214), "c0mpany"),
                                     ocr.OcrWord((150, 200, 200, 214), "hello"),
                                     ocr.OcrWord((210, 200, 260, 214), "1990s")])
    path = tmp_path / "ocr.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    yield s
    s.close()


def test_only_layer_words_are_listed(session):
    assert [w.text for w in ocr_words(session.doc, 0)] == ["c0mpany", "hello", "1990s"]


def test_suspects():
    assert looks_wrong("c0mpany") and looks_wrong("l|ne")
    assert not looks_wrong("1990s") and not looks_wrong("$12.50") and not looks_wrong("1st")


def test_suspects_in_document(session):
    assert [s.text for s in find_suspects(session.doc, [0])] == ["c0mpany"]


def test_correction_rewrites_in_place(session):
    before = ocr_words(session.doc, 0)
    assert correct_words(session, 0, {0: "company"}) == 1
    after = ocr_words(session.doc, 0)
    assert [w.text for w in after] == ["company", "hello", "1990s"]
    assert after[1].origin == pytest.approx(before[1].origin, abs=0.01)  # (unchanged words stay put)
    assert after[1].size == pytest.approx(before[1].size, abs=0.01)
    assert abs(after[0].rect[0] - before[0].rect[0]) < 0.5
    assert session.doc[0].search_for("company")
    assert "Visible text" in session.doc[0].get_text()
    # again: nothing drifts
    correct_words(session, 0, {1: "Hello"})
    assert ocr_words(session.doc, 0)[1].origin == pytest.approx(before[1].origin, abs=0.01)


def test_empty_correction_drops_the_word(session):
    correct_words(session, 0, {2: ""})
    assert [w.text for w in ocr_words(session.doc, 0)] == ["c0mpany", "hello"]


def test_word_picture(session):
    png = word_picture(session.doc, 0, ocr_words(session.doc, 0)[0].rect)
    assert png.startswith(b"\x89PNG")
