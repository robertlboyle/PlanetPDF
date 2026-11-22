"""Acrobat-look stamp artwork (user screenshots 2026-10-01).

* ``tile``: rounded tile with a left-to-right gradient, colored border, condensed bold
  italic text (one large line, the others small).
* ``flag``: the Sign Here arrow flag (point on the left).
* ``check`` / ``cross``: a green check mark / red X.

Gradients are drawn as thin vertical strips trimmed to the outline (PyMuPDF shapes
can't clip), and the border stroke covers the strip edges.
"""

from __future__ import annotations

import math

import pymupdf

from planetpdf.core.appearance import finish_pdf
from planetpdf.core.document import PdfError
from planetpdf.core.resources import font_path

BIG_SIZE = 24.0
SMALL_SIZE = 9.0
FLAG_SIZE = 15.0
PAD_X = 9.0
PAD_Y = 4.0
STRIPS = 80


def _palette(spec):
    """(border, text, fill left, fill right): a named tint, or made from the stamp's color."""
    from planetpdf.core.stamps import PALETTES

    if spec.tint in PALETTES:
        return PALETTES[spec.tint]
    color = tuple(spec.color)
    white = (1.0, 1.0, 1.0)
    return _mix(color, (0, 0, 0), 0.15), color, _mix(color, white, 0.75), _mix(color, white, 0.93)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _alpha(spec) -> float:
    return max(0.05, min(1.0, spec.opacity))


def _gradient(shape, width: float, height: float, extent, left, right, alpha: float) -> None:
    """Fill the outline given by ``extent(x) -> (top, bottom)`` with a horizontal gradient."""
    step = width / STRIPS
    for i in range(STRIPS):
        x0 = i * step
        top, bottom = extent(x0 + step / 2)
        if bottom <= top:
            continue
        # each strip reaches well under the next one, so no antialiased seams show at any zoom
        shape.draw_rect(pymupdf.Rect(x0, top, min(width - 1.0, x0 + 2.5 * step), bottom))
        color = _mix(left, right, i / (STRIPS - 1))
        shape.finish(color=None, fill=color, fill_opacity=alpha, width=0)


def _text(shape, point, text, size, key, color, alpha) -> None:
    shape.insert_text(
        point, text, fontsize=size, fontfile=str(font_path(key)), fontname="Stamp" + key.title().replace("_", ""),
        color=color, fill=color, fill_opacity=alpha, stroke_opacity=alpha,
    )


def _tile(spec, lines: list[str]) -> bytes:
    if not lines:
        raise PdfError("The stamp has no text.")
    border, ink, left, right = _palette(spec)
    big = min(max(spec.big, 0), len(lines) - 1)
    keys = ["stamp" if i == big else "stamp_sub" for i in range(len(lines))]
    sizes = [BIG_SIZE if i == big else SMALL_SIZE for i in range(len(lines))]
    fonts = {k: pymupdf.Font(fontfile=str(font_path(k))) for k in set(keys)}
    # italic glyphs lean past their advance width on the right: allow for it
    widths = [fonts[k].text_length(t, s) + s * 0.12 for t, k, s in zip(lines, keys, sizes)]
    # a big line's box is about its cap height; small lines get a bit of leading
    heights = [s * (0.86 if i == big else 1.2) for i, s in enumerate(sizes)]
    width = max(widths) + 2 * PAD_X
    height = sum(heights) + 2 * PAD_Y + (2.0 if len(lines) > 1 else 0.0)
    radius = min(height, width) * 0.22

    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    alpha = _alpha(spec)
    shape = page.new_shape()
    inset = 1.2  # half the border width

    def extent(x: float):
        top, bottom = inset, height - inset
        edge = min(x - inset, width - inset - x)
        if edge < radius:
            d = radius - max(edge, 0.0)
            cut = radius - math.sqrt(max(radius * radius - d * d, 0.0))
            top, bottom = top + cut, bottom - cut
        return top, bottom

    _gradient(shape, width, height, extent, left, right, alpha)
    shape.draw_rect(page.rect + (inset, inset, -inset, -inset), radius=radius / min(width, height) * 1.0)
    shape.finish(color=border, width=2 * inset, stroke_opacity=alpha)
    y = PAD_Y + (1.0 if len(lines) > 1 else 0.0)
    for i, (t, k, s, w, h) in enumerate(zip(lines, keys, sizes, widths, heights)):
        x = (width - w) / 2
        baseline = y + (h if i == big else s * 0.95)
        _text(shape, (x, baseline), t, s, k, ink, alpha)
        y += h
    shape.commit()
    return finish_pdf(doc)


def _flag(spec, lines: list[str]) -> bytes:
    text = " ".join(lines) or spec.name.upper()
    border, ink, left, right = _palette(spec)
    font = pymupdf.Font(fontfile=str(font_path("stamp")))
    text_w = font.text_length(text, FLAG_SIZE)
    height = FLAG_SIZE * 1.55
    point = height * 0.55  # length of the arrow tip
    width = point + text_w + 2 * PAD_X
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    alpha = _alpha(spec)
    shape = page.new_shape()
    inset = 0.8
    mid = height / 2

    def extent(x: float):
        if x < point:
            half = (mid - inset) * max(x - inset, 0.0) / (point - inset)
            return mid - half, mid + half
        return inset, height - inset

    _gradient(shape, width, height, extent, left, right, alpha)
    outline = [(inset, mid), (point, inset), (width - inset, inset), (width - inset, height - inset), (point, height - inset)]
    shape.draw_polyline(outline + [outline[0]])
    shape.finish(color=border, width=2 * inset, closePath=True, stroke_opacity=alpha)
    baseline = mid + FLAG_SIZE * 0.34
    _text(shape, (point + PAD_X * 0.6, baseline), text, FLAG_SIZE, "stamp", ink, alpha)
    shape.commit()
    return finish_pdf(doc)


def _mark(spec, cross: bool) -> bytes:
    size = 36.0
    doc = pymupdf.open()
    page = doc.new_page(width=size, height=size)
    alpha = _alpha(spec)
    border = _palette(spec)[0]
    color = (0.80, 0.07, 0.07) if cross else (0.20, 0.55, 0.12)
    shape = page.new_shape()
    if cross:
        shape.draw_line((7, 7), (29, 29))
        shape.draw_line((29, 7), (7, 29))
    else:
        shape.draw_polyline([(5, 19), (14, 29), (31, 6)])
    # a darker edge under the stroke, as Acrobat's marks have
    shape.finish(color=border, width=8.5, lineCap=1, lineJoin=1, closePath=False, stroke_opacity=alpha)
    if cross:
        shape.draw_line((7, 7), (29, 29))
        shape.draw_line((29, 7), (7, 29))
    else:
        shape.draw_polyline([(5, 19), (14, 29), (31, 6)])
    shape.finish(color=color, width=6.0, lineCap=1, lineJoin=1, closePath=False, stroke_opacity=alpha)
    shape.commit()
    return finish_pdf(doc)


def render(spec, lines: list[str]) -> bytes:
    """Upright source PDF for an Acrobat-look stamp (rotation is applied by the caller)."""
    if spec.style == "flag":
        return _flag(spec, lines)
    if spec.style in ("check", "cross"):
        return _mark(spec, spec.style == "cross")
    return _tile(spec, lines)
