"""Undo for operations that rewrite page content (headers/footers, watermarks,
content editing): snapshot a page's content streams, resources and boxes, and
restore them *in place*.

Restoring in place keeps the page object itself, so links, bookmarks and form
fields that point at the page stay valid (replacing the page would break them).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfSession


@dataclass(frozen=True)
class PageSnapshot:
    page: int
    contents: tuple[int, ...]  # content stream xrefs, in order
    streams: dict[int, bytes]  # their bytes
    resources: tuple[str, str]  # (kind, value) of /Resources
    resources_object: str | None  # the object source if /Resources is indirect
    cropbox: tuple[float, float, float, float]


def take(session: PdfSession, pages: Iterable[int]) -> list[PageSnapshot]:
    doc = session.doc
    shots = []
    for index in pages:
        page = doc[index]
        contents = tuple(page.get_contents())
        kind, value = doc.xref_get_key(page.xref, "Resources")
        res_obj = doc.xref_object(int(value.split()[0]), compressed=True) if kind == "xref" else None
        shots.append(
            PageSnapshot(index, contents, {x: doc.xref_stream(x) or b"" for x in contents}, (kind, value), res_obj, tuple(page.cropbox))
        )
    return shots


def restore(session: PdfSession, shots: Iterable[PageSnapshot]) -> None:
    doc = session.doc
    for shot in shots:
        page = doc[shot.page]
        for xref, data in shot.streams.items():
            doc.update_stream(xref, data)
        if len(shot.contents) == 1:
            doc.xref_set_key(page.xref, "Contents", f"{shot.contents[0]} 0 R")
        else:
            doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in shot.contents) + "]")
        kind, value = shot.resources
        if kind == "xref" and shot.resources_object is not None:
            doc.update_object(int(value.split()[0]), shot.resources_object)
            doc.xref_set_key(page.xref, "Resources", value)
        elif kind != "null":
            doc.xref_set_key(page.xref, "Resources", value)
        page = doc[shot.page]
        if tuple(page.cropbox) != shot.cropbox:
            page.set_cropbox(pymupdf.Rect(shot.cropbox))
    session.mark_modified()
