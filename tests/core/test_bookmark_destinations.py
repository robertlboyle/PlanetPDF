"""Editing the bookmark list never re-records the other bookmarks (user bug 2026-10-04):
other programs' destinations (/FitH, named destinations) and looks are kept exactly."""

import pymupdf

from planetpdf.core import links as L


class _Session:
    def __init__(self, doc):
        self.doc = doc

    def mark_modified(self):
        pass


def _foreign() -> pymupdf.Document:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc.set_toc([[1, "Fit width", 2], [1, "Named", 3]])
    outlines = [e[3]["xref"] for e in doc.get_toc(simple=False)]
    pages = [doc[i].xref for i in range(3)]
    doc.xref_set_key(outlines[0], "A", "null")
    doc.xref_set_key(outlines[0], "Dest", f"[{pages[1]} 0 R /FitH 500]")
    doc.xref_set_key(outlines[0], "C", "[1 0 0]")
    names = doc.get_new_xref()
    doc.update_object(names, f"<< /Names [(chap3) [{pages[2]} 0 R /XYZ 0 300 null]] >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<< /Dests {names} 0 R >>")
    doc.xref_set_key(outlines[1], "A", "null")
    doc.xref_set_key(outlines[1], "Dest", "(chap3)")
    return pymupdf.open("pdf", doc.tobytes())


def _sources(doc):
    return {e[1]: doc.xref_object(e[3]["xref"], compressed=True) for e in doc.get_toc(simple=False)}


def test_named_destination_read_top_down():
    marks = L.get_bookmarks(_Session(_foreign()))
    assert [(m.title, m.page, m.y) for m in marks] == [("Fit width", 1, 292.0), ("Named", 2, 492.0)]


def test_edits_keep_other_bookmarks_exactly():
    doc = _foreign()
    session = _Session(doc)
    for _ in range(3):  # (add one, then rename, then delete: each rewrites the list)
        marks = L.get_bookmarks(session)
        L.set_bookmarks(session, marks + [L.Bookmark(1, "Mine", 0, 100)])
        L.set_bookmarks(session, [m for m in L.get_bookmarks(session) if m.title != "Mine"])
    sources = _sources(doc)
    assert "/Dest[" in sources["Fit width"] and "/FitH 500" in sources["Fit width"]
    assert "/C[1 0 0]" in sources["Fit width"]
    assert "/Dest(chap3)" in sources["Named"]
    assert [(m.title, m.page, m.y) for m in L.get_bookmarks(session)] == [("Fit width", 1, 292.0),
                                                                          ("Named", 2, 492.0)]
    with pymupdf.open("pdf", doc.tobytes()) as reopened:  # (and after saving)
        assert [(m.title, m.page, m.y) for m in L.get_bookmarks(_Session(reopened))] == [
            ("Fit width", 1, 292.0), ("Named", 2, 492.0)]


def test_our_own_bookmark_keeps_its_place():
    doc = _foreign()
    session = _Session(doc)
    L.set_bookmarks(session, L.get_bookmarks(session) + [L.Bookmark(1, "Mine", 0, 100)])
    for _ in range(3):
        L.set_bookmarks(session, L.get_bookmarks(session))
    mine = next(m for m in L.get_bookmarks(session) if m.title == "Mine")
    assert (mine.page, mine.y) == (0, 100.0)
