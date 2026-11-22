"""Single Page View / Two Page View (user 2026-10-02): like the scrolling views, but one
page (or pair) at a time; Page Down / Up, Next / Previous Page and the wheel past the
page's edge change the whole page(s) shown instead of scrolling through them."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.page_view import LayoutMode


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    for n in range(5):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Page {n + 1}", fontname="helv", fontsize=14)
    path = tmp_path / "five.pdf"
    doc.save(path)
    w = MainWindow(tmp_path / "profile")
    w.resize(1200, 850)
    w.show()
    w.open_file(path)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _shown(win):
    return [i for i, item in enumerate(win.view._items) if item.isVisible()]


def _wheel(view, down: bool):
    pos = QPointF(view.viewport().rect().center())
    event = QWheelEvent(pos, QPointF(view.viewport().mapToGlobal(pos.toPoint())), QPoint(0, 0),
                        QPoint(0, -120 if down else 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)


def test_single_page_view_shows_one_page_and_page_down_turns_it(win):
    win.layout_actions[LayoutMode.SINGLE_PAGE].trigger()
    view = win.view
    assert _shown(win) == [0] and view.current_page == 0
    win.view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert _shown(win) == [1] and view.current_page == 1 and win.page_spin.value() == 2
    QTest.keyClick(view, Qt.Key.Key_PageUp)
    assert _shown(win) == [0]
    win.act_last.trigger()
    assert _shown(win) == [4]
    QTest.keyClick(view, Qt.Key.Key_PageDown)  # (the last page: nothing further)
    assert _shown(win) == [4]


def test_two_page_view_turns_the_pair(win):
    win.layout_actions[LayoutMode.TWO_PAGE].trigger()
    assert _shown(win) == [0, 1]
    win.act_next.trigger()  # Next Page: the next pair, not the right-hand page
    assert _shown(win) == [2, 3] and win.view.current_page == 2
    win.act_next.trigger()
    assert _shown(win) == [4]
    win.act_prev.trigger()
    assert _shown(win) == [2, 3]


def test_going_to_a_page_shows_it(win):
    win.layout_actions[LayoutMode.SINGLE_PAGE].trigger()
    win.thumbnails.pageActivated.emit(3)  # e.g. a click in the Pages panel
    assert _shown(win) == [3] and win.view.current_page == 3


def test_the_wheel_scrolls_within_the_page_then_turns_it(win):
    win.layout_actions[LayoutMode.SINGLE_PAGE].trigger()
    view = win.view
    view.set_zoom(3.0)  # taller than the window: the wheel scrolls inside the page first
    QCoreApplication.processEvents()
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.minimum()
    _wheel(view, down=True)
    assert _shown(win) == [0] and bar.value() > bar.minimum()  # scrolled, same page
    bar.setValue(bar.maximum())
    view._last_wheel_turn = 0
    _wheel(view, down=True)  # past the bottom: the next page, from its top
    assert _shown(win) == [1] and bar.value() == bar.minimum()
    view._last_wheel_turn = 0
    _wheel(view, down=False)  # past the top: back, ending at the bottom of page 1
    assert _shown(win) == [0] and bar.value() == bar.maximum()


def test_scrolling_views_show_every_page_and_the_choice_is_remembered(win, tmp_path):
    win.layout_actions[LayoutMode.TWO_PAGE].trigger()
    assert win.act_cover.isEnabled()  # (two columns)
    win.layout_actions[LayoutMode.SINGLE].trigger()  # Enable Scrolling: all pages again
    assert _shown(win) == [0, 1, 2, 3, 4]
    win.layout_actions[LayoutMode.SINGLE_PAGE].trigger()
    win.close()
    again = MainWindow(tmp_path / "profile")
    assert again.layout_actions[LayoutMode.SINGLE_PAGE].isChecked()
    again.close()


def _key(win, key, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(QApplication.focusWidget() or win, key, mod)
    QCoreApplication.processEvents()
    return win.view.current_page


@pytest.mark.parametrize("where", ["page", "panel"])
def test_acrobat_page_navigation_keys(win, where):
    """Home / Left / Right / End, Alt+Left / Alt+Right (user screenshot 2026-10-02), with
    the page or the Pages panel focused."""
    (win.view if where == "page" else win.thumbnails).setFocus()
    K = Qt.Key
    assert _key(win, K.Key_Right) == 1 and _key(win, K.Key_Right) == 2
    assert _key(win, K.Key_Left) == 1
    assert _key(win, K.Key_End) == 4 and _key(win, K.Key_Home) == 0
    assert _key(win, K.Key_Left, Qt.KeyboardModifier.AltModifier) == 4  # Previous View
    assert _key(win, K.Key_Right, Qt.KeyboardModifier.AltModifier) == 0  # Next View
    assert win.act_go_to_page.shortcut().toString() == "Ctrl+Shift+N"


def _tick(view, seconds):
    view._auto_last -= seconds
    view._auto_tick()


def test_automatically_scroll(win):
    view = win.view
    view.set_zoom(2.0)
    QCoreApplication.processEvents()
    bar = view.verticalScrollBar()
    assert win.act_auto_scroll.shortcut().toString() == "Ctrl+Shift+H"
    win.act_auto_scroll.trigger()  # on page 1: forward
    assert view.auto_scrolling and win.act_auto_scroll.isChecked()
    start = bar.value()
    _tick(view, 0.1)
    assert bar.value() > start
    speed = view.auto_scroll_speed(0)
    _key(win, Qt.Key.Key_Plus)
    assert view.auto_scroll_speed(0) > speed
    _key(win, Qt.Key.Key_Minus)
    _key(win, Qt.Key.Key_Minus)
    assert view.auto_scroll_speed(0) < speed
    _key(win, Qt.Key.Key_Up)  # Up: backward
    now = bar.value()
    _tick(view, 0.1)
    assert bar.value() < now and view.auto_scrolling
    _key(win, Qt.Key.Key_Down)
    now = bar.value()
    _tick(view, 0.1)
    assert bar.value() > now
    _key(win, Qt.Key.Key_Escape)
    assert not view.auto_scrolling and not win.act_auto_scroll.isChecked()


def test_automatically_scroll_backward_from_the_last_page_and_stops_at_the_start(win):
    view = win.view
    win.act_last.trigger()
    QCoreApplication.processEvents()
    win.act_auto_scroll.trigger()
    assert view._auto_direction == -1
    bar = view.verticalScrollBar()
    for _ in range(400):
        if not view.auto_scrolling:
            break
        _tick(view, 0.2)
    assert not view.auto_scrolling and bar.value() == bar.minimum()


def test_automatically_scroll_turns_pages_in_single_page_view(win):
    win.layout_actions[LayoutMode.SINGLE_PAGE].trigger()
    view = win.view
    win.act_auto_scroll.trigger()
    for _ in range(200):
        if view.current_page == 1:
            break
        _tick(view, 0.2)
    assert view.current_page == 1 and _shown(win) == [1]
    _tick(view, 0.2)
    assert view.current_page == 1  # (stays a while on the new page, not one page per tick)
    win.act_auto_scroll.trigger()  # Shift+Ctrl+H again: stop
    assert not view.auto_scrolling


def test_a_click_stops_automatically_scroll(win):
    win.act_auto_scroll.trigger()
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=win.view.viewport().rect().center())
    assert not win.view.auto_scrolling


def test_menu_in_acrobat_order(win):
    view_menu = next(a.menu() for a in win.menuBar().actions() if a.menu() and a.menu().title() == "&View")
    display = next(a.menu() for a in view_menu.actions() if a.menu() and a.menu().title() == "&Page Display")
    names = [a.text().replace("&", "") for a in display.actions() if a.text()][:5]
    assert names == ["Single Page View", "Enable Scrolling", "Two Page View", "Two Page Scrolling",
                     "Four Page Scrolling (2×2)"]
    assert all(a.isEnabled() for a in display.actions()[:5])
