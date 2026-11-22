"""One tool panel at a time (user 2026-10-01): the open tool's panel flies out and the
previous tool's closes, instead of the panels piling up down the right."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 850)
    w.show()
    w.open_file(pdf_copy("acroform.pdf"))  # has form fields
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _open_panels(w):
    return [d.objectName() for d in w.tool_panel_docks() if d.isVisible()]


@pytest.mark.parametrize("sequence", [
    ["redact", "edit", "fill_sign", "comment", "redact"],
    ["comment", "stamp", "edit", "organize", "fill_sign"],
])
def test_switching_tools_swaps_the_panel(win, sequence):
    expected = {"redact": ["redactionDock"], "edit": ["editPdfDock"], "fill_sign": ["formsDock"],
                "comment": ["commentsDock"], "stamp": [], "organize": []}
    for key in sequence:
        win.tool_panes.strip_actions[key].trigger()  # the button on the right
        QCoreApplication.processEvents()
        assert _open_panels(win) == expected[key], key


def test_menu_entry_and_closing(win):
    tools = next(a.menu() for a in win.menuBar().actions() if a.menu() and a.menu().title() == "&Tools")
    by_text = {a.text().replace("&&", "&"): a for a in tools.actions()}
    by_text["Redact"].trigger()
    assert _open_panels(win) == ["redactionDock"]
    by_text["Comment"].trigger()
    assert _open_panels(win) == ["commentsDock"]
    win.tool_panes.close()
    assert _open_panels(win) == []


def test_pages_and_history_panels_are_left_alone(win):
    win._history_dock.show()
    win.tool_panes.open("redact")
    win.tool_panes.open("edit")
    assert win._history_dock.isVisible()
