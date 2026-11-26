"""Redaction correctness: content under marks is really gone, by two independent parsers
and at the byte level; nothing else is damaged."""

from __future__ import annotations

import pymupdf
import pypdf
import pytest

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionOptions, redact_to_file
from planetpdf.core.redaction.patterns import PRESETS
from planetpdf.core.redaction.sanitize import SanitizeOptions
from planetpdf.core.redaction.search import build_pattern, search_pages
from planetpdf.core.redaction.verify import Status
from tests.fixtures import generate as g

NO_SANITIZE = SanitizeOptions(**{k: False for k in SanitizeOptions.__dataclass_fields__})
# text.pdf deliberately has the SSN in its metadata (Subject), so tests that expect a
# clean result must at least scrub metadata.
META_ONLY = SanitizeOptions(**{k: k == "metadata" for k in SanitizeOptions.__dataclass_fields__})


def _mark_hits(session, pattern, label, pages=None):
    for hit in search_pages(session, pattern, pages):
        M.add_mark(session, M.make_spec(hit.page, hit.rects, label))


def _all_text_mupdf(path) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def _all_text_pypdf(path) -> str:
    return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(path).pages)


def _all_bytes(path) -> bytes:
    """Raw bytes plus every decompressed object/stream."""
    raw = path.read_bytes()
    with pymupdf.open(path) as doc:
        return raw + doc.tobytes(expand=255)


def _assert_gone(path, value: str) -> None:
    assert value not in _all_text_mupdf(path), f"{value} extractable by PyMuPDF"
    assert value not in _all_text_pypdf(path), f"{value} extractable by pypdf"
    assert not g.bytes_contain_text(_all_bytes(path), value), f"{value} present in file bytes"


