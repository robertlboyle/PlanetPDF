"""PDF files dragged in from Explorer (user 2026-10-07): with nothing open they open; on
the Pages panel they go in between the pages they're dropped between.

The drops are delivered the way Qt does it, not by calling `dropEvent` on the window: the
drag-enter goes to the widget under the mouse and up through its parents (those accepting
drops) until one accepts it, and that one alone then gets the move and the drop. Calling
the window's `dropEvent` directly hid both bugs: the page area accepted the enter and then
refused the move ("no" cursor), and the Pages panel's viewport didn't take drops at all, so
the file opened as a tab instead of going in."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.resize(1100, 750)
    w.show()
    QCoreApplication.processEvents()
    yield w
    for window in MainWindow.open_windows():
        for doc in window.documents:
            doc.undo_stack.setClean()
        window.close()


def _pdf(folder, name, pages):
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"{name} page {n + 1}")
    doc.save(folder / name)
    return folder / name


def _drop(widget, pos: QPoint, paths) -> bool:
    """Drops `paths` on `widget` (the one under the mouse) at `pos`, as Windows hands it to
    Qt: QApplication passes the enter up the parents itself and remembers which widget
    accepted it; the move and the drop then go to that widget. False = the cursor would
    have shown "no" and nothing was dropped."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    buttons, mods = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(pos, Qt.DropAction.CopyAction, data, buttons, mods)
    enter.setAccepted(False)
    QApplication.sendEvent(widget, enter)
    if not enter.isAccepted():
        return False
    move = QDragMoveEvent(pos, Qt.DropAction.CopyAction, data, buttons, mods)
    move.setAccepted(True)  # carried over from the enter; only an explicit ignore() refuses
    QApplication.sendEvent(widget, move)
    if not move.isAccepted():
        return False
    QApplication.sendEvent(widget, QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, data, buttons, mods))
    for _ in range(5):
        QCoreApplication.processEvents()
    return True


def _page_texts(win):
    return [win.session.doc[n].get_text().strip() for n in range(win.session.doc.page_count)]


def test_a_pdf_dropped_on_the_empty_window_opens(win, tmp_path):
    a = _pdf(tmp_path, "a.pdf", 2)
    center = win.rect().center()
    target = win.childAt(center)  # the big gray page area
    assert _drop(target, target.mapFrom(win, center), [a])
    assert [d.session.path.name for d in win.documents] == ["a.pdf"]


def test_a_pdf_dropped_on_the_page_area_opens_in_a_tab(win, tmp_path):
    win.open_paths([_pdf(tmp_path, "a.pdf", 2)])
    b = _pdf(tmp_path, "b.pdf", 1)
    center = win.rect().center()
    target = win.childAt(center)
    assert _drop(target, target.mapFrom(win, center), [b])
    assert [d.session.path.name for d in win.documents] == ["a.pdf", "b.pdf"]


def test_a_pdf_dropped_between_pages_goes_in_between(win, tmp_path):
    win.open_paths([_pdf(tmp_path, "a.pdf", 2)])
    thumbs = win.thumbnails
    assert thumbs.isVisible() and thumbs.count() == 2
    r0, r1 = thumbs.visualItemRect(thumbs.item(0)), thumbs.visualItemRect(thumbs.item(1))
    gap = QPoint(r0.center().x(), (r0.bottom() + r1.top()) // 2)  # where the drop line is drawn
    assert thumbs.drop_row_at(gap) == 1
    assert _drop(thumbs.viewport(), gap, [_pdf(tmp_path, "c.pdf", 2)])
    assert _page_texts(win) == ["a.pdf page 1", "c.pdf page 1", "c.pdf page 2", "a.pdf page 2"]
    assert [d.session.path.name for d in win.documents] == ["a.pdf"]  # not opened as a tab
    # On the lower half of a page: after it.
    lower = QPoint(r0.center().x(), r0.center().y() + r0.height() // 4)
    assert thumbs.drop_row_at(lower) == 1
    win.undo_stack.undo()
    assert _page_texts(win) == ["a.pdf page 1", "a.pdf page 2"]
