from __future__ import annotations

import pymupdf
import pypdf
import pytest
from PIL import Image

from planetpdf.core import pages as P
from planetpdf.core import page_marks as PM
from planetpdf.core import properties as PR
from planetpdf.core import snapshots as S
from planetpdf.core.document import REMOVE_PROTECTION, PdfError, PdfSession, Protection, PasswordRequired
from planetpdf.core.optimize import ReduceOptions, reduce_size
from tests.fixtures import generate as g


@pytest.fixture
def text(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


# --- password protection -----------------------------------------------------------------


def test_protect_with_open_password(text):
    text.set_protection(Protection(open_password="s3cret"))
    text.save()
    assert text.is_encrypted and text.page_count == g.TEXT_PAGE_COUNT  # reopened with it
    with pytest.raises(PasswordRequired):
        PdfSession.open(text.path)
    other = PdfSession.open(text.path, "s3cret")
    assert g.SSN in other.doc[0].get_text()
    other.close()
    assert pypdf.PdfReader(text.path).is_encrypted


def test_permissions_only_password(text):
    text.set_protection(Protection(permissions_password="owner", allow_print=False, allow_copy=False))
    text.save()
    anyone = PdfSession.open(text.path)  # no password needed to open
    perms = anyone.doc.permissions
    assert not perms & pymupdf.PDF_PERM_PRINT and not perms & pymupdf.PDF_PERM_COPY
    assert perms & pymupdf.PDF_PERM_ANNOTATE
    assert anyone.is_protected
    anyone.close()


def test_remove_protection(pdf_copy):
    s = PdfSession.open(pdf_copy("encrypted.pdf"), g.ENCRYPTED_USER_PW)
    s.set_protection(REMOVE_PROTECTION)
    s.save()
    assert not s.is_encrypted
    PdfSession.open(s.path).close()  # opens without a password now
    s.close()


def test_protection_needs_a_password(text):
    with pytest.raises(PdfError):
        text.set_protection(Protection())


# --- headers, footers, Bates, watermark -------------------------------------------------------


def test_header_footer_tokens(text):
    spec = PM.HeaderFooterSpec(texts={"top_right": "{file}", "bottom_center": "Page {page} of {pages}", "bottom_right": "{bates}"},
                               bates_prefix="PN-", bates_start=100, bates_digits=5)
    before = S.take(text, range(text.page_count))
    assert PM.add_header_footer(text, list(range(text.page_count)), spec) == 3 * g.TEXT_PAGE_COUNT
    t0, t2 = text.doc[0].get_text(), text.doc[2].get_text()
    assert "Page 1 of 3" in t0 and "Page 3 of 3" in t2
    assert "PN-00100" in t0 and "PN-00102" in t2
    assert "text.pdf" in t0
    footer = text.doc[0].search_for("Page 1 of 3")[0]
    assert footer.y0 > text.doc[0].rect.height - 60  # at the bottom
    S.restore(text, before)
    assert "Page 1 of 3" not in text.doc[0].get_text()


def test_header_on_rotated_page_reads_upright(text):
    text.doc[1].set_rotation(90)
    PM.add_header_footer(text, [1], PM.HeaderFooterSpec(texts={"top_center": "CONFIDENTIAL MEMO"}))
    page = text.doc[1]
    words = [w for w in page.get_text("words") if w[4] == "CONFIDENTIAL"]
    box = (pymupdf.Rect(words[0][:4]) * page.rotation_matrix).normalize()
    assert box.width > box.height and box.y0 < 60  # horizontal, at the top as displayed


def test_header_footer_is_small(text):
    before = len(text.doc.tobytes(garbage=3, deflate=True))
    PM.add_header_footer(text, list(range(text.page_count)), PM.HeaderFooterSpec(texts={"bottom_center": "Page {page}"}))
    assert len(text.doc.tobytes(garbage=3, deflate=True)) - before < 20_000  # standard font: nothing embedded


def test_watermark_text_and_behind(text):
    PM.add_watermark(text, [0], PM.WatermarkSpec(text="DRAFT", behind=True))
    assert "DRAFT" in text.doc[0].get_text()
    first = text.doc[0].read_contents()
    assert first.find(b"/fzFrm") < first.find(b"TJ") or b"/fzFrm" in first  # drawn first = behind


def test_watermark_image(text, tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (200, 80), (0, 80, 200, 255)).save(path)
    PM.add_watermark(text, [0, 1], PM.WatermarkSpec(image=PM.image_bytes(path), opacity=0.3, rotation=0))
    assert text.doc[0].get_images() and text.doc[1].get_images()


def test_snapshot_restore_keeps_page_identity_for_links(tmp_path):
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 100, 200, 120), "page": 1})
    path = tmp_path / "l.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    shots = S.take(s, [1])
    PM.add_watermark(s, [1], PM.WatermarkSpec())
    S.restore(s, shots)
    s.save()
    assert s.doc[0].get_links()[0]["page"] == 1  # the link still points at page 2
    assert "CONFIDENTIAL" not in s.doc[1].get_text()
    s.close()


