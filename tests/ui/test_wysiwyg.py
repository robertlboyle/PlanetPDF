"""WYSIWYG behavior, driven with real mouse events on the page view."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem

from planetpdf.core import markup as K
from planetpdf.core.markup import Kind
from planetpdf.core.stamps import BUILTIN_STAMPS, builtin
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    win.view.set_zoom(1.0)
    QCoreApplication.processEvents()
    win.markup.set_markup_mode(True)
    yield win
    win.undo_stack.setClean()
    win.close()


def _vp(window, x, y, page=0):
    """Page point -> viewport position."""
    return window.view.mapFromScene(window.view._items[page].mapToScene(QPointF(x, y)))


def _send(window, kind, x, y, buttons=Qt.MouseButton.LeftButton):
    view = window.view
    pos = _vp(window, x, y)
    button = Qt.MouseButton.LeftButton if kind != QMouseEvent.Type.MouseMove else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def press(w, x, y):
    _send(w, QMouseEvent.Type.MouseButtonPress, x, y)


def move(w, x, y, held=True):
    _send(w, QMouseEvent.Type.MouseMove, x, y, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton)


def release(w, x, y):
    _send(w, QMouseEvent.Type.MouseButtonRelease, x, y, Qt.MouseButton.NoButton)


def _hidden(window, xref) -> bool:
    page = window.session.doc[0]  # keep the page alive (annots reference it weakly)
    return bool(page.load_annot(xref).flags & pymupdf.PDF_ANNOT_IS_HIDDEN)


def _preview(window):
    return window.view._drag["item"] if window.view._drag else None


def test_circle_previews_as_a_circle(window):
    window.markup.set_tool(Kind.ELLIPSE)
    press(window, 100, 400)
    move(window, 220, 480)
    item = _preview(window)
    assert isinstance(item, QGraphicsEllipseItem)  # not a box
    assert item.pen().color().name() == "#d11a1a" and item.pen().widthF() == pytest.approx(2.0)
    assert item.rect().width() == pytest.approx(120, abs=1.5)
    release(window, 220, 480)
    [created] = K.list_markup(window.session)
    assert created.kind is Kind.ELLIPSE
    assert window.view._lingering.get(0)  # the preview stays until the page redraws


def test_preview_uses_the_current_style(window):
    window.settings.markup_width = 6.0
    window.settings.markup_opacity = 0.5
    window.settings.markup_fill_enabled = True
    window.markup.set_tool(Kind.RECT)
    press(window, 100, 400)
    move(window, 200, 450)
    item = _preview(window)
    assert isinstance(item, QGraphicsRectItem)
    assert item.pen().widthF() == pytest.approx(6.0) and item.opacity() == pytest.approx(0.5)
    assert item.brush().style() != Qt.BrushStyle.NoBrush  # filled, like the result
    release(window, 200, 450)


def test_arrow_previews_with_its_head(window):
    window.markup.set_tool(Kind.ARROW)
    press(window, 100, 500)
    move(window, 300, 560)
    item = _preview(window)
    assert isinstance(item, QGraphicsPathItem)
    assert item.path().elementCount() > 4  # line + closed head
    release(window, 300, 560)
    assert [i.kind for i in K.list_markup(window.session)] == [Kind.ARROW]


def test_highlight_selects_words_in_reading_order(window):
    window.markup.set_tool(Kind.HIGHLIGHT)
    page = window.session.doc[0]
    start = page.search_for("Name:")[0]
    end = page.search_for(g.SSN)[0]  # next line
    press(window, start.x0 + 2, (start.y0 + start.y1) / 2)
    move(window, end.x1 - 2, (end.y0 + end.y1) / 2)
    drag = window.view._drag
    assert len(drag["words_items"]) == 2  # one box per line, not a rectangle
    release(window, end.x1 - 2, (end.y0 + end.y1) / 2)
    [item] = K.list_markup(window.session)
    assert item.kind is Kind.HIGHLIGHT and len(item.spec.points) == 8  # 2 line quads
    text = page.get_text("text", clip=pymupdf.Rect(item.spec.rect))
    assert "John Q. Public" in text


def test_stamp_follows_the_cursor_then_places(window):
    window.markup.place_stamp(builtin("approved").stamp_id)
    move(window, 300, 300, held=False)  # hovering
    ghost = window.view._ghost_item
    assert isinstance(ghost, QGraphicsPixmapItem)
    size = window.markup._pending_size
    assert ghost.pos().x() == pytest.approx(300 - size.width() / 2, abs=1)
    press(window, 300, 300)
    release(window, 300, 300)
    [item] = K.list_markup(window.session)
    assert item.kind is Kind.STAMP
    assert pymupdf.Rect(item.rect).width == pytest.approx(size.width(), abs=0.5)
    assert window.view._ghost_item is None and window.view.draw_tool is None


def test_moving_lifts_the_item_and_drags_its_picture(window):
    window.markup.set_tool(Kind.RECT)
    press(window, 100, 400)
    move(window, 200, 460)
    release(window, 200, 460)
    [item] = K.list_markup(window.session)
    window.markup.set_tool(None)
    press(window, 150, 402)  # on the box's edge
    move(window, 250, 452)
    drag = window.view._drag
    assert drag["mode"] == "move" and isinstance(drag.get("ghost"), QGraphicsPixmapItem)
    assert _hidden(window, item.xref)  # taken off the page while dragged
    release(window, 250, 452)
    [moved] = K.list_markup(window.session)
    assert moved.spec.rect[0] == pytest.approx(200, abs=1.5)
    assert not _hidden(window, moved.xref)


def test_canceled_drag_puts_the_item_back(window):
    window.markup.set_tool(Kind.RECT)
    press(window, 100, 400)
    move(window, 200, 460)
    release(window, 200, 460)
    [item] = K.list_markup(window.session)
    window.markup.set_tool(None)
    press(window, 150, 402)
    move(window, 250, 452)
    from PySide6.QtGui import QKeyEvent

    QApplication.sendEvent(window.view, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not _hidden(window, item.xref)
    assert K.list_markup(window.session)[0].spec.rect == item.spec.rect


def test_appearance_items_keep_their_custom_look_after_hiding(window):
    window.markup.place_stamp(builtin("approved").stamp_id)
    press(window, 300, 300)
    release(window, 300, 300)
    [item] = K.list_markup(window.session)
    K.set_hidden(window.session, item.item_id, True)
    K.set_hidden(window.session, item.item_id, False)
    assert "APPROVED" in window.session.doc[0].get_text()  # our appearance, not a stock stamp
