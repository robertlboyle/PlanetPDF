"""Drawn shapes (vector paths) as objects for Edit PDF: list, move, delete, and the
free-form eraser (user request 2026-10-01).

* A shape is one path from ``page.get_drawings()``: lines, rules, boxes, underlines.
  Page-filling backgrounds are left out.
* Removing a shape uses a graphics-only redaction (text and images untouched) that
  takes paths *fully inside* the box; other shapes caught that way are drawn again.
* A line of text owns its *decorations*: a thin shape just under it (an underline) or
  a box drawn tightly round it (Word's word frames), and link areas over it. They move
  and are deleted with the text (user bug: link underlines stayed behind).
* The eraser redacts everything the brush passes over: text, image pixels (to white:
  MuPDF), and every shape it touches (vector paths can't be cut).

Coordinates are unrotated page coordinates, like all PyMuPDF text and drawings.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field

import pymupdf

from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import marks as M

RectTuple = tuple[float, float, float, float]
BACKGROUND_SHARE = 0.8  # a shape covering more of the page than this is a background


@dataclass(frozen=True)
class PageDrawing:
    page: int
    index: int  # position in get_drawings() (stable while the page doesn't change)
    rect: RectTuple
    path: dict = field(compare=False, repr=False)


def _page_area(page: pymupdf.Page) -> float:
    box = page.cropbox
    return max(box.width * box.height, 1.0)


def page_drawings(session: PdfSession, index: int) -> list[PageDrawing]:
    page = session.doc[index]
    area = _page_area(page)
    out = []
    for n, path in enumerate(page.get_drawings()):
        r = pymupdf.Rect(path["rect"])
        if path.get("color") is None and path.get("fill") is None:
            continue  # invisible (clipping paths and the like)
        if r.width * r.height > BACKGROUND_SHARE * area:
            continue  # a page background, not an object
        out.append(PageDrawing(index, n, tuple(r), path))
    return out


@contextmanager
def marks_aside(session: PdfSession, index: int):
    """The user's pending redaction marks are set aside while an internal redaction is
    applied, then put back (so they're never applied by accident)."""
    pending = M.list_marks(session, [index])
    if pending:
        M.remove_marks(session, [m.mark_id for m in pending])
    try:
        yield
    finally:
        if pending:
            M.add_marks(session, [m.spec for m in pending])


def redraw(page: pymupdf.Page, path: dict, dx: float = 0.0, dy: float = 0.0) -> None:
    """Draw a get_drawings() path again, moved by (dx, dy)."""
    shape = page.new_shape()
    d = pymupdf.Point(dx, dy)
    for item in path["items"]:
        kind = item[0]
        if kind == "l":
            shape.draw_line(item[1] + d, item[2] + d)
        elif kind == "re":
            shape.draw_rect(pymupdf.Rect(item[1]) + (dx, dy, dx, dy))
        elif kind == "qu":
            q = item[1]
            shape.draw_quad(pymupdf.Quad(q.ul + d, q.ur + d, q.ll + d, q.lr + d))
        elif kind == "c":
            shape.draw_bezier(item[1] + d, item[2] + d, item[3] + d, item[4] + d)
    color = path.get("color")
    caps = path.get("lineCap") or (0,)
    shape.finish(
        color=color,
        fill=path.get("fill"),
        width=(path.get("width") or 1.0) if color is not None else 0,
        dashes=path.get("dashes") or None,
        even_odd=bool(path.get("even_odd", False)),
        closePath=bool(path.get("closePath", False)),
        lineJoin=int(path.get("lineJoin") or 0),
        lineCap=int(max(caps) if isinstance(caps, (tuple, list)) else caps),
        stroke_opacity=path.get("stroke_opacity") if path.get("stroke_opacity") is not None else 1,
        fill_opacity=path.get("fill_opacity") if path.get("fill_opacity") is not None else 1,
    )
    shape.commit()


def _padded(rect, pad: float = 1.0) -> pymupdf.Rect:
    """A shape's box, never empty: a straight line's box has no width or no height, and
    PyMuPDF treats an empty rectangle as touching / containing nothing."""
    return pymupdf.Rect(rect) + (-pad, -pad, pad, pad)


def _inside(rect, box: pymupdf.Rect) -> bool:
    r = pymupdf.Rect(rect)
    return box.contains(r.tl) and box.contains(r.br)


def _item_keys(path: dict) -> set[tuple]:
    """The pieces of a path, as comparable keys (kind + rounded coordinates)."""
    keys = set()
    for item in path["items"]:
        values = []
        for part in item[1:]:
            if isinstance(part, (pymupdf.Point, pymupdf.Rect, pymupdf.Quad)):
                values += [round(v, 1) for v in (part if not isinstance(part, pymupdf.Quad) else part.rect)]
        keys.add((item[0], *values))
    return keys


def _box(drawing: PageDrawing) -> pymupdf.Rect:
    pad = 0.5 + (drawing.path.get("width") or 0) / 2
    return pymupdf.Rect(drawing.rect) + (-pad, -pad, pad, pad)


def remove_drawings(session: PdfSession, index: int, drawings: list[PageDrawing]) -> None:
    """Remove these shapes (and nothing else) from a page."""
    if not drawings:
        return
    boxes = [_box(d) for d in drawings]
    # Shapes that are not these but lie inside a box would go too: put them back. Known
    # by their pieces' geometry, not their position in the list (rewriting the page,
    # e.g. removing text, can group the same pieces into different paths).
    ours = set().union(*(_item_keys(d.path) for d in drawings))
    others = [o for o in page_drawings(session, index)
              if not _item_keys(o.path) <= ours and any(_inside(o.rect, b) for b in boxes)]
    with marks_aside(session, index):
        # One box at a time: with several redaction boxes applied together, MuPDF 1.28
        # left thin shapes behind that each box alone removes (the vertical edges of
        # Word's word frames).
        for box in boxes:
            page = session.doc[index]
            page.add_redact_annot(box, fill=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_NONE)
    page = session.doc[index]
    for other in others:  # caught inside a removed shape's box: put back
        redraw(page, other.path)
    session.mark_modified()


# --- turning, flipping and recoloring shapes (the Edit PDF panel) ------------------------------------

# as displayed (y down): a quarter turn clockwise / counterclockwise, mirror left-right / top-bottom
_DISPLAY_OPS = {
    "rotate_cw": pymupdf.Matrix(0, 1, -1, 0, 0, 0),
    "rotate_ccw": pymupdf.Matrix(0, -1, 1, 0, 0, 0),
    "flip_h": pymupdf.Matrix(-1, 0, 0, 1, 0, 0),
    "flip_v": pymupdf.Matrix(1, 0, 0, -1, 0, 0),
}


def _transformed_path(page: pymupdf.Page, path: dict, operation: str) -> dict:
    """The path turned / mirrored about its own center, as the reader sees the page."""
    to_display, back = page.rotation_matrix, page.derotation_matrix
    shown = pymupdf.Rect(path["rect"]) * to_display
    c = pymupdf.Point((shown.x0 + shown.x1) / 2, (shown.y0 + shown.y1) / 2)
    m = to_display * pymupdf.Matrix(1, 0, 0, 1, -c.x, -c.y) * _DISPLAY_OPS[operation] * pymupdf.Matrix(1, 0, 0, 1, c.x, c.y) * back
    items = []
    for item in path["items"]:
        kind = item[0]
        if kind == "l":
            items.append(("l", item[1] * m, item[2] * m))
        elif kind == "re":
            r = pymupdf.Rect(item[1])
            corners = [p * m for p in (r.tl, r.tr, r.br, r.bl)]
            items.append(("re", pymupdf.Rect(min(p.x for p in corners), min(p.y for p in corners),
                                             max(p.x for p in corners), max(p.y for p in corners)), *item[2:]))
        elif kind == "qu":
            items.append(("qu", item[1] * m))
        elif kind == "c":
            items.append(("c", item[1] * m, item[2] * m, item[3] * m, item[4] * m))
    new = dict(path)
    new["items"] = items
    corners = [p * m for p in (pymupdf.Rect(path["rect"]).tl, pymupdf.Rect(path["rect"]).br)]
    new["rect"] = pymupdf.Rect(min(p.x for p in corners), min(p.y for p in corners), max(p.x for p in corners),
                               max(p.y for p in corners))
    return new


def transform_drawings(session: PdfSession, index: int, drawings: list[PageDrawing], operation: str) -> None:
    """Turn or mirror shapes, each about its own center (``rotate_cw`` / ``rotate_ccw`` /
    ``flip_h`` / ``flip_v``)."""
    if operation not in _DISPLAY_OPS:
        raise ValueError(operation)
    page = session.doc[index]
    new_paths = [_transformed_path(page, d.path, operation) for d in drawings]
    remove_drawings(session, index, drawings)
    page = session.doc[index]
    for path in new_paths:
        redraw(page, path)
    session.mark_modified()


def recolor_drawings(session: PdfSession, index: int, drawings: list[PageDrawing], color) -> None:
    """A new color for shapes: their outline, or their fill when they have no outline."""
    new_paths = []
    for d in drawings:
        path = dict(d.path)
        if path.get("color") is not None:
            path["color"] = tuple(color)
            if path.get("fill") is not None and path["fill"] == d.path.get("color"):
                path["fill"] = tuple(color)
        else:
            path["fill"] = tuple(color)
        new_paths.append(path)
    remove_drawings(session, index, drawings)
    page = session.doc[index]
    for path in new_paths:
        redraw(page, path)
    session.mark_modified()


def add_underline(session: PdfSession, index: int, rect: RectTuple, origin, size: float, color) -> None:
    """Underline a line of text (Edit PDF ▸ FORMAT ▸ Underline): a thin bar in the text's
    color just under the baseline, the way Word draws one; it then moves with the text."""
    page = session.doc[index]
    thickness = max(0.5, size * 0.06)
    top = origin[1] + size * 0.11
    page.draw_rect(pymupdf.Rect(rect[0], top, rect[2], top + thickness), color=None, fill=tuple(color), width=0)
    session.mark_modified()


# --- what belongs to a line of text ----------------------------------------------------------------


def decorations(drawings: list[PageDrawing], rect: RectTuple, size: float) -> list[PageDrawing]:
    """Shapes that belong to a line of text at ``rect``: an underline (a thin shape just
    under it, at least half of it under the line) or a box drawn tightly round it."""
    line = pymupdf.Rect(rect)
    out = []
    for d in drawings:
        r = pymupdf.Rect(d.rect)
        thin = r.height <= max(2.5, 0.25 * size) or r.width <= 2.5
        if thin:
            middle = (r.y0 + r.y1) / 2
            overlap = min(r.x1, line.x1 + 1) - max(r.x0, line.x0 - 1)
            if line.y0 - 2 <= middle <= line.y1 + 0.35 * size and overlap >= 0.5 * min(max(r.width, 0.1), line.width):
                out.append(d)
        elif all(abs(a - b) <= 2.5 for a, b in zip(r, line)):
            out.append(d)
    return out


def links_over(page: pymupdf.Page, rect: RectTuple) -> list[dict]:
    """Link areas lying (mostly) over ``rect``."""
    line = pymupdf.Rect(rect) + (-1, -1, 1, 1)
    out = []
    for link in page.get_links():
        area = pymupdf.Rect(link["from"])
        inter = pymupdf.Rect(area) & line
        if area.width * area.height > 0 and inter.width * inter.height >= 0.5 * area.width * area.height:
            out.append(link)
    return out


def move_links(session: PdfSession, moves: list[tuple[int, dict, float, float, int]]) -> None:
    """(page, link, dx, dy, target page): the link area moves (onto that page)."""
    doc = session.doc
    changed: set[int] = set()
    for page_index, link, dx, dy, target in moves:
        page = doc[page_index]
        new = {k: v for k, v in link.items() if k not in ("xref", "id")}
        new["from"] = pymupdf.Rect(link["from"]) + (dx, dy, dx, dy)
        page.delete_link(link)
        doc[target].insert_link(new)
        changed |= {page_index, target}
    _reload(doc, changed)


def _reload(doc: pymupdf.Document, pages: set[int]) -> None:
    """MuPDF keeps a page's list of links; links added / removed through PyMuPDF only
    show in get_links() once it is synced (reload_page would refuse a page in use)."""
    for index in pages:
        pymupdf.mupdf.pdf_sync_links(pymupdf.mupdf.pdf_page_from_fz_page(doc[index].this))


def delete_links(session: PdfSession, page_index: int, links: list[dict]) -> None:
    page = session.doc[page_index]
    for link in links:
        page.delete_link(link)
    _reload(session.doc, {page_index})


# --- the eraser --------------------------------------------------------------------------------------


def eraser_boxes(points: list[tuple[float, float]], brush: float) -> list[pymupdf.Rect]:
    """Squares of the brush along the stroke, close enough to leave no gaps."""
    half = brush / 2
    step = max(brush / 3, 0.5)
    centers = []
    for n, point in enumerate(points):
        if n:
            (x0, y0), (x1, y1) = points[n - 1], point
            steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
            centers += [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps) for k in range(1, steps + 1)]
        else:
            centers.append(point)
    return [pymupdf.Rect(x - half, y - half, x + half, y + half) for x, y in centers]


