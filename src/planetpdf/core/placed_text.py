"""Placed text (Acrobat's Fill & Sign "Add Text"): typed text that stays an item you can
move, resize and change the size of until the page is flattened.

It is an appearance item like a stamp or signature: the text is drawn into a small
upright source PDF, which becomes the annotation's appearance (and, on flatten, page
content: pixel-identical). The layout matches the on-page editor it was typed in
(Helvetica/Arial metrics, same padding and line spacing), so it lands where it was typed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from planetpdf.core.appearance import finish_pdf

PADDING = 2.0  # = inline editor's document margin
ASCENT = 0.905  # Helvetica/Arial ascent (em), where the editor draws the first baseline
LEADING = 1.15  # Arial line spacing (em)
MIN_SIZE, MAX_SIZE = 4.0, 144.0
_NOTO = "PPDFNoto"


UNDERLINE = 0.12  # em below the baseline
STRIKEOUT = 0.28  # em above the baseline (about half the x-height)
RULE = 0.06  # em: underline / strike-out thickness
SLANT = 0.2  # synthetic italic: sideways per unit of height
BOLD_STROKE = 0.04  # synthetic bold outline (PyMuPDF: a fraction of the size)


def face_for(text: str, font: str = "Helvetica", bold: bool = False, italic: bool = False, doc=None):
    """The font face text is drawn in (``core.fonts``): the chosen one, or Noto Sans when
    it can't draw every letter (the standard fonts only have Latin-1). A font only in the
    document (``doc``) stays chosen: the letters it lacks are drawn in its closest font
    (``_runs``)."""
    from planetpdf.core import fonts

    chosen = fonts.face(font, bold, italic, doc=doc, text=text)
    if chosen.has_glyphs(text) or chosen.in_document:
        return chosen
    return fonts.face("Noto Sans", bold, italic)


def _runs(line: str, face) -> list[tuple[str, object]]:
    """A line as (text, face) pieces: a document font's missing letters in its fallback."""
    if not face.in_document or face.has_glyphs(line):
        return [(line, face)]
    from planetpdf.core import fonts

    other = fonts.face(face.fallback, face.fake_bold, face.fake_italic)
    if not other.has_glyphs(line):
        other = fonts.face("Noto Sans", face.fake_bold, face.fake_italic)
    runs: list[tuple[str, object]] = []
    for c in line:
        use = face if c.isspace() or face.has_glyphs(c) else other
        if runs and runs[-1][1] is use:
            runs[-1] = (runs[-1][0] + c, use)
        else:
            runs.append((c, use))
    return runs


def _line_width(line: str, face, size: float) -> float:
    return sum(f.width(part, size) for part, f in _runs(line, face))


# --- formatting runs (user 2026-10-03: bold or italic on single words) ----------------------------------
#
# A text has one format (font, size, color, bold, italic, underline, strike-out: its Style)
# plus *runs*: (start, end, bold, italic, underline, strikeout) for characters [start, end)
# that differ from it. Characters outside every run have the text's own format.

Format = tuple[bool, bool, bool, bool]  # bold, italic, underline, strikeout
STYLE_KEYS = ("bold", "italic", "underline", "strikeout")


def char_formats(text: str, base: Format, runs=()) -> list[Format]:
    formats = [tuple(base)] * len(text)
    for start, end, *fmt in runs:
        for i in range(max(0, int(start)), min(len(text), int(end))):
            formats[i] = tuple(bool(v) for v in fmt)
    return formats


def make_runs(formats: list[Format], base: Format) -> tuple:
    """The fewest runs that give each character its format (none: all like ``base``)."""
    runs: list[list] = []
    for i, fmt in enumerate(formats):
        if tuple(fmt) == tuple(base):
            continue
        if runs and runs[-1][1] == i and tuple(runs[-1][2:]) == tuple(fmt):
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, *fmt])
    return tuple(tuple(r) for r in runs)


