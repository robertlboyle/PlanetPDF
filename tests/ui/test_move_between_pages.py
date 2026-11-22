"""Drag an item onto another page (user request: Acrobat can't do this)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication

from planetpdf.core import markup as K
from planetpdf.core.forms import fields as F
from planetpdf.core.markup import Kind
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1000, 900)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _text(win, page=0, at=(100, 500), text="Moving words"):
    win.markup.add(K.placed_text(win.session, page, at, text, win.markup.style_for(Kind.TEXT)))
    return next(i for i in win.markup.items if i.kind is Kind.TEXT and i.spec.text == text)


def test_no_stray_buttons_over_the_menu_bar(window):
    """A button that isn't on a toolbar or panel is drawn in the window's corner (bug)."""
    stray = [b.text() for b in window.findChildren(QAbstractButton) if b.parentWidget() is window and b.isVisible()]
    assert stray == []


def test_mouse_drag_to_the_next_page(window):
    view = window.view
    view.set_zoom(0.35)  # pages 1 and 2 both on screen
    QCoreApplication.processEvents()
    item = _text(window)
    overlay = view._markup_items[item.item_id]
    start = view.mapFromScene(overlay.sceneBoundingRect().center())
    end = view.mapFromScene(view._items[1].sceneBoundingRect().center())
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for t in (0.25, 0.5, 0.75, 1.0):
        QTest.mouseMove(viewport, QPoint(int(start.x() + (end.x() - start.x()) * t), int(start.y() + (end.y() - start.y()) * t)))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    [moved] = [i for i in window.markup.items if i.kind is Kind.TEXT]
    assert moved.page == 1 and moved.item_id == item.item_id
    assert window.undo_stack.undoText() == "Move Text to Page 2"
    window.undo_stack.undo()
    assert [i.page for i in window.markup.items if i.kind is Kind.TEXT] == [0]


def test_signature_symbol_and_comment_move_to_page_3(window):
    window.markup.add(K.symbol(window.session, 0, (200, 200), "check", (0, 0, 1)))
    window.markup.add(K.MarkupSpec(K.new_id(), 0, Kind.RECT, (50, 50, 150, 100)))
    for item in [i for i in window.markup.items if i.kind in (Kind.SYMBOL, Kind.RECT)]:
        assert window.markup.move_to_page(item.item_id, 2, QRectF(300, 300, 40, 40))
    pages = {i.kind: i.page for i in window.markup.items}
    assert pages[Kind.SYMBOL] == 2 and pages[Kind.RECT] == 2


def test_to_a_rotated_page_stays_upright(window):
    window.session.doc[1].set_rotation(90)
    window.view.set_session(window.session)
    item = _text(window)
    window.markup.move_to_page(item.item_id, 1, QRectF(50, 60, 100, 20))
    [moved] = [i for i in window.markup.items if i.kind is Kind.TEXT]
    from planetpdf.core.redaction.marks import to_display

    shown = to_display(window.session.doc[1], moved.spec.rect)
    assert shown.width > shown.height and (shown.x0, shown.y0) == pytest.approx((50, 60))


def test_edit_pdf_image_and_prepare_form_field(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.show()
    path = pdf_copy("image.pdf")
    doc = pymupdf.open(path)
    doc.new_page()
    doc.saveIncr()
    doc.close()
    win.open_file(path)
    try:
        win.markup_ui.set_mode("edit")
        [image_id] = [k for k in win.view._markup_items if k.startswith("img:0:")]
        win.view.markupMovedToPage.emit(image_id, 1, QRectF(100, 100, 120, 120))
        assert win.edit._page_images(0) == [] and len(win.edit._page_images(1)) == 1
        win.undo_stack.undo()
        assert len(win.edit._page_images(0)) == 1 and win.edit._page_images(1) == []
        # Prepare Form: a field moves to the other page
        win.markup_ui.set_mode("design")
        from planetpdf.core.forms.fields import FieldKind

        win.edit.set_tool(FieldKind.TEXT)
        win.view.shapeDrawn.emit("box", 0, QRectF(72, 400, 2, 2))
        [fld] = [k for k in win.view._markup_items if k.startswith("fld:")]
        win.view.markupMovedToPage.emit(fld, 1, QRectF(72, 72, 160, 20))
        assert F.get_field(win.session, "Text1").widgets[0].page == 1
        win.undo_stack.undo()
        assert F.get_field(win.session, "Text1").widgets[0].page == 0
    finally:
        win.undo_stack.setClean()
        win.close()
