from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import pages as P
from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.patterns import PRESETS
from planetpdf.core.redaction.search import build_pattern, search_pages
from tests.fixtures import generate as g


@pytest.fixture
def text_session(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


# --- presets ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [
        ("ssn", ["123-45-6789", "123 45 6789"], ["000-12-3456", "666-12-3456", "123-00-4567", "123-45-0000", "123-456789", "1234-56-7890"]),
        ("phone", ["(555) 867-5309", "555-867-5309", "555.867.5309", "+1 555 867 5309"], ["867-5309", "555-8675-309"]),
        ("email", ["a.b+c@example.co.uk", "john.public@example.com"], ["john@", "@example.com", "john@example"]),
        ("account", ["12345678", "0012345678901", "12345678901234567"], ["1234567", "123456789012345678"]),
    ],
)
def test_presets(key, good, bad):
    pattern = PRESETS[key].pattern
    for value in good:
        assert pattern.fullmatch(value) or pattern.search(f"x {value} y"), f"{key} should match {value}"
    for value in bad:
        found = pattern.search(f"x {value} y")
        assert not found or found.group() != value, f"{key} should not match {value}"


def test_presets_find_fixture_values(text_session):
    for key, expected in [("ssn", g.SSN), ("phone", g.PHONE), ("email", g.EMAIL), ("account", g.ACCOUNT)]:
        hits = search_pages(text_session, PRESETS[key].pattern)
        assert [h.text for h in hits] == [expected] * g.TEXT_PAGE_COUNT, key
        assert {h.page for h in hits} == set(range(g.TEXT_PAGE_COUNT))


# --- search -----------------------------------------------------------------------------


def test_exact_search_options(text_session):
    exact = build_pattern("john q. public", regex=False, case_sensitive=False, whole_word=False)
    assert len(search_pages(text_session, exact)) == g.TEXT_PAGE_COUNT
    cased = build_pattern("john q. public", regex=False, case_sensitive=True, whole_word=False)
    assert search_pages(text_session, cased) == []
    part = build_pattern("Publ", regex=False, case_sensitive=True, whole_word=False)
    assert len(search_pages(text_session, part, pages=[0])) == 1
    whole = build_pattern("Publ", regex=False, case_sensitive=True, whole_word=True)
    assert search_pages(text_session, whole, pages=[0]) == []


def test_regex_search_and_errors(text_session):
    pattern = build_pattern(r"Page \d", regex=True, case_sensitive=True, whole_word=False)
    assert [h.text for h in search_pages(text_session, pattern)] == ["Page 1", "Page 2", "Page 3"]
    with pytest.raises(PdfError):
        build_pattern("(unclosed", regex=True, case_sensitive=False, whole_word=False)
    with pytest.raises(PdfError):
        build_pattern("   ", regex=False, case_sensitive=False, whole_word=False)


def test_hit_rect_covers_exactly_the_match(text_session):
    hit = search_pages(text_session, PRESETS["ssn"].pattern, pages=[0])[0]
    assert len(hit.rects) == 1
    page = text_session.doc[0]
    assert page.get_textbox(pymupdf.Rect(hit.rects[0])).strip() == g.SSN
    assert "SSN:" in hit.context


def test_phrase_across_line_wrap(pdf_copy, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "the account holder is Jane", fontsize=12)
    page.insert_text((72, 86), "Doe and she lives here", fontsize=12)
    doc.save(tmp_path / "wrap.pdf")
    session = PdfSession.open(tmp_path / "wrap.pdf")
    hits = search_pages(session, build_pattern("Jane Doe", regex=False, case_sensitive=True, whole_word=True))
    assert len(hits) == 1
    assert len(hits[0].rects) == 2  # one rect per line
    session.close()


