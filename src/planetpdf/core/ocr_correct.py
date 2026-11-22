"""Scan & OCR ▸ Recognize Text ▸ Correct Recognized Text (Acrobat).

Lists the words of PlanetPDF's invisible OCR text that look wrong ("suspects": not in
the dictionary, or letters mixed with digits and odd marks), so each can be compared
with the picture and retyped. A correction rewrites that page's OCR text: the old layer
text is removed and every word written again, the corrected ones with their new text, in
the same boxes (``ocr.insert_ocr_text``), so search and copy find the right words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from planetpdf.core import ocr
from planetpdf.core.document import PdfSession

_LETTERS = re.compile(r"[A-Za-z]")
_ODD = re.compile(r"[^\w'’\-.,;:!?()/&%$#@\"“”]")


@dataclass(frozen=True)
class Suspect:
    page: int
    index: int  # the word's place in ``ocr_words(doc, page)``
    rect: tuple[float, float, float, float]  # unrotated
    text: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerWord:
    rect: tuple[float, float, float, float]  # unrotated
    text: str
    origin: tuple[float, float]  # where it was written (unrotated), so a rewrite puts it back exactly
    size: float


def ocr_words(doc: pymupdf.Document, index: int) -> list[LayerWord]:
    """The page's OCR words (PlanetPDF's invisible layer text), in reading order."""
    if not ocr.page_has_ocr(doc, index):
        return []
    page = doc[index]
    out = []
    for block in page.get_text("rawdict", sort=True)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span.get("alpha", 255) != 0:
                    continue
                word: list[dict] = []
                for ch in span["chars"] + [None]:
                    if ch is None or not ch["c"].strip():
                        if word:
                            rect = pymupdf.Rect(word[0]["bbox"])
                            for c in word[1:]:
                                rect |= pymupdf.Rect(c["bbox"])
                            out.append(LayerWord(tuple(rect), "".join(c["c"] for c in word),
                                                 tuple(word[0]["origin"]), span["size"]))
                        word = []
                    else:
                        word.append(ch)
    return out


def looks_wrong(word: str, speller=None) -> bool:
    core = word.strip(".,;:!?()\"'“”’")
    if not core or not _LETTERS.search(core):
        return False  # (numbers, dates, prices)
    if _ODD.search(core):
        return True  # "W0rd§", "l|ne"
    if any(c.isdigit() for c in core) and any(c.isalpha() for c in core):
        letters = sum(c.isalpha() for c in core)
        if not re.fullmatch(r"\d+(st|nd|rd|th|s|am|pm|AM|PM)", core) and letters > 1:
            return True  # "c0mpany", "5ervice" (not "1st", "1990s")
    return speller is not None and len(core) > 1 and speller.is_wrong(core)


def find_suspects(doc: pymupdf.Document, pages, speller=None, *, progress=None) -> list[Suspect]:
    from planetpdf.core.progress import report

    pages = list(pages)
    out = []
    for n, index in enumerate(pages):
        if not report(progress, n, len(pages), "Checking page"):
            break
        for k, word in enumerate(ocr_words(doc, index)):
            if looks_wrong(word.text, speller):
                hints = tuple(speller.suggestions(word.text.strip(".,;:!?()\"'“”’"), 5)) if speller else ()
                out.append(Suspect(index, k, word.rect, word.text, hints))
    return out


def correct_words(session: PdfSession, index: int, changes: dict[int, str]) -> int:
    """Rewrite page ``index``'s OCR text with ``changes`` (word index -> new text; empty =
    the word is dropped). Returns the number of words changed."""
    doc = session.doc
    words = ocr_words(doc, index)
    if not any(k < len(words) and changes[k] != words[k].text for k in changes):
        return 0
    ocr.remove_ocr_text(session, [index])
    page = doc[index]
    shape = page.new_shape()
    changed = 0
    for k, word in enumerate(words):
        text, size = word.text, word.size
        if k in changes and changes[k] != word.text:
            changed += 1
            text = changes[k].strip()
            if not text:
                continue
            # the new word fills the old one's width (search and redaction boxes follow it)
            width = abs((pymupdf.Rect(word.rect) * page.rotation_matrix).width)
            unit = pymupdf.get_text_length(text, fontname=ocr._FONT, fontsize=1)
            if unit > 0:
                size = max(1.0, min(word.size * 2, width / unit))
        try:
            shape.insert_text(pymupdf.Point(word.origin), text, fontsize=size, fontname=ocr._FONT,
                              rotate=page.rotation, render_mode=3)
        except (ValueError, RuntimeError):
            continue  # (characters the font can't encode)
    shape.commit(overlay=True)
    ocr._wrap_last_stream_in_layer(doc, page, ocr._ensure_layer(doc))
    session.mark_modified()
    return changed


def correct_at(session: PdfSession, index: int, rect, old: str, new: str) -> int:
    """Check Spelling's Change on OCR text: the layer word under ``rect`` holding ``old``."""
    area = pymupdf.Rect(rect)
    for k, word in enumerate(ocr_words(session.doc, index)):
        r = pymupdf.Rect(word.rect)
        if old in word.text and r.intersects(area) and (r & area).get_area() > 0.3 * min(r.get_area(), area.get_area()):
            return correct_words(session, index, {k: word.text.replace(old, new, 1)})
    return 0


def word_picture(doc: pymupdf.Document, page: int, rect, *, dpi: int = 200, margin: float = 3.0) -> bytes:
    """PNG of the scan under a word (as displayed), for comparing with the recognized text."""
    p = doc[page]
    shown = (pymupdf.Rect(rect) * p.rotation_matrix).normalize()
    clip = pymupdf.Rect(shown.x0 - margin * 4, shown.y0 - margin, shown.x1 + margin * 4, shown.y1 + margin) & p.rect
    return p.get_pixmap(dpi=dpi, clip=clip, annots=False).tobytes("png")
