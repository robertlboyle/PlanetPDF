"""Edit existing page content: retype a line of text, delete it, add text; move,
resize, replace, delete or add images.

Text: the original characters are removed with a *text-only* redaction of the line
(images and graphics untouched), then the new text is written at the same baseline,
size and color, in the original embedded font if it contains every character
needed, otherwise in the closest standard font (family, bold, italic).

The user's own pending redaction marks are set aside while we apply our internal
redaction and put back afterwards, so editing never applies them by accident.

Undo is handled by the caller with ``core.snapshots`` (page content in place).
"""

from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.contents import replace_contents
from planetpdf.core.redaction.ocg import tokenize

RectTuple = tuple[float, float, float, float]

# PyMuPDF span flags
_ITALIC, _SERIF, _MONO, _BOLD = 2, 4, 8, 16
_BASE14 = {
    ("sans", False, False): "helv", ("sans", True, False): "hebo", ("sans", False, True): "heit", ("sans", True, True): "hebi",
    ("serif", False, False): "tiro", ("serif", True, False): "tibo", ("serif", False, True): "tiit", ("serif", True, True): "tibi",
    ("mono", False, False): "cour", ("mono", True, False): "cobo", ("mono", False, True): "coit", ("mono", True, True): "cobi",
}


@dataclass(frozen=True)
class TextLine:
    page: int
    rect: RectTuple  # unrotated
    text: str
    font: str  # PDF font name of the first span
    size: float
    color: tuple[float, float, float]
    flags: int
    origin: tuple[float, float]  # baseline start of the first span (unrotated)
    direction: tuple[float, float]  # writing direction in unrotated space
    # a space before / after its text (``text`` is trimmed): where a run of another font goes on
    lead_space: bool = False
    trail_space: bool = False

    @property
    def family(self) -> str:
        name = self.font.lower()
        if self.flags & _MONO or "courier" in name or "mono" in name:
            return "mono"
        if self.flags & _SERIF or "times" in name or "serif" in name and "sans" not in name:
            return "serif"
        return "sans"

    @property
    def bold(self) -> bool:
        return bool(self.flags & _BOLD) or "bold" in self.font.lower()

    @property
    def italic(self) -> bool:
        return bool(self.flags & _ITALIC) or "italic" in self.font.lower() or "oblique" in self.font.lower()


@dataclass(frozen=True)
class PageImage:
    page: int
    xref: int
    name: str  # resource name used in the content stream
    rect: RectTuple  # unrotated bounding box of this placement


def _rgb(srgb: int) -> tuple[float, float, float]:
    return (((srgb >> 16) & 255) / 255, ((srgb >> 8) & 255) / 255, (srgb & 255) / 255)


def page_text(page: pymupdf.Page, kind: str = "dict", clip=None, sort: bool = False) -> dict:
    """``page.get_text(kind)`` of the page's own content only: MuPDF also reads the text of
    annotations (placed text, stamps, comments), which Edit PDF offered as page text and
    rewrote into the page (2026-10-03). Same coordinates as ``get_text`` (unrotated);
    ``clip``: only characters whose middle is inside it (rawdict) / lines inside it."""
    import pymupdf.mupdf as M

    options = M.FzStextOptions()
    options.flags = pymupdf.TEXTFLAGS_RAWDICT if kind == "rawdict" else pymupdf.TEXTFLAGS_DICT
    stext = M.FzStextPage(M.fz_bound_page(page.this))
    device = M.fz_new_stext_device(stext, options)
    try:
        M.pdf_run_page_contents(M.pdf_page_from_fz_page(page.this), device, M.FzMatrix(*page.derotation_matrix),
                                M.FzCookie())
    finally:
        M.fz_close_device(device)
    textpage = pymupdf.TextPage(stext)
    textpage.parent = page
    data = page.get_text(kind, textpage=textpage, sort=sort)
    if clip is None:
        return data
    box = pymupdf.Rect(clip)

    def inside(bbox) -> bool:
        r = pymupdf.Rect(bbox)
        return box.contains(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))

    blocks = []
    for block in data["blocks"]:
        lines = []
        for line in block.get("lines", []):
            spans = []
            for span in line["spans"]:
                if "chars" in span:
                    chars = [c for c in span["chars"] if inside(c["bbox"])]
                    if chars:
                        spans.append({**span, "chars": chars})
                elif inside(span["bbox"]):
                    spans.append(span)
            if spans:
                lines.append({**line, "spans": spans})
        if lines:
            blocks.append({**block, "lines": lines})
    return {**data, "blocks": blocks}


def text_lines(session: PdfSession, index: int) -> list[TextLine]:
    """The editable lines of text on a page.

    MuPDF's "line" is everything on one baseline, which can be text drawn on top of
    other text, text far apart, or several fonts. A line here is one run of text in one
    font, size and color that doesn't overlap itself (user bug 2026-10-01: a moved line
    that landed on other text was picked up together with it, and rewritten in one font)."""
    page = session.doc[index]
    groups: list[tuple[list[dict], tuple]] = []
    for block in page_text(page, "dict", sort=True)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for group in _span_groups(line):
                spans = [s for s in group if s["text"].strip()]
                if not spans or all(s.get("alpha", 255) == 0 for s in spans):
                    continue  # invisible text (an OCR layer): retyping it would draw over the scan
                groups.append((group, tuple(line["dir"])))
    groups = _join_pieces(groups)
    lines = []
    for group, direction in groups:
        spans = [s for s in group if s["text"].strip()]
        first = spans[0]
        text = ""
        for s in group:
            if text and s["text"].strip() and _gap_before(text_end, s, direction) > 0.15 * s["size"] \
                    and not text.endswith(" ") and not s["text"].startswith(" "):
                text += " "  # (pieces joined below: the space between them isn't a character)
            text += s["text"]
            if s["text"].strip():
                text_end = s
        box = pymupdf.Rect(spans[0]["bbox"])
        for s in spans[1:]:
            box |= pymupdf.Rect(s["bbox"])
        lines.append(
            TextLine(index, tuple(box), " ".join(text.split()) if "  " in text else text.strip(), first["font"],
                     first["size"], _rgb(first["color"]), first["flags"], tuple(first["origin"]), direction,
                     text[:1].isspace(), text[-1:].isspace())
        )
    return lines


def _gap_before(a: dict, b: dict, direction) -> float:
    r = a["bbox"]
    end = max(_along(c, direction) for c in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])))
    return _along(b["origin"], direction) - end


