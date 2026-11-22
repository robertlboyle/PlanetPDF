"""Header & Footer / Watermark / Bates / Background: Update and Remove (Acrobat, user
request 2026-10-02). PlanetPDF's marks are tagged Form XObjects carrying their settings."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import page_marks as PM
from planetpdf.core import snapshots
from planetpdf.core.document import PdfSession


@pytest.fixture
def session(tmp_path):
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 300), f"Body of page {n + 1}", fontname="helv", fontsize=12)
    doc[2].set_rotation(90)
    path = tmp_path / "three.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    yield s
    s.close()


def _text(session, index):
    return session.doc[index].get_text()


def test_header_is_tagged_and_removed_without_touching_the_body(session):
    spec = PM.HeaderFooterSpec(texts={"bottom_center": "Page {page} of {pages}", "top_left": "DRAFT"})
    PM.add_header_footer(session, [0, 1, 2], spec)
    assert "Page 2 of 3" in _text(session, 1) and "DRAFT" in _text(session, 2)
    assert PM.pages_with(session, "header") == [0, 1, 2] and PM.pages_with(session, "bates") == []
    got, pages = PM.saved_spec(session, "header")
    assert got == spec and pages == [0, 1, 2]
    assert PM.remove_marks(session, "header") == 6
    for i in range(3):
        assert "Page" not in _text(session, i).replace("Body of page", "") and "DRAFT" not in _text(session, i)
        assert f"Body of page {i + 1}" in _text(session, i)
    assert PM.pages_with(session, "header") == [] and PM.saved_spec(session, "header") is None


def test_bates_watermark_and_background_are_separate_kinds(session):
    PM.add_header_footer(session, [0, 1], PM.HeaderFooterSpec(texts={"bottom_right": "{bates}"}, bates_prefix="PN-"))
    PM.add_watermark(session, [1], PM.WatermarkSpec(text="SECRET"))
    PM.add_background(session, [2], PM.BackgroundSpec(color=(0.9, 0.9, 1.0)))
    assert PM.pages_with(session, "bates") == [0, 1]
    assert PM.pages_with(session, "watermark") == [1] and PM.pages_with(session, "background") == [2]
    assert PM.remove_marks(session, "bates") == 2
    assert "PN-" not in _text(session, 0) and "SECRET" in _text(session, 1)  # (the watermark stays)
    spec, pages = PM.saved_spec(session, "watermark")
    assert spec.text == "SECRET" and pages == [1]


def test_background_is_under_the_text_and_upright_on_a_turned_page(session):
    PM.add_background(session, [2], PM.BackgroundSpec(color=(0.0, 0.0, 1.0)))
    page = session.doc[2]
    pix = page.get_pixmap(dpi=36)
    corner = pix.pixel(2, 2)
    assert corner[2] > 200 and corner[0] < 60  # (blue fills the page)
    assert "Body of page 3" in page.get_text()
    contents = page.read_contents()
    name = PM._tagged(session, 2, "background")[0][1].encode()
    assert contents.find(b"/" + name + b" Do") < contents.find(b"BT")  # (drawn first: underneath)


def test_image_settings_survive_the_round_trip(session):
    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    img.set_rect(img.irect, (200, 0, 0))
    png = img.tobytes("png")
    PM.add_watermark(session, [0], PM.WatermarkSpec(image=png, opacity=0.5))
    spec, _pages = PM.saved_spec(session, "watermark")
    assert spec.image == png and spec.opacity == 0.5


def test_undo_by_snapshot_brings_removed_marks_back(session):
    PM.add_header_footer(session, [0], PM.HeaderFooterSpec(texts={"top_center": "HEADLINE"}))
    shots = snapshots.take(session, [0])
    PM.remove_marks(session, "header")
    assert "HEADLINE" not in _text(session, 0)
    snapshots.restore(session, shots)
    assert "HEADLINE" in _text(session, 0) and PM.pages_with(session, "header") == [0]


def test_marks_survive_save_and_reopen(session, tmp_path):
    PM.add_watermark(session, [0, 1], PM.WatermarkSpec(text="KEEP"))
    out = tmp_path / "saved.pdf"
    session.doc.save(out, garbage=4, deflate=True)
    again = PdfSession.open(out)
    try:
        assert PM.pages_with(again, "watermark") == [0, 1]
        # (the save merged the two pages' identical "draw the watermark" streams into one)
        assert PM.remove_marks(again, "watermark", [0]) == 1
        assert "KEEP" not in again.doc[0].get_text() and "Body of page 1" in again.doc[0].get_text()
        assert "KEEP" in again.doc[1].get_text() and "Body of page 2" in again.doc[1].get_text()
        assert "Body of page 1" not in again.doc[1].get_text()  # (page 1's content didn't leak into it)
        assert PM.remove_marks(again, "watermark") == 1 and PM.pages_with(again, "watermark") == []
    finally:
        again.close()


def test_deleting_an_image_leaves_a_page_sharing_its_stream_alone(tmp_path):
    """The same latent bug in Edit PDF's image delete: content was written into a stream
    that a full save had shared between identical pages."""
    from planetpdf.core import content_edit as CE

    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    img.set_rect(img.irect, (0, 120, 0))
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=300, height=300)
        page.insert_image(pymupdf.Rect(50, 50, 150, 150), stream=img.tobytes("png"))
    path = tmp_path / "two.pdf"
    doc.save(path, garbage=4, deflate=True)
    s = PdfSession.open(path)
    try:
        assert set(s.doc[0].get_contents()) & set(s.doc[1].get_contents())  # (shared, as in the bug)
        CE.delete_image(s, CE.page_images(s, 0)[0])
        assert not s.doc[0].get_image_info() and len(s.doc[1].get_image_info()) == 1
    finally:
        s.close()
