"""Drawn shapes as objects, decorations that travel with their text, links, and the
free-form eraser (user request 2026-10-01)."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import content_edit as E
from planetpdf.core import drawings as DR
from planetpdf.core.document import PdfSession


def _session(tmp_path, draw) -> PdfSession:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    draw(page)
    path = tmp_path / "shapes.pdf"
    doc.save(path)
    return PdfSession.open(path)


def _underlined_link(page):
    page.insert_text((72, 100), "daryl.schmoldt@gmail.com", fontname="helv", fontsize=11)
    page.draw_rect(pymupdf.Rect(72, 102, 210, 102.7), color=None, fill=(0, 0, 1))  # the underline
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 210, 103), "uri": "mailto:daryl@example.com"})
    page.insert_text((72, 300), "Another line", fontname="helv", fontsize=11)
    page.draw_line((72, 340), (540, 340), color=(0.5, 0.5, 0.5), width=0.75)  # a section rule, far from text


def _shape_rects(s, page=0):
    return [tuple(round(v) for v in d.rect) for d in DR.page_drawings(s, page)]


def test_page_backgrounds_are_not_objects(tmp_path):
    s = _session(tmp_path, lambda p: (p.draw_rect(p.rect, color=None, fill=(1, 1, 0.9)),
                                      p.draw_line((72, 72), (300, 72), color=(0, 0, 0))))
    assert _shape_rects(s) == [(72, 72, 300, 72)]
    s.close()


def test_removing_a_frame_keeps_what_is_inside_it(tmp_path):
    s = _session(tmp_path, lambda p: (p.draw_rect(pymupdf.Rect(100, 100, 400, 300), color=(0, 0, 0), width=1),
                                      p.draw_line((150, 200), (250, 200), color=(1, 0, 0), width=2)))
    frame = next(d for d in DR.page_drawings(s, 0) if d.rect[2] > 390)
    DR.remove_drawings(s, 0, [frame])
    assert _shape_rects(s) == [(150, 199, 250, 201)] or _shape_rects(s) == [(150, 200, 250, 200)]
    s.close()


def test_an_underline_and_its_link_belong_to_the_line(tmp_path):
    s = _session(tmp_path, _underlined_link)
    line = next(l for l in E.text_lines(s, 0) if "gmail" in l.text)
    decos = DR.decorations(DR.page_drawings(s, 0), line.rect, line.size)
    assert [tuple(round(v) for v in d.rect) for d in decos] == [(72, 102, 210, 103)]  # not the section rule
    assert len(DR.links_over(s.doc[0], line.rect)) == 1
    other = next(l for l in E.text_lines(s, 0) if l.text == "Another line")
    assert DR.decorations(DR.page_drawings(s, 0), other.rect, other.size) == []
    s.close()


def test_moving_a_line_with_its_underline_and_link(tmp_path):
    s = _session(tmp_path, _underlined_link)
    page = s.doc[0]
    line = next(l for l in E.text_lines(s, 0) if "gmail" in l.text)
    decos = DR.decorations(DR.page_drawings(s, 0), line.rect, line.size)
    links = [(0, link, 50, 200, 0) for link in DR.links_over(page, line.rect)]
    E.move_group(s, [(line, 50, 200)], [], [(d, 50, 200) for d in decos], links)
    assert (122, 302, 260, 303) in _shape_rects(s) and (72, 102, 210, 103) not in _shape_rects(s)
    [link] = s.doc[0].get_links()
    assert tuple(round(v) for v in link["from"]) == (122, 290, 260, 303) and link["uri"] == "mailto:daryl@example.com"
    s.close()


def test_eraser_takes_text_and_touched_shapes_only(tmp_path):
    def draw(page):
        page.draw_rect(page.rect, color=None, fill=(1, 1, 0.9))  # a page background
        page.insert_text((72, 100), "Erase this line", fontname="helv", fontsize=11)
        page.insert_text((72, 200), "Keep this line", fontname="helv", fontsize=11)
        page.draw_line((300, 80), (300, 120), color=(0, 0, 0), width=1)  # crossed by the stroke
        page.draw_rect(pymupdf.Rect(60, 60, 560, 400), color=(0, 0, 1), width=1)  # a big empty frame around it all
    s = _session(tmp_path, draw)
    DR.erase(s, 0, [(70, 96), (200, 96), (320, 96)], 12)
    text = s.doc[0].get_text()
    assert "Erase" not in text and "Keep this line" in text
    rects = [tuple(round(v) for v in p["rect"]) for p in s.doc[0].get_drawings()]
    assert (300, 80, 300, 120) not in rects  # the line it crossed
    assert (60, 60, 560, 400) in rects  # the frame: its inside was crossed, not its outline
    assert (0, 0, 612, 792) in rects  # the background
    s.close()


def test_eraser_boxes_leave_no_gaps():
    boxes = DR.eraser_boxes([(0, 0), (100, 0)], 10)
    xs = sorted(b.x0 for b in boxes)
    assert all(b - a <= 10 for a, b in zip(xs, xs[1:])) and boxes[-1].x1 >= 105


def test_touches_follows_the_outline():
    path = {"items": [("re", pymupdf.Rect(0, 0, 100, 100), 1)], "fill": None, "width": 1, "rect": pymupdf.Rect(0, 0, 100, 100)}
    assert DR.touches(path, pymupdf.Rect(-2, 40, 2, 44))  # on the left edge
    assert not DR.touches(path, pymupdf.Rect(40, 40, 50, 50))  # inside
    filled = dict(path, fill=(1, 0, 0))
    assert DR.touches(filled, pymupdf.Rect(40, 40, 50, 50))