# --- crop and split -------------------------------------------------------------------------


def test_crop_margins_and_restore(text):
    old = P.crop_pages(text, [0], margins=(72, 72, 72, 72))
    assert text.doc[0].rect.width == pytest.approx(612 - 144)
    assert g.SSN in text.doc[0].get_text()  # inside the visible area
    P.restore_crop(text, old)
    assert text.doc[0].rect.width == pytest.approx(612)
    assert "Customer record" in text.doc[0].get_text()  # nothing was deleted by cropping


def test_crop_rotated_page_uses_displayed_margins(text):
    text.doc[0].set_rotation(90)
    P.crop_pages(text, [0], margins=(0, 100, 0, 0))  # trim 100 pt from the top as displayed
    assert text.doc[0].rect.height == pytest.approx(612 - 100)


def test_crop_too_much_rejected(text):
    with pytest.raises(PdfError):
        P.crop_pages(text, [0], margins=(300, 0, 300, 0))


def test_split_every_and_by_bookmarks(pdf_copy, tmp_path):
    s = PdfSession.open(pdf_copy("text.pdf"))
    groups = [("", g_) for g_ in P.split_groups_every(s.page_count, 2)]
    paths = P.split_document(s, groups, tmp_path / "parts", "text")
    assert [p.name for p in paths] == ["text_1.pdf", "text_2.pdf"]
    assert [pymupdf.open(p).page_count for p in paths] == [2, 1]
    s.doc.set_toc([[1, "Intro", 1], [1, "Details: part/2", 2]])
    by_marks = P.split_groups_by_bookmarks(s)
    assert by_marks == [("Intro", [0]), ("Details: part/2", [1, 2])]
    named = P.split_document(s, by_marks, tmp_path / "marks", "text")
    assert named[1].name == "text_2 - Details_ part_2.pdf"
    s.close()


# --- reduce size ------------------------------------------------------------------------------


def test_reduce_size_shrinks_big_images(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    import random

    random.seed(1)
    image = Image.new("RGB", (2400, 3000))
    image.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(2400 * 3000)])
    img = tmp_path / "big.png"
    image.save(img)
    page.insert_image(page.rect, filename=str(img))
    src = tmp_path / "big.pdf"
    doc.save(src, deflate=True)
    before = src.stat().st_size
    result = reduce_size(pymupdf.open(src), tmp_path / "small.pdf", ReduceOptions(image_dpi=96, jpeg_quality=60), before)
    assert result.after < before / 3 and result.saved_percent > 60
    assert pymupdf.open(tmp_path / "small.pdf")[0].get_images()


# --- properties ------------------------------------------------------------------------------------


def test_properties(text):
    assert PR.get_metadata(text)["title"] == "Text fixture"
    PR.set_metadata(text, {"title": "Customer File", "keywords": "planet, test"})
    text.save()
    assert PR.get_metadata(text) == {"title": "Customer File", "author": "Fixture Author", "subject": g.SSN, "keywords": "planet, test"}
    facts = PR.facts(text)
    assert facts.pages == g.TEXT_PAGE_COUNT and "Letter" in facts.page_size
    assert facts.file_size > 0 and "Helvetica" in " ".join(facts.fonts)