def _join_pieces(groups: list[tuple[list[dict], tuple]]) -> list[tuple[list[dict], tuple]]:
    """Join runs that continue each other on one baseline but that MuPDF read as separate
    lines or blocks (letters put back by a later write after a move: user bug 2026-10-03,
    "Must be Filed Eac" + "h Year")."""
    def first(g):
        return next(s for s in g if s["text"].strip())

    def last(g):
        return [s for s in g if s["text"].strip()][-1]

    def across(g, d):
        o = first(g)["origin"]
        return -o[0] * d[1] + o[1] * d[0]

    # same baseline (to the point) and direction: in writing order, a piece joins the one before it
    rows: dict[tuple, list[int]] = {}
    for n, (g, d) in enumerate(groups):
        rows.setdefault((d, round(across(g, d))), []).append(n)
    into: dict[int, int] = {}  # piece -> the piece it was joined to
    merged = {n: list(g) for n, (g, _d) in enumerate(groups)}
    for (d, _row), members in rows.items():
        members.sort(key=lambda n: _along(first(groups[n][0])["origin"], d))
        head = None
        for n in members:
            if head is not None and _same_run(last(merged[head]), first(groups[n][0]), d):
                merged[head] += groups[n][0]
                into[n] = head
            else:
                head = n
    return [(merged[n], d) for n, (_g, d) in enumerate(groups) if n not in into]


def _along(point, direction) -> float:
    return point[0] * direction[0] + point[1] * direction[1]


def _span_groups(line: dict) -> list[list[dict]]:
    """A MuPDF line's spans, in writing order, cut into runs of one font / size / color
    on one baseline, where no span starts inside the previous one or far after it."""
    direction = tuple(line["dir"])
    spans = sorted(line["spans"], key=lambda s: _along(s["origin"], direction))
    groups: list[list[dict]] = []
    current: list[dict] = []
    last = None
    for span in spans:
        if not span["text"].strip():
            if current:
                current.append(span)  # (spaces belong to the run they're in)
            continue
        if last is not None and not _same_run(last, span, direction):
            groups.append(current)
            current = []
        current.append(span)
        last = span
    if current:
        groups.append(current)
    return groups


def _same_run(a: dict, b: dict, direction) -> bool:
    if a["font"] != b["font"] or abs(a["size"] - b["size"]) > 0.2 or a["color"] != b["color"]:
        return False
    dx, dy = direction
    across = lambda p: -p[0] * dy + p[1] * dx  # noqa: E731 (distance from the baseline)
    if abs(across(a["origin"]) - across(b["origin"])) > 1.0:
        return False
    r = a["bbox"]
    end = max(_along(c, direction) for c in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])))
    start = _along(b["origin"], direction)
    # not on top of it (a letter's worth of tight spacing is fine: a font split into
    # copies of different weights draws a letter a little wider than its place), not far away
    return end - max(1.0, 0.25 * a["size"]) <= start <= end + 3 * a["size"]


# --- fonts -------------------------------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.split("+")[-1].lower())


def _font_candidates(doc: pymupdf.Document, page: pymupdf.Page, name: str) -> list[bytes]:
    """Every embedded font program on the page that goes by this span font name."""
    wanted = _norm(name)
    accepted = {wanted, wanted + "regular", wanted + "roman", wanted + "book"}
    fonts = page.get_fonts(full=True)
    exact = [f for f in fonts if _norm(f[3]) == wanted]
    found: list[bytes] = []
    for xref, ext, _type, basefont, *_rest in exact or [f for f in fonts if _norm(f[3]) in accepted]:
        if ext in ("ttf", "otf", "cff", "pfa", "pfb", "n/a") and xref:
            try:
                _n, _e, _t, content = doc.extract_font(xref)
            except (RuntimeError, ValueError):
                continue
            if content and content not in found:
                found.append(content)
    # Copies PlanetPDF put on the page (resource "PPDF…") are listed under the font's own
    # long name, but their text reads back under the span name: match them by that, or a
    # letter moved twice lost its font once the original copy had left the page.
    for xref, ext, _type, _basefont, refname, *_rest in fonts:
        if not refname.startswith("PPDF") or not xref:
            continue
        try:
            _n, _e, _t, content = doc.extract_font(xref)
            if content and content not in found and _norm(_span_name(content)) in accepted:
                found.append(content)
        except (RuntimeError, ValueError):
            continue
    return found


_SPAN_NAMES: dict[int, str] = {}


def _span_name(buffer: bytes) -> str:
    """The font name text drawn with this program reads back under (cached)."""
    key = zlib.crc32(buffer)
    if key not in _SPAN_NAMES:
        name = ""
        scratch = pymupdf.open()
        try:
            page = scratch.new_page(width=60, height=40)
            page.insert_font(fontname="F", fontbuffer=buffer)
            font = pymupdf.Font(fontbuffer=buffer)
            ch = next((c for c in "aeoAEOnst1" if font.has_glyph(ord(c))), "a")
            page.insert_text((5, 30), ch, fontname="F", fontsize=20)
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        name = name or span["font"]
        except (RuntimeError, ValueError):
            pass
        finally:
            scratch.close()
        _SPAN_NAMES[key] = name
    return _SPAN_NAMES[key]


_INKED: dict[tuple[int, str], bool] = {}


def _inked(buffer: bytes, font: pymupdf.Font, ch: str) -> bool:
    """Whether this font program really draws ``ch``. A subset keeps the glyph ids (and
    widths) of letters it doesn't carry, with empty outlines, so ``has_glyph`` and the
    glyph box can't tell: it's drawn once and looked at (cached)."""
    key = (zlib.crc32(buffer), ch)
    if key not in _INKED:
        scratch = pymupdf.open()
        page = scratch.new_page(width=40, height=40)
        writer = pymupdf.TextWriter(page.rect)
        writer.append((6, 30), ch, font=font, fontsize=24)
        writer.write_text(page)
        pix = page.get_pixmap(dpi=36)
        samples = pix.samples
        _INKED[key] = any(samples[i] < 160 for i in range(0, len(samples), pix.n))
        scratch.close()
    return _INKED[key]


def _font_runs(doc: pymupdf.Document, page: pymupdf.Page, line: TextLine) -> list[tuple[str, tuple, bytes]] | None:
    """A line drawn from several partial copies of one font, as letter runs: (text, its
    start, the font program that draws it). Some PDFs (user bug 2026-10-02, "BE Filing
    Acknowledgement") cut a variable font into several subsets of two weights, each with
    the shapes of only some letters, and switch between them letter by letter; no single
    copy can draw a moved line the way it looked. Each letter is taken from a copy that
    really draws it, of the weight whose widths match the line's spacing. None: the line's
    font is a single font (the usual case)."""
    candidates = _font_candidates(doc, page, line.font)
    if len(candidates) <= 1:
        return None
    chars = _line_chars(page, line)
    if not chars:
        return None
    try:
        fonts = {id(b): pymupdf.Font(fontbuffer=b) for b in candidates}
    except (RuntimeError, ValueError):
        return None
    along = [_along(at, line.direction) for _c, at in chars]

    def misfit(buffer: bytes) -> float:  # (how far its widths are from the line's spacing)
        font, errors = fonts[id(buffer)], []
        for (c, _at), (n, _nat), a, b in zip(chars, chars[1:], along, along[1:]):
            if not c.isspace() and not n.isspace() and _inked(buffer, font, c):
                errors.append(abs(font.glyph_advance(ord(c)) * line.size - (b - a)))
        return sum(errors) / len(errors) if errors else float("inf")

    fit = {id(b): misfit(b) for b in candidates}
    runs: list[tuple[str, tuple, bytes]] = []
    text, start, current = "", None, None
    expected = None  # where the next letter of the run would be drawn
    for c, at in chars:
        if c.isspace():
            if text:
                runs.append((text, start, current))
            text, start, current, expected = "", None, None, None
            continue
        drawing = [b for b in candidates if fonts[id(b)].has_glyph(ord(c)) and _inked(b, fonts[id(b)], c)]
        if not drawing:
            return None  # (no copy draws it: lay the line out as before)
        best = min(drawing, key=lambda b: fit[id(b)])
        # A run goes on only while each letter is exactly where the one before leads to:
        # lines moved on top of each other read back as one mixed line, and a letter of
        # the other line laid out by advances landed in the wrong place (user bug
        # 2026-10-03: "the more I moved boxes around, … some letters vanished").
        off_place = expected is None or abs(at[0] - expected[0]) > 0.4 or abs(at[1] - expected[1]) > 0.4
        if text and (best is not current or off_place):
            runs.append((text, start, current))
            text, start = "", None
        if not text:
            start = at
        text += c
        current = best
        step = fonts[id(best)].glyph_advance(ord(c)) * line.size
        expected = (at[0] + step * line.direction[0], at[1] + step * line.direction[1])
    if text:
        runs.append((text, start, current))
    return runs


