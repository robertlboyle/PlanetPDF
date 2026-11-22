"""Find a document's sections for bookmarks: its table of contents, else its headings.

``find_sections(doc)`` returns ``Section``s in reading order (level, title, page, y in
unrotated page coordinates, like ``links.Bookmark``):

* **Table of contents** (``from_toc``): pages with at least four lines that end in a page
  number after dot leaders or a wide gap ("Introduction ........ 3"). An entry's target is
  the link on it (Word's TOC entries are links), else its title found on the pages after
  the contents, in order, else the printed page number shifted like the entries before it.
  Levels come from numbering ("2.1") or the entry's indent.
* **Headings** (``from_headings``), when there is no contents: short lines clearly larger
  than the body text, or bold and numbered ("Chapter 3", "2.1 Scope"), that aren't running
  heads (the same text near the same place on several pages). Levels from the heading
  styles, largest first (at most three).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from planetpdf.core.progress import report

MIN_TOC_LINES = 4
TOC_SCAN_PAGES = 12  # contents are near the front
MAX_LEVELS = 3
HEADING_RATIO = 1.15  # a heading is at least this much larger than the body text
_LEADER = re.compile(r"^(?P<title>.*?\S)\s*(?:[.·…_\-]\s*){3,}\s*(?P<num>\d{1,4}|[ivxlc]{1,7})$", re.I)
_GAP_NUM = re.compile(r"^(?P<title>.*?\S)\s+(?P<num>\d{1,4})$")
_NUMBERED = re.compile(r"^(?P<n>\d+(?:\.\d+)*)\.?\s+\S")
_CHAPTER = re.compile(r"^(chapter|section|part|appendix|article)\s+([0-9ivxlc]+|[a-z])\b", re.I)


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    page: int
    y: float = 0.0
    found: str = "heading"  # "toc" (from the table of contents) or "heading"


@dataclass
class _Row:
    page: int
    text: str
    rect: pymupdf.Rect
    size: float
    bold: bool
    block_lines: int = 1
    first_in_block: bool = True


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _rows(page: pymupdf.Page, index: int) -> list[_Row]:
    """The page's lines joined by baseline (a TOC's title, leader and number are often
    separate MuPDF lines), with their size and boldness; horizontal text only."""
    spans = []
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    for b, block in enumerate(data["blocks"]):
        lines = [ln for ln in block.get("lines", []) if abs(ln["dir"][1]) < 0.01 and ln["dir"][0] > 0]
        for n, line in enumerate(lines):
            for span in line["spans"]:
                if not span["text"].strip() or span.get("alpha", 255) == 0:
                    continue
                spans.append((round(span["origin"][1]), span, b, n, len(lines)))
    spans.sort(key=lambda s: (s[0], s[1]["origin"][0]))
    rows: list[_Row] = []
    group: list = []

    def flush() -> None:
        if not group:
            return
        group.sort(key=lambda s: s[1]["bbox"][0])
        text, last_x = "", None
        for _y, span, *_rest in group:
            if last_x is not None and span["bbox"][0] - last_x > span["size"] * 0.15 and not text.endswith(" "):
                text += " "
            text += span["text"]
            last_x = span["bbox"][2]
        chars = Counter()
        for _y, span, *_rest in group:
            chars[(round(span["size"], 1), bool(span["flags"] & 16) or "bold" in span["font"].lower())] += len(span["text"].strip())
        (size, bold), _n = chars.most_common(1)[0]
        rect = pymupdf.Rect()
        for _y, span, *_rest in group:
            rect |= pymupdf.Rect(span["bbox"])
        _y, _s, _b, line_no, count = group[0]
        rows.append(_Row(index, _clean(text), rect, size, bold, count, line_no == 0))

    for item in spans:
        if group and abs(item[0] - group[-1][0]) > 2:
            flush()
            group = []
        group.append(item)
    flush()
    return rows


def _roman(text: str) -> int | None:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    text = text.lower()
    if not text or any(c not in values for c in text):
        return None
    total = 0
    for a, b in zip(text, text[1:] + " "):
        total += -values[a] if b in values and values[b] > values[a] else values[a]
    return total


def _toc_entry(row: _Row) -> tuple[str, str] | None:
    match = _LEADER.match(row.text)
    if match is None:
        match = _GAP_NUM.match(row.text)
        if match is None:
            return None
    title = match.group("title").strip(" .·…_-\t")
    if len(title) < 2 or not re.search(r"[A-Za-z]", title):
        return None
    return title, match.group("num")


def _links_on(page: pymupdf.Page) -> list[dict]:
    return [ln for ln in page.get_links() if ln.get("kind") == pymupdf.LINK_GOTO and ln.get("page", -1) >= 0]


def _levels_by_indent(lefts: list[float]) -> list[int]:
    steps: list[float] = []
    for x in sorted(set(round(x) for x in lefts)):
        if not steps or x - steps[-1] > 6:
            steps.append(x)
    return [min(max(sum(1 for s in steps if s <= round(x)), 1), MAX_LEVELS) for x in lefts]


def _find_title(doc: pymupdf.Document, title: str, start: int) -> tuple[int, float] | None:
    words = title.split()
    probe = " ".join(words[:6])
    if len(probe) < 3:
        return None
    for index in range(start, doc.page_count):
        hits = doc[index].search_for(probe)
        if hits:
            return index, hits[0].y0
    return None


def from_toc(doc: pymupdf.Document) -> list[Section]:
    toc_rows: list[tuple[_Row, str, str]] = []
    toc_pages: list[int] = []
    for index in range(min(doc.page_count, TOC_SCAN_PAGES)):
        entries = [(row, *e) for row in _rows(doc[index], index) if (e := _toc_entry(row)) is not None]
        leaders = sum(1 for row, *_e in entries if _LEADER.match(row.text))
        heading = any(re.search(r"\b(contents|table of contents)\b", r.text, re.I) for r in _rows(doc[index], index)[:5])
        if len(entries) >= MIN_TOC_LINES and (leaders >= MIN_TOC_LINES or heading or len(entries) >= 8):
            toc_rows += entries
            toc_pages.append(index)
        elif toc_pages:
            break  # (contents run on consecutive pages)
    if not toc_rows:
        return []
    levels = _levels_by_indent([row.rect.x0 for row, *_e in toc_rows])
    after = toc_pages[-1] + 1
    links = {i: _links_on(doc[i]) for i in toc_pages}
    out: list[Section] = []
    shift: int | None = None  # physical page - printed number, from the entries placed so far
    search_from = after
    for (row, title, num), level in zip(toc_rows, levels):
        numbered = _NUMBERED.match(title)
        if numbered:
            level = min(numbered.group("n").count(".") + 1, MAX_LEVELS)
        target: tuple[int, float] | None = None
        for link in links[row.page]:
            if pymupdf.Rect(link["from"]).intersects(row.rect):
                to = link.get("to")
                target = (link["page"], float(to.y) if to is not None else 0.0)
                break
        printed = int(num) if num.isdigit() else None
        if target is None:
            target = _find_title(doc, title, search_from)
            if target is not None and printed is not None and shift is not None and abs(target[0] - (printed + shift)) > 3:
                target = None  # (found the words somewhere unrelated)
        if target is None and printed is not None and shift is not None:
            target = (min(max(printed + shift, 0), doc.page_count - 1), 0.0)
        if target is None:
            continue
        if printed is not None:
            shift = target[0] - printed
        search_from = max(search_from, target[0])
        out.append(Section(level, title, target[0], max(target[1] - 4, 0.0), "toc"))
    return _fix_levels(out)


def _fix_levels(sections: list[Section]) -> list[Section]:
    out, previous = [], 0
    for s in sections:
        level = max(1, min(s.level, previous + 1))
        out.append(Section(level, s.title, s.page, s.y, s.found))
        previous = level
    return out


def _body_size(rows: list[_Row]) -> float:
    sizes = Counter()
    for row in rows:
        sizes[row.size] += len(row.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _running_heads(rows: list[_Row], page_count: int) -> set[str]:
    """Text repeated word for word near the same height on many pages (a document-wide
    header or footer). Chapter headings differ in their numbers, so digits count."""
    seen: dict[tuple[str, int], set[int]] = {}
    for row in rows:
        seen.setdefault((row.text.lower(), round(row.rect.y0 / 20)), set()).add(row.page)
    return {text for (text, _y), pages in seen.items() if len(pages) >= max(2, page_count // 2)}


def from_headings(doc: pymupdf.Document, progress=None, *, skip: set[int] = frozenset()) -> list[Section]:
    rows: list[_Row] = []
    for index in range(doc.page_count):
        report(progress, index, doc.page_count, "Reading page")
        if index not in skip:
            rows += _rows(doc[index], index)
    report(progress, doc.page_count, doc.page_count, "Reading page")
    if not rows:
        return []
    body = _body_size(rows)
    heads = _running_heads(rows, doc.page_count) if doc.page_count > 2 else set()
    candidates: list[_Row] = []
    for row in rows:
        text = row.text
        if not (2 <= len(text) <= 120) or not re.search(r"[A-Za-z]{2}", text):
            continue
        if (text.endswith((".", ",", ";")) and not _CHAPTER.match(text)) or _LEADER.match(text):
            continue  # (a sentence, or a contents line)
        if text.lower() in heads or row.block_lines > 3 or not row.first_in_block:
            continue
        larger = row.size >= body * HEADING_RATIO
        marked = row.bold and row.size >= body - 0.1 and (_CHAPTER.match(text) or _NUMBERED.match(text))
        if larger or marked:
            candidates.append(row)
    if len(candidates) < 2:
        return []
    styles = sorted({(c.size, c.bold) for c in candidates}, key=lambda s: (-s[0], not s[1]))
    level_of = {style: min(i + 1, MAX_LEVELS) for i, style in enumerate(styles)}
    out = []
    for row in candidates:
        if any(s.title == row.text and s.page < row.page for s in out[-3:]):
            continue  # (a chapter's name repeated at the top of its pages)
        level = level_of[(row.size, row.bold)]
        numbered = _NUMBERED.match(row.text)
        if numbered and not row.size >= body * 1.6:
            level = max(level, min(numbered.group("n").count(".") + 1, MAX_LEVELS))
        if out and out[-1].page == row.page and abs(out[-1].y + 4 - row.rect.y0) < row.size * 1.6 \
                and out[-1].level == level:
            prev = out.pop()  # (a heading wrapped onto two lines)
            out.append(Section(level, f"{prev.title} {row.text}", prev.page, prev.y))
            continue
        out.append(Section(level, row.text, row.page, max(row.rect.y0 - 4, 0.0)))
    return _fix_levels(out)


def find_sections(doc: pymupdf.Document, progress=None) -> list[Section]:
    """The table of contents' entries when there is one, else the headings."""
    toc = from_toc(doc)
    if toc:
        return toc
    return from_headings(doc, progress)
