"""Redaction marks, stored in the PDF as /Redact annotations (see CLAUDE.md).

Our annotations carry ``/NM ppdf-redact-<markid>-<k>`` (one per rectangle) and the
mark's label in ``/T``. /Redact annotations made by other tools are shown as marks too,
one mark per annotation. Rects are PyMuPDF unrotated page coordinates.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfError, PdfSession

RectTuple = tuple[float, float, float, float]
NM_PREFIX = "ppdf-redact-"
_NM_RE = re.compile(rf"^{NM_PREFIX}([0-9a-f]+)-(\d+)$")
FOREIGN_LABEL = "Redaction (from file)"
MIN_SIZE = 1.0  # points; smaller rectangles are ignored


@dataclass(frozen=True)
class MarkSpec:
    """Everything needed to (re)create a mark, e.g. for redo."""

    mark_id: str
    page: int
    rects: tuple[RectTuple, ...]
    label: str


@dataclass(frozen=True)
class Mark:
    mark_id: str
    page: int
    rects: tuple[RectTuple, ...]
    label: str
    ours: bool

    @property
    def spec(self) -> MarkSpec:
        return MarkSpec(self.mark_id, self.page, self.rects, self.label)


def new_mark_id() -> str:
    return uuid.uuid4().hex[:16]


def make_spec(page: int, rects: Iterable[RectTuple], label: str) -> MarkSpec:
    cleaned = tuple(
        tuple(pymupdf.Rect(r).normalize())
        for r in rects
        if pymupdf.Rect(r).normalize().width >= MIN_SIZE and pymupdf.Rect(r).normalize().height >= MIN_SIZE
    )
    if not cleaned:
        raise PdfError("The area is too small to redact.")
    return MarkSpec(new_mark_id(), page, cleaned, label)


def _pdf_string(text: str) -> str:
    return pymupdf.get_pdf_str(text)


def add_mark(session: PdfSession, spec: MarkSpec) -> None:
    page = session.doc[spec.page]
    for k, rect in enumerate(spec.rects):
        annot = page.add_redact_annot(pymupdf.Rect(rect), cross_out=False)
        annot.set_info(title=spec.label)
        annot.set_colors(stroke=(0.85, 0.1, 0.1))
        annot.update()
        session.doc.xref_set_key(annot.xref, "NM", _pdf_string(f"{NM_PREFIX}{spec.mark_id}-{k}"))
    session.mark_modified()


def add_marks(session: PdfSession, specs: Sequence[MarkSpec]) -> None:
    for spec in specs:
        add_mark(session, spec)


def _annot_name(doc: pymupdf.Document, xref: int) -> str:
    kind, value = doc.xref_get_key(xref, "NM")
    if kind != "string":
        return ""
    return value


def list_marks(session: PdfSession, pages: Iterable[int] | None = None) -> list[Mark]:
    doc = session.doc
    marks: dict[str, dict] = {}
    order: list[str] = []
    for index in range(doc.page_count) if pages is None else pages:
        page = doc[index]
        for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]):
            match = _NM_RE.match(_annot_name(doc, annot.xref))
            if match:
                mark_id, k, ours = match.group(1), int(match.group(2)), True
                label = annot.info.get("title") or "Redaction"
            else:
                mark_id, k, ours = f"x{annot.xref}", 0, False
                label = FOREIGN_LABEL
            key = f"{index}:{mark_id}"
            if key not in marks:
                marks[key] = {"page": index, "id": mark_id, "label": label, "ours": ours, "rects": []}
                order.append(key)
            marks[key]["rects"].append((k, tuple(annot.rect)))
    result = []
    for key in order:
        m = marks[key]
        rects = tuple(r for _k, r in sorted(m["rects"]))
        result.append(Mark(m["id"], m["page"], rects, m["label"], m["ours"]))
    return result


def _annots_for(session: PdfSession, mark_id: str) -> list[tuple[int, int]]:
    """(page index, annot xref) for every annotation of a mark."""
    found = []
    doc = session.doc
    foreign_xref = int(mark_id[1:]) if mark_id.startswith("x") else None
    for index in range(doc.page_count):
        for annot in doc[index].annots(types=[pymupdf.PDF_ANNOT_REDACT]):
            if foreign_xref is not None:
                if annot.xref == foreign_xref:
                    found.append((index, annot.xref))
            else:
                match = _NM_RE.match(_annot_name(doc, annot.xref))
                if match and match.group(1) == mark_id:
                    found.append((index, annot.xref))
    return found


def remove_marks(session: PdfSession, mark_ids: Iterable[str]) -> list[MarkSpec]:
    """Delete marks; returns their specs (for undo). Unknown ids are ignored.

    A foreign mark comes back as one of ours when re-added (its id changes).
    """
    wanted = set(mark_ids)
    # Foreign marks get a fresh id of ours, so undo re-creates them under an id that a
    # later redo can find again.
    specs = [
        m.spec if m.ours else MarkSpec(new_mark_id(), m.page, m.rects, m.label)
        for m in list_marks(session)
        if m.mark_id in wanted
    ]
    for mark_id in wanted:
        for index, xref in _annots_for(session, mark_id):
            page = session.doc[index]
            annot = page.load_annot(xref)
            if annot is not None:
                page.delete_annot(annot)
    if specs:
        session.mark_modified()
    return specs


def mark_text(session: PdfSession, mark: Mark | MarkSpec) -> str:
    """The text currently under a mark (what will be removed)."""
    page = session.doc[mark.page]
    parts = [page.get_textbox(pymupdf.Rect(r)).strip() for r in mark.rects]
    return " ".join(p for p in parts if p)


def to_display(page: pymupdf.Page, rect: RectTuple) -> pymupdf.Rect:
    """Unrotated page coordinates -> displayed (rotated) page coordinates."""
    return (pymupdf.Rect(rect) * page.rotation_matrix).normalize()


def from_display(page: pymupdf.Page, rect: RectTuple) -> RectTuple:
    """Displayed (rotated) page coordinates -> unrotated page coordinates."""
    return tuple((pymupdf.Rect(rect) * page.derotation_matrix).normalize())