def _layout_font_runs(doc: pymupdf.Document, page: pymupdf.Page, line: TextLine, text: str):
    """Like ``_font_runs``, for new text typed into such a line: laid out from its start
    with the fonts' own widths, each letter from a copy that draws it (the weight the line
    was in). None: a single font, or a letter no copy draws."""
    candidates = _font_candidates(doc, page, line.font)
    if len(candidates) <= 1:
        return None
    old = _font_runs(doc, page, line)
    if not old:
        return None
    key = zlib.crc32  # (the same font program read twice is two different bytes objects)
    try:
        fonts = {key(b): pymupdf.Font(fontbuffer=b) for b in candidates}
    except (RuntimeError, ValueError):
        return None
    used = [key(b) for _t, _at, b in old]  # (the weight the line is in, most used first)
    rank = {key(b): -used.count(key(b)) for b in candidates}
    base = fonts.get(max(set(used), key=used.count)) or next(iter(fonts.values()))
    dx, dy = line.direction
    pos, runs = 0.0, []
    current, start, run = None, None, ""
    for c in text:
        if c.isspace():
            if run:
                runs.append((run, start, current))
            run, current = "", None
            pos += (base.glyph_advance(32) or 0.25) * line.size
            continue
        drawing = [b for b in candidates if fonts[key(b)].has_glyph(ord(c)) and _inked(b, fonts[key(b)], c)]
        if not drawing:
            return None
        best = min(drawing, key=lambda b: rank[key(b)])
        if run and best is not current:
            runs.append((run, start, current))
            run = ""
        if not run:
            start = (line.origin[0] + pos * dx, line.origin[1] + pos * dy)
        run += c
        current = best
        pos += fonts[key(best)].glyph_advance(ord(c)) * line.size
    if run:
        runs.append((run, start, current))
    return runs


def _insert_font_runs(page: pymupdf.Page, runs, dx: float, dy: float, size: float, color, rotate: int) -> None:
    """Write ``_font_runs`` back (moved by dx, dy), each run in its own font program."""
    added: set[str] = set()
    for text, (x, y), buffer in runs:
        name = f"PPDF{zlib.crc32(buffer):08x}"
        if name not in added:
            xref = page.insert_font(fontname=name, fontbuffer=buffer)
            _prefer_plain_unicode(page.parent, xref, buffer)
            added.add(name)
        page.insert_text(pymupdf.Point(x + dx, y + dy), text, fontname=name, fontsize=size, color=color, rotate=rotate)


def _embedded_font(doc: pymupdf.Document, page: pymupdf.Page, name: str, line: TextLine | None = None) -> bytes | None:
    """The embedded font program used by a span, if it can be extracted.

    A span names its font by family + style ("Calibri", "Calibri-Bold"), the PDF by its
    BaseFont, which for a font PlanetPDF put on the page reads "Calibri Regular": the
    regular style matches with or without "Regular" / "Roman" / "Book" (without this a
    line moved a second time fell back to Helvetica and came out wider: user bug).

    Several fonts can share one name: subsets of a variable font cut at different
    weights (user bug 2026-10-02, "BE Filing Acknowledgement": five
    "BettoniVF-Regular-Dark" fonts; the first one was a light weight and a moved bold
    heading came out thin). With ``line``, the one whose glyph widths match how the
    line's characters are actually spaced is used."""
    found = _font_candidates(doc, page, name)
    if len(found) <= 1 or line is None:
        return found[0] if found else None
    chars = _line_chars(page, line)
    if len(chars) < 2:
        return found[0]
    along = [_along(at, line.direction) for _c, at in chars]

    def misfit(buffer: bytes) -> float:
        try:
            font = pymupdf.Font(fontbuffer=buffer)
        except (RuntimeError, ValueError):
            return float("inf")
        if not all(font.has_glyph(ord(c)) for c, _at in chars if not c.isspace()):
            return float("inf")
        return sum(abs(font.glyph_advance(ord(c)) * line.size - (b - a))
                   for (c, _at), a, b in zip(chars, along, along[1:]) if not c.isspace())

    return min(found, key=misfit)


def choose_font(doc: pymupdf.Document, page: pymupdf.Page, line: TextLine, text: str) -> tuple[str, bytes | None]:
    """(fontname, fontbuffer): the original font when it has every glyph, else a
    standard font of the same family and style (Latin text), else bundled Noto Sans."""
    buffer = _embedded_font(doc, page, line.font, line)
    if buffer:
        try:
            font = pymupdf.Font(fontbuffer=buffer)
            if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                # (named by its contents: same-named fonts of different weights don't mix)
                return f"PPDF{zlib.crc32(buffer):08x}", buffer
        except (RuntimeError, ValueError):
            pass
    if all(ord(c) < 256 for c in text):
        return _BASE14[(line.family, line.bold, line.italic)], None
    from planetpdf.core.resources import font_path

    return "PPDFNoto", font_path("sans").read_bytes()


def font_change(doc: pymupdf.Document, line: TextLine, text: str) -> str | None:
    """None when rewriting ``line`` as ``text`` keeps its own font, else the readable
    name of the font it would be drawn in instead (for a warning first: Check Spelling)."""
    fontname, buffer = choose_font(doc, doc[line.page], line, text)
    if fontname.startswith("PPDF") and fontname != "PPDFNoto":
        return None
    if fontname == "PPDFNoto":
        return "Noto Sans"
    family = {"sans": "Helvetica", "serif": "Times", "mono": "Courier"}[line.family]
    style = " ".join(s for s, on in (("Bold", line.bold), ("Italic", line.italic)) if on)
    return f"{family} {style}".strip()


