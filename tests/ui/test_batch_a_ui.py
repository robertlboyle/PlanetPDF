from __future__ import annotations

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from planetpdf.core import links as L
from planetpdf.core import markup as K
from planetpdf.core.combine import CombineOptions
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, tmp_path, path):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(path)
    win.view.set_zoom(1.0)
    QCoreApplication.processEvents()
    return win


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = _window(qapp, tmp_path, pdf_copy("text.pdf"))
    yield win
    win.undo_stack.setClean()
    win.close()


def _send(win, kind, x, y, held=True, page=0):
    view = win.view
    pos = view.mapFromScene(view._items[page].mapToScene(QPointF(x, y)))
    button = Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    QApplication.sendEvent(view.viewport(), QMouseEvent(kind, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), button, buttons, Qt.KeyboardModifier.NoModifier))


# --- find --------------------------------------------------------------------------------------


def test_find_counts_highlights_and_steps(window):
    window.act_find.trigger()
    assert window.find_bar.isVisible()
    window.find_bar.field.setText("SSN")
    window.find_bar.search()
    assert len(window.find_bar.hits) == g.TEXT_PAGE_COUNT
    assert window.find_bar.count.text().strip() == f"1 of {g.TEXT_PAGE_COUNT}"
    assert len(window.view._highlight_items["find"]) == g.TEXT_PAGE_COUNT
    window.find_bar.step(1)
    assert window.find_bar.current == 1 and window.view.current_page == 1
    window.find_bar.step(-1)
    window.find_bar.step(-1)
    assert window.find_bar.current == g.TEXT_PAGE_COUNT - 1  # wraps around
    window.find_bar.close_bar()
    assert "find" not in window.view._highlight_items or not window.view._highlight_items["find"]


def test_find_starts_on_the_current_page(window):
    window.view.go_to_page(2)
    window.find_bar.open_bar()
    window.find_bar.field.setText("Customer")
    window.find_bar.search()
    assert window.find_bar.hits[window.find_bar.current].page == 2


def test_find_no_matches(window):
    window.find_bar.open_bar()
    window.find_bar.field.setText("zebra")
    window.find_bar.search()
    assert window.find_bar.count.text() == "No matches"


def _long_document(path, pages=30):
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"Page {n + 1}: the needle is here", fontsize=12)
    doc.save(path)
    return path


def test_find_goes_on_in_slices_and_counts_pages(qapp, tmp_path, monkeypatch):
    """A long document is searched a slice at a time (user 2026-10-23: no progress for Find): the
    bar says which page, the window keeps answering, and every match is found in the end."""
    from planetpdf.ui import text_tools

    monkeypatch.setattr(text_tools, "FIRST_SLICE", 3)
    monkeypatch.setattr(text_tools, "SLICE_SECONDS", 0)  # (one page per later slice)
    win = _window(qapp, tmp_path, _long_document(tmp_path / "long.pdf"))
    try:
        bar = win.find_bar
        bar.open_bar()
        bar.field.setText("needle")
        bar.search()
        assert bar.searching and len(bar.hits) == 3
        assert bar.count.text().strip() == "Searching page 4 of 30… 3 matches"
        assert bar.act_stop.isVisible() and bar.current == 0  # (the first match is shown at once)
        while bar.searching:
            QCoreApplication.processEvents()
        assert len(bar.hits) == 30 and bar.count.text().strip() == "1 of 30" and not bar.act_stop.isVisible()
    finally:
        win.undo_stack.setClean()
        win.close()


def test_find_can_be_stopped_and_keeps_what_it_found(qapp, tmp_path, monkeypatch):
    from planetpdf.ui import text_tools

    monkeypatch.setattr(text_tools, "FIRST_SLICE", 5)
    monkeypatch.setattr(text_tools, "SLICE_SECONDS", 0)
    win = _window(qapp, tmp_path, _long_document(tmp_path / "long.pdf"))
    try:
        bar = win.find_bar
        bar.open_bar()
        bar.field.setText("needle")
        bar.search()
        bar._enter()  # (Enter while still looking with matches: steps, doesn't start over)
        assert bar.searching and bar.current == 1
        bar.stop_search()
        assert not bar.searching and len(bar.hits) == 5
        assert bar.count.text().strip() == "2 of 5 (stopped)"
        bar.field.setText("zebra")
        bar.search()  # (a new search replaces the old one)
        while bar.searching:
            bar.stop_search()
        assert bar.count.text().strip() == "No matches (stopped)"
    finally:
        win.undo_stack.setClean()
        win.close()


