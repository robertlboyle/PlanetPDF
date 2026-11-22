"""Words on a page in reading order, and the text of a selection of them."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfSession


@dataclass(frozen=True)
class Word:
    rect: tuple[float, float, float, float]  # unrotated page coordinates
    text: str
    block: int
    line: int


def page_words(session: PdfSession, index: int) -> list[Word]:
    page = session.doc[index]
    return [Word(tuple(w[:4]), w[4], w[5], w[6]) for w in page.get_text("words", sort=True)]


def selection_text(words: list[Word]) -> str:
    """Join selected words: spaces within a line, a newline between lines, and a blank
    line only where there's a real vertical gap (a new paragraph). MuPDF often makes
    every line its own "block", so blocks alone aren't paragraphs."""
    out: list[str] = []
    prev: Word | None = None
    for word in words:
        if prev is not None:
            if (word.block, word.line) == (prev.block, prev.line):
                out.append(" ")
            else:
                height = prev.rect[3] - prev.rect[1]
                gap = word.rect[1] - prev.rect[3]
                out.append("\n\n" if gap > height * 0.9 else "\n")
        out.append(word.text)
        prev = word
    return "".join(out)


def line_rects(words: list[Word]) -> list[pymupdf.Rect]:
    """One rectangle per line of the selection (for highlighting)."""
    lines: dict[tuple[int, int], pymupdf.Rect] = {}
    for word in words:
        key = (word.block, word.line)
        lines[key] = lines[key] | pymupdf.Rect(word.rect) if key in lines else pymupdf.Rect(word.rect)
    return list(lines.values())
