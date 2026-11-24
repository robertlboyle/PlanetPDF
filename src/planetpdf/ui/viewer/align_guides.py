"""Smart guides while an object is dragged (user 2026-11-24: "as I drag an object, it should show
dotted guidelines when it aligns with something else close by such as the text to the left or right
aligned horizontally or a row above or below that is vertically aligned. It should snap to being
aligned if it is within a few pixels").

Pure geometry on page coordinates (points, as displayed), so it can be tested without a view:
`align` finds the nudge that lines the moving box up with the nearest other box (left, center or
right edges for a column; top, middle or bottom for a row) when it is within ``tolerance``, and the
dotted lines to draw between them.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QLineF, QPointF, QRectF

SNAP_PIXELS = 6.0  # on the screen: closer than this snaps (divided by the zoom for page points)
ALIGNED = 0.5  # points: edges this close count as lined up (a guide is drawn)


@dataclass(frozen=True)
class Alignment:
    dx: float  # the nudge that lines it up (0 when nothing is close)
    dy: float
    lines: tuple[QLineF, ...]  # dotted guides, page coordinates


def _xs(r: QRectF) -> tuple[float, float, float]:
    return r.left(), r.center().x(), r.right()


def _ys(r: QRectF) -> tuple[float, float, float]:
    return r.top(), r.center().y(), r.bottom()


def _gap(a: QRectF, b: QRectF) -> float:
    """How far apart two boxes are (0 when they touch or overlap): the nearer one wins a tie."""
    dx = max(0.0, max(a.left(), b.left()) - min(a.right(), b.right()))
    dy = max(0.0, max(a.top(), b.top()) - min(a.bottom(), b.bottom()))
    return (dx * dx + dy * dy) ** 0.5


def _best(moving: QRectF, targets: list[QRectF], edges, tolerance: float) -> float:
    best, best_key = 0.0, None
    for target in targets:
        for a, b in zip(edges(moving), edges(target)):  # left with left, center with center…
            diff = b - a
            if abs(diff) <= tolerance:
                key = (round(abs(diff), 3), _gap(moving, target))
                if best_key is None or key < best_key:
                    best, best_key = diff, key
    return best


def align(moving: QRectF, targets: list[QRectF], tolerance: float) -> Alignment:
    """Snap ``moving`` to the boxes in ``targets`` (each axis on its own) and the guides to show."""
    targets = [t for t in targets if t.isValid() or t.width() > 0 or t.height() > 0]
    dx = _best(moving, targets, _xs, tolerance)
    dy = _best(moving, targets, _ys, tolerance)
    placed = moving.translated(dx, dy)
    lines: list[QLineF] = []
    # a column (vertical guide) per lined-up x, to the nearest box lined up there
    for edges, vertical in ((_xs, True), (_ys, False)):
        nearest: dict[int, tuple[float, QRectF, float]] = {}
        for target in targets:
            for i, (a, b) in enumerate(zip(edges(placed), edges(target))):
                if abs(b - a) <= ALIGNED:
                    gap = _gap(placed, target)
                    if i not in nearest or gap < nearest[i][0]:
                        nearest[i] = (gap, target, a)
        for _gap_, target, at in nearest.values():
            if vertical:
                top, bottom = min(placed.top(), target.top()), max(placed.bottom(), target.bottom())
                lines.append(QLineF(QPointF(at, top), QPointF(at, bottom)))
            else:
                left, right = min(placed.left(), target.left()), max(placed.right(), target.right())
                lines.append(QLineF(QPointF(left, at), QPointF(right, at)))
    return Alignment(dx, dy, tuple(lines))