def _insert(page: pymupdf.Page, point, text: str, fontname: str, buffer: bytes | None, size: float, color, rotate: int,
            *, direction: tuple[float, float] = (1.0, 0.0), runs: list[tuple[str, tuple]] | None = None) -> None:
    """Write ``text`` at ``point`` (the baseline start, unrotated coordinates).

    Fonts embedded by Word / Print to PDF are often subsets without a space character;
    writing a space with them produces a no-break space (U+00A0), so the line no longer
    matches a search and copies oddly. With such a font the words are written one by one
    at their positions instead (``runs``: given positions, e.g. the line's original ones)."""
    if buffer is not None:
        xref = page.insert_font(fontname=fontname, fontbuffer=buffer)
        _prefer_plain_unicode(page.parent, xref, buffer)
        if runs is not None:  # exact word positions (a moved line, or one put back)
            for word, at in runs:
                page.insert_text(pymupdf.Point(at), word, fontname=fontname, fontsize=size, color=color, rotate=rotate)
            return
        if " " in text:
            font = pymupdf.Font(fontbuffer=buffer)
            space = font.has_glyph(32)  # (glyph id; 0 = none)
            # none, or the very glyph of the no-break space: the space reads back as U+00A0
            if not space or space == font.has_glyph(0xA0):
                for word, at in runs or _word_runs(font, text, size, point, direction):
                    page.insert_text(pymupdf.Point(at), word, fontname=fontname, fontsize=size, color=color, rotate=rotate)
                return
    page.insert_text(pymupdf.Point(point), text, fontname=fontname, fontsize=size, color=color, rotate=rotate)


def _prefer_plain_unicode(doc: pymupdf.Document, font_xref: int, buffer: bytes) -> None:
    """Rewrite an inserted font's ToUnicode map so a glyph shared by two characters
    reads back as the plain one: subset fonts from Word use one glyph for space and
    no-break space, for hyphen and U+2010, …, and PyMuPDF's map picks the unusual one
    (copied / searched text then has odd spaces and hyphens)."""
    kind, value = doc.xref_get_key(font_xref, "ToUnicode")
    if kind != "xref":
        return
    try:
        font = pymupdf.Font(fontbuffer=buffer)
        codepoints = font.valid_codepoints()
    except (RuntimeError, ValueError):
        return
    best: dict[int, int] = {}
    for cp in codepoints:
        glyph = font.has_glyph(cp)
        if not glyph or cp > 0xFFFF:
            continue
        old = best.get(glyph)
        # ASCII first, then the lowest code point
        if old is None or (cp < 0x80, -cp) > (old < 0x80, -old):
            best[glyph] = cp
    if not best:
        return
    entries = [f"<{glyph:04x}> <{cp:04x}>" for glyph, cp in sorted(best.items()) if glyph <= 0xFFFF]
    chunks = [entries[i : i + 100] for i in range(0, len(entries), 100)]
    body = "".join(f"{len(c)} beginbfchar\n" + "\n".join(c) + "\nendbfchar\n" for c in chunks)
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo <</Registry(Adobe)/Ordering(UCS)/Supplement 0>> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{body}endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    )
    doc.update_stream(int(value.split()[0]), cmap.encode("ascii"))


def _word_runs(font: pymupdf.Font, text: str, size: float, point, direction) -> list[tuple[str, tuple]]:
    """Each word of ``text`` and where it starts, laid out with the font's advances."""
    space = font.glyph_advance(0xA0) * size if font.has_glyph(0xA0) else 0.25 * size
    dx, dy = direction
    runs, pos = [], 0.0
    for token in re.split(r"( +)", text):
        if not token:
            continue
        if token.startswith(" "):
            pos += len(token) * space
            continue
        runs.append((token, (point[0] + pos * dx, point[1] + pos * dy)))
        pos += font.text_length(token, size)
    return runs


def _line_chars(page: pymupdf.Page, line: TextLine) -> list[tuple[str, tuple]]:
    """The characters of a line (in its font and size, inside its box) with their
    origins, in writing order. Other text overlapping the box is left out."""
    box = pymupdf.Rect(line.rect) + (-0.5, -0.5, 0.5, 0.5)
    chars = []
    for block in page_text(page, "rawdict", clip=box)["blocks"]:
        for raw in block.get("lines", []):
            for span in raw["spans"]:
                if span["font"] != line.font or abs(span["size"] - line.size) > 0.2:
                    continue
                for ch in span["chars"]:
                    r = pymupdf.Rect(ch["bbox"])
                    if box.contains(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)):
                        chars.append((ch["c"], tuple(ch["origin"])))
    chars.sort(key=lambda c: _along(c[1], line.direction))
    return chars


def _runs_of(chars: list[tuple[str, tuple]], keep=lambda c, at: True) -> list[tuple[str, tuple]]:
    """Consecutive (kept, non-space) characters joined into words with their start points."""
    runs: list[tuple[str, tuple]] = []
    word, start = "", None
    for c, at in chars:
        if c.isspace() or not keep(c, at):
            if word:
                runs.append((word, start))
            word, start = "", None
            continue
        if not word:
            start = at
        word += c
    if word:
        runs.append((word, start))
    return runs


def _line_runs(page: pymupdf.Page, line: TextLine) -> list[tuple[str, tuple]]:
    """The words of a line of page text with their original start points (unrotated)."""
    return _runs_of(_line_chars(page, line))


def _rotation_of(direction: tuple[float, float]) -> int:
    angle = math.degrees(math.atan2(-direction[1], direction[0]))
    return int(round(angle / 90.0)) * 90 % 360


# --- letters exactly as drawn ---------------------------------------------------------------------------
#
# Moving text works letter by letter (user 2026-10-03: "re-render the box when it is
# moved and leave the rest of the page as is"): every letter in the area is noted first
# (its place, size, color and the very font program that draws it), the moved text is
# taken off, and any other letter that went with it is put back exactly where it was.
# Lines read back from the page can mix (two lines moved onto each other read as one), so
# nothing here depends on lines being "whole".


@dataclass(frozen=True)
class _Glyph:
    c: str
    at: tuple[float, float]  # baseline origin (unrotated page coordinates)
    size: float
    color: tuple[float, float, float]
    font: str
    flags: int
    direction: tuple[float, float]
    invisible: bool  # (an OCR layer's text: put back invisible)

    @property
    def key(self) -> tuple:
        return self.c, round(self.at[0], 1), round(self.at[1], 1)


def _page_glyphs(page: pymupdf.Page, clip: pymupdf.Rect | None = None) -> list[_Glyph]:
    out = []
    for block in page_text(page, "rawdict", clip=clip)["blocks"]:
        for raw in block.get("lines", []):
            direction = tuple(raw["dir"])
            for span in raw["spans"]:
                invisible = span.get("alpha", 255) == 0
                for ch in span["chars"]:
                    if ch["c"].strip():
                        out.append(_Glyph(ch["c"], tuple(ch["origin"]), span["size"], _rgb(span["color"]), span["font"],
                                          span["flags"], direction, invisible))
    return out