def normalize_runs(text: str, old_base: Format, new_base: Format, runs=(), *, changed: tuple[str, ...] = ()) -> tuple:
    """Runs after the text's own format changed from ``old_base`` to ``new_base``. A key
    set for the whole text (``changed``) applies to every character, words included."""
    formats = char_formats(text, old_base, runs)
    keys = [STYLE_KEYS.index(k) for k in changed]
    fixed = []
    for fmt in formats:
        fmt = list(fmt)
        for k in keys:
            fmt[k] = new_base[k]
        fixed.append(tuple(fmt))
    return make_runs(fixed, new_base)


def edit_runs(old_text: str, new_text: str, base: Format, runs=()) -> tuple:
    """Runs kept through a change of the words that didn't come from the editor (e.g. Check
    Spelling): the unchanged start and end keep their formats; new letters take their
    neighbor's."""
    if not runs:
        return ()
    old = char_formats(old_text, base, runs)
    head = 0
    while head < min(len(old_text), len(new_text)) and old_text[head] == new_text[head]:
        head += 1
    tail = 0
    while (tail < min(len(old_text), len(new_text)) - head
           and old_text[len(old_text) - 1 - tail] == new_text[len(new_text) - 1 - tail]):
        tail += 1
    middle = len(new_text) - head - tail
    fill = old[head - 1] if head else (old[len(old_text) - tail] if tail else tuple(base))
    return make_runs(old[:head] + [fill] * middle + old[len(old_text) - tail:], base)


@dataclass
class _Piece:
    text: str
    face: object
    underline: bool
    strikeout: bool
    x: float = 0.0


@dataclass
class _Line:
    pieces: list
    width: float


def _layout(text: str, font_size: float, font: str, bold: bool, italic: bool, underline: bool, strikeout: bool,
            runs, doc, wrap_width: float | None):
    """Lines of pieces (one font / underline / strike-out each, x from the line start) and
    the face that sets the line spacing. ``wrap_width``: break lines at spaces to fit."""
    base = (bold, italic, underline, strikeout)
    formats = char_formats(text, base, runs)
    faces: dict[tuple[bool, bool], object] = {}

    def face(b: bool, i: bool):
        if (b, i) not in faces:
            faces[(b, i)] = face_for(text, font, b, i, doc)
        return faces[(b, i)]

    main = face(bold, italic)
    lines: list[_Line] = []
    start = 0
    for paragraph in text.split("\n"):
        fmts = formats[start:start + len(paragraph)]
        start += len(paragraph) + 1
        # pieces: same format, and words kept apart so lines can break between them
        tokens: list[tuple[str, Format]] = []
        for c, fmt in zip(paragraph, fmts):
            if tokens and tokens[-1][1] == fmt and not (c != " " and tokens[-1][0].endswith(" ")):
                tokens[-1] = (tokens[-1][0] + c, fmt)
            else:
                tokens.append((c, fmt))
        # words = tokens up to a space (a word may change format in the middle)
        words: list[list[tuple[str, Format]]] = []
        for token in tokens:
            if words and not words[-1][-1][0].endswith(" "):
                words[-1].append(token)
            else:
                words.append([token])
        line: list[_Piece] = []
        x = 0.0

        def width_of(parts, trim: bool = False) -> float:
            total = 0.0
            for n, (t, fmt) in enumerate(parts):
                if trim and n == len(parts) - 1:
                    t = t.rstrip(" ")
                total += _line_width(t, face(fmt[0], fmt[1]), font_size)
            return total

        for word in words:
            if wrap_width is not None and line and x + width_of(word, trim=True) > wrap_width:
                lines.append(_Line(line, x))
                line, x = [], 0.0
            for t, fmt in word:
                f = face(fmt[0], fmt[1])
                for part, pf in _runs(t, f):
                    line.append(_Piece(part, pf, fmt[2], fmt[3], x))
                    x += pf.width(part, font_size)
        trailing = 0.0
        if line and line[-1].text.endswith(" "):  # (a line's trailing spaces don't count)
            last = line[-1]
            trailing = last.face.width(last.text, font_size) - last.face.width(last.text.rstrip(" "), font_size)
        lines.append(_Line(line, x - trailing))
    return lines, main


