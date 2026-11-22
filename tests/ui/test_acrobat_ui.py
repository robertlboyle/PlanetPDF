"""Acrobat-style menus and keyboard shortcuts (user decision 2026-09-30)."""

from __future__ import annotations

from collections import defaultdict

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.shortcuts import ACROBAT, ShortcutsDialog, shortcut_rows
from planetpdf.ui.viewer.page_view import ZoomMode


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def test_menu_bar_follows_acrobat(window):
    titles = [_plain(a.menu().title()) for a in window.menuBar().actions() if a.menu()]
    assert titles == ["File", "Edit", "View", "Tools", "Window", "Help"]  # classic Acrobat (no E-Sign: user decision)
    modes = [_plain(a.text()) for a in (window.markup_ui.act_mode_form, window.markup_ui.act_mode_markup,
                                         window.markup_ui.act_mode_edit, window.markup_ui.act_mode_design)]
    assert modes == ["Fill & Sign", "Comment", "Edit PDF", "Prepare Form"]


def test_every_acrobat_shortcut_is_bound(window):
    for name, keys in ACROBAT.items():
        action = getattr(window, name)
        assert [s.toString() for s in action.shortcuts()] == [QKeySequence(k).toString() for k in keys], name


def test_no_ambiguous_shortcuts(window):
    """Two window-wide actions on one key would make Qt ignore the key entirely."""
    owners = defaultdict(set)
    for action in window.findChildren(type(window.act_open)):
        if action.shortcutContext() != Qt.ShortcutContext.WindowShortcut:
            continue
        for seq in action.shortcuts():
            owners[seq.toString()].add(action)
    clashes = {key: sorted(a.text() for a in acts) for key, acts in owners.items() if len(acts) > 1}
    assert not clashes


def test_keys_do_what_acrobat_users_expect(window):
    view = window.view
    view.setFocus()
    QTest.keyClick(window, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert view.zoom_mode is ZoomMode.FIT_PAGE
    QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    assert view.zoom_mode is ZoomMode.FIT_WIDTH
    QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    assert view.zoom == pytest.approx(1.0)
    QTest.keyClick(view, Qt.Key.Key_End)
    assert view.current_page == 2
    QTest.keyClick(view, Qt.Key.Key_Left)
    assert view.current_page == 1
    QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    focus = QApplication.focusWidget()
    assert focus is window.page_spin or focus.parent() is window.page_spin
    QTest.keyClick(window, Qt.Key.Key_F4)
    assert not window.side_panel.is_open
    QTest.keyClick(window, Qt.Key.Key_F4)
    assert window.side_panel.is_open


def test_full_screen_and_menu_bar_toggle(window):
    window.act_toggle_menubar.trigger()
    assert not window.menuBar().isVisible()
    window.act_toggle_menubar.trigger()
    assert window.menuBar().isVisible()
    window.toggle_full_screen()
    assert window.isFullScreen() and not window.menuBar().isVisible() and window.act_full_screen.isChecked()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not window.isFullScreen() and window.menuBar().isVisible() and not window.act_full_screen.isChecked()


def test_ctrl_shift_d_deletes_pages_undoably(window):
    window.act_delete_pages_menu.trigger()
    assert window.session.page_count == 2
    window.undo_stack.undo()
    assert window.session.page_count == 3


def test_shortcuts_list(window):
    rows = shortcut_rows(window)
    commands = {(menu.split(" ▸ ")[0], command): keys for menu, command, keys in rows}
    assert commands[("File", "Properties")] == QKeySequence("Ctrl+D").toString(QKeySequence.SequenceFormat.NativeText)
    assert ("View", "Go To Page") in commands and commands[("View", "First Page")] == "Home" and ("Edit", "Preferences") in commands
    dialog = ShortcutsDialog(window)
    assert dialog.table.rowCount() == len(rows)