def _segment_hits(a, b, box: pymupdf.Rect) -> bool:
    """Does the segment a-b pass through ``box``? (sampled finely enough for a brush)"""
    a, b = pymupdf.Point(a), pymupdf.Point(b)
    steps = max(1, int(abs(b - a) / max(min(box.width, box.height) / 2, 0.25)))
    return any(box.contains(a + (b - a) * (k / steps)) for k in range(steps + 1))


def touches(path: dict, box: pymupdf.Rect) -> bool:
    """Does the brush square touch the shape itself (its outline, or its inside when it
    is filled), not just its bounding box?"""
    filled = path.get("fill") is not None
    pad = (path.get("width") or 0) / 2
    for item in path["items"]:
        kind = item[0]
        if kind == "l":
            if _segment_hits(item[1], item[2], box + (-pad, -pad, pad, pad)):
                return True
        elif kind in ("re", "qu"):
            r = pymupdf.Rect(item[1]) if kind == "re" else item[1].rect
            if filled and r.intersects(box):
                return True
            corners = [r.tl, r.tr, r.br, r.bl, r.tl]
            if any(_segment_hits(corners[i], corners[i + 1], box + (-pad, -pad, pad, pad)) for i in range(4)):
                return True
        elif kind == "c":
            p0, p1, p2, p3 = item[1:5]
            pts = [p0 * (1 - t) ** 3 + p1 * (3 * t * (1 - t) ** 2) + p2 * (3 * t * t * (1 - t)) + p3 * t ** 3
                   for t in (i / 12 for i in range(13))]
            if filled and pymupdf.Rect(path["rect"]).intersects(box):
                return True
            if any(_segment_hits(pts[i], pts[i + 1], box + (-pad, -pad, pad, pad)) for i in range(12)):
                return True
    return False


def erase(session: PdfSession, index: int, points: list[tuple[float, float]], brush: float) -> None:
    """Free-form eraser: everything under the brush goes: text, image pixels (to white),
    and every shape the brush touches (whole: a vector shape can't be cut). Page
    backgrounds are never erased."""
    boxes = eraser_boxes(points, brush)
    if not boxes:
        return
    shapes = page_drawings(session, index)  # (without backgrounds)
    touched = [d for d in shapes
               if any(_padded(d.rect).intersects(b) and touches(d.path, b) for b in boxes)]
    remove_drawings(session, index, touched)
    with marks_aside(session, index):
        page = session.doc[index]
        for box in boxes:
            page.add_redact_annot(box, fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    session.mark_modified()
