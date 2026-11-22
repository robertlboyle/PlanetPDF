"""Rewriting a page's content stream safely.

A page's content stream objects can be **shared with other pages**: a full save
(``garbage=4``) merges identical objects, e.g. the small "q /fzFrm0 Do Q" stream that
``show_pdf_page`` adds to every page with the same watermark. Writing a page's new
content into one of its existing streams would then change the other pages too (found
2026-10-02 while building Watermark ▸ Remove). So the new content always goes into a
fresh stream object of its own; the old ones stay for any page still using them (and
for ``core.snapshots``, which puts them back on undo).
"""

from __future__ import annotations

import pymupdf


def replace_contents(doc: pymupdf.Document, page_index: int, data: bytes) -> int:
    """Make ``data`` the whole content of page ``page_index``. Returns the new xref."""
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data)
    doc[page_index].set_contents(xref)
    return xref
