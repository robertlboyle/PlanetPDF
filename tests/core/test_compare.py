from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import compare as C
from tests.fixtures import generate as g


def _doc(lines_per_page: list[list[str]], rotation: int = 0) -> pymupdf.Document:
    doc = pymupdf.open()
    for lines in lines_per_page:
        page = doc.new_page()
        for n, line in enumerate(lines):
            page.insert_text((72, 72 + 20 * n), line, fontsize=12)
        page.set_rotation(rotation)
    return doc


def test_identical():
    a = _doc([["Hello world", "Second line"]])
    b = _doc([["Hello world", "Second line"]])
    result = C.compare(a, b)
    assert result.identical and result.summary() == "No differences in the text."


def test_word_changes_added_removed():
    a = _doc([["The price is 100 dollars", "Keep this line", "Remove this line"], ["Page two"]])
    b = _doc([["The price is 150 dollars", "Keep this line", "A brand new line"], ["Page two", "Extra ending"]])
    result = C.compare(a, b)
    kinds = [(c.kind, c.old_text, c.new_text) for c in result.changes]
    assert (C.CHANGED, "100", "150") in kinds
    assert (C.CHANGED, "Remove this", "A brand new") in kinds  # word level: "line" is the same
    assert (C.ADDED, "", "Extra ending") in kinds
    price = next(c for c in result.changes if c.old_text == "100")
    assert price.old_page == 0 and price.new_page == 0
    page, rect = price.new_places[0]
    word = next(w for w in b[0].get_text("words") if w[4] == "150")
    assert rect == pytest.approx(tuple(word[:4]), abs=0.01)
    assert result.counts()[C.ADDED] >= 1 and "added" in result.summary()


def test_moved_page_text_is_not_a_page_by_page_mess():
    a = _doc([["Alpha one"], ["Bravo two"], ["Charlie three"]])
    b = _doc([["Alpha one"], ["Inserted page"], ["Bravo two"], ["Charlie three"]])
    result = C.compare(a, b)
    assert [(c.kind, c.new_text) for c in result.changes] == [(C.ADDED, "Inserted page")]
    assert result.changes[0].new_page == 1
    assert "3 pages before, 4 after" in result.summary()


def test_places_are_displayed_coordinates_on_rotated_pages():
    a = _doc([["Rotated text here"]], rotation=90)
    b = _doc([["Rotated text there"]], rotation=90)
    [change] = C.compare(a, b).changes
    page = b[0]
    word = next(w for w in page.get_text("words") if w[4] == "there")
    expected = (pymupdf.Rect(word[:4]) * page.rotation_matrix).normalize()
    assert change.new_places[0][1] == pytest.approx(tuple(expected), abs=0.01)


def test_report_pdf(tmp_path):
    a = _doc([["The price is 100 dollars"]], rotation=90)
    b = _doc([["The price is 150 dollars"], ["New page"]])
    result = C.compare(a, b)
    out = C.comparison_report(a, b, result, tmp_path / "report.pdf", "old.pdf", "new.pdf")
    report = pymupdf.open(out)
    summary = report[0].get_text()
    assert "Document comparison" in summary and "“100” -> “150”" in summary
    pair = report[1]
    assert "old.pdf — page 1" in pair.get_text() and "new.pdf — page 1" in pair.get_text()
    # each page is shown as the reader sees it: the rotated old page is landscape
    # with its text running downwards; the new page is portrait with normal text
    old_word, new_word = sorted((w for w in pair.get_text("words") if w[4] == "price"), key=lambda w: w[0])
    assert (old_word[3] - old_word[1]) > (old_word[2] - old_word[0])
    assert (new_word[2] - new_word[0]) > (new_word[3] - new_word[1])
    assert a[0].rect.width > a[0].rect.height  # (A4, rotated)
    assert pair.rect.width == pytest.approx(36 * 2 + a[0].rect.width + 24 + b[0].rect.width)
    # and the highlight sits on the changed word in the displayed copy
    old_mark = C.compare(a, b).changes[0].old_places[0][1]
    old_100 = next(w for w in pair.get_text("words") if w[4] == "100")
    assert tuple(old_100[:4]) == pytest.approx(tuple(pymupdf.Rect(old_mark) + (36, 64, 36, 64)), abs=0.5)
    assert "(no such page)" in report[2].get_text()
    assert out.stat().st_size < 200_000


def test_fixture_files(pdf_copy):
    a = pymupdf.open(pdf_copy("text.pdf"))
    b = pymupdf.open(pdf_copy("text.pdf"))
    b[1].add_redact_annot(b[1].search_for(g.SSN)[0])
    b[1].apply_redactions()
    result = C.compare(a, b)
    [change] = result.changes
    assert change.kind == C.REMOVED and g.SSN in change.old_text and change.old_page == 1
