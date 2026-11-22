"""Zoom and Rotate View keys (user 2026-11-21: Ctrl+Shift+Plus "zooms in most of the time but sometimes
rotates", zoom steps "not the same amount", "entering a percentage and clicking enter does nothing")."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.page_view import ZOOM_LEVELS, next_zoom


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "config")
    win.resize(1000, 800)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    win.activateWindow()
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _press(widget, key, text, modifiers):
    """A key as Qt delivers it: the shortcut check first, then the press."""
    override = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifiers, text)
    QApplication.sendEvent(widget, override)
    if not override.isAccepted():
        return False  # (a shortcut may take it: the rotate filter didn't)
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))
    return True


def test_ctrl_shift_plus_always_turns_the_view(window, monkeypatch):
    ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    zoomed = []
    monkeypatch.setattr(window.view, "zoom_in", lambda: zoomed.append(1))
    window.view.setFocus()
    for key, text in ((Qt.Key.Key_Plus, "+"), (Qt.Key.Key_Equal, "="), (Qt.Key.Key_Plus, "+")):
        assert _press(window.view, key, text, ctrl_shift)
    assert window.view.view_rotation == 270 and zoomed == []
    assert _press(window.view, Qt.Key.Key_Underscore, "_", ctrl_shift)
    assert window.view.view_rotation == 180
    # without Shift the keys are the zoom's (left to the shortcuts)
    assert not _press(window.view, Qt.Key.Key_Plus, "+", Qt.KeyboardModifier.ControlModifier)


def test_zoom_in_and_out_go_through_the_same_levels():
    assert next_zoom(1.0, up=True) == 1.25 and next_zoom(1.25, up=True) == 1.5
    assert next_zoom(1.0, up=False) == 0.75 and next_zoom(0.75, up=False) == pytest.approx(2 / 3)
    assert next_zoom(1.13, up=True) == 1.25 and next_zoom(1.13, up=False) == 1.0  # (from a fitted zoom)
    assert next_zoom(ZOOM_LEVELS[-1], up=True) == ZOOM_LEVELS[-1]


def test_zoom_buttons_come_back_to_100(window):
    window.view.set_zoom(1.0)
    seen = []
    for _ in range(3):
        window.view.zoom_in()
        seen.append(round(window.view.zoom * 100))
    for _ in range(3):
        window.view.zoom_out()
    assert seen == [125, 150, 200] and round(window.view.zoom * 100) == 100


def test_a_typed_percentage_and_enter_zooms(window):
    edit = window.zoom_combo.lineEdit()
    edit.setText("137")
    edit.returnPressed.emit()
    assert round(window.view.zoom * 100) == 137
    edit.setText("80%")
    edit.returnPressed.emit()
    assert round(window.view.zoom * 100) == 80