def _line_glyphs(page: pymupdf.Page, line: TextLine) -> list[_Glyph]:
    """The letters of one editable line (its font and size, inside its box)."""
    box = pymupdf.Rect(line.rect) + (-0.5, -0.5, 0.5, 0.5)
    ux, uy = line.direction
    out = []
    for g in _page_glyphs(page, box):
        if g.font != line.font or abs(g.size - line.size) > 0.2 or g.invisible:
            continue
        off = (g.at[0] - line.origin[0]) * uy - (g.at[1] - line.origin[1]) * ux  # (off the baseline)
        if abs(off) > 0.5:
            continue
        out.append(g)
    return sorted(out, key=lambda g: _along(g.at, line.direction))


def _moving_glyphs(doc: pymupdf.Document, line: TextLine):
    """The letters of a line about to move, with their fonts; None when they don't read
    back as the line's text (then the line is laid out from its font instead)."""
    page = doc[line.page]
    glyphs = _line_glyphs(page, line)
    if "".join(g.c for g in glyphs) != "".join(line.text.split()):
        return None
    return glyphs, _glyph_fonts(doc, page, glyphs)


def _base14_for(font: str, flags: int) -> str:
    probe = TextLine(0, (0, 0, 0, 0), "", font, 0, (0, 0, 0), flags, (0, 0), (1, 0))
    return _BASE14[(probe.family, probe.bold, probe.italic)]


def _glyph_fonts(doc: pymupdf.Document, page: pymupdf.Page, glyphs: list[_Glyph]) -> dict[int, tuple[str, bytes | None]]:
    """For each letter (by index): the font to draw it with, (resource name, program or
    None for a standard font). Its own embedded font; when one font name is several
    partial copies, the copy that really draws the letter, of the weight that matches the
    letters' spacing; a standard font only when nothing embedded can draw it."""
    out: dict[int, tuple[str, bytes | None]] = {}
    groups: dict[tuple[str, float], list[int]] = {}
    for n, g in enumerate(glyphs):
        groups.setdefault((g.font, round(g.size, 1)), []).append(n)
    for (name, size), members in groups.items():
        candidates = _font_candidates(doc, page, name)
        fonts = {}
        for b in candidates:
            try:
                fonts[zlib.crc32(b)] = (b, pymupdf.Font(fontbuffer=b))
            except (RuntimeError, ValueError):
                continue
        fit: dict[int, float] = {}
        if len(fonts) > 1:  # (which weight: the copy whose widths match the spacing)
            for key, (b, font) in fonts.items():
                errors = []
                for a, z in zip(members, members[1:]):
                    ga, gz = glyphs[a], glyphs[z]
                    if abs(ga.at[1] - gz.at[1]) > 0.5 or not _inked(b, font, ga.c):
                        continue
                    gap = _along(gz.at, ga.direction) - _along(ga.at, ga.direction)
                    if 0 < gap < size * 2:  # (neighbors on one baseline, no space between)
                        errors.append(abs(font.glyph_advance(ord(ga.c)) * size - gap))
                fit[key] = sum(errors) / len(errors) if errors else float("inf")
        for n in members:
            g = glyphs[n]
            drawing = [k for k, (b, font) in fonts.items() if font.has_glyph(ord(g.c))
                       and (len(fonts) == 1 or _inked(b, font, g.c))]
            if drawing:
                best = min(drawing, key=lambda k: fit.get(k, 0.0))
                out[n] = (f"PPDF{best:08x}", fonts[best][0])
            elif ord(g.c) < 256:
                out[n] = (_base14_for(g.font, g.flags), None)
            else:
                from planetpdf.core.resources import font_path

                out[n] = ("PPDFNoto", font_path("sans").read_bytes())
    return out


def _put_glyphs(page: pymupdf.Page, glyphs: list[_Glyph], fonts: dict[int, tuple[str, bytes | None]],
                dx: float = 0.0, dy: float = 0.0, indices: list[int] | None = None) -> None:
    """Draw letters back (moved by dx, dy), each exactly at its own place: neighbors in
    one font that sit where the font's widths lead are drawn as one run."""
    order = indices if indices is not None else list(range(len(glyphs)))
    added: set[str] = set()
    measure: dict[str, pymupdf.Font] = {}
    run: list[int] = []

    def advance(n: int) -> float:
        name, buffer = fonts[n]
        if name not in measure:
            measure[name] = pymupdf.Font(fontbuffer=buffer) if buffer else pymupdf.Font(name)
        return measure[name].glyph_advance(ord(glyphs[n].c)) * glyphs[n].size

    def flush() -> None:
        if not run:
            return
        first = glyphs[run[0]]
        name, buffer = fonts[run[0]]
        if buffer is not None and name not in added:
            xref = page.insert_font(fontname=name, fontbuffer=buffer)
            _prefer_plain_unicode(page.parent, xref, buffer)
            added.add(name)
        page.insert_text(pymupdf.Point(first.at[0] + dx, first.at[1] + dy), "".join(glyphs[n].c for n in run),
                         fontname=name, fontsize=first.size, color=first.color, rotate=_rotation_of(first.direction),
                         render_mode=3 if first.invisible else 0)
        run.clear()

    for n in order:
        g = glyphs[n]
        if run:
            p = glyphs[run[-1]]
            step = advance(run[-1])
            expected = (p.at[0] + step * p.direction[0], p.at[1] + step * p.direction[1])
            same = (fonts[n][0] == fonts[run[-1]][0] and abs(g.size - p.size) < 0.01 and g.color == p.color
                    and g.direction == p.direction and g.invisible == p.invisible)
            if not same or abs(g.at[0] - expected[0]) > 0.4 or abs(g.at[1] - expected[1]) > 0.4:
                flush()
        run.append(n)
    flush()


# --- the internal, text-only redaction ------------------------------------------------------------------


def _remove_lines(session: PdfSession, index: int, lines: list[TextLine]) -> None:
    """Remove these lines of text from a page, and nothing else.

    The text-only redaction takes every letter inside a line's box, including other text
    drawn there (a line moved on top of another, a neighbor's ascenders). Every letter in
    the area is noted first, and any that went missing without being one of these lines'
    is put back exactly where it was, drawn by the same font program (user bugs
    2026-10-01 "it dragged the text it had landed on and it disappeared", 2026-10-03 "the
    display got more and more wonky the more I moved boxes around")."""
    doc = session.doc
    page = doc[index]

    def box_of(line: TextLine) -> pymupdf.Rect:
        r = pymupdf.Rect(line.rect)
        inset = r.height * 0.2  # (neighboring lines' boxes touch this one's)
        return pymupdf.Rect(r.x0, r.y0 + inset, r.x1, r.y1 - inset)

    boxes = [box_of(line) for line in lines]
    removing = {g.key for line in lines for g in _line_glyphs(page, line)}
    area = pymupdf.Rect(boxes[0])
    for box in boxes[1:]:
        area |= box
    margin = max((line.size for line in lines), default=12.0) * 1.5
    area = area + (-margin, -margin, margin, margin)
    before = _page_glyphs(page, area)
    fonts = _glyph_fonts(doc, page, before)
    pending = M.list_marks(session, [index])
    if pending:
        M.remove_marks(session, [m.mark_id for m in pending])
    page = doc[index]
    for box in boxes:
        page.add_redact_annot(box)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    if pending:
        M.add_marks(session, [m.spec for m in pending])
    page = doc[index]
    still = {g.key for g in _page_glyphs(page, area)}
    lost = [n for n, g in enumerate(before) if g.key not in still and g.key not in removing]
    if lost:
        _put_glyphs(page, before, fonts, indices=lost)


