"""Fill & Sign symbols (as in Acrobat): cross, check mark, circle, line and dot, for
marking paper-style forms. Each is an appearance item (like placed text): movable and
resizable until the page is flattened, in the color you pick.

The circle (user 2026-10-26) is drawn from corner to corner, so it can be an oval, and is either an
outline of a chosen line width or filled solid; it is drawn again at its real size whenever its size
changes, so the line keeps its width (the other symbols just scale).
"""

from __future__ import annotations

import pymupdf

from planetpdf.core.appearance import finish_pdf

CIRCLE_LINE = 1.5  # the circle's outline (points) unless one is chosen
LINE_WIDTHS = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)  # Fill & Sign ▸ circle ▾ ▸ Line Width

# name -> (label, default width, default height) in points
SYMBOLS = {
    "cross": ("Cross", 12.0, 12.0),
    "check": ("Check Mark", 13.0, 12.0),
    "circle": ("Circle", 24.0, 16.0),
    "line": ("Line", 36.0, 6.0),
    "dot": ("Dot", 6.0, 6.0),
}


def render(name: str, color=(0.0, 0.0, 0.0), width: float | None = None, height: float | None = None, *,
           line: float | None = None, filled: bool = False) -> bytes:
    """The symbol's upright source PDF (page = the symbol's box). The circle: ``line`` = its
    outline's width (points; ``CIRCLE_LINE`` when None), ``filled`` = solid instead."""
    if name not in SYMBOLS:
        raise ValueError(f"Unknown symbol {name!r}")
    _label, w0, h0 = SYMBOLS[name]
    w, h = width or w0, height or h0
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    stroke = max(1.0, min(w, h) * 0.12)
    pad = stroke / 2 + 0.5
    shape = page.new_shape()
    if name == "cross":
        shape.draw_line((pad, pad), (w - pad, h - pad))
        shape.draw_line((w - pad, pad), (pad, h - pad))
        shape.finish(color=color, width=stroke, lineCap=1)
    elif name == "check":
        shape.draw_polyline([(pad, h * 0.55), (w * 0.38, h - pad), (w - pad, pad)])
        shape.finish(color=color, width=stroke, lineCap=1, lineJoin=1, closePath=False)
    elif name == "circle":
        if filled:
            shape.draw_oval(pymupdf.Rect(0, 0, w, h))
            shape.finish(color=None, fill=color)
        else:
            stroke = min(line or CIRCLE_LINE, min(w, h) / 2)  # (no thicker than the circle allows)
            pad = stroke / 2
            shape.draw_oval(pymupdf.Rect(pad, pad, w - pad, h - pad))
            shape.finish(color=color, width=stroke)
    elif name == "line":
        shape.draw_line((pad, h / 2), (w - pad, h / 2))
        shape.finish(color=color, width=max(1.0, h * 0.25), lineCap=1)
    else:  # dot
        shape.draw_oval(pymupdf.Rect(0, 0, w, h))
        shape.finish(color=None, fill=color)
    shape.commit()
    return finish_pdf(doc)