# --- select text & copy -----------------------------------------------------------------------


def test_select_text_and_copy(window):
    window.text_tools.act_select.setChecked(True)
    assert window.view.draw_tool == "text"
    page = window.session.doc[0]
    a = page.search_for("Name:")[0]
    b = page.search_for(g.SSN)[0]
    _send(window, QMouseEvent.Type.MouseButtonPress, a.x0 + 2, (a.y0 + a.y1) / 2)
    _send(window, QMouseEvent.Type.MouseMove, b.x1 - 2, (b.y0 + b.y1) / 2)
    _send(window, QMouseEvent.Type.MouseButtonRelease, b.x1 - 2, (b.y0 + b.y1) / 2, held=False)
    assert window.text_tools.selected_text() == f"Name: John Q. Public\nSSN: {g.SSN}"
    assert window.text_tools.copy()
    assert QGuiApplication.clipboard().text() == f"Name: John Q. Public\nSSN: {g.SSN}"
    assert K.list_markup(window.session) == []  # selecting doesn't mark up the document
    assert not window.is_dirty()


def test_highlight_selected_text(window):
    words = __import__("planetpdf.core.text", fromlist=["page_words"]).page_words(window.session, 0)
    window.text_tools.set_selection(0, words[2:6])
    window.text_tools.highlight_selection()
    [item] = K.list_markup(window.session)
    assert item.type_name == "Highlight"


# --- links ------------------------------------------------------------------------------------------


@pytest.fixture
def linked(qapp, tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 100, 200, 120), "page": 2, "to": pymupdf.Point(0, 0)})
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 140, 200, 160), "uri": "https://example.com/x"})
    doc.set_toc([[1, "Intro", 1], [2, "Detail", 2], [1, "End", 3]])
    path = tmp_path / "links.pdf"
    doc.save(path)
    win = _window(qapp, tmp_path, path)
    yield win
    win.undo_stack.setClean()
    win.close()


def test_internal_link_jumps(linked):
    assert len(linked.view._link_items) == 2
    _send(linked, QMouseEvent.Type.MouseButtonPress, 100, 110)
    assert linked.view.current_page == 2


def test_web_link_asks_first(linked, monkeypatch):
    opened, asked = [], []
    monkeypatch.setattr(linked, "ask", lambda title, text: (asked.append(text), False)[1])
    monkeypatch.setattr("planetpdf.ui.outside_links.QDesktopServices.openUrl", lambda url: opened.append(url))
    _send(linked, QMouseEvent.Type.MouseButtonPress, 100, 150)
    assert asked and "https://example.com/x" in asked[0]
    assert opened == []  # declined
    monkeypatch.setattr(linked, "ask", lambda title, text: True)
    _send(linked, QMouseEvent.Type.MouseButtonPress, 100, 150)
    assert opened and opened[0].toString() == "https://example.com/x"


# --- bookmarks ----------------------------------------------------------------------------------------


def test_bookmarks_panel(linked):
    panel = linked.bookmarks_panel
    assert linked.side_panel.is_open and linked.side_panel.current == "bookmarks"
    assert panel.tree.topLevelItemCount() == 2 and panel.tree.topLevelItem(0).childCount() == 1
    panel._go(panel.tree.topLevelItem(1))
    assert linked.view.current_page == 2
    linked.view.go_to_page(1)
    panel.add_current()
    assert [b.title for b in L.get_bookmarks(linked.session)][-1] == "Page 2"
    item = panel.tree.topLevelItem(panel.tree.topLevelItemCount() - 1)
    item.setText(0, "My section")  # typed in place
    assert L.get_bookmarks(linked.session)[-1].title == "My section"
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    panel.delete_selected()  # deletes "Intro" and its child
    assert [b.title for b in L.get_bookmarks(linked.session)] == ["End", "My section"]
    for _ in range(3):
        linked.undo_stack.undo()
    assert [b.title for b in L.get_bookmarks(linked.session)] == ["Intro", "Detail", "End"]


