"""Compare two PDFs: what text was added, removed or changed, and where.

Text is compared in reading order, first line by line (fast, and it keeps unrelated
changes apart), then word by word inside the lines that differ. Positions are in
*displayed* page coordinates (what the reader sees, rotation applied), so they can
be highlighted directly.

``comparison_report`` writes a PDF: a summary of the changes, then each page pair
side by side (old left, new right) with removals marked red, additions green and
changes amber.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from planetpdf.core.progress import Progress, report

RectTuple = tuple[float, float, float, float]

ADDED, REMOVED, CHANGED = "added", "removed", "changed"
COLORS = {REMOVED: (0.9, 0.15, 0.15), ADDED: (0.1, 0.65, 0.2), CHANGED: (0.95, 0.65, 0.0)}
LABELS = {ADDED: "Added", REMOVED: "Removed", CHANGED: "Changed"}


@dataclass(frozen=True)
class Word:
    page: int
    rect: RectTuple  # displayed coordinates
    text: str
    line: tuple[int, int, int]  # (page, block, line)


@dataclass(frozen=True)
class Change:
    kind: str  # ADDED / REMOVED / CHANGED
    old_text: str
    new_text: str
    old_places: tuple[tuple[int, RectTuple], ...] = ()  # (page, rect) in the old document
    new_places: tuple[tuple[int, RectTuple], ...] = ()

    @property
    def old_page(self) -> int | None:
        return self.old_places[0][0] if self.old_places else None

    @property
    def new_page(self) -> int | None:
        return self.new_places[0][0] if self.new_places else None

    def describe(self, limit: int = 80, arrow: str = "→") -> str:
        def cut(text: str) -> str:
            return text if len(text) <= limit else text[: limit - 1] + "…"

        if self.kind == ADDED:
            return f"Added “{cut(self.new_text)}”"
        if self.kind == REMOVED:
            return f"Removed “{cut(self.old_text)}”"
        return f"“{cut(self.old_text)}” {arrow} “{cut(self.new_text)}”"


@dataclass
class Comparison:
    old_pages: int
    new_pages: int
    changes: list[Change] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.changes and self.old_pages == self.new_pages

    def counts(self) -> dict[str, int]:
        return {kind: sum(1 for c in self.changes if c.kind == kind) for kind in (ADDED, REMOVED, CHANGED)}

    def summary(self) -> str:
        if self.identical:
            return "No differences in the text."
        c = self.counts()
        parts = [f"{c[CHANGED]} changed", f"{c[ADDED]} added", f"{c[REMOVED]} removed"]
        text = ", ".join(parts)
        if self.old_pages != self.new_pages:
            text += f"; {self.old_pages} pages before, {self.new_pages} after"
        return text


def document_words(doc: pymupdf.Document, progress: Progress | None = None) -> list[Word]:
    words: list[Word] = []
    for index in range(doc.page_count):
        if progress is not None and not progress(index, doc.page_count):
            break
        page = doc[index]
        matrix = page.rotation_matrix
        for w in page.get_text("words", sort=True):
            shown = (pymupdf.Rect(w[:4]) * matrix).normalize()
            words.append(Word(index, tuple(shown), w[4], (index, w[5], w[6])))
    return words


def _lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for w in words:
        if lines and lines[-1][-1].line == w.line:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def _places(words: list[Word]) -> tuple[tuple[int, RectTuple], ...]:
    """One box per line run (not per word), so highlights look like a marker pen."""
    out: list[tuple[int, pymupdf.Rect]] = []
    last_line = None
    for w in words:
        if out and w.line == last_line:
            out[-1] = (w.page, out[-1][1] | pymupdf.Rect(w.rect))
        else:
            out.append((w.page, pymupdf.Rect(w.rect)))
        last_line = w.line
    return tuple((page, tuple(rect)) for page, rect in out)


def _text(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def _change(old: list[Word], new: list[Word]) -> Change:
    kind = CHANGED if old and new else (ADDED if new else REMOVED)
    return Change(kind, _text(old), _text(new), _places(old), _places(new))


def compare(old: pymupdf.Document, new: pymupdf.Document, progress: Progress | None = None) -> Comparison:
    """``progress(done, total, stage)`` (``core.progress``): "Reading the old document:
    page x of y", the same for the new one, then "Comparing changed passage x of y"."""

    def old_progress(done, total):
        return report(progress, done, total, "Reading the old document: page")

    def new_progress(done, total):
        return report(progress, done, total, "Reading the new document: page")

    a_lines = _lines(document_words(old, old_progress))
    b_lines = _lines(document_words(new, new_progress))
    result = Comparison(old.page_count, new.page_count)
    report(progress, 0, 0, f"Matching {len(a_lines):,} lines against {len(b_lines):,}…")
    line_matcher = SequenceMatcher(None, [_text(l) for l in a_lines], [_text(l) for l in b_lines], autojunk=False)
    opcodes = [op for op in line_matcher.get_opcodes() if op[0] != "equal"]
    for n, (op, a0, a1, b0, b1) in enumerate(opcodes):
        if not report(progress, n, len(opcodes), "Comparing changed passage"):
            break  # (canceled: the caller drops the partial result)
        a_words = [w for line in a_lines[a0:a1] for w in line]
        b_words = [w for line in b_lines[b0:b1] for w in line]
        if op != "replace":
            result.changes.append(_change(a_words, b_words))
            continue
        # Lines differ: find the words that do.
        word_matcher = SequenceMatcher(None, [w.text for w in a_words], [w.text for w in b_words], autojunk=False)
        for wop, i0, i1, j0, j1 in word_matcher.get_opcodes():
            if wop != "equal":
                result.changes.append(_change(a_words[i0:i1], b_words[j0:j1]))
    return result


