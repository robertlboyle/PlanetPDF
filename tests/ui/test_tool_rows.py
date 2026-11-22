"""The open tool's bar is on its own row under the main toolbar, whichever way the tool
is opened (Tools menu or the strip on the right), even with an old saved layout."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QToolBar

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.toolbars import TOOLS


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _open(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1200, 800)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    return win


def test_menu_and_strip_open_the_same_tool(qapp, tmp_path, pdf_copy):
    win = _open(qapp, tmp_path, pdf_copy)
    try:
        tools_menu = next(a.menu() for a in win.menuBar().actions() if a.menu() and a.menu().title() == "&Tools")
        by_text = {a.text().replace("&&", "&"): a for a in tools_menu.actions()}
        for tool in TOOLS:
            if tool.key == "compare":
                continue
            by_text[tool.label].trigger()  # Tools menu
            QCoreApplication.processEvents()
            from_menu = (win.tool_panes.current, win.markup_ui.mode, win.tool_panes.bars[tool.key].isVisible())
            win.tool_panes.close()
            win.tool_panes.strip_actions[tool.key].trigger()  # the button on the right
            QCoreApplication.processEvents()
            from_strip = (win.tool_panes.current, win.markup_ui.mode, win.tool_panes.bars[tool.key].isVisible())
            assert from_menu == from_strip == (tool.key, tool.mode, True), tool.key
            bar = win.tool_panes.bars[tool.key]
            main = win.findChild(QToolBar, "mainToolbar")
            assert bar.geometry().top() >= main.geometry().bottom() - 1, f"{tool.key} bar isn't under the main toolbar"
            win.tool_panes.close()
    finally:
        win.undo_stack.setClean()
        win.close()


def test_old_saved_layout_still_puts_the_tool_bar_on_its_own_row(qapp, tmp_path, pdf_copy):
    win = _open(qapp, tmp_path, pdf_copy)
    # Simulate an older build's layout: every top toolbar crammed onto one row, saved
    # without a version (as older builds did).
    import base64

    main = win.findChild(QToolBar, "mainToolbar")
    for bar in [main, *win.tool_panes.bars.values()]:
        win.removeToolBarBreak(bar)
        win.addToolBar(bar)
    old_state = base64.b64encode(bytes(win.saveState())).decode("ascii")
    win.undo_stack.setClean()
    win.close()
    profile = tmp_path / "profile"
    from planetpdf.core.storage.profile import Profile
    from planetpdf.core.storage.settings import load_settings, save_settings

    settings_dir = Profile(profile).settings_dir
    settings = load_settings(settings_dir)
    settings.window_state = old_state
    save_settings(settings, settings_dir)
    again = MainWindow(profile)
    again.resize(1200, 800)
    again.show()
    again.open_file(pdf_copy("text.pdf"))
    try:
        again.tool_panes.open("fill_sign")
        QCoreApplication.processEvents()
        bar = again.tool_panes.bars["fill_sign"]
        main = again.findChild(QToolBar, "mainToolbar")
        assert bar.geometry().top() >= main.geometry().bottom() - 1
        assert main.isVisible() and again.tool_panes.strip.isVisible()
    finally:
        again.undo_stack.setClean()
        again.close()