# --- text edits -----------------------------------------------------------------------------------------


def replace_line(session: PdfSession, line: TextLine, new_text: str) -> None:
    if "\n" in new_text:
        raise PdfError("Replace one line at a time (use Add Text for more lines).")
    doc = session.doc
    page = doc[line.page]
    if " ".join(new_text.split()) == " ".join(line.text.split()):
        return  # (nothing changed: the line stays exactly as it is)
    fontname, buffer = choose_font(doc, page, line, new_text)
    font_runs = _layout_font_runs(doc, page, line, new_text)  # (a font split into partial copies)
    _remove_lines(session, line.page, [line])
    if font_runs:
        _insert_font_runs(doc[line.page], font_runs, 0, 0, line.size, line.color, _rotation_of(line.direction))
        session.mark_modified()
        return
    if new_text.strip():
        page = doc[line.page]
        _insert(page, line.origin, new_text, fontname, buffer, line.size, line.color, _rotation_of(line.direction),
                direction=line.direction)
    session.mark_modified()


# --- lines in several fonts (user 2026-10-23) ------------------------------------------------------------
#
# A line of page text is cut into runs of one font, size and color (``text_lines``): a
# sentence with a bold word is three runs. To edit such a line as one, ``line_group`` finds
# the runs that continue each other on its baseline and ``replace_runs`` writes the edited
# text back piece by piece, each piece in the font, size and color of the run it came from.


def _across(point, direction) -> float:
    return -point[0] * direction[1] + point[1] * direction[0]


def _end_along(line: TextLine) -> float:
    r = line.rect
    return max(_along(c, line.direction) for c in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])))


def _continues(a: TextLine, b: TextLine) -> bool:
    """``b`` goes on from ``a`` on the same line (at most a wide word space between them)."""
    size = max(a.size, b.size)
    if max(a.size, b.size) > 1.3 * min(a.size, b.size):
        return False
    gap = _along(b.origin, a.direction) - _end_along(a)
    return -0.25 * size <= gap <= 1.2 * size


def line_group(lines: list[TextLine], line: TextLine) -> list[TextLine]:
    """The runs of ``line``'s whole line, in writing order (just ``[line]`` when it's alone)."""
    row = sorted((l for l in lines if l.page == line.page and l.direction == line.direction
                  and abs(_across(l.origin, line.direction) - _across(line.origin, line.direction))
                  <= 0.2 * max(l.size, line.size)), key=lambda l: _along(l.origin, line.direction))
    if line not in row:
        return [line]
    at = row.index(line)
    start, end = at, at
    while start > 0 and _continues(row[start - 1], row[start]):
        start -= 1
    while end + 1 < len(row) and _continues(row[end], row[end + 1]):
        end += 1
    return row[start:end + 1]


def group_text(group: list[TextLine]) -> list[tuple[str, int]]:
    """The line's text as (piece, run index): a space between runs belongs to the run before it."""
    pieces = []
    for n, run in enumerate(group):
        text = run.text
        if n + 1 < len(group):
            after = group[n + 1]
            if run.trail_space or after.lead_space \
                    or _along(after.origin, run.direction) - _end_along(run) > 0.15 * run.size:
                text += " "
        pieces.append((text, n))
    return pieces


def can_edit_together(session: PdfSession, group: list[TextLine]) -> bool:
    """Not a font cut into partial copies (those are put back letter by letter, one run at a time)."""
    page = session.doc[group[0].page]
    return len(group) > 1 and all(len(_font_candidates(session.doc, page, run.font)) <= 1 for run in group)


def _advance(font: pymupdf.Font, text: str, size: float) -> float:
    """How far ``text`` takes the pen, written the way ``_insert`` writes it."""
    space = font.has_glyph(32)
    if " " in text and (not space or space == font.has_glyph(0xA0)):
        words = _word_runs(font, text, size, (0.0, 0.0), (1.0, 0.0))
        tail = len(text) - len(text.rstrip(" "))
        step = font.glyph_advance(0xA0) * size if font.has_glyph(0xA0) else 0.25 * size
        return (words[-1][1][0] + font.text_length(words[-1][0], size) if words else 0.0) + tail * step
    return font.text_length(text, size)


def replace_runs(session: PdfSession, group: list[TextLine], pieces: list[tuple[str, int]]) -> None:
    """Rewrite a line of several runs (``line_group``) as ``pieces``: (text, index of the run
    whose font, size and color it's written in), laid out from the line's start."""
    new_text = "".join(text for text, _n in pieces)
    if "\n" in new_text:
        raise PdfError("Replace one line at a time (use Add Text for more lines).")
    if len(group) == 1:
        replace_line(session, group[0], new_text)
        return
    if " ".join(new_text.split()) == " ".join("".join(t for t, _n in group_text(group)).split()) \
            and [n for t, n in pieces if t.strip()] == [n for t, n in group_text(group) if t.strip()]:
        return  # (nothing changed: the line stays exactly as it is)
    doc = session.doc
    index = group[0].page
    page = doc[index]
    merged: list[list] = []
    for text, n in pieces:
        if merged and merged[-1][1] == n:
            merged[-1][0] += text
        elif text:
            merged.append([text, n])
    prepared = []
    for text, n in merged:  # (fonts read before the line is removed)
        run = group[n]
        fontname, buffer = choose_font(doc, page, run, text)
        font = pymupdf.Font(fontbuffer=buffer) if buffer is not None else pymupdf.Font(fontname)
        prepared.append((text, run, fontname, buffer, font))
    _remove_lines(session, index, group)
    first = group[0]
    d = first.direction
    pen = _along(first.origin, d)
    rotate = _rotation_of(d)
    page = doc[index]
    for text, run, fontname, buffer, font in prepared:
        across = _across(run.origin, d)  # (a run raised or lowered a little keeps that)
        point = (pen * d[0] - across * d[1], pen * d[1] + across * d[0])
        if text.strip():
            _insert(page, point, text, fontname, buffer, run.size, run.color, rotate, direction=d)
        pen += _advance(font, text, run.size)
    session.mark_modified()