# --- combine -------------------------------------------------------------------------------------------


def test_combine_opens_the_result(window, fixture_dir, tmp_path):
    img = tmp_path / "photo.png"
    Image.new("RGB", (200, 100), "blue").save(img)
    target = tmp_path / "all.pdf"
    assert window.combine_files([fixture_dir / "mixed_sizes.pdf", img], target, CombineOptions())
    assert window.session.path == target.resolve()
    assert window.session.page_count == len(g.MIXED_SIZES) + 1


def test_combine_asks_for_passwords(window, fixture_dir, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (g.ENCRYPTED_USER_PW, True))
    asked = []
    monkeypatch.setattr(window, "ask_three", lambda title, text: asked.append(text) or False)  # (No: unprotected)
    assert window.combine_files([fixture_dir / "encrypted.pdf", fixture_dir / "text.pdf"], tmp_path / "c.pdf", CombineOptions())
    assert window.session.page_count == 1 + g.TEXT_PAGE_COUNT
    assert asked and "Protect the combined PDF too?" in asked[0]


def test_a_combined_pdf_of_protected_files_is_protected_as_asked(window, fixture_dir, tmp_path, monkeypatch):
    """Security review 2026-11-27: Combine Files wrote the result unprotected without asking."""
    from PySide6.QtWidgets import QInputDialog

    from planetpdf.core.document import Protection

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (g.ENCRYPTED_USER_PW, True))
    monkeypatch.setattr(window, "ask_three", lambda title, text: None)  # (Cancel: nothing made)
    assert not window.combine_files([fixture_dir / "encrypted.pdf"], tmp_path / "c.pdf", CombineOptions())
    assert not (tmp_path / "c.pdf").exists()
    monkeypatch.setattr(window, "ask_combined_protection", lambda files: Protection(open_password="combined-pw"))
    window.undo_stack.setClean()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("combined-pw" if "c2" in str(a) else g.ENCRYPTED_USER_PW, True))
    window.combine_files([fixture_dir / "encrypted.pdf", fixture_dir / "text.pdf"], tmp_path / "c2.pdf", CombineOptions())
    import pymupdf

    with pymupdf.open(tmp_path / "c2.pdf") as doc:
        assert doc.needs_pass and doc.authenticate("combined-pw")


def test_a_long_save_shows_its_steps(qapp, tmp_path, monkeypatch):
    """User 2026-10-23 (no progress for Save): a long document's save goes through the counted
    window, not cancelable; a short one is saved straight away."""
    from planetpdf.core.progress import stage_text
    from planetpdf.ui import main_window, tasks

    shown = []

    def run(parent, title, label, work, **kw):
        shown.append((title, label, kw.get("cancelable")))
        return work(lambda done, total, stage="": shown.append(stage_text(stage, done, total)) or True)

    monkeypatch.setattr(tasks, "run_with_progress", run)
    win = _window(qapp, tmp_path, _long_document(tmp_path / "long.pdf", pages=3))
    try:
        win.session.doc[0].add_text_annot((100, 100), "note")
        win.session.mark_modified()
        assert win.save() and shown == []  # (3 pages: straight away)
        monkeypatch.setattr(main_window, "SAVE_PROGRESS_PAGES", 3)
        win.session.doc[0].add_text_annot((150, 100), "another")
        win.session.mark_modified()
        assert win.save()
        assert shown[0] == ("Saving", "Saving long.pdf", False)
        assert shown[1:] == ["Step 2 of 3: writing 3 pages…", "Step 3 of 3: opening the saved file…"]
    finally:
        win.undo_stack.setClean()
        win.close()
