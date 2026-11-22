"""Page-range text such as ``1,5,8-12``.

UI text is 1-based; results are 0-based page indices. Supported parts, comma
separated (spaces ignored):

* ``7``      a single page
* ``8-12``   an inclusive range (``12-8`` runs backwards)
* ``5-``     page 5 to the last page
* ``-3``     first page to page 3
* ``all``    every page

Pages come back in the order typed, with later duplicates dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_PART = re.compile(r"^(\d*)\s*-\s*(\d*)$")


class PageRangeError(ValueError):
    """Message is suitable for showing to the user."""


def parse_page_ranges(text: str, page_count: int) -> list[int]:
    if page_count < 1:
        raise PageRangeError("The document has no pages.")
    cleaned = text.strip().lower()
    if not cleaned:
        raise PageRangeError("Enter one or more pages, for example 1,5,8-12.")
    result: list[int] = []
    seen: set[int] = set()

    def add(pages: Iterable[int]) -> None:
        for page in pages:
            if page not in seen:
                seen.add(page)
                result.append(page)

    for raw in cleaned.split(","):
        part = raw.strip()
        if not part:
            continue
        if part == "all":
            add(range(page_count))
            continue
        if part.isdigit():
            add([_index(part, page_count)])
            continue
        match = _PART.match(part)
        if not match or not (match.group(1) or match.group(2)):
            raise PageRangeError(f'"{raw.strip()}" is not a page or range. Use e.g. 1,5,8-12.')
        start = _index(match.group(1), page_count) if match.group(1) else 0
        end = _index(match.group(2), page_count) if match.group(2) else page_count - 1
        step = 1 if end >= start else -1
        add(range(start, end + step, step))
    if not result:
        raise PageRangeError("Enter one or more pages, for example 1,5,8-12.")
    return result


def _index(number: str, page_count: int) -> int:
    value = int(number)
    if not 1 <= value <= page_count:
        raise PageRangeError(f"Page {value} doesn't exist (the document has {page_count} pages).")
    return value - 1


def format_page_ranges(indices: Iterable[int]) -> str:
    """0-based indices -> compact 1-based text, e.g. [0, 4, 7, 8, 9] -> '1,5,8-10'."""
    pages = sorted(set(indices))
    parts: list[str] = []
    i = 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        first, last = pages[i] + 1, pages[j] + 1
        parts.append(str(first) if first == last else f"{first}-{last}")
        i = j + 1
    return ",".join(parts)
