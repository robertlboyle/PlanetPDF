"""Each tool shows its own cursor (user 2026-10-24: "show the correct cursor for the tool/function
that is currently in use"). Offscreen there's no real mouse, so moves are sent to the view."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from planetpdf.core.forms import design as D
from planetpdf.core.forms.fields import FieldKind
from planetpdf.core.markup import Kind
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer import cursors

C = Qt.CursorShape


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1100, 850)
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _move(win, x, y):
    view = win.view
    pos = view.mapFromScene(view._items[0].mapToScene(QPointF(x, y)))
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _shape(win):
    return win.view.viewport().cursor().shape()


def _word_center(win):
    view = win.view
    rect, _key = view._word_provider(0)[0]
    return rect.center().x(), rect.center().y()


def test_tool_cursors_drawn_from_the_icons(qapp):
    for name in cursors.CURSORS:
        cursor = cursors.cursor(name)
        assert cursor.shape() == C.BitmapCursor and not cursor.pixmap().isNull()
    pen = cursors.cursor("pen")
    assert pen.hotSpot().x() < 4 and pen.hotSpot().y() > 18  # (the pen's tip, bottom left)


def test_select_arrow_shows_the_i_beam_over_text(win):
    x, y = _word_center(win)
    _move(win, x, y)
    assert _shape(win) == C.IBeamCursor  # (a drag here selects the text)
    _move(win, 500, 700)
    assert _shape(win) == C.ArrowCursor


def test_hand_and_text_tools(win):
    win.view.set_hand_tool(True)
    assert _shape(win) == C.OpenHandCursor
    x, y = _word_center(win)
    _move(win, x, y)
    assert _shape(win) == C.OpenHandCursor  # (the Hand pans, even over text)
    win.view.set_hand_tool(False)
    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.TEXTBOX)
    assert _shape(win) == C.IBeamCursor
    win.markup.set_tool(Kind.RECT)
    assert _shape(win) == C.CrossCursor


def test_pencil_shows_the_pen_and_the_eraser_its_circle(win):
    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.INK)
    assert win.view.viewport().cursor().pixmap().cacheKey() == cursors.cursor("pen").pixmap().cacheKey()
    _move(win, 300, 300)
    assert _shape(win) == C.BitmapCursor  # (still the pen after moving)
    win.markup_ui.set_mode("edit")
    win.edit.set_tool("erase")
    assert win.view.viewport().cursor().pixmap().cacheKey() != cursors.cursor("pen").pixmap().cacheKey()


def test_zoom_tools_show_the_magnifier(win):
    win.marquee_zoom()
    assert win.view.viewport().cursor().pixmap().cacheKey() == cursors.cursor("zoom_in").pixmap().cacheKey()
    win.markup.cancel_pick_box()
    win.view.set_view_tool("dynamic_zoom")
    assert win.view.viewport().cursor().pixmap().cacheKey() == cursors.cursor("zoom").pixmap().cacheKey()
    win.view.set_view_tool(None)
    assert _shape(win) == C.ArrowCursor
    win.markup.pick_box(lambda page, rect: None, "Drag a box", name="crop")
    assert _shape(win) == C.CrossCursor  # (other box tools: the crosshair)
    win.markup.cancel_pick_box()


def test_form_fields_show_where_you_type_and_where_you_click(win):
    session = win.session
    D.create_field(session, D.FieldSpec("Name", FieldKind.TEXT, 0, (300, 600, 450, 620)))
    D.create_field(session, D.FieldSpec("Agree", FieldKind.CHECKBOX, 0, (300, 630, 312, 642)))
    D.create_field(session, D.FieldSpec("Clear", FieldKind.BUTTON, 0, (300, 650, 380, 670), caption="Clear",
                                        action="reset"))
    win.forms.refresh()
    by_name = {n: next(i for k, i in win.view._field_items.items() if k.startswith(n)) for n in ("Name", "Agree", "Clear")}
    assert by_name["Name"].cursor().shape() == C.IBeamCursor
    assert by_name["Agree"].cursor().shape() == C.PointingHandCursor
    assert by_name["Clear"].cursor().shape() == C.PointingHandCursor  # (a click presses it)
