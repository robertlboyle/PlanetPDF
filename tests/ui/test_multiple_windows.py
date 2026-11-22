"""Several documents in one window, as tabs; a window of its own only when popped out
(user 2026-10-02: "open multiple documents inside one copy of the program")."""

from __future__ import annotations

import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QFileDialog

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.menus import DocumentMenuRow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def files(tmp_path, pdf_copy):
    out = []
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        target = tmp_path / name
        shutil.copy(pdf_copy("text.pdf"), target)
        out.append(target)
    return out


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.resize(1000, 700)
    w.show()
    QCoreApplication.processEvents()
    yield w
    for window in MainWindow.open_windows():
        for doc in window.documents:
            doc.undo_stack.setClean()
        window.close()


def _tabs(window):
    return [window.tabs.tabText(n) for n in range(window.tabs.count())]


def test_open_several_files_as_tabs_in_one_window(win, files, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(p) for p in files], ""))
    win.open_dialog()
    assert MainWindow.open_windows() == [win]  # (no new windows, no new copies of the program)
    assert _tabs(win) == ["one.pdf", "two.pdf", "three.pdf"] and win.tabs.isVisible()
    assert win.session.path.name == "three.pdf"  # the last one opened is in front
    win.tabs.setCurrentIndex(0)
    assert win.session.path.name == "one.pdf" and win.windowTitle().startswith("one.pdf")


def test_an_open_file_is_brought_forward_not_opened_twice(win, files):
    win.open_paths(files[:2])
    win.tabs.setCurrentIndex(0)
    assert win.open_paths([files[1]]) == [win]
    assert win.tabs.count() == 2 and win.session.path.name == "two.pdf"


def test_each_tab_keeps_its_own_changes_undo_and_view(win, files):
    win.open_paths(files[:2])
    one, two = win.documents
    win._activate(one)
    win.act_rotate_cw.trigger()  # an edit in one.pdf
    win.act_rotate_view_cw.trigger()  # and its view turned
    assert win.tabs.tabText(0) == "one.pdf *"
    win._activate(two)
    assert not win.undo_stack.canUndo() and not win.act_undo.isEnabled()  # two.pdf has no history
    assert win.view.view_rotation == 0 and win.tabs.tabText(1) == "two.pdf"
    win.next_tab()  # Ctrl+Tab: back to one.pdf (wraps around)
    assert win.document is one and win.undo_stack.undoText() == "Rotate Page" or win.undo_stack.canUndo()
    assert win.view.view_rotation == 90  # its view as it was left
    win.act_undo.trigger()
    assert win.session.doc[0].rotation == 0 and win.tabs.tabText(0) == "one.pdf"


def test_window_menu_lists_documents_with_a_pop_out_button(win, files):
    win.open_paths(files[:2])
    menu = win.window_menu
    menu.aboutToShow.emit()
    rows = [a for a in menu.actions() if isinstance(a, DocumentMenuRow)]
    assert [r.doc.name for r in rows] == ["one.pdf", "two.pdf"]
    assert [r.current for r in rows] == [False, True] and all(r.can_pop_out for r in rows)
    rows[0].show_it()
    assert win.session.path.name == "one.pdf"
    assert win.act_move_to_new_window.isEnabled()


def test_move_to_new_window_takes_the_changes_and_undo_history(win, files):
    win.open_paths(files[:2])
    two = win.documents[1]
    win._activate(two)
    win.act_rotate_cw.trigger()
    other = win.move_document(two)
    assert other is not None and other is not win and MainWindow.open_windows() == [win, other]
    assert _tabs(win) == ["one.pdf"] and _tabs(other) == ["two.pdf *"]
    assert other.session.doc[0].rotation == 90 and other.settings is win.settings
    other.act_undo.trigger()  # its undo history came along and works in the new window
    assert other.session.doc[0].rotation == 0 and not other.is_dirty()
    assert not win.act_move_to_new_window.isEnabled()  # (alone in its window now)
    assert win.move_document() is None


def test_tab_right_click_menu_pops_the_document_out(win, files, monkeypatch):
    """User bug 2026-10-02: right-clicking a tab failed (NameError) instead of showing the menu."""
    win.open_paths(files[:2])
    menu = win.tab_menu(win.tabs.document(win.tabs.tabAt(win.tabs.tabRect(0).center())))
    items = [a for a in menu.actions() if a.text()]
    assert [a.text() for a in items] == ["Move to New Window", "Close", "Close Other Tabs"]
    assert all(a.isEnabled() for a in items) and not items[0].icon().isNull()
    items[0].trigger()
    other = next(w for w in MainWindow.open_windows() if w is not win)
    assert _tabs(other) == ["one.pdf"] and _tabs(win) == ["two.pdf"]


