from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import pages as P
from planetpdf.core.document import PasswordRequired, PdfError, PdfSession
from tests.fixtures import generate as g


def _labels(session: PdfSession) -> list[str]:
    """Identify pages by their first text line, e.g. 'Page 2: 792x612' ('' if blank)."""
    return [
        (session.doc[i].get_text().strip().splitlines() or [""])[0]
        for i in range(session.page_count)
    ]


@pytest.fixture
def mixed(pdf_copy):
    session = PdfSession.open(pdf_copy("mixed_sizes.pdf"))
    yield session
    session.close()


LABELS = [f"Page {i + 1}: {w}x{h}" for i, (w, h) in enumerate(g.MIXED_SIZES)]


# --- plan_move ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pages", "dest", "expected"),
    [
        ([0], 3, [1, 2, 0]),  # first to end
        ([2], 0, [2, 0, 1]),  # last to front
        ([0], 2, [1, 0, 2]),  # drop before old index 2
        ([1], 1, [0, 1, 2]),  # drop onto itself: no change
        ([1], 2, [0, 1, 2]),  # drop just after itself: no change
        ([0, 2], 1, [0, 2, 1]),  # multi-select keeps relative order
        ([2, 0], 3, [1, 0, 2]),  # selection order doesn't matter
    ],
)
def test_plan_move(pages, dest, expected):
    assert P.plan_move(3, pages, dest) == expected


def test_plan_move_rejects_bad_destination():
    with pytest.raises(PdfError):
        P.plan_move(3, [0], 4)


# --- reorder --------------------------------------------------------------------------


def test_reorder_and_undo(mixed):
    order = P.plan_move(3, [0], 3)
    mapping = P.reorder_pages(mixed, order)
    assert mapping == [1, 2, 0]
    assert _labels(mixed) == [LABELS[1], LABELS[2], LABELS[0]]
    assert mixed.page_size(2) == pytest.approx(g.MIXED_SIZES[0])
    assert mixed.is_modified

    P.reorder_pages(mixed, P.inverse_order(order))
    assert _labels(mixed) == LABELS


def test_reorder_rejects_non_permutation(mixed):
    with pytest.raises(PdfError):
        P.reorder_pages(mixed, [0, 0, 1])


def test_reorder_survives_save(mixed):
    P.reorder_pages(mixed, [2, 0, 1])
    mixed.save()
    assert _labels(mixed) == [LABELS[2], LABELS[0], LABELS[1]]


def test_reorder_keeps_form_fields(pdf_copy):
    session = PdfSession.open(pdf_copy("acroform.pdf"))
    P.insert_blank_page(session, 0)
    P.reorder_pages(session, [1, 0])
    session.save()
    page = session.doc[0]
    names = {w.field_name for w in page.widgets()}
    assert names == set(g.FORM_FIELDS)
    session.close()


# --- delete / restore --------------------------------------------------------------------


def test_delete_and_restore(mixed):
    removed, mapping = P.delete_pages(mixed, [0, 2])
    assert mapping == [1]
    assert _labels(mixed) == [LABELS[1]]

    mapping = P.restore_pages(mixed, removed)
    assert mapping == [None, 0, None]
    assert _labels(mixed) == LABELS
    for i, size in enumerate(g.MIXED_SIZES):
        assert mixed.page_size(i) == pytest.approx(size)


def test_cannot_delete_every_page(mixed):
    with pytest.raises(PdfError):
        P.delete_pages(mixed, [0, 1, 2])
    assert mixed.page_count == 3


def test_delete_rejects_out_of_range(mixed):
    with pytest.raises(PdfError):
        P.delete_pages(mixed, [5])


def test_deleted_page_content_is_gone_after_save(pdf_copy):
    session = PdfSession.open(pdf_copy("mixed_sizes.pdf"))
    P.delete_pages(session, [1])
    session.save()
    with pymupdf.open(session.path) as doc:
        text = "".join(page.get_text() for page in doc)
        expanded = doc.tobytes(expand=255)
    assert LABELS[1] not in text
    assert not g.bytes_contain_text(expanded, LABELS[1])
    session.close()


# --- insert --------------------------------------------------------------------------------


def test_insert_blank_page_default_size(mixed):
    mapping = P.insert_blank_page(mixed, 2)
    assert mapping == [0, 1, None, 2]
    assert mixed.page_count == 4
    assert mixed.page_size(2) == pytest.approx(g.MIXED_SIZES[1])  # size of the page before
    assert mixed.doc[2].get_text().strip() == ""


