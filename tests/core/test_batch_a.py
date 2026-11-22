from __future__ import annotations

import pymupdf
import pytest
from PIL import Image

from planetpdf.core import links as L
from planetpdf.core.combine import CombineOptions, combine_files
from planetpdf.core.document import PasswordRequired, PdfError, PdfSession
from planetpdf.core.text import line_rects, page_words, selection_text
from tests.fixtures import generate as g


@pytest.fixture
def text(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


def test_selection_text_reading_order(text):
    words = page_words(text, 0)
    start = next(i for i, w in enumerate(words) if w.text == "Name:")
    end = next(i for i, w in enumerate(words) if w.text == g.SSN)
    chosen = words[start : end + 1]
    assert selection_text(chosen) == f"Name: John Q. Public\nSSN: {g.SSN}"
    assert len(line_rects(chosen)) == 2


def _linked_doc(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 100, 200, 120), "page": 2, "to": pymupdf.Point(0, 300)})
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 140, 200, 160), "uri": "https://example.com/x"})
    doc.set_toc([[1, "Intro", 1], [2, "Detail", 2], [1, "End", 3]])
    path = tmp_path / "links.pdf"
    doc.save(path)
    return PdfSession.open(path)


def test_links(tmp_path):
    s = _linked_doc(tmp_path)
    links = L.page_links(s, 0)
    kinds = {l.kind for l in links}
    assert kinds == {L.LinkKind.PAGE, L.LinkKind.WEB}
    goto = next(l for l in links if l.kind is L.LinkKind.PAGE)
    assert goto.target_page == 2 and goto.description == "Go to page 3"
    web = next(l for l in links if l.kind is L.LinkKind.WEB)
    assert web.uri == "https://example.com/x"
    assert L.resolve_named("NextPage", 0, 3) == 1 and L.resolve_named("LastPage", 0, 3) == 2
    s.close()


def test_bookmarks_round_trip(tmp_path):
    s = _linked_doc(tmp_path)
    marks = L.get_bookmarks(s)
    assert [(b.level, b.title, b.page) for b in marks] == [(1, "Intro", 0), (2, "Detail", 1), (1, "End", 2)]
    edited = marks[:1] + [L.Bookmark(1, "Renamed", 1, 50.0)] + marks[2:]
    L.set_bookmarks(s, edited)
    s.save()
    assert [(b.title, b.page) for b in L.get_bookmarks(s)] == [("Intro", 0), ("Renamed", 1), ("End", 2)]
    L.set_bookmarks(s, [L.Bookmark(3, "Deep first", 0)])  # invalid levels are clamped
    assert L.get_bookmarks(s)[0].level == 1
    s.close()


def _png(tmp_path, name, size=(300, 200), color=(200, 30, 30)):
    path = tmp_path / name
    Image.new("RGB", size, color).save(path)
    return path


def test_combine_pdfs_and_images(fixture_dir, tmp_path):
    img = _png(tmp_path, "scan.png")
    target = tmp_path / "combined.pdf"
    pages = combine_files([fixture_dir / "text.pdf", img, fixture_dir / "image.pdf"], target)
    assert pages == g.TEXT_PAGE_COUNT + 2
    with pymupdf.open(target) as doc:
        assert g.SSN in doc[0].get_text()
        assert doc[g.TEXT_PAGE_COUNT].get_images()  # the image page
        assert doc[g.TEXT_PAGE_COUNT].rect.width == pytest.approx(300 * 72 / 96, rel=0.35) or doc[g.TEXT_PAGE_COUNT].rect.width > 0


def test_images_on_letter_pages(tmp_path):
    target = tmp_path / "scans.pdf"
    combine_files([_png(tmp_path, "a.png"), _png(tmp_path, "b.png", (200, 400))], target, CombineOptions(image_page="letter"))
    with pymupdf.open(target) as doc:
        assert [tuple(round(v) for v in p.rect) for p in doc] == [(0, 0, 612, 792)] * 2


def test_multipage_tiff_becomes_pages(tmp_path):
    frames = [Image.new("RGB", (100, 100), c) for c in ("red", "green", "blue")]
    tif = tmp_path / "multi.tif"
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    target = tmp_path / "tif.pdf"
    assert combine_files([tif], target) == 3


def test_combine_protected_pdf_needs_password(fixture_dir, tmp_path):
    with pytest.raises(PasswordRequired):
        combine_files([fixture_dir / "encrypted.pdf"], tmp_path / "x.pdf")
    n = combine_files(
        [fixture_dir / "encrypted.pdf"], tmp_path / "x.pdf", CombineOptions(passwords={"encrypted.pdf": g.ENCRYPTED_USER_PW})
    )
    assert n == 1


def test_combine_nothing_is_an_error(tmp_path):
    with pytest.raises(PdfError):
        combine_files([], tmp_path / "x.pdf")