# --- the report -------------------------------------------------------------------------------------

_MARGIN = 36.0
_GAP = 24.0
_HEADER = 28.0
_FONT = "PPDFNoto"  # bundled Noto Sans: document text can be any script


def _font(page: pymupdf.Page) -> None:
    from planetpdf.core.resources import font_path

    page.insert_font(fontname=_FONT, fontfile=str(font_path("sans")))


def _upright_copy(doc: pymupdf.Document) -> tuple[pymupdf.Document, list[int]]:
    """A copy with every page's /Rotate reset (show_pdf_page draws the unrotated page
    into the rotated page's box otherwise); returns the original rotations."""
    copy = pymupdf.open("pdf", doc.tobytes(garbage=0))
    rotations = []
    for page in copy:
        rotations.append(page.rotation)
        page.set_rotation(0)
    return copy, rotations


def _summary_pages(out: pymupdf.Document, comparison: Comparison, old_label: str, new_label: str) -> None:
    width, height = pymupdf.paper_size("letter")
    lines = [("Document comparison", 16, (0, 0, 0)),
             (f"Old: {old_label}", 10, (0.3, 0.3, 0.3)),
             (f"New: {new_label}", 10, (0.3, 0.3, 0.3)),
             ("", 8, (0, 0, 0)),
             (comparison.summary(), 11, (0, 0, 0)),
             ("", 8, (0, 0, 0))]
    for change in comparison.changes:
        where = []
        if change.old_page is not None:
            where.append(f"old p.{change.old_page + 1}")
        if change.new_page is not None:
            where.append(f"new p.{change.new_page + 1}")
        lines.append((f"{LABELS[change.kind]} ({', '.join(where)}): {change.describe(90, arrow="->")}", 9, COLORS[change.kind]))
    page, y = None, height
    for text, size, color in lines:
        if page is None or y + size * 1.5 > height - _MARGIN:
            page = out.new_page(width=width, height=height)
            _font(page)
            y = _MARGIN
        if text:
            # insert_textbox wraps long lines; measure what it used.
            box = pymupdf.Rect(_MARGIN, y, width - _MARGIN, height - _MARGIN)
            spare = page.insert_textbox(box, text, fontsize=size, fontname=_FONT, color=color)
            used = box.height - spare if spare >= 0 else size * 1.4
            y += max(used, size * 1.2) + 2
        else:
            y += size


class ReportCanceled(Exception):
    pass


def comparison_report(old: pymupdf.Document, new: pymupdf.Document, comparison: Comparison, out_path: Path,
                      old_label: str = "Old", new_label: str = "New", progress: Progress | None = None) -> Path:
    """``progress``: "Drawing page pair x of y", then "Saving the report…"; False from it
    stops (``ReportCanceled``, nothing written)."""
    out = pymupdf.open()
    _summary_pages(out, comparison, old_label, new_label)
    old_copy, old_rot = _upright_copy(old)
    new_copy, new_rot = _upright_copy(new)
    marks: dict[tuple[str, int], list[tuple[str, RectTuple]]] = {}
    for change in comparison.changes:
        for page, rect in change.old_places:
            marks.setdefault(("old", page), []).append((change.kind, rect))
        for page, rect in change.new_places:
            marks.setdefault(("new", page), []).append((change.kind, rect))
    pairs = max(old.page_count, new.page_count)
    try:
        for index in range(pairs):
            if not report(progress, index, pairs, "Drawing page pair"):
                raise ReportCanceled()
            sides = []
            for side, src, copy, rots in (("old", old, old_copy, old_rot), ("new", new, new_copy, new_rot)):
                sides.append((side, src[index].rect if index < src.page_count else None, copy, rots))
            w_old = sides[0][1].width if sides[0][1] else sides[1][1].width
            w_new = sides[1][1].width if sides[1][1] else sides[0][1].width
            h = max(r.height for _s, r, _c, _r in sides if r is not None)
            page = out.new_page(width=_MARGIN * 2 + w_old + _GAP + w_new, height=_MARGIN * 2 + _HEADER + h)
            _font(page)
            x = _MARGIN
            for (side, shown, copy, rots), width in zip(sides, (w_old, w_new)):
                label = f"{old_label if side == 'old' else new_label} — page {index + 1}"
                page.insert_text((x, _MARGIN + 12), label, fontsize=10, fontname=_FONT, color=(0.25, 0.25, 0.25))
                top = _MARGIN + _HEADER
                if shown is None:
                    page.insert_text((x, top + 20), "(no such page)", fontsize=10, fontname=_FONT, color=(0.5, 0.5, 0.5))
                else:
                    target = pymupdf.Rect(x, top, x + shown.width, top + shown.height)
                    page.draw_rect(target, color=(0.75, 0.75, 0.75), width=0.5)
                    page.show_pdf_page(target, copy, index, rotate=-rots[index])
                    for kind, rect in marks.get((side, index), []):
                        r = pymupdf.Rect(rect) + (x, top, x, top)
                        page.draw_rect(r, color=None, fill=COLORS[kind], fill_opacity=0.35, overlay=True)
                        page.draw_line(r.bl, r.br, color=COLORS[kind], width=1)
                x += width + _GAP
        report(progress, 0, 0, f"Saving the report ({out.page_count} pages)…")
        out.subset_fonts()
        out.save(str(out_path), garbage=3, deflate=True)
    finally:
        old_copy.close()
        new_copy.close()
        out.close()
    return Path(out_path)
