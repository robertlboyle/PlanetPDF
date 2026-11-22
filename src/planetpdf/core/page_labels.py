"""Page labels (Organize Pages ▸ More ▸ Page Labels, Acrobat): how pages are numbered for
the reader, e.g. i, ii, iii for the front matter, then 1, 2, 3. Stored in the PDF's
``/PageLabels`` number tree, so every viewer shows them."""

from __future__ import annotations

from dataclasses import dataclass

from planetpdf.core.document import PdfError, PdfSession

STYLES = {"D": "1, 2, 3", "r": "i, ii, iii", "R": "I, II, III", "a": "a, b, c", "A": "A, B, C", "": "None (prefix only)"}


@dataclass(frozen=True)
class LabelRule:
    start: int  # 0-based first page the rule applies to (until the next rule)
    style: str = "D"  # a key of STYLES
    prefix: str = ""
    first: int = 1  # the number of the rule's first page


def get_rules(session: PdfSession) -> list[LabelRule]:
    return [LabelRule(r.get("startpage", 0), r.get("style", "") or "", r.get("prefix", "") or "",
                      max(1, r.get("firstpagenum", 1) or 1))
            for r in session.doc.get_page_labels()]


def set_rules(session: PdfSession, rules: list[LabelRule]) -> None:
    """Replace the labels; an empty list removes them (pages are 1, 2, 3 again)."""
    starts = [r.start for r in rules]
    if len(set(starts)) != len(starts):
        raise PdfError("Two label ranges start on the same page.")
    if any(not 0 <= r.start < session.page_count for r in rules):
        raise PdfError("A label range starts on a page that doesn't exist.")
    if any(r.style not in STYLES for r in rules):
        raise PdfError("Unknown numbering style.")
    session.doc.set_page_labels([{"startpage": r.start, "style": r.style, "prefix": r.prefix, "firstpagenum": r.first}
                                 for r in sorted(rules, key=lambda r: r.start)])
    session.mark_modified()


def label(session: PdfSession, index: int) -> str:
    """The page's label ("" when the document has none)."""
    return session.doc[index].get_label() if session.doc.get_page_labels() else ""
