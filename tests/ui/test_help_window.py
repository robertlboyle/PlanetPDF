"""Help ▸ PlanetPDF Help (F1): the manual's window, and What's New in Help ▸ About."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from planetpdf.ui.help_window import HelpWindow, menu_reference, shortcut_reference
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.show()
    QCoreApplication.processEvents()
    yield w
    if HelpWindow._instance is not None:
        HelpWindow._instance.close()
        HelpWindow._instance.deleteLater()
        HelpWindow._instance = None
    w.undo_stack.setClean()
    w.close()


def test_f1_opens_the_manual(win):
    assert [s.toString() for s in win.act_help.shortcuts()] == ["F1"]
    assert not win.act_help_shortcuts.shortcuts()  # (F1 moved to the manual, as in Acrobat)
    win.act_help.trigger()
    help_window = HelpWindow._instance
    assert help_window is not None and help_window.isVisible()
    assert help_window.current_page() == "index.html"
    assert "Welcome to PlanetPDF" in help_window.browser.toPlainText()
    assert help_window.contents.topLevelItemCount() >= 15


def test_contents_index_and_search_open_pages(win):
    help_window = HelpWindow.show_help(win)
    help_window.open_link("pages.html#bates")
    assert help_window.current_page() == "pages.html"
    help_window.index_filter.setText("bates")
    visible = [help_window.index_list.item(n) for n in range(help_window.index_list.count())
               if not help_window.index_list.item(n).isHidden()]
    assert visible
    hits = help_window.search("redact social security")
    assert hits
    help_window._open_result(help_window.results.item(0))
    assert help_window.current_page() == hits[0].section.file


def test_the_reference_pages_come_from_the_menus(win):
    menus = menu_reference(win)
    assert "PlanetPDF Help" in menus and "Save As" in menus and "F1" in menus
    keys = shortcut_reference(win)
    # (as this computer writes it: "Ctrl+O" on Windows and Linux, "⌘O" on a Mac)
    assert QKeySequence("Ctrl+O").toString(QKeySequence.SequenceFormat.NativeText) in keys
    help_window = HelpWindow.show_help(win, "menus.html")
    assert "Menu Reference" in help_window.browser.toPlainText()


def test_about_shows_whats_new(win):
    from planetpdf import __version__
    from planetpdf.ui.dialogs.about import AboutDialog

    dialog = AboutDialog(win)
    assert dialog.tabs.tabText(0) == "What's New"
    assert f"Version {__version__}" in dialog.whats_new.toPlainText()
    dialog.close()
