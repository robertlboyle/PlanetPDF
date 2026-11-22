"""User bug 2026-10-01: in the two-page view a Sign Here / Initial Here stamp sometimes
showed its preview but never appeared. The preview (a child of the page it was last
over) hung over the neighboring page, the click was measured against the wrong page and
the stamp landed off that page's edge, out of sight."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind
from planetpdf.core.stamps import builtin
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.page_view import LayoutMode


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1400, 900)
    w.show()
    w.open_file(pdf_copy("text.pdf"))  # 3 Letter pages
    w.view.set_layout(LayoutMode.TWO_UP)
    w.view.fit_page()
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def test_click_goes_to_the_page_under_the_mouse_not_the_ghosts_page(win):
    view = win.view
    left, right = view._items[0], view._items[1]
    win.markup.place_stamp(builtin("sign-here").stamp_id)
    # the preview was last drawn on the right-hand page, hanging over the left one
    view._place_ghost(right, QRectF(-160, 600, 220, 40))
    target = right.mapToScene(QPointF(-90, 620))  # inside the left-hand page
    assert left.mapRectToScene(left.boundingRect()).contains(target)
    assert view._page_at(target) is left
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(target))
    QCoreApplication.processEvents()
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    assert item.page == 0
    page = win.session.doc[0]
    assert page.rect.contains(pymupdf.Rect(item.spec.rect))  # on the page, visible


def test_a_stamp_is_never_placed_off_the_page(win):
    win.markup.place_stamp(builtin("initial-here").stamp_id)
    win.view.shapeDrawn.emit("place", 1, QRectF(-150, 700, 2, 2))  # a click right at the edge
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    assert win.session.doc[1].rect.contains(pymupdf.Rect(item.spec.rect))
