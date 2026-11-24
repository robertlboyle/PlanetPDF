"""With Fill & Sign's Add Text tool still on, text already added is selected, moved, made bigger
or smaller, and retyped by clicking it, as in Edit PDF (user 2026-11-24: "After adding text in
signature mode, I should still be able to edit anything I have added … Now once it is typed, I
need to switch to edit mode to be able to modify the text")."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind, list_markup
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "blank.pdf")
    doc.close()
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(tmp_path / "blank.pdf")
    w.activateWindow()
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _vp(win, x, y):
    return win.view.mapFromScene(win.view._items[0].mapToScene(QPointF(x, y)))


def _texts(win):
    return [i for i in list_markup(win.session) if i.kind is Kind.TEXT]


def _add(win, qapp, x, y, text):
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=_vp(win, x, y))
    QCoreApplication.processEvents()
    assert win.view.editor is not None
    QTest.keyClicks(qapp.focusWidget(), text)
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Return)
    QCoreApplication.processEvents()


def test_added_text_is_grabbed_moved_resized_and_retyped_with_the_tool_on(win, qapp):
    win.markup.set_tool(Kind.TEXT)
    _add(win, qapp, 100, 200, "First")
    _add(win, qapp, 100, 400, "Second")
    assert win.markup.tool is Kind.TEXT  # (the tool stays on)
    first = next(i for i in _texts(win) if i.spec.text == "First")
    overlay = win.view._markup_items[first.item_id]
    center = overlay.rect().center()

    # a click on it selects it (no new text)
    win.view.set_selected_markup(None)
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=_vp(win, center.x(), center.y()))
    QCoreApplication.processEvents()
    assert win.view.editor is None
    assert win.view.selected_markup == first.item_id
    assert len(_texts(win)) == 2

    # dragged: it moves, still one text
    vp = win.view.viewport()
    win.view.set_selected_markup(None)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(win, center.x(), center.y()))
    for n in range(1, 6):
        QTest.mouseMove(vp, _vp(win, center.x() + 60 * n / 5, center.y() + 30 * n / 5))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _vp(win, center.x() + 60, center.y() + 30))
    QCoreApplication.processEvents()
    moved = next(i for i in _texts(win) if i.spec.text == "First")
    assert len(_texts(win)) == 2
    assert moved.spec.rect[0] == pytest.approx(first.spec.rect[0] + 60, abs=1)
    assert win.markup.tool is Kind.TEXT

    # A+ makes it bigger
    size = moved.spec.style.font_size
    assert win.view.selected_markup == moved.item_id  # (still selected after the move)
    assert win.markup.change_text_size(win.view.selected_markup, +1)
    QCoreApplication.processEvents()
    bigger = next(i for i in _texts(win) if i.spec.text == "First")
    assert bigger.spec.style.font_size > size
    moved = bigger

    # a double-click retypes it
    center = win.view._markup_items[moved.item_id].rect().center()
    QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=_vp(win, center.x(), center.y()))
    QCoreApplication.processEvents()
    assert win.view.editor is not None
    assert len(_texts(win)) == 2


def test_a_click_on_empty_page_still_adds_text(win, qapp):
    win.markup.set_tool(Kind.TEXT)
    _add(win, qapp, 100, 200, "One")
    _add(win, qapp, 300, 600, "Two")
    assert sorted(i.spec.text for i in _texts(win)) == ["One", "Two"]


def test_arrow_keys_and_delete_work_on_it_with_the_tool_on(win, qapp):
    win.markup.set_tool(Kind.TEXT)
    _add(win, qapp, 100, 200, "Nudge me")
    item = _texts(win)[0]
    center = win.view._markup_items[item.item_id].rect().center()
    win.view.set_selected_markup(None)
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=_vp(win, center.x(), center.y()))
    QCoreApplication.processEvents()
    assert win.view.selected_markup == item.item_id
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Right)
    QCoreApplication.processEvents()
    assert _texts(win)[0].spec.rect[0] == pytest.approx(item.spec.rect[0] + 1, abs=0.2)
    QTest.keyClick(win.view, Qt.Key.Key_Delete)
    QCoreApplication.processEvents()
    assert _texts(win) == []
    assert win.markup.tool is Kind.TEXT
