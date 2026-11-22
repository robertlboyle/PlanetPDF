"""Fill & Sign symbols (user 2026-10-26): the circle is dragged from corner to corner (any oval;
Shift = a perfect circle), outline of a chosen width or solid; and the next symbol of a kind takes
the size of the last one resized."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from planetpdf.core import markup as K
from planetpdf.core import symbols as S
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    window = MainWindow(tmp_path / "profile")
    window.show()
    window.open_file(g.make_text_pdf(tmp_path / "text.pdf"))
    yield window
    window.undo_stack.setClean()
    window.close()


def _symbols(win, name):
    return [i for i in K.list_markup(win.session) if i.spec is not None and i.spec.text == name]


def _size(item):
    rect = pymupdf.Rect(item.rect)
    return round(rect.width, 2), round(rect.height, 2)


def test_the_circle_is_dragged_corner_to_corner(win):
    win.markup.place_symbol("circle")
    assert win.view.draw_tool == "ellipse"
    win.view.shapeDrawn.emit("ellipse", 0, QRectF(100, 100, 80, 40))  # an oval
    [oval] = _symbols(win, "circle")
    assert _size(oval) == (80.0, 40.0)
    win.view.shapeDrawn.emit("ellipse", 0, QRectF(300, 300, 0, 0))  # a click: the last size
    assert [_size(c) for c in _symbols(win, "circle")] == [(80.0, 40.0), (80.0, 40.0)]


def test_outline_width_and_solid_fill(win):
    win.markup.set_circle_style(line=4.0)
    win.markup.place_symbol("circle")
    win.view.shapeDrawn.emit("ellipse", 0, QRectF(100, 100, 60, 60))
    [circle] = _symbols(win, "circle")
    assert circle.spec.style.width == 4.0 and circle.spec.style.fill is None
    drawn = pymupdf.open("pdf", circle.spec.source)[0].get_drawings()
    assert drawn[0]["width"] == pytest.approx(4.0) and drawn[0]["fill"] is None
    win.markup.set_tool(None)
    win.view.set_selected_markup(circle.item_id)
    win.markup.set_circle_style(filled=True)  # the selected circle, and the next ones
    [circle] = _symbols(win, "circle")
    assert circle.spec.style.fill is not None and win.settings.circle_filled
    drawn = pymupdf.open("pdf", circle.spec.source)[0].get_drawings()
    assert drawn[0]["fill"] is not None
    win.undo_stack.undo()
    assert _symbols(win, "circle")[0].spec.style.fill is None


def test_the_line_keeps_its_width_when_the_circle_is_resized(win):
    win.markup.set_circle_style(line=2.0)
    win.markup.place_symbol("circle")
    win.view.shapeDrawn.emit("ellipse", 0, QRectF(100, 100, 40, 40))
    [circle] = _symbols(win, "circle")
    win.markup.set_tool(None)
    win.view.markupMoved.emit(circle.item_id, QRectF(100, 100, 120, 60))  # a corner dragged
    [circle] = _symbols(win, "circle")
    page = pymupdf.open("pdf", circle.spec.source)[0]
    assert (page.rect.width, page.rect.height) == (120, 60)  # drawn at its real size…
    assert page.get_drawings()[0]["width"] == pytest.approx(2.0)  # …so the line stays 2 pt


def test_the_next_symbol_takes_the_size_of_the_last_one_resized(win):
    default = S.SYMBOLS["check"][1:]
    win.markup.place_symbol("check")
    win.view.shapeDrawn.emit("point", 0, QRectF(100, 100, 0, 0))
    [first] = _symbols(win, "check")
    assert _size(first) == default
    win.markup.set_tool(None)
    win.view.markupMoved.emit(first.item_id, QRectF(100, 100, 26, 24))  # resized with a corner
    win.markup.place_symbol("check")
    win.view.shapeDrawn.emit("point", 0, QRectF(200, 200, 0, 0))
    win.view.shapeDrawn.emit("point", 0, QRectF(300, 300, 0, 0))
    assert [_size(c) for c in _symbols(win, "check")] == [(26.0, 24.0)] * 3
    win.markup.place_symbol("cross")  # each kind keeps its own size
    win.view.shapeDrawn.emit("point", 0, QRectF(400, 400, 0, 0))
    assert _size(_symbols(win, "cross")[0]) == S.SYMBOLS["cross"][1:]
    win.markup.set_tool(None)
    moved = _symbols(win, "check")[1]
    win.view.markupMoved.emit(moved.item_id, QRectF(250, 250, 26, 24))  # only moved: no change
    assert win.settings.symbol_sizes["check"] == [26.0, 24.0]