def layout_size(text: str, font_size: float, font: str = "Helvetica", bold: bool = False,
                italic: bool = False, doc=None, *, runs=(), wrap_width: float | None = None) -> tuple[float, float]:
    """(width, height) in points of the text drawn at ``font_size`` (padding included).
    ``wrap_width``: lines break at spaces to fit it (a text box); the width is then that."""
    lines, main = _layout(text, font_size, font, bold, italic, False, False, runs, doc, wrap_width)
    width = wrap_width if wrap_width is not None else max((line.width for line in lines), default=0.0)
    if wrap_width is None and any(p.face.fake_italic for line in lines for p in line.pieces):
        width += font_size * main.ascent * SLANT
    return width + 2 * PADDING, len(lines) * font_size * main.leading + 2 * PADDING


def render(text: str, font_size: float, color=(0.0, 0.0, 0.0), *, font: str = "Helvetica", bold: bool = False,
           italic: bool = False, underline: bool = False, strikeout: bool = False, doc=None, runs=(),
           wrap_width: float | None = None) -> bytes:
    """The source PDF (page = the text's box). ``doc``: the document the text goes in (its
    own fonts can be used); ``runs``: words in another format; ``wrap_width``: a text box."""
    font_size = clamp_size(font_size)
    lines, main = _layout(text, font_size, font, bold, italic, underline, strikeout, runs, doc, wrap_width)
    width, height = layout_size(text, font_size, font, bold, italic, doc, runs=runs, wrap_width=wrap_width)
    out = pymupdf.open()
    page = out.new_page(width=max(width, 1.0), height=max(height, 1.0))
    added: set[str] = set()
    for n, line in enumerate(lines):
        baseline = PADDING + font_size * (main.ascent + n * main.leading)
        for piece in line.pieces:
            f = piece.face
            if not piece.text.strip():
                continue
            if f.buffer is not None and f.fontname not in added:
                page.insert_font(fontname=f.fontname, fontbuffer=f.buffer)
                added.add(f.fontname)
            at = pymupdf.Point(PADDING + piece.x, baseline)
            extra = {}
            if f.fake_bold:
                extra.update(render_mode=2, border_width=BOLD_STROKE, stroke_opacity=1)
            if f.fake_italic:
                extra["morph"] = (at, pymupdf.Matrix(1, 0, SLANT, 1, 0, 0))
            page.insert_text(at, piece.text, fontsize=font_size, fontname=f.fontname, color=color,
                             fill=color if f.fake_bold else None, **extra)
        # underline / strike-out: one rule per stretch of pieces that have it (spaces at a
        # stretch's end left out)
        for wanted, offset in (("underline", UNDERLINE), ("strikeout", -STRIKEOUT)):
            stretch = None
            for k, piece in enumerate(line.pieces + [None]):
                on = piece is not None and getattr(piece, wanted)
                if on:
                    end = piece.x + piece.face.width(piece.text.rstrip(" "), font_size)
                    stretch = (stretch[0] if stretch else piece.x, max(end, stretch[1] if stretch else end))
                    if piece.text.strip() == "" and stretch[0] == piece.x:
                        stretch = None
                if not on and stretch is not None:
                    y = baseline + offset * font_size
                    page.draw_line((PADDING + stretch[0], y), (PADDING + stretch[1], y), color=color,
                                   width=RULE * font_size)
                    stretch = None
    return finish_pdf(out)


def clamp_size(font_size: float) -> float:
    return max(MIN_SIZE, min(MAX_SIZE, float(font_size)))


def size_steps(font_size: float, step: int) -> float:
    """Next larger (step=1) / smaller (step=-1) size, like Acrobat's A+ / A- buttons."""
    sizes = [6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72, 96, 144]
    if step > 0:
        bigger = [s for s in sizes if s > font_size + 0.01]
        return clamp_size(bigger[0] if bigger else font_size)
    smaller = [s for s in sizes if s < font_size - 0.01]
    return clamp_size(smaller[-1] if smaller else font_size)