def move_line(session: PdfSession, line: TextLine, dx: float, dy: float, target_page: int | None = None) -> None:
    """Move a line of page text by (dx, dy) in unrotated page coordinates, optionally onto
    another page (same place there). Same text, font, size and color; the font is read
    from the source page before the line is removed."""
    doc = session.doc
    page = doc[line.page]
    fontname, buffer = choose_font(doc, page, line, line.text)
    target = line.page if target_page is None else target_page
    if not 0 <= target < doc.page_count:
        raise PdfError("Page out of range.")
    # the words keep their exact spacing (justified lines too)
    runs = [(word, (x + dx, y + dy)) for word, (x, y) in _line_runs(page, line)]
    if " ".join(w for w, _ in runs) != " ".join(line.text.split()):
        runs = None  # (couldn't read the words back cleanly: lay them out from the font)
    font_runs = _font_runs(doc, page, line)  # (a font split into partial copies: letter by letter)
    letters = _moving_glyphs(doc, line)  # (each letter as drawn, by the font copy that draws it)
    _remove_lines(session, line.page, [line])
    if letters:
        _put_glyphs(doc[target], letters[0], letters[1], dx, dy)
        session.mark_modified()
        return
    if font_runs:
        _insert_font_runs(doc[target], font_runs, dx, dy, line.size, line.color, _rotation_of(line.direction))
        session.mark_modified()
        return
    origin = (line.origin[0] + dx, line.origin[1] + dy)
    _insert(doc[target], origin, line.text, fontname, buffer, line.size, line.color, _rotation_of(line.direction),
            direction=line.direction, runs=runs)
    session.mark_modified()


def move_group(session: PdfSession, lines: list[tuple], images: list[tuple], drawings: list[tuple] = (),
               links: list[tuple] = ()) -> None:
    """Move several lines of text and images together, each by its (dx, dy) (unrotated):
    entries ``(line, dx, dy)`` / ``(image, dx, dy)``, or with a 4th item, the page to put
    it on (the offset then is in that page's coordinates: dropped on another page).

    Everything is lifted off first and put down afterwards: moving a paragraph down one
    line one at a time would otherwise erase each moved line again when the next line's
    old place is cleared.

    ``drawings``: ``(PageDrawing, dx, dy[, page])`` shapes; ``links``: ``(page, link, dx,
    dy, target)`` link areas (`core.drawings`)."""
    from planetpdf.core import drawings as DR

    doc = session.doc
    prepared_lines = []
    for line, dx, dy, *to in lines:
        page = doc[line.page]
        fontname, buffer = choose_font(doc, page, line, line.text)
        runs = [(word, (x + dx, y + dy)) for word, (x, y) in _line_runs(page, line)]
        if " ".join(w for w, _ in runs) != " ".join(line.text.split()):
            runs = None
        prepared_lines.append((line, dx, dy, fontname, buffer, runs, _font_runs(doc, page, line),
                               _moving_glyphs(doc, line),
                               to[0] if to else line.page))  # (the target page last: checked below)
    prepared_images = [(image, dx, dy, image_png(session, image), to[0] if to else image.page)
                       for image, dx, dy, *to in images]
    prepared_drawings = [(d, dx, dy, to[0] if to else d.page) for d, dx, dy, *to in drawings]
    for target in [entry[-1] for entry in prepared_lines + prepared_images + prepared_drawings]:
        if not 0 <= target < doc.page_count:
            raise PdfError("Page out of range.")
    # shapes first: removing text rewrites the page, and its shapes may then re-extract differently
    shapes_by_page: dict[int, list] = {}
    for d, *_rest in prepared_drawings:
        shapes_by_page.setdefault(d.page, []).append(d)
    for page_index, page_shapes in shapes_by_page.items():
        DR.remove_drawings(session, page_index, page_shapes)
    by_page: dict[int, list[TextLine]] = {}
    for line, *_rest in prepared_lines:
        by_page.setdefault(line.page, []).append(line)
    for page_index, page_lines in by_page.items():
        _remove_lines(session, page_index, page_lines)
    removed: set[tuple[int, str]] = set()
    for image, *_rest in prepared_images:
        if (image.page, image.name) not in removed:  # (removing one removes every placement of it)
            _remove_image_invocations(session, image)
            removed.add((image.page, image.name))
    for d, dx, dy, target in prepared_drawings:
        DR.redraw(doc[target], d.path, dx, dy)
    for image, dx, dy, png, target in prepared_images:
        r = image.rect
        place_image(session, target, (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy), png)
    for line, dx, dy, fontname, buffer, runs, font_runs, letters, target in prepared_lines:
        if letters:
            _put_glyphs(doc[target], letters[0], letters[1], dx, dy)
            continue
        if font_runs:
            _insert_font_runs(doc[target], font_runs, dx, dy, line.size, line.color, _rotation_of(line.direction))
            continue
        origin = (line.origin[0] + dx, line.origin[1] + dy)
        _insert(doc[target], origin, line.text, fontname, buffer, line.size, line.color,
                _rotation_of(line.direction), direction=line.direction, runs=runs)
    DR.move_links(session, list(links))
    session.mark_modified()


