"""Pages / Bookmarks fly out from the right and collapse like a menu; "Page x of y" at
the bottom; each thumbnail shows its page number."""

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
    w.open_file(pdf_copy("text.pdf"))  # 3 pages
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_panel_is_on_the_left_with_its_rail_by_default(win):
    panel = win.side_panel
    assert panel.is_open  # open by default
    view_left = win.central.mapTo(win, win.central.rect().topLeft()).x()
    assert panel.mapTo(win, panel.rect().topRight()).x() <= view_left
    assert win.nav_rail.isVisible() and win.nav_rail.actions() == [panel.actions["pages"], panel.actions["bookmarks"]]
    assert win.nav_rail.mapTo(win, win.nav_rail.rect().topRight()).x() <= panel.mapTo(win, panel.rect().topLeft()).x()
    assert not any(item.isVisible() for item in win.tool_panes.strip_side_items)  # not on the right strip too
    assert win.side_actions["left"].isChecked()


def test_panel_can_go_on_the_right(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 850)
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    w.side_actions["right"].trigger()  # View ▸ Show/Hide ▸ Navigation Panes ▸ on the Right
    QCoreApplication.processEvents()
    panel = w.side_panel
    assert panel.mapTo(w, panel.rect().topLeft()).x() >= w.central.mapTo(w, w.central.rect().topRight()).x()
    assert not w.nav_rail.isVisible() and all(item.isVisible() for item in w.tool_panes.strip_side_items)
    w.close()
    again = MainWindow(tmp_path / "profile")  # remembered
    assert again.settings.side_panel_side == "right" and again.side_actions["right"].isChecked()
    again.close()


def test_buttons_fly_out_switch_and_collapse(win):
    panel, pages, bookmarks = win.side_panel, win.side_panel.actions["pages"], win.side_panel.actions["bookmarks"]
    pages.trigger()  # it was open on Pages: collapses
    assert not panel.is_open and not pages.isChecked()
    pages.trigger()
    assert panel.is_open and panel.current == "pages" and pages.isChecked()
    bookmarks.trigger()  # switch
    assert panel.is_open and panel.current == "bookmarks" and bookmarks.isChecked() and not pages.isChecked()
    assert panel.title.text() == "Bookmarks"
    panel.collapse_button.click()
    assert not panel.is_open and not bookmarks.isChecked()


def test_page_x_of_y_and_numbers_under_thumbnails(win):
    panel = win.side_panel
    assert panel.footer.text() == "Page 1 of 3"
    win.view.go_to_page(2)
    QCoreApplication.processEvents()
    assert panel.footer.text() == "Page 3 of 3"
    assert [win.thumbnails.item(i).text() for i in range(3)] == ["1", "2", "3"]


def test_open_state_is_remembered(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    w.side_panel.show_panel("bookmarks")
    w.close()
    again = MainWindow(tmp_path / "profile")
    again.open_file(pdf_copy("text.pdf"))
    assert again.side_panel.is_open and again.side_panel.current == "bookmarks"
    again.side_panel.collapse()
    again.close()
    third = MainWindow(tmp_path / "profile")
    third.open_file(pdf_copy("text.pdf"))
    assert not third.side_panel.is_open
    third.close()


def test_no_panel_without_a_document(qapp, tmp_path, pdf_copy):
    """User 2026-10-02: don't show the page panel when no document is open."""
    w = MainWindow(tmp_path / "profile")
    w.show()
    assert not w.side_panel.is_open and not w.side_panel.actions["pages"].isEnabled()
    w.open_file(pdf_copy("text.pdf"))
    assert w.side_panel.is_open and w.side_panel.actions["pages"].isEnabled()  # (wanted: open by default)
    w.close_document()
    assert not w.side_panel.is_open and not w.side_panel.actions["pages"].isEnabled()
    w.open_file(pdf_copy("text.pdf"))
    assert w.side_panel.is_open  # back as it was
    w.side_panel.collapse()
    w.close_document()
    w.open_file(pdf_copy("text.pdf"))
    assert not w.side_panel.is_open  # (closed by the user: stays closed)
    w.undo_stack.setClean()
    w.close()


def test_dragging_the_splitter_resizes_the_thumbnails(win):
    """User 2026-10-01: drag the splitter to grow or shrink the page thumbnails."""
    import time

    def settle():
        end = time.perf_counter() + 0.5
        while time.perf_counter() < end:
            QCoreApplication.processEvents()

    splitter, thumbs = win.central_splitter, win.thumbnails
    total = sum(splitter.sizes())
    splitter.setSizes([360, total - 360])  # the panel is on the left
    settle()
    wide = thumbs.iconSize().width()
    assert wide > 250 and not thumbs._unrendered  # re-rendered at the new size
    assert thumbs.visualItemRect(thumbs.item(0)).width() >= wide  # the cells grew too (not cropped)
    splitter.setSizes([110, total - 110])
    settle()
    assert thumbs.iconSize().width() < 90 < wide
    assert thumbs.visualItemRect(thumbs.item(0)).width() < wide
    assert thumbs.iconSize().height() > thumbs.iconSize().width()  # still page-shaped

def test_thumbnail_size_does_not_depend_on_the_scroll_bar(win):
    """User bug 2026-10-17: on some files the thumbnails kept jumping between two sizes. At
    one size the list needed a scroll bar, the bar narrowed the viewport, the thumbnails
    shrank, the bar went away, and they grew again. Their size is now the same either way."""
    from PySide6.QtCore import Qt

    thumbs = win.thumbnails
    win.side_panel.show_panel("pages")
    QCoreApplication.processEvents()
    widths = []
    for policy in (Qt.ScrollBarPolicy.ScrollBarAlwaysOn, Qt.ScrollBarPolicy.ScrollBarAlwaysOff):
        thumbs.setVerticalScrollBarPolicy(policy)
        QCoreApplication.processEvents()
        widths.append(thumbs.available_width())
    thumbs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    assert widths[0] == widths[1]
    # and room is left for the bar: a thumbnail never sits under it
    bar = thumbs.verticalScrollBar().sizeHint().width()
    assert thumbs.available_width() + bar <= thumbs.contentsRect().width()
