"""Comment tools that were grayed out (built 2026-10-02): Replace Text, Cloud, Attach File,
and the eraser for pencil drawings."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core.document import PdfSession


@pytest.fixture
def session(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The quick brown fox", fontname="helv", fontsize=12)
    path = tmp_path / "one.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    yield s
    s.close()


def _spec(kind, **kw):
    return K.MarkupSpec(mark_id=K.new_id(), page=0, kind=kind, **kw)


def _types(session):
    return sorted(a.type[1] for a in session.doc[0].annots())


def test_replace_text_is_a_strikeout_with_a_grouped_caret(session):
    words = pymupdf.Rect(session.doc[0].search_for("brown")[0])
    q = words.quad
    spec = _spec(K.Kind.REPLACE, rect=tuple(words), points=(tuple(q.ul), tuple(q.ur), tuple(q.ll), tuple(q.lr)),
                 text="red")
    K.add_markup(session, spec)
    assert _types(session) == ["Caret", "StrikeOut"]
    caret = next(a for a in session.doc[0].annots() if a.type[1] == "Caret")
    assert caret.info["content"] == "red"
    assert session.doc.xref_get_key(caret.xref, "RT") == ("name", "/Group")
    [item] = K.list_markup(session)  # (listed once: the caret belongs to it)
    assert item.kind is K.Kind.REPLACE and item.spec.text == "red"
    removed = K.remove_markup(session, item.item_id)
    assert _types(session) == []
    K.restore_markup(session, removed)
    assert _types(session) == ["Caret", "StrikeOut"]


def test_cloud_has_a_cloudy_border(session):
    points = ((100, 200), (300, 200), (300, 350), (100, 350))
    K.add_markup(session, _spec(K.Kind.CLOUD, rect=K.bounds(points), points=points))
    page = session.doc[0]  # (annotations only hold a weak reference to their page)
    annot = next(page.annots())
    assert annot.type[1] == "Polygon"
    assert "/BE" in session.doc.xref_object(annot.xref) and annot.border.get("clouds", 0) >= 1


def test_attached_file_round_trips(session, tmp_path):
    data = b"quarterly numbers\n1,2,3\n"
    spec = _spec(K.Kind.ATTACH, rect=(400, 400, 420, 420), source=data, text="numbers.csv")
    K.add_markup(session, spec)
    [item] = K.list_markup(session)
    assert item.kind is K.Kind.ATTACH and item.spec.source == data and item.spec.text == "numbers.csv"
    removed = K.remove_markup(session, item.item_id)
    K.restore_markup(session, removed)  # (undo puts the file back too)
    out = tmp_path / "saved.pdf"
    session.doc.save(out)
    with pymupdf.open(out) as doc:
        page = doc[0]
        annot = next(page.annots())
        assert annot.type[1] == "FileAttachment" and annot.get_file() == data


def test_eraser_cuts_pencil_strokes():
    stroke = tuple((float(x), 100.0) for x in range(0, 201, 5))
    spec = _spec(K.Kind.INK, rect=K.bounds(stroke), points=stroke, strokes=(len(stroke),))
    cut = K.erase_from_ink(spec, [(100.0, 90.0), (100.0, 110.0)], radius=8)  # a vertical swipe across the middle
    assert cut is not None and cut is not False and len(cut.strokes) == 2
    assert max(p[0] for p in cut.points[: cut.strokes[0]]) < 92 and min(p[0] for p in cut.points[cut.strokes[0]:]) > 108
    assert K.erase_from_ink(spec, [(100.0, 300.0)], radius=8) is False  # (missed)
    assert K.erase_from_ink(spec, [(0.0, 100.0), (200.0, 100.0)], radius=8) is None  # (all of it)
