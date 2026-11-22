"""Find text to redact, with exact per-character rectangles.

Each text block is flattened into one string (lines joined with "\\n") with a box
for every character, so a regex match maps back to precise character boxes. A match
that wraps across lines yields one rectangle per line. Coordinates are PyMuPDF
*unrotated* page coordinates (the same space as annotations).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.progress import Progress, report

RectTuple = tuple[float, float, float, float]

# Expand ligatures ("ﬁ" -> "f", "i") so searches for "fi" match.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES


@dataclass(frozen=True)
class SearchHit:
    page: int
    text: str
    rects: tuple[RectTuple, ...]
    context: str  # the surrounding line(s), for the results list


def build_pattern(term: str, *, regex: bool, case_sensitive: bool, whole_word: bool) -> re.Pattern[str]:
    if not term.strip():
        raise PdfError("Enter something to search for.")
    if regex:
        source = term
    else:
        # Any run of whitespace in the term matches any whitespace, including line breaks.
        source = r"\s+".join(re.escape(part) for part in term.split())
    if whole_word:
        source = rf"(?<!\w)(?:{source})(?!\w)"
    try:
        return re.compile(source, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise PdfError(f"Invalid regular expression: {exc}") from exc


def _page_blocks(page: pymupdf.Page) -> list[tuple[str, list[RectTuple | None], list[int]]]:
    """For each text block: (text, box per char or None for separators, line number per char)."""
    blocks = []
    for block in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        chars: list[str] = []
        boxes: list[RectTuple | None] = []
        lines: list[int] = []
        for line_no, line in enumerate(block["lines"]):
            if chars:
                chars.append("\n")
                boxes.append(None)
                lines.append(line_no)
            for span in line["spans"]:
                for ch in span["chars"]:
                    chars.append(ch["c"])
                    boxes.append(tuple(ch["bbox"]))
                    lines.append(line_no)
        if chars:
            blocks.append(("".join(chars), boxes, lines))
    return blocks


def _rects_for(boxes: list[RectTuple | None], lines: list[int], text: str, start: int, end: int):
    by_line: dict[int, pymupdf.Rect] = {}
    for i in range(start, end):
        box = boxes[i]
        if box is None or text[i].isspace():
            continue
        rect = pymupdf.Rect(box)
        if rect.is_empty:
            continue
        line = lines[i]
        by_line[line] = by_line[line] | rect if line in by_line else rect
    return tuple(tuple(by_line[k]) for k in sorted(by_line))


def search_pages(
    session: PdfSession, pattern: re.Pattern[str], pages: Iterable[int] | None = None,
    progress: Progress | None = None,
) -> list[SearchHit]:
    """Every match on ``pages`` (all by default). ``progress(done, total, "Searching page")``
    after each page; False from it stops the search (the matches found so far are returned)."""
    hits: list[SearchHit] = []
    indices = list(range(session.page_count) if pages is None else pages)
    for done, index in enumerate(indices):
        if not report(progress, done, len(indices), "Searching page"):
            break
        hits += search_one_page(session, pattern, index)
    return hits


def search_one_page(session: PdfSession, pattern: re.Pattern[str], index: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    page = session.doc[index]
    for text, boxes, lines in _page_blocks(page):
        for match in pattern.finditer(text):
            if match.end() == match.start():
                continue
            rects = _rects_for(boxes, lines, text, match.start(), match.end())
            if not rects:
                continue
            context_start = text.rfind("\n", 0, match.start()) + 1
            context_end = text.find("\n", match.end())
            context = text[context_start : None if context_end < 0 else context_end]
            hits.append(SearchHit(index, match.group(), rects, " ".join(context.split())[:120]))
    return hits


def page_has_images_but_no_text(session: PdfSession, index: int) -> bool:
    """Likely a scanned page: search can't find anything on it (no OCR)."""
    page = session.doc[index]
    return bool(page.get_images()) and not page.get_text().strip()