def test_insert_blank_page_at_ends(mixed):
    P.insert_blank_page(mixed, 0, (100, 200))
    P.insert_blank_page(mixed, mixed.page_count)
    assert mixed.page_size(0) == pytest.approx((100, 200))
    assert mixed.page_count == 5
    assert _labels(mixed)[1:4] == LABELS


def test_insert_pdf_pages_and_undo(mixed, fixture_dir):
    data = (fixture_dir / "text.pdf").read_bytes()
    count, mapping = P.insert_pdf_pages(mixed, data, 1, name="text.pdf")
    assert count == g.TEXT_PAGE_COUNT
    assert mapping == [0, None, None, None, 1, 2]
    assert g.SSN in mixed.doc[1].get_text()
    assert _labels(mixed)[4] == LABELS[1]

    mapping = P.remove_inserted(mixed, 1, count)
    assert mapping == [0, 4, 5]
    assert _labels(mixed) == LABELS


def test_insert_pdf_append(mixed, fixture_dir):
    data = (fixture_dir / "text.pdf").read_bytes()
    P.insert_pdf_pages(mixed, data, mixed.page_count)
    assert g.SSN in mixed.doc[mixed.page_count - 1].get_text()


def test_insert_encrypted_pdf_needs_password(mixed, fixture_dir):
    data = (fixture_dir / "encrypted.pdf").read_bytes()
    with pytest.raises(PasswordRequired):
        P.insert_pdf_pages(mixed, data, 0)
    count, _ = P.insert_pdf_pages(mixed, data, 0, password=g.ENCRYPTED_USER_PW)
    assert count == 1
    assert g.ENCRYPTED_TEXT in mixed.doc[0].get_text()


def test_insert_rejects_bad_position(mixed):
    with pytest.raises(PdfError):
        P.insert_blank_page(mixed, 10)


# --- rotate ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("degrees", "expected"), [(90, 90), (180, 180), (270, 270), (-90, 270), (-270, 90)])
def test_rotate_directions_and_amounts(mixed, degrees, expected):
    mapping = P.rotate_pages(mixed, [0], degrees)
    assert mapping == [None, 1, 2]
    assert mixed.doc[0].rotation == expected
    w, h = g.MIXED_SIZES[0]
    assert mixed.page_size(0) == pytest.approx((h, w) if expected in (90, 270) else (w, h))


def test_rotate_is_exactly_reversible(mixed):
    P.rotate_pages(mixed, [0, 2], 90)
    P.rotate_pages(mixed, [0, 2], -90)
    assert [mixed.doc[i].rotation for i in range(3)] == [0, 0, 0]


def test_rotate_accumulates_and_survives_save(mixed):
    P.rotate_pages(mixed, [1], 90)
    P.rotate_pages(mixed, [1], 270)  # full circle
    P.rotate_pages(mixed, range(3), 180)
    mixed.save()
    assert [mixed.doc[i].rotation for i in range(3)] == [180, 180, 180]
    assert _labels(mixed) == LABELS  # content unchanged, only orientation


def test_rotate_rejects_odd_angles(mixed):
    with pytest.raises(PdfError):
        P.rotate_pages(mixed, [0], 45)


# --- export ----------------------------------------------------------------------------------


def test_export_pages_in_given_order(mixed, tmp_path):
    target = tmp_path / "out" / "export.pdf"
    target.parent.mkdir()
    P.rotate_pages(mixed, [2], 90)  # unsaved edit is included
    P.export_pages(mixed, [2, 0], target)
    with pymupdf.open(target) as doc:
        assert doc.page_count == 2
        assert doc[0].get_text().startswith(LABELS[2])
        assert doc[0].rotation == 90
        assert doc[1].get_text().startswith(LABELS[0])
    assert mixed.page_count == 3  # open document untouched
    assert not list(target.parent.glob(".~planetpdf-*"))


def test_export_refuses_to_overwrite_open_document(mixed):
    with pytest.raises(PdfError):
        P.export_pages(mixed, [0], mixed.path)


def test_export_from_encrypted_is_unencrypted(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("encrypted.pdf"), g.ENCRYPTED_USER_PW)
    P.export_pages(session, [0], tmp_path / "plain.pdf")
    session.close()
    with pymupdf.open(tmp_path / "plain.pdf") as doc:
        assert not doc.needs_pass
        assert g.ENCRYPTED_TEXT in doc[0].get_text()