def delete_group(session: PdfSession, lines: list[TextLine] = (), images: list[PageImage] = (),
                 drawings: list = (), links: list[tuple[int, dict]] = ()) -> None:
    """Delete these lines of text, images, shapes and link areas: ``links`` = (page, link)."""
    from planetpdf.core import drawings as DR

    shapes: dict[int, list] = {}
    for d in drawings:
        shapes.setdefault(d.page, []).append(d)
    for page_index, page_shapes in shapes.items():
        DR.remove_drawings(session, page_index, page_shapes)
    by_page: dict[int, list[TextLine]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    for page_index, page_lines in by_page.items():
        _remove_lines(session, page_index, page_lines)
    done: set[tuple[int, str]] = set()
    for image in images:
        if (image.page, image.name) not in done:
            _remove_image_invocations(session, image)
            done.add((image.page, image.name))
    for page_index, link in links:
        DR.delete_links(session, page_index, [link])
    session.mark_modified()


def delete_line(session: PdfSession, line: TextLine) -> None:
    _remove_lines(session, line.page, [line])
    session.mark_modified()


def add_text(session: PdfSession, index: int, point: tuple[float, float], text: str, *, size: float = 12.0,
             color=(0.0, 0.0, 0.0), family: str = "sans", bold: bool = False) -> None:
    """New text as page content; ``point`` = top-left in displayed coordinates."""
    page = session.doc[index]
    lines = text.split("\n")
    if all(ord(c) < 256 for c in text):
        fontname, buffer = _BASE14[(family, bold, False)], None
    else:
        from planetpdf.core.resources import font_path

        fontname, buffer = "PPDFNoto", font_path("sans").read_bytes()
    for n, line in enumerate(lines):
        if not line:
            continue
        baseline = pymupdf.Point(point[0], point[1] + size * (0.9 + n * 1.2)) * page.derotation_matrix
        _insert(page, baseline, line, fontname, buffer, size, color, page.rotation)
    session.mark_modified()


# --- images ---------------------------------------------------------------------------------------------


def page_images(session: PdfSession, index: int) -> list[PageImage]:
    page = session.doc[index]
    names = {item[0]: item[7] for item in page.get_images(full=True)}
    out = []
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref") or 0
        if xref and xref in names:
            out.append(PageImage(index, xref, names[xref], tuple(info["bbox"])))
    return out


def image_png(session: PdfSession, image: PageImage) -> bytes:
    """The image (with its transparency) as PNG, e.g. to re-place it."""
    doc = session.doc
    pix = pymupdf.Pixmap(doc, image.xref)
    info = doc.extract_image(image.xref)
    smask = info.get("smask") if info else 0
    if smask:
        try:
            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
        except (RuntimeError, ValueError):
            pass
    if pix.n - pix.alpha > 3:  # CMYK -> RGB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("png")


def _remove_image_invocations(session: PdfSession, image: PageImage) -> None:
    doc = session.doc
    page = doc[image.page]
    data = page.read_contents()
    tokens = list(tokenize(data))
    cut = []
    target = b"/" + image.name.encode("latin-1")
    for i, tok in enumerate(tokens):
        if tok.kind == "op" and tok.value == b"Do" and i and tokens[i - 1].value == target:
            cut.append((tokens[i - 1].start, tok.end))
    if not cut:
        raise PdfError("That image is drawn in a way that can't be edited here.")
    out, pos = bytearray(), 0
    for start, end in cut:
        out += data[pos:start] + b" "
        pos = end
    out += data[pos:]
    replace_contents(doc, image.page, bytes(out))  # (never in place: streams can be shared)


def delete_image(session: PdfSession, image: PageImage) -> None:
    _remove_image_invocations(session, image)
    session.mark_modified()


def place_image(session: PdfSession, index: int, rect: RectTuple, png: bytes) -> None:
    """Insert an image at ``rect`` (unrotated), upright to the reader."""
    page = session.doc[index]
    page.insert_image(pymupdf.Rect(rect), stream=png, rotate=page.rotation, keep_proportion=False)
    session.mark_modified()


def move_image(session: PdfSession, image: PageImage, new_rect: RectTuple) -> None:
    png = image_png(session, image)
    _remove_image_invocations(session, image)
    place_image(session, image.page, new_rect, png)


def replace_image(session: PdfSession, image: PageImage, png: bytes) -> None:
    """Swap the picture, fitting the new one inside the old one's box."""
    pix = pymupdf.Pixmap(png)
    box = pymupdf.Rect(image.rect)
    scale = min(box.width / pix.width, box.height / pix.height)
    w, h = pix.width * scale, pix.height * scale
    fitted = (box.x0 + (box.width - w) / 2, box.y0 + (box.height - h) / 2, box.x0 + (box.width + w) / 2, box.y0 + (box.height + h) / 2)
    _remove_image_invocations(session, image)
    place_image(session, image.page, fitted, png)


# --- Edit PDF panel: format and objects (Acrobat's right-hand panel) ---------------------------------


def line_font(line: TextLine) -> str:
    """The `core.fonts` family a line of page text is in, as the FORMAT box shows it:
    "ABCDEF+Calibri-BoldItalic" -> "Calibri"; the standard fonts by their family; a font
    that is only embedded in the document by its own name ("BettoniVF", as
    ``fonts.document_fonts`` lists it)."""
    from planetpdf.core import fonts

    found = fonts.known_family(line.font)
    if found:
        return found
    words = fonts.family_name(line.font)
    if words.lower().startswith("helvetica"):
        return "Helvetica"
    if words.lower().startswith("arial"):
        return fonts.known_family("Arial") or "Helvetica"
    return words


def restyle_line(session: PdfSession, line: TextLine, *, size: float | None = None, color=None,
                 family: str | None = None, bold: bool | None = None, italic: bool | None = None,
                 font: str | None = None) -> None:
    """Redraw a line of page text with a new size / color / font style, on the same baseline.
    Same family and style keep the original embedded font when it can; a change of family
    or style uses the matching standard font (Noto Sans for non-Latin text). ``font``: any
    family from `core.fonts`, including the document's own (installed fonts are embedded;
    their own bold / italic files; a document font in the weight that has the letters)."""
    doc = session.doc
    page = doc[line.page]
    new_family = family or line.family
    new_bold = line.bold if bold is None else bold
    new_italic = line.italic if italic is None else italic
    from planetpdf.core import fonts

    chosen = None
    current = line_font(line)
    if font is None and (new_bold, new_italic) != (line.bold, line.italic) and (
            current in fonts.installed() or current in fonts.document_fonts(doc)):
        font = current  # (bold / italic of a line in an installed or document font: that font's own weights)
    if font is not None and not fonts.is_standard(font):
        chosen = fonts.face(font, new_bold, new_italic, doc=doc, text=line.text)
        if not chosen.has_glyphs(line.text):  # (a document font lacking letters: its closest font)
            fallback = chosen.fallback if chosen.in_document else "Noto Sans"
            chosen = fonts.face(fallback, new_bold, new_italic)
            if chosen.buffer is None and not chosen.has_glyphs(line.text):
                chosen = fonts.face("Noto Sans", new_bold, new_italic)
    elif font is not None:
        new_family = {v: k for k, v in fonts.FAMILY_OF_KEY.items()}[font]
    if chosen is not None:
        buffer = chosen.buffer
        if buffer is not None:
            buffer = fonts.subset(buffer, line.text)
        fontname = f"PPDF{zlib.crc32(buffer):08x}" if buffer is not None else chosen.fontname
    elif (new_family, new_bold, new_italic) == (line.family, line.bold, line.italic):
        fontname, buffer = choose_font(doc, page, line, line.text)
    elif all(ord(c) < 256 for c in line.text):
        fontname, buffer = _BASE14[(new_family, new_bold, new_italic)], None
    else:
        from planetpdf.core.resources import font_path

        fontname, buffer = "PPDFNoto", font_path("sans").read_bytes()
    _remove_lines(session, line.page, [line])
    page = doc[line.page]
    _insert(page, line.origin, line.text, fontname, buffer, size or line.size, color or line.color,
            _rotation_of(line.direction), direction=line.direction)
    session.mark_modified()


def transform_image(session: PdfSession, image: PageImage, operation: str) -> None:
    """Edit PDF ▸ Objects: "rotate_cw", "rotate_ccw", "flip_h" (left-right) or "flip_v"
    (top-bottom), as the reader sees the page. Rotating keeps the picture's center."""
    import io

    from PIL import Image

    picture = Image.open(io.BytesIO(image_png(session, image)))
    ops = {
        "rotate_cw": Image.Transpose.ROTATE_270, "rotate_ccw": Image.Transpose.ROTATE_90,
        "flip_h": Image.Transpose.FLIP_LEFT_RIGHT, "flip_v": Image.Transpose.FLIP_TOP_BOTTOM,
    }
    if operation not in ops:
        raise ValueError(operation)
    page = session.doc[image.page]
    if operation.startswith("flip") and page.rotation % 180:
        # the stored picture lies sideways to the reader: flip along the other axis
        operation = "flip_v" if operation == "flip_h" else "flip_h"
    out = io.BytesIO()
    picture.transpose(ops[operation]).save(out, format="PNG")
    box = pymupdf.Rect(image.rect)
    if operation.startswith("rotate"):
        c = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        box = pymupdf.Rect(c[0] - box.height / 2, c[1] - box.width / 2, c[0] + box.height / 2, c[1] + box.width / 2)
    _remove_image_invocations(session, image)
    place_image(session, image.page, tuple(box), out.getvalue())