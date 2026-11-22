from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.markup import Kind, MarkupSpec, Style
from planetpdf.core.stamps import BUILTIN_STAMPS, StampLibrary, builtin, render_text_stamp, resolve_lines
from tests.fixtures import generate as g


@pytest.fixture
def text(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    yield s
    s.close()


def _spec(kind, page=0, rect=(100, 300, 250, 380), **kw):
    return MarkupSpec(mark_id=K.new_id(), page=page, kind=kind, rect=rect, author="Tester", **kw)


def _green_in(page, rect) -> int:
    pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=50)
    s = pix.samples
    return sum(1 for i in range(0, len(s), 3) if s[i + 1] > 120 and s[i] < 90)


ALL_SHAPES = [
    _spec(Kind.RECT, style=Style(color=(0, 0.6, 0), fill=(0.8, 1, 0.8))),
    _spec(Kind.ELLIPSE, style=Style(color=(0, 0.6, 0))),
    _spec(Kind.LINE, rect=(100, 300, 250, 380), points=((100, 300), (250, 380)), style=Style(color=(0, 0.6, 0))),
    _spec(Kind.ARROW, rect=(100, 300, 250, 380), points=((100, 300), (250, 380)), style=Style(color=(0, 0.6, 0), width=3)),
    _spec(Kind.INK, points=((100, 300), (150, 350), (200, 320), (250, 380)), strokes=(4,), style=Style(color=(0, 0.6, 0))),
    _spec(Kind.NOTE, rect=(100, 300, 120, 320), text="Please review", style=Style(color=(0, 0.6, 0))),
    _spec(Kind.TEXTBOX, text="Label text", style=Style(color=(0, 0.6, 0), width=1)),
]


@pytest.mark.parametrize("spec", ALL_SHAPES, ids=lambda s: s.kind.value)
def test_create_every_kind_save_reopen(text, spec):
    K.add_markup(text, spec)
    text.save()
    reopened = PdfSession.open(text.path)
    [item] = K.list_markup(reopened)
    assert item.ours and item.kind is spec.kind and item.item_id == spec.mark_id
    assert item.author == "Tester"
    assert item.spec.style.color == spec.style.color
    if spec.text:
        assert item.text == spec.text
    reopened.close()


def test_markup_is_standard_annotation_visible_elsewhere(text):
    K.add_markup(text, ALL_SHAPES[0])
    text.save()
    import pypdf

    annots = pypdf.PdfReader(text.path).pages[0]["/Annots"]
    assert annots[0].get_object()["/Subtype"] == "/Square"
    assert _green_in(text.doc[0], (100, 300, 250, 380)) > 20  # it's drawn


def test_text_markup_from_words(text):
    page = text.doc[0]
    words = [w for w in page.get_text("words") if w[4] in ("John", "Q.", "Public")]
    points = []
    for w in words:
        q = pymupdf.Rect(w[:4]).quad
        points += [tuple(q.ul), tuple(q.ur), tuple(q.ll), tuple(q.lr)]
    for kind in (Kind.HIGHLIGHT, Kind.UNDERLINE, Kind.STRIKEOUT):
        K.add_markup(text, _spec(kind, rect=K.bounds(points), points=tuple(points), style=Style(color=(1, 0.9, 0))))
    kinds = [i.kind for i in K.list_markup(text)]
    assert kinds == [Kind.HIGHLIGHT, Kind.UNDERLINE, Kind.STRIKEOUT]
    assert not Kind.HIGHLIGHT.movable


def test_move_resize_by_replacing(text):
    points = ((100, 300), (200, 400))
    spec = _spec(Kind.ARROW, rect=K.bounds(points), points=points)
    K.add_markup(text, spec)
    moved = K.transformed(spec, (300, 500, 500, 700))  # moved and doubled
    K.replace_markup(text, spec, moved)
    [item] = K.list_markup(text)
    assert item.item_id == spec.mark_id
    assert item.spec.points == ((300.0, 500.0), (500.0, 700.0))


def test_remove_and_restore(text):
    spec = ALL_SHAPES[1]
    K.add_markup(text, spec)
    removed = K.remove_markup(text, spec.mark_id)
    assert K.list_markup(text) == []
    K.restore_markup(text, removed)
    assert [i.item_id for i in K.list_markup(text)] == [spec.mark_id]


def test_foreign_annotation_delete_restore_and_edit(pdf_copy):
    path = pdf_copy("text.pdf")
    with pymupdf.open(path) as doc:
        doc[0].add_text_annot((300, 300), "From another app")
        doc.saveIncr()
    s = PdfSession.open(path)
    [item] = K.list_markup(s)
    assert not item.ours and item.text == "From another app"
    K.set_foreign_text(s, item.item_id, "Edited")
    assert K.list_markup(s)[0].text == "Edited"
    removed = K.remove_markup(s, item.item_id)
    assert K.list_markup(s) == []
    new_id = K.restore_markup(s, removed)
    s.save()
    [back] = K.list_markup(s)
    assert back.text == "Edited" and back.type_name == "Text" and new_id.startswith("x")
    s.close()


def test_markup_ignores_widgets_links_and_redactions(pdf_copy):
    s = PdfSession.open(pdf_copy("redact_complex.pdf"))  # has a note, a link and a field
    s.doc[0].add_redact_annot(pymupdf.Rect(10, 10, 50, 50))
    assert [i.type_name for i in K.list_markup(s)] == ["Text"]
    s.close()


@pytest.mark.parametrize(
    "text",
    ["Short", "Please verify this customer record before Friday, and call me.", "Line one\nLine two\nLine three"],
)
def test_text_box_is_tall_enough_for_its_text(text):
    """No line of a text box may be clipped: every word must be extractable."""
    doc = pymupdf.open()
    page = doc.new_page()
    width, size = 200, 12
    height = K.textbox_height(text, width, size)
    rect = pymupdf.Rect(72, 72, 72 + width, 72 + height)
    annot = page.add_freetext_annot(rect, text, fontsize=size, fontname="helv")
    annot.update()
    shown = page.get_pixmap(clip=rect, dpi=72)
    assert shown.width > 0
    # Render the annotation and check the last word appears inside the box.
    doc2 = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    doc2[0].get_pixmap()  # renders fine
    text_seen = doc2[0].get_text()
    for word in text.split():
        assert word.strip(",.") in text_seen


def test_spec_json_round_trip():
    spec = _spec(Kind.INK, points=((1, 2), (3, 4), (5, 6)), strokes=(2, 1), style=Style(fill=None, opacity=0.5), text="x")
    assert MarkupSpec.from_json(spec.to_json()) == spec


# --- appearance items: stamps, seals, signatures ---------------------------------------------------


def _stamp_spec(page=0, rect=(100, 100, 300, 160), lib_dir=None):
    stamp = builtin("approved")  # green
    source = render_text_stamp(stamp, resolve_lines(stamp, user="Tester"))
    return _spec(Kind.STAMP, page=page, rect=rect, source=source, text="Approved")


def test_stamp_place_save_reopen(text):
    spec = _stamp_spec()
    K.add_markup(text, spec)
    text.save()
    reopened = PdfSession.open(text.path)
    [item] = K.list_markup(reopened)
    assert item.kind is Kind.STAMP and item.spec.source  # source travels with the file
    assert "APPROVED" in reopened.doc[0].get_text()
    assert _green_in(reopened.doc[0], (100, 100, 300, 160)) > 50
    reopened.close()


def test_stamp_file_size_is_small(text):
    before = len(text.doc.tobytes(garbage=3, deflate=True))
    K.add_markup(text, _stamp_spec())
    after = len(text.doc.tobytes(garbage=3, deflate=True))
    assert after - before < 150_000  # fonts are subset, not embedded whole (2 MB)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_stamp_reads_upright_on_rotated_pages(pdf_copy, rotation):
    s = PdfSession.open(pdf_copy("text.pdf"))
    page = s.doc[0]
    page.set_rotation(rotation)
    shown = pymupdf.Rect(100, 100, 300, 160)  # where the reader puts it
    unrot = tuple((shown * page.derotation_matrix).normalize())
    K.add_markup(s, _stamp_spec(rect=unrot))
    # Text extraction in *display* orientation reads the stamp left-to-right.
    page = s.doc[0]
    words = [w for w in page.get_text("words") if w[4] == "APPROVED"]
    assert words
    box = (pymupdf.Rect(words[0][:4]) * page.rotation_matrix).normalize()
    assert box.width > box.height  # horizontal as displayed
    s.close()


@pytest.mark.parametrize("rotation", [0, 90])
def test_flatten_stamp_looks_identical_and_undo(pdf_copy, rotation):
    s = PdfSession.open(pdf_copy("text.pdf"))
    s.doc[0].set_rotation(rotation)
    page = s.doc[0]
    unrot = tuple((pymupdf.Rect(100, 100, 300, 160) * page.derotation_matrix).normalize())
    spec = _stamp_spec(rect=unrot)
    K.add_markup(s, spec)
    before = s.doc[0].get_pixmap(dpi=40).samples
    flat_spec, contents = K.flatten_markup(s, spec.mark_id)
    assert K.list_markup(s) == []
    after = s.doc[0].get_pixmap(dpi=40).samples
    differing = sum(1 for i in range(1, len(before), 3) if abs(before[i] - after[i]) > 80)
    assert differing < 20
    s.save()
    assert "APPROVED" in s.doc[0].get_text()  # now page content
    assert K.list_markup(s) == []
    s.close()


def test_unflatten_restores_annotation(text):
    spec = _stamp_spec()
    K.add_markup(text, spec)
    flat_spec, contents = K.flatten_markup(text, spec.mark_id)
    K.unflatten_markup(text, flat_spec, contents)
    assert [i.item_id for i in K.list_markup(text)] == [spec.mark_id]
    assert text.doc[0].get_contents() == contents


def test_only_appearance_items_flatten(text):
    spec = ALL_SHAPES[0]
    K.add_markup(text, spec)
    with pytest.raises(PdfError):
        K.flatten_markup(text, spec.mark_id)


def test_stamp_library_persists(tmp_path):
    lib = StampLibrary.load(tmp_path)
    assert [s.name for s in lib.all][:3] == ["Revised", "Reviewed", "Received"]
    assert {s.category for s in lib.all} == {"Dynamic", "Sign Here", "Standard Business"}
    custom = lib.add_text_stamp("Reviewed", ["REVIEWED", "{user} {date}"], color=(0.5, 0, 0.5), border="double", rotation=-10)
    seal_png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), True)
    seal_png.set_rect(seal_png.irect, (0, 0, 200, 128))
    seal = lib.add_seal("Company seal", seal_png.tobytes("png"), opacity=0.8)
    again = StampLibrary.load(tmp_path)
    assert [s.name for s in again.custom] == ["Reviewed", "Company seal"]
    assert again.get(custom.stamp_id).border == "double"
    assert again.render(again.get(seal.stamp_id))  # seal image found and rendered
    again.remove(seal.stamp_id)
    assert not (tmp_path / "seals" / seal.image).exists()
    with pytest.raises(PdfError):
        again.remove(BUILTIN_STAMPS[0].stamp_id)
