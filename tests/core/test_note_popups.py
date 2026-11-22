"""Notes in pop-up boxes (user 2026-10-04): the PDF's /Popup keeps each note's box (place and
open / collapsed), for highlights with a note and sticky notes alike."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core.document import PdfSession


@pytest.fixture(params=[0, 90])
def session(tmp_path, request):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Some words to highlight", fontsize=12)
    page.set_rotation(request.param)
    doc.save(tmp_path / "doc.pdf")
    s = PdfSession.open(tmp_path / "doc.pdf")
    yield s
    s.close()


def _highlight(session, text="Check this", popup=None, open_=True):
    page = session.doc[0]
    quad = page.search_for("words to")[0].quad
    points = (tuple(quad.ul), tuple(quad.ur), tuple(quad.ll), tuple(quad.lr))
    spec = K.MarkupSpec(K.new_id(), 0, K.Kind.HIGHLIGHT, K.bounds(points), points=points, text=text,
                        popup=popup or K.default_popup(page, K.bounds(points)), popup_open=open_)
    K.add_markup(session, spec)
    return spec


def test_highlight_with_a_note_keeps_its_box(session, tmp_path):
    spec = _highlight(session)
    [item] = K.list_markup(session)
    assert item.has_note and item.popup_open
    assert item.popup == pytest.approx(spec.popup, abs=0.01)
    # beside the words as displayed, on the page
    page = session.doc[0]
    shown_box = pymupdf.Rect(item.popup) * page.rotation_matrix
    shown_words = pymupdf.Rect(item.spec.rect) * page.rotation_matrix
    shown_box, shown_words = shown_box.normalize(), shown_words.normalize()
    # (right of the words, or left when they're near the right edge: never over them)
    assert (shown_box.x0 >= shown_words.x1 or shown_box.x1 <= shown_words.x0) and page.rect.contains(shown_box)
    # saved and opened again: the same (it's the PDF's own /Popup)
    session.save()
    path = session.path
    session.close()
    again = PdfSession.open(path)
    try:
        [item] = K.list_markup(again)
        assert item.popup_open and item.popup == pytest.approx(spec.popup, abs=0.01)
    finally:
        again.close()


def test_move_and_collapse_in_place(session):
    _highlight(session)
    [item] = K.list_markup(session)
    old = K.set_popup(session, item.item_id, rect=(300, 300, 480, 420))
    assert old == (item.popup, True)
    K.set_popup(session, item.item_id, open_=False, modified=False)
    [item] = K.list_markup(session)
    assert item.popup == pytest.approx((300, 300, 480, 420), abs=0.01) and not item.popup_open
    assert item.spec.popup == item.popup and not item.spec.popup_open  # (our description says the same)
    # recreated from its description (an edit of its text): the box stays where it was
    K.replace_markup(session, item.spec, K.replace(item.spec, text="Changed"))
    [item] = K.list_markup(session)
    assert item.text == "Changed" and item.popup == pytest.approx((300, 300, 480, 420), abs=0.01)


def test_sticky_notes_always_have_a_box(session):
    spec = K.MarkupSpec(K.new_id(), 0, K.Kind.NOTE, (400, 400, 420, 420), text="A sticky note")
    K.add_markup(session, spec)
    [item] = K.list_markup(session)
    assert item.has_note and item.popup is not None and not item.popup_open
    page = session.doc[0]
    assert (pymupdf.Rect(item.popup) * page.rotation_matrix).normalize().x0 > 0


def test_a_highlight_without_text_is_no_note(session):
    page = session.doc[0]
    quad = page.search_for("Some")[0].quad
    points = (tuple(quad.ul), tuple(quad.ur), tuple(quad.ll), tuple(quad.lr))
    K.add_markup(session, K.MarkupSpec(K.new_id(), 0, K.Kind.HIGHLIGHT, K.bounds(points), points=points))
    [item] = K.list_markup(session)
    assert not item.has_note and item.popup is None


def test_deleting_takes_the_box_too(session):
    spec = _highlight(session)
    removed = K.remove_markup(session, spec.mark_id)
    page = session.doc[0]
    assert not [x for x in page.annot_xrefs() if x[1] == pymupdf.PDF_ANNOT_POPUP]
    K.restore_markup(session, removed)
    [item] = K.list_markup(session)
    assert item.popup is not None

def test_replies_make_a_thread(session):
    """User 2026-10-04 (Acrobat screenshot): replies to a comment, as Acrobat keeps them:
    Text annotations /IRT the comment, listed in its thread, not as items of their own."""
    spec = _highlight(session, text="Is this right?")
    first = K.add_reply(session, spec.mark_id, "Yes, checked.", "Robert")
    second = K.add_reply(session, spec.mark_id, "Thanks!", "Ann")
    [item] = K.list_markup(session)  # (the replies aren't items of their own)
    assert [(r.author, r.text) for r in item.replies] == [("Robert", "Yes, checked."), ("Ann", "Thanks!")]
    doc = session.doc
    reply_xref = item.replies[0].xref
    assert doc.xref_get_key(reply_xref, "IRT")[1] == f"{item.xref} 0 R" and doc.xref_get_key(reply_xref, "RT")[1] == "/R"
    # edited / moved: the comment is made again, its thread stays with it
    K.replace_markup(session, item.spec, K.replace(item.spec, text="Is this right now?"))
    [item] = K.list_markup(session)
    assert len(item.replies) == 2
    assert K.set_reply_text(session, first, "Yes, checked twice.") == "Yes, checked."
    parent, removed = K.remove_reply(session, second)
    assert parent == spec.mark_id and removed.text == "Thanks!"
    [item] = K.list_markup(session)
    assert [r.text for r in item.replies] == ["Yes, checked twice."]
    # saved and opened again: the same thread
    session.save()
    path = session.path
    session.close()
    again = PdfSession.open(path)
    try:
        [item] = K.list_markup(again)
        assert [r.text for r in item.replies] == ["Yes, checked twice."]
    finally:
        again.close()


def test_a_reply_to_a_reply_joins_the_thread_and_orphans_stay_visible(session):
    spec = _highlight(session)
    first = K.add_reply(session, spec.mark_id, "One")
    [item] = K.list_markup(session)
    xref = item.replies[0].xref
    second = K.add_reply(session, spec.mark_id, "Two")
    session.doc.xref_set_key(K._find_reply(session, second)[1].xref, "IRT", f"{xref} 0 R")  # (a reply to "One")
    [item] = K.list_markup(session)
    assert sorted(r.text for r in item.replies) == ["One", "Two"]
    # a reply whose comment is gone (another program left it pointing nowhere): still shown
    session.doc.xref_set_key(xref, "IRT", f"{session.doc.pdf_catalog()} 0 R")
    texts = sorted(i.text for i in K.list_markup(session))
    assert "One" in texts and first
