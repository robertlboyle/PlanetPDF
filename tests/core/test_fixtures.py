"""Sanity checks that the generated fixtures contain what later tests rely on."""

from __future__ import annotations

import pymupdf
import pypdf

from tests.fixtures import generate as g


def test_text_fixture_has_sensitive_values(fixture_dir):
    with pymupdf.open(fixture_dir / "text.pdf") as doc:
        assert doc.page_count == g.TEXT_PAGE_COUNT
        text = doc[0].get_text()
        for value in (g.SSN, g.PHONE, g.EMAIL, g.ACCOUNT):
            assert value in text
        assert doc.metadata["subject"] == g.SSN
    # The independent library sees the same text.
    reader = pypdf.PdfReader(fixture_dir / "text.pdf")
    assert g.SSN in reader.pages[0].extract_text()


def test_image_fixture_has_one_image_with_marker(fixture_dir):
    with pymupdf.open(fixture_dir / "image.pdf") as doc:
        images = doc[0].get_images(full=True)
        assert len(images) == 1
        pix = pymupdf.Pixmap(doc, images[0][0])
        assert (pix.width, pix.height) == (g.IMAGE_SIZE, g.IMAGE_SIZE)
        center = g.IMAGE_MARK_RECT.x0 + 10, g.IMAGE_MARK_RECT.y0 + 10
        assert pix.pixel(*center)[:3] == g.IMAGE_MARK_RGB
        assert pix.pixel(5, 5)[:3] == g.IMAGE_BASE_RGB
        placed = doc[0].get_image_rects(images[0][0])[0]
        assert placed == g.IMAGE_RECT


def test_vector_fixture_has_expected_shapes(fixture_dir):
    with pymupdf.open(fixture_dir / "vector.pdf") as doc:
        rects = [d["rect"] for d in doc[0].get_drawings()]
    for expected in (g.VECTOR_INSIDE, g.VECTOR_CROSSING, g.VECTOR_OUTSIDE):
        assert any(abs(r.x0 - expected.x0) < 2 and abs(r.y1 - expected.y1) < 2 for r in rects)
    assert g.VECTOR_REDACT_RECT.contains(g.VECTOR_INSIDE)
    assert g.VECTOR_REDACT_RECT.intersects(g.VECTOR_CROSSING)
    assert not g.VECTOR_REDACT_RECT.contains(g.VECTOR_CROSSING)
    assert not g.VECTOR_REDACT_RECT.intersects(g.VECTOR_OUTSIDE)


def test_acroform_fixture_has_every_field_type(fixture_dir):
    doc = pymupdf.open(fixture_dir / "acroform.pdf")
    page = doc[0]  # widgets hold only a weak reference to their page: keep it alive
    by_name: dict[str, list[pymupdf.Widget]] = {}
    for w in page.widgets():
        by_name.setdefault(w.field_name, []).append(w)
    assert set(by_name) == set(g.FORM_FIELDS)
    assert len(by_name["plan"]) == 2
    assert all(w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON for w in by_name["plan"])
    assert sorted(w.on_state() for w in by_name["plan"]) == g.FORM_RADIO_CHOICES
    assert by_name["state"][0].choice_values == g.FORM_STATE_CHOICES
    assert "AFDate_FormatEx" in (by_name["start_date"][0].script_format or "")
    doc.close()


def test_multirevision_fixture_hides_but_retains_rev1(fixture_dir):
    path = fixture_dir / "multirevision.pdf"
    raw = path.read_bytes()
    assert raw.count(b"%%EOF") >= 2, "expected an incremental update"
    assert g.bytes_contain_text(raw, g.REV1_SECRET), "rev 1 content should survive in the bytes"
    with pymupdf.open(path) as doc:
        text = doc[0].get_text()
    assert g.REV1_SECRET not in text
    assert g.REV2_TEXT in text


def test_encrypted_fixture_requires_password(fixture_dir):
    with pymupdf.open(fixture_dir / "encrypted.pdf") as doc:
        assert doc.needs_pass
        assert doc.authenticate(g.ENCRYPTED_USER_PW)
        assert g.ENCRYPTED_TEXT in doc[0].get_text()