def test_rotated_page_search_uses_unrotated_coordinates(pdf_copy):
    session = PdfSession.open(pdf_copy("redact_complex.pdf"))
    pattern = build_pattern(g.ROTATED_SECRET, regex=False, case_sensitive=True, whole_word=False)
    hit = search_pages(session, pattern, pages=[2])[0]
    page = session.doc[2]
    assert page.rotation == 90
    assert page.get_textbox(pymupdf.Rect(hit.rects[0])).strip() == g.ROTATED_SECRET
    shown = M.to_display(page, hit.rects[0])
    assert page.rect.contains(shown)  # inside the rotated page as displayed
    assert M.from_display(page, tuple(shown)) == pytest.approx(hit.rects[0])
    session.close()


# --- marks --------------------------------------------------------------------------------


def test_add_list_remove_marks(text_session):
    hit = search_pages(text_session, PRESETS["ssn"].pattern, pages=[1])[0]
    spec = M.make_spec(1, hit.rects, "SSN")
    M.add_mark(text_session, spec)
    marks = M.list_marks(text_session)
    assert [(m.mark_id, m.page, m.label, m.ours) for m in marks] == [(spec.mark_id, 1, "SSN", True)]
    assert M.mark_text(text_session, marks[0]) == g.SSN

    removed = M.remove_marks(text_session, [spec.mark_id])
    assert [(s.mark_id, s.page, s.label) for s in removed] == [(spec.mark_id, 1, "SSN")]
    assert removed[0].rects[0] == pytest.approx(spec.rects[0], abs=1e-3)  # PDF float precision
    assert M.list_marks(text_session) == []
    M.add_marks(text_session, removed)  # redo/undo round trip
    again = M.list_marks(text_session)[0]
    assert (again.mark_id, again.label) == (spec.mark_id, "SSN")
    assert again.rects[0] == pytest.approx(spec.rects[0], abs=1e-3)


def test_multi_rect_mark_is_one_mark(text_session):
    spec = M.make_spec(0, [(72, 60, 150, 75), (72, 80, 150, 95)], "Area")
    M.add_mark(text_session, spec)
    marks = M.list_marks(text_session)
    assert len(marks) == 1
    assert len(marks[0].rects) == 2


def test_tiny_area_rejected():
    with pytest.raises(PdfError):
        M.make_spec(0, [(10, 10, 10.5, 30)], "Area")


def test_marks_survive_save_reopen_and_page_moves(text_session):
    spec = M.make_spec(2, [(72, 60, 200, 80)], "Area")
    M.add_mark(text_session, spec)
    text_session.save()
    assert [m.page for m in M.list_marks(text_session)] == [2]
    P.reorder_pages(text_session, [2, 0, 1])
    assert [m.page for m in M.list_marks(text_session)] == [0]
    removed, _ = P.delete_pages(text_session, [0])
    assert M.list_marks(text_session) == []
    P.restore_pages(text_session, removed)
    marks = M.list_marks(text_session)
    assert [(m.mark_id, m.page) for m in marks] == [(spec.mark_id, 0)]


def test_foreign_redact_annotations_are_marks(pdf_copy):
    path = pdf_copy("text.pdf")
    with pymupdf.open(path) as doc:
        doc[0].add_redact_annot(pymupdf.Rect(72, 60, 200, 80))
        doc.saveIncr()
    session = PdfSession.open(path)
    marks = M.list_marks(session)
    assert len(marks) == 1 and not marks[0].ours
    assert marks[0].label == M.FOREIGN_LABEL
    M.remove_marks(session, [marks[0].mark_id])
    assert M.list_marks(session) == []
    session.close()


def test_search_counts_pages_and_can_be_stopped(text_session):
    """Long searches say which page (user 2026-10-23); False from progress stops with what's found."""
    pattern = build_pattern("Page", regex=False, case_sensitive=True, whole_word=True)
    seen = []
    hits = search_pages(text_session, pattern, progress=lambda done, total, stage: seen.append((done, total, stage)))
    assert seen == [(n, g.TEXT_PAGE_COUNT, "Searching page") for n in range(g.TEXT_PAGE_COUNT)]
    assert len(hits) == g.TEXT_PAGE_COUNT
    stopped = search_pages(text_session, pattern, progress=lambda done, total, stage: done < 1)
    assert [h.page for h in stopped] == [0]
