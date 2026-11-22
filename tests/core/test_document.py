from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core.document import (
    NotAPdf,
    PasswordRequired,
    PdfError,
    PdfSession,
    WrongPassword,
)
from planetpdf.core.render import render_page
from tests.fixtures import generate as g


def _leftover_temp_files(directory):
    return list(directory.glob(".~planetpdf-*"))


def test_open_reports_pages_and_sizes(pdf_copy):
    session = PdfSession.open(pdf_copy("mixed_sizes.pdf"))
    try:
        assert session.page_count == len(g.MIXED_SIZES)
        for i, size in enumerate(g.MIXED_SIZES):
            assert session.page_size(i) == pytest.approx(size)
        assert not session.is_modified
        assert not session.is_encrypted
        assert not session.has_digital_signatures
    finally:
        session.close()


def test_open_missing_file(tmp_path):
    with pytest.raises(PdfError):
        PdfSession.open(tmp_path / "nope.pdf")


def test_open_non_pdf(tmp_path):
    bogus = tmp_path / "bogus.pdf"
    bogus.write_text("this is not a pdf")
    with pytest.raises(NotAPdf):
        PdfSession.open(bogus)


def test_encrypted_needs_correct_password(pdf_copy):
    path = pdf_copy("encrypted.pdf")
    with pytest.raises(PasswordRequired):
        PdfSession.open(path)
    with pytest.raises(WrongPassword):
        PdfSession.open(path, "wrong")
    session = PdfSession.open(path, g.ENCRYPTED_USER_PW)
    assert session.is_encrypted
    session.close()


def test_render_page_dimensions(pdf_copy):
    session = PdfSession.open(pdf_copy("mixed_sizes.pdf"))
    try:
        page = render_page(session, 1, 2.0)
        assert (page.width, page.height) == (1584, 1224)
        assert page.stride >= page.width * 3
        assert len(page.samples) == page.stride * page.height
    finally:
        session.close()


def _add_note(session: PdfSession, text: str) -> None:
    session.doc[0].add_text_annot((100, 100), text)
    session.mark_modified()


def test_save_in_place_rewrites_and_reopens(pdf_copy):
    path = pdf_copy("text.pdf")
    session = PdfSession.open(path)
    _add_note(session, "hello")
    assert session.is_modified
    before = session.revision
    session.save()
    assert not session.is_modified
    assert session.revision == before + 1
    assert session.path == path.resolve()
    # The session keeps working after the reopen.
    assert session.doc[0].first_annot.info["content"] == "hello"
    session.close()
    assert not _leftover_temp_files(path.parent)

    with pymupdf.open(path) as doc:
        assert doc[0].first_annot.info["content"] == "hello"
        assert doc.page_count == g.TEXT_PAGE_COUNT


def test_full_save_drops_old_revisions(pdf_copy):
    path = pdf_copy("multirevision.pdf")
    session = PdfSession.open(path)
    session.mark_modified()
    session.save()
    session.close()
    with pymupdf.open(path) as doc:
        expanded = doc.tobytes(expand=255)  # decompress every stream before scanning
    assert not g.bytes_contain_text(path.read_bytes(), g.REV1_SECRET)
    assert not g.bytes_contain_text(expanded, g.REV1_SECRET)
    assert path.read_bytes().count(b"%%EOF") == 1


def test_save_as_leaves_original_untouched(pdf_copy, tmp_path):
    path = pdf_copy("text.pdf")
    original = path.read_bytes()
    target = tmp_path / "sub dir" / "copy with spaces.pdf"
    target.parent.mkdir()
    session = PdfSession.open(path)
    _add_note(session, "copy only")
    session.save_as(target)
    assert session.path == target.resolve()
    assert not session.is_modified
    session.close()

    assert path.read_bytes() == original
    with pymupdf.open(target) as doc:
        assert doc[0].first_annot.info["content"] == "copy only"
    assert not _leftover_temp_files(tmp_path)


def test_save_as_same_path_is_save(pdf_copy):
    path = pdf_copy("text.pdf")
    session = PdfSession.open(path)
    _add_note(session, "same")
    session.save_as(path)
    session.close()
    with pymupdf.open(path) as doc:
        assert doc[0].first_annot is not None


def test_save_keeps_encryption(pdf_copy):
    path = pdf_copy("encrypted.pdf")
    session = PdfSession.open(path, g.ENCRYPTED_USER_PW)
    _add_note(session, "still secret")
    session.save()
    session.close()
    with pymupdf.open(path) as doc:
        assert doc.needs_pass
        assert doc.authenticate(g.ENCRYPTED_USER_PW)
        assert doc[0].first_annot.info["content"] == "still secret"


def test_failed_save_keeps_session_usable(pdf_copy, tmp_path, monkeypatch):
    path = pdf_copy("text.pdf")
    session = PdfSession.open(path)
    _add_note(session, "unsaved")

    import planetpdf.core.document as document_module

    def boom(src, dst):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(document_module, "replace_with_retry", boom)
    with pytest.raises(PermissionError):
        session.save_as(tmp_path / "out.pdf")
    # Still open on the original file, and the temp file was cleaned up.
    assert session.path == path.resolve()
    assert session.page_count == g.TEXT_PAGE_COUNT
    assert not _leftover_temp_files(tmp_path)
    session.close()