def test_dragging_a_tab_out_and_onto_another_window(win, files, monkeypatch):
    win.open_paths(files)
    monkeypatch.setattr(MainWindow, "window_at", classmethod(lambda cls, pos: None))
    win._tab_torn_off(2, QPoint(1500, 400))  # let go away from any window: a new one
    other = next(w for w in MainWindow.open_windows() if w is not win)
    assert _tabs(other) == ["three.pdf"] and _tabs(win) == ["one.pdf", "two.pdf"]
    monkeypatch.setattr(MainWindow, "window_at", classmethod(lambda cls, pos: win))
    other._tab_torn_off(0, QPoint(10, 10))  # dropped on the first window: back there
    QCoreApplication.processEvents()
    assert _tabs(win) == ["one.pdf", "two.pdf", "three.pdf"]
    assert MainWindow.open_windows() == [win]  # (the emptied window closed)


def test_closing_tabs_and_the_last_one(win, files):
    win.open_paths(files[:2])
    win.close_document()  # Ctrl+W: the current tab
    assert _tabs(win) == ["one.pdf"] and win.session.path.name == "one.pdf"
    win._on_tab_close(0)  # the tab's ×
    assert win.documents == [] and win.session is None and not win.tabs.isVisible()
    assert MainWindow.open_windows() == [win]  # (the only window stays, empty)


def test_closing_the_window_asks_about_each_unsaved_document(win, files):
    win.open_paths(files[:2])
    asked = []

    def ask() -> bool:
        asked.append(win.session.path.name)
        return True

    win.maybe_save = ask
    assert win.maybe_save_all() and asked == ["one.pdf", "two.pdf"]
    del win.maybe_save


def test_exit_remembers_every_document_for_next_time(win, files, tmp_path):
    win.open_paths(files[:2])
    other = win.move_document(win.documents[1])
    other.open_paths([files[2]])
    assert win.quit_all()
    assert MainWindow.open_windows() == []
    again = MainWindow(tmp_path / "profile")
    again.show()
    assert sorted(p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in again.settings.last_session) == [
        "one.pdf", "three.pdf", "two.pdf"]
    assert again.reopen_last_session()
    assert sorted(_tabs(again)) == ["one.pdf", "three.pdf", "two.pdf"]  # all in one window, as tabs


def test_exit_stops_when_a_document_keeps_its_changes(win, files):
    win.open_paths(files[:2])
    win.act_rotate_cw.trigger()
    win.maybe_save = lambda: False  # e.g. Cancel in "Save changes?"
    assert not win.quit_all()
    assert win in MainWindow.open_windows() and win.documents
    del win.maybe_save


def test_dropping_several_pdfs_makes_tabs(win, files):
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in files[:2]])
    event = QDropEvent(QPointF(50, 50), Qt.DropAction.CopyAction, data, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    win.dropEvent(event)
    assert _tabs(win) == ["one.pdf", "two.pdf"] and MainWindow.open_windows() == [win]


def test_tabs_can_be_hidden(win, files):
    win.open_paths(files[:2])
    win.act_show_tabs.trigger()
    assert not win.tabs.isVisible() and not win.settings.show_document_tabs
    win.act_show_tabs.trigger()
    assert win.tabs.isVisible()


def test_a_later_start_hands_its_files_to_the_running_one(win, files, qapp):
    """Double-clicking another PDF opens it in the running PlanetPDF (one copy)."""
    from planetpdf.ui.single_instance import InstanceServer, send_to_running

    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    import planetpdf

    received = []
    server = InstanceServer(received.append, name=f"PlanetPDF-test-{os.getpid()}")
    assert server.listening
    # the second start is a separate process, as when another PDF is double-clicked
    code = ("from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from planetpdf.ui.single_instance import send_to_running; "
            f"print(send_to_running([{str(files[0])!r}], name={server.name!r}))")
    env = dict(os.environ, PYTHONPATH=str(Path(planetpdf.__file__).parents[1]))
    second = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    end = time.time() + 30
    while second.poll() is None and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert second.stdout.read().strip() == "True" and received == [[files[0].resolve()]]
    assert not send_to_running([files[0]], name=server.name + "-nobody")  # (none running: start normally)
    server.server.close()


def test_a_whole_document_change_keeps_the_view_where_it_was(win, files):
    """User bug 2026-10-02: every move of an object on page 2 jumped the view to page 1
    (a move is a whole-document change, and the view was rebuilt at the top)."""
    win.open_paths([files[0]])
    win.view.go_to_page(2)
    QCoreApplication.processEvents()
    assert win.view.current_page == 2
    win.document_replaced()  # (what undo / redo of a move, flatten… do)
    QCoreApplication.processEvents()
    assert win.view.current_page == 2