@pytest.fixture
def text_session(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


# --- text ------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["ssn", "phone", "email", "account"])
def test_preset_values_removed_everywhere(text_session, tmp_path, key):
    _mark_hits(text_session, PRESETS[key].pattern, PRESETS[key].label)
    out = tmp_path / "out.pdf"
    result = redact_to_file(text_session, out, RedactionOptions(), META_ONLY)
    value = {"ssn": g.SSN, "phone": g.PHONE, "email": g.EMAIL, "account": g.ACCOUNT}[key]
    _assert_gone(out, value)
    assert result.report.passed, result.report.as_text()
    assert result.marks_applied == g.TEXT_PAGE_COUNT
    # Everything else is intact.
    text = _all_text_mupdf(out)
    assert "Customer record" in text and "This line contains no sensitive data." in text
    others = {g.SSN, g.PHONE, g.EMAIL, g.ACCOUNT} - {value}
    assert all(o in text for o in others)


def test_original_untouched_and_marks_kept(text_session, tmp_path):
    original = text_session.path.read_bytes()
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN")
    redact_to_file(text_session, tmp_path / "out.pdf", RedactionOptions(), NO_SANITIZE)
    assert text_session.path.read_bytes() == original
    assert len(M.list_marks(text_session)) == g.TEXT_PAGE_COUNT
    assert g.SSN in text_session.doc[0].get_text()


def test_verification_catches_metadata_leak(text_session, tmp_path):
    """Redacting page text but leaving the metadata (which also holds the SSN) must fail."""
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN")
    result = redact_to_file(text_session, tmp_path / "out.pdf", RedactionOptions(), NO_SANITIZE)
    assert not result.report.passed
    failure = result.report.failures[0]
    assert "can still be extracted" in failure.title
    assert "file data" in failure.detail
    assert g.SSN not in result.report.as_text()  # the report masks the value it found


def test_overwrite_original_reopens_session(text_session):
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN")
    result = redact_to_file(text_session, text_session.path, RedactionOptions(), META_ONLY)
    assert result.report.passed
    assert M.list_marks(text_session) == []
    assert g.SSN not in text_session.doc[0].get_text()
    _assert_gone(text_session.path, g.SSN)


def test_no_marks_is_an_error(text_session, tmp_path):
    with pytest.raises(PdfError):
        redact_to_file(text_session, tmp_path / "x.pdf", RedactionOptions(), NO_SANITIZE)


def test_neighboring_lines_survive(text_session, tmp_path):
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN", pages=[0])
    out = tmp_path / "out.pdf"
    redact_to_file(text_session, out, RedactionOptions(), NO_SANITIZE)
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    assert "Name: John Q. Public" in text
    assert f"Phone: {g.PHONE}" in text
    assert "SSN:" in text  # the label next to the value stays


def test_fill_color_and_overlay_text(text_session, tmp_path):
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN", pages=[0])
    out = tmp_path / "out.pdf"
    opts = RedactionOptions(fill=(1.0, 0.0, 0.0), overlay_text="REDACTED")
    result = redact_to_file(text_session, out, opts, META_ONLY)
    # Regression (user report): the overlay label itself was reported as leftover text.
    assert result.report.passed, result.report.as_text()
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert "REDACTED" in page.get_text()
        mark = page.search_for("REDACTED")[0]
        pix = page.get_pixmap(clip=pymupdf.Rect(mark.x0 - 2, mark.y0, mark.x0 - 1, mark.y1))
        r, gg, b = pix.pixel(0, pix.height // 2)[:3]
        assert r > 200 and gg < 60 and b < 60  # red fill beside the text


def test_hidden_text_under_mark_removed(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("redact_complex.pdf"))
    _mark_hits(session, PRESETS["ssn"].pattern, "SSN", pages=[0])
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    _assert_gone(out, g.SSN)
    _assert_gone(out, g.HIDDEN_SSN)  # invisible text in the same spot
    assert result.report.passed, result.report.as_text()


def test_overlapping_note_link_and_field_removed(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("redact_complex.pdf"))
    for key in ("ssn", "email", "account"):
        _mark_hits(session, PRESETS[key].pattern, key, pages=[0])
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    with pymupdf.open(out) as doc:
        page = doc[0]
        assert list(page.annots()) == []
        assert page.get_links() == []
        assert list(page.widgets()) == []
        assert "Keep this line intact." in page.get_text()
    assert result.overlapping_removed == {"annotations": 1, "links": 1, "form fields": 1}
    for value in (g.SSN, g.EMAIL, g.ACCOUNT, g.NOTE_TEXT):
        _assert_gone(out, value)


def test_rotated_page_redaction(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("redact_complex.pdf"))
    pattern = build_pattern(g.ROTATED_SECRET, regex=False, case_sensitive=True, whole_word=False)
    _mark_hits(session, pattern, "Search")
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    _assert_gone(out, g.ROTATED_SECRET)
    with pymupdf.open(out) as doc:
        assert doc[2].rotation == 90
        assert "Rotated page, other text." in doc[2].get_text()
    assert result.report.passed


def test_area_mark_removes_text_without_a_search(text_session, tmp_path):
    page = text_session.doc[0]
    area = page.search_for("Customer record")[0]
    M.add_mark(text_session, M.make_spec(0, [tuple(area + (-1, -1, 1, 1))], "Area"))
    out = tmp_path / "out.pdf"
    result = redact_to_file(text_session, out, RedactionOptions(), NO_SANITIZE)
    with pymupdf.open(out) as doc:
        assert "Customer record" not in doc[0].get_text()
        assert "Customer record" in doc[1].get_text()  # only page 1 was marked
    assert result.report.passed
    assert any(c.title.startswith("Page 1: no text under") for c in result.report.checks)


# --- images and vector graphics -----------------------------------------------------------


def _image_pixel(path, page_no, x, y):
    with pymupdf.open(path) as doc:
        xref = doc[page_no].get_images()[0][0]
        return pymupdf.Pixmap(doc, xref).pixel(x, y)


def test_image_pixels_under_mark_are_blanked(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("image.pdf"))
    # Cover the red marker square (image px 50..150 == page 122..222, since 1 px == 1 pt).
    r = g.IMAGE_MARK_RECT
    area = (g.IMAGE_RECT.x0 + r.x0, g.IMAGE_RECT.y0 + r.y0, g.IMAGE_RECT.x0 + r.x1, g.IMAGE_RECT.y0 + r.y1)
    M.add_mark(session, M.make_spec(0, [area], "Area"))
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    assert _image_pixel(out, 0, 100, 100)[:3] != g.IMAGE_MARK_RGB  # red is gone
    assert _image_pixel(out, 0, 5, 5)[:3] == g.IMAGE_BASE_RGB  # outside the mark: intact
    assert _image_pixel(out, 0, 195, 195)[:3] == g.IMAGE_BASE_RGB
    assert result.report.passed, result.report.as_text()
    assert any("image areas under redactions are blank" in c.title for c in result.report.checks)


def test_image_with_transparency_is_blanked(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("redact_complex.pdf"))
    r = g.IMAGE_MARK_RECT
    base = g.ALPHA_IMAGE_RECT
    area = (base.x0 + r.x0, base.y0 + r.y0, base.x0 + r.x1, base.y0 + r.y1)
    M.add_mark(session, M.make_spec(1, [area], "Area"))
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    with pymupdf.open(out) as doc:
        page = doc[1]
        # Render the marked region: none of the red marker may remain.
        pix = page.get_pixmap(clip=pymupdf.Rect(area) + (4, 4, -4, -4), annots=False)
        reds = sum(
            1
            for y in range(0, pix.height, 4)
            for x in range(0, pix.width, 4)
            if pix.pixel(x, y)[0] > 150 and pix.pixel(x, y)[1] < 100
        )
        assert reds == 0
    assert result.report.passed, result.report.as_text()


def _drawing_rects(path):
    with pymupdf.open(path) as doc:
        return [d["rect"] for d in doc[0].get_drawings()]


def _has_shape(rects, expected):
    return any(abs(r.x0 - expected.x0) < 2 and abs(r.y1 - expected.y1) < 2 for r in rects)


def test_vector_default_removes_only_covered_shapes(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("vector.pdf"))
    M.add_mark(session, M.make_spec(0, [tuple(g.VECTOR_REDACT_RECT)], "Area"))
    out = tmp_path / "out.pdf"
    redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    rects = _drawing_rects(out)
    assert not _has_shape(rects, g.VECTOR_INSIDE)
    assert _has_shape(rects, g.VECTOR_CROSSING)  # only partly covered: kept by default
    assert _has_shape(rects, g.VECTOR_OUTSIDE)


def test_vector_touching_option_removes_crossing_shapes(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("vector.pdf"))
    M.add_mark(session, M.make_spec(0, [tuple(g.VECTOR_REDACT_RECT)], "Area"))
    out = tmp_path / "out.pdf"
    redact_to_file(session, out, RedactionOptions(remove_touching_graphics=True), NO_SANITIZE)
    session.close()
    rects = _drawing_rects(out)
    assert not _has_shape(rects, g.VECTOR_INSIDE)
    assert not _has_shape(rects, g.VECTOR_CROSSING)
    assert _has_shape(rects, g.VECTOR_OUTSIDE)


# --- revisions and encryption ------------------------------------------------------------


def test_no_earlier_revision_survives(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("multirevision.pdf"))
    pattern = build_pattern(g.REV2_TEXT, regex=False, case_sensitive=True, whole_word=False)
    _mark_hits(session, pattern, "Search")
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    raw = out.read_bytes()
    assert raw.count(b"%%EOF") == 1
    assert not g.bytes_contain_text(_all_bytes(out), g.REV1_SECRET)  # the old revision
    _assert_gone(out, g.REV2_TEXT)
    assert result.report.passed


def test_encrypted_input_gives_unencrypted_output(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("encrypted.pdf"), g.ENCRYPTED_USER_PW)
    pattern = build_pattern("Encrypted", regex=False, case_sensitive=True, whole_word=True)
    _mark_hits(session, pattern, "Search")
    out = tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE)
    session.close()
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass
        assert "fixture body" in doc[0].get_text()
        assert "Encrypted" not in doc[0].get_text()
    assert result.report.passed


@pytest.mark.parametrize("overwrite", [False, True])
def test_redacted_output_can_be_protected_and_is_checked_before_encryption(pdf_copy, tmp_path, overwrite):
    """Backlog item (2026-10-02): the redacted copy keeps a password. Verification runs
    on the file before it's encrypted (encrypted bytes would hide any leftover)."""
    from planetpdf.core.document import Protection

    session = PdfSession.open(pdf_copy("encrypted.pdf"), g.ENCRYPTED_USER_PW)
    pattern = build_pattern("Encrypted", regex=False, case_sensitive=True, whole_word=True)
    _mark_hits(session, pattern, "Search")
    protection = Protection("new-open", "new-owner", allow_copy=False)
    out = session.path if overwrite else tmp_path / "out.pdf"
    result = redact_to_file(session, out, RedactionOptions(), NO_SANITIZE, protection=protection)
    assert result.report.passed and result.report.output == out
    assert any(c.title == "Protected with a password" for c in result.report.checks)
    if overwrite:  # (the session reopened on the protected result, with its password)
        assert session.is_encrypted and "Encrypted" not in session.doc[0].get_text()
    session.close()
    with pymupdf.open(out) as doc:
        assert doc.needs_pass and doc.authenticate("new-open")
        assert "fixture body" in doc[0].get_text() and "Encrypted" not in doc[0].get_text()
        assert not doc.permissions & pymupdf.PDF_PERM_COPY


def test_report_flags_unmarked_occurrences_as_warning(text_session, tmp_path):
    # Mark the SSN on page 1 only; pages 2-3 still have it.
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN", pages=[0])
    # (the fixture's subject holds the SSN too: kept, that's a failure since 2026-11-25)
    result = redact_to_file(text_session, tmp_path / "out.pdf", RedactionOptions(), META_ONLY)
    assert result.report.passed  # not a failure...
    warns = [c for c in result.report.warnings if "wasn't marked" in c.title]
    assert warns and "[2, 3]" in warns[0].detail  # ...but clearly reported


# --- shapes partly under a box (security review 2026-11-21) -----------------------------------------


def _signature_scribble(tmp_path):
    """A page with a curved "drawn signature" that sticks out of the box we'll draw over it."""
    path = tmp_path / "scribble.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_bezier((100, 200), (150, 120), (220, 280), (300, 200))
    shape.draw_bezier((300, 200), (330, 170), (360, 230), (380, 200))
    shape.finish(color=(0, 0, 0.6), width=2)
    shape.commit()
    page.insert_text((72, 500), "Plain text elsewhere", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize("touching, fails", [(False, True), (True, False)])
def test_curved_shapes_partly_under_a_box_fail_unless_removed(tmp_path, touching, fails):
    session = PdfSession.open(_signature_scribble(tmp_path))
    try:
        M.add_mark(session, M.make_spec(0, [(110, 150, 330, 260)], "Area"))
        result = redact_to_file(session, tmp_path / "out.pdf", RedactionOptions(remove_touching_graphics=touching),
                                NO_SANITIZE)
    finally:
        session.close()
    curved = [c for c in result.report.failures if "curved or slanted shape" in c.title]
    assert bool(curved) is fails
    assert result.report.passed is not fails


def test_an_unreadable_second_parser_is_a_failure(text_session, tmp_path, monkeypatch):
    from planetpdf.core.redaction import verify

    monkeypatch.setattr(verify, "_pypdf_text", lambda data: ["\x00pypdf-error:broken"])
    _mark_hits(text_session, build_pattern(g.SSN, regex=False, case_sensitive=True, whole_word=False), "SSN")
    result = redact_to_file(text_session, tmp_path / "out.pdf", RedactionOptions(), META_ONLY)
    assert not result.report.passed
    assert any("second parser" in c.title for c in result.report.failures)


def test_unmarked_occurrences_dont_excuse_the_term_in_the_files_information(text_session, tmp_path):
    _mark_hits(text_session, PRESETS["ssn"].pattern, "SSN", pages=[0])
    result = redact_to_file(text_session, tmp_path / "out.pdf", RedactionOptions(), NO_SANITIZE)
    assert any("can still be extracted" in c.title and "document information" in c.detail
               for c in result.report.failures)
