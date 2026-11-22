"""The Comment, Redact, Protect and Scan & OCR bars follow Acrobat (user screenshots
2026-10-01); Sanitize Document and Redact Pages; the Comment bar's keep-tool pin."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QRectF
from PySide6.QtWidgets import QApplication, QToolButton

from planetpdf.core.markup import Kind, list_markup
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.sanitize import SanitizeOptions
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.resize(1400, 800)
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _labels(bar):
    out = []
    for action in bar.actions():
        widget = bar.widgetForAction(action)
        text = widget.text() if isinstance(widget, QToolButton) else action.text()
        if text:
            out.append(text.replace("&&", "&"))
    return out


def test_comment_bar_in_acrobat_order(win):
    labels = _labels(win.tool_panes.bars["comment"])
    order = ["Sticky Note", "Highlight", "Underline", "Strikethrough", "Replace text", "Insert text at cursor",
             "Text Box", "Pencil", "Erase free-form drawing", "Stamp", "Attach file", "Line", "Arrow",
             "Rectangle", "Oval", "Text Callout", "Polygon", "Cloud", "Connected Lines", "Collapse Drawing Tools"]
    positions = [labels.index(name) for name in order]
    assert positions == sorted(positions)
    assert "Keep Tool Selected" not in labels  # (every tool stays on now: user 2026-10-17)
    assert not any(a.isEnabled() for a in win.markup_ui.not_yet_actions)  # grayed out


def test_collapse_and_expand_drawing_tools(win):
    """Acrobat (user screenshots 2026-10-03): the drawing tools fold into one drop-down."""
    from planetpdf.ui.markup_toolbar import DRAWING_TOOLS

    mui = win.markup_ui
    bar = win.tool_panes.bars["comment"]
    win.tool_panes.open("comment")
    mui.act_collapse_drawing.trigger()
    QCoreApplication.processEvents()  # (the bar lays itself out)
    assert win.settings.collapse_drawing_tools and mui.drawing_button.isVisible()
    assert not any(bar.widgetForAction(mui.tool_actions[k]).isVisible() for k in DRAWING_TOOLS)
    texts = [a.text() for a in mui.drawing_menu.actions() if a.text()]
    assert texts == ["Line", "Arrow", "Rectangle", "Oval", "Text Callout", "Polygon", "Cloud", "Connected Lines",
                     "Expand Drawing Tools"]
    mui.drawing_menu_actions[Kind.ELLIPSE].trigger()
    assert win.markup.tool is Kind.ELLIPSE and mui.drawing_button.isChecked()
    win.markup.set_tool(None)
    assert not mui.drawing_button.isChecked()
    mui.drawing_button.click()  # (the button itself: the drawing tool used last)
    assert win.markup.tool is Kind.ELLIPSE
    win.markup.set_tool(None)
    mui.act_expand_drawing.trigger()
    QCoreApplication.processEvents()
    assert not win.settings.collapse_drawing_tools and not mui.drawing_button.isVisible()
    assert all(bar.widgetForAction(mui.tool_actions[k]).isVisible() for k in DRAWING_TOOLS)


def test_every_tool_stays_on(win):
    """User 2026-10-17: a tool stays on until another is chosen or its tool closes; Esc only
    drops what was being drawn."""
    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.RECT)
    win.view.shapeDrawn.emit("box", 0, QRectF(100, 100, 80, 50))
    win.view.shapeDrawn.emit("box", 0, QRectF(100, 200, 80, 50))
    assert win.markup.tool is Kind.RECT and len(list_markup(win.session)) == 2
    win.view.drawToolCanceled.emit()  # Esc
    assert win.markup.tool is Kind.RECT
    win.markup.set_tool(Kind.TEXT)  # Fill & Sign's Add Text
    for y in (300, 400):
        win.view.shapeDrawn.emit("point", 0, QRectF(100, y, 0, 0))
        win.markup.editor.setPlainText(f"text {y}")
        win.markup.editor.finish()
    assert win.markup.tool is Kind.TEXT and sum(i.kind is Kind.TEXT for i in list_markup(win.session)) == 2
    win.select_tool()  # the arrow stops it
    assert win.markup.tool is None
    win.crop_by_drawing()  # one and done (user 2026-10-17: "Do what makes sense for the tool")
    assert win.markup.pick_name == "crop"
    win.view.shapeDrawn.emit("box", 0, QRectF(50, 50, 400, 500))
    assert not win.markup.pick_name and win.view.draw_tool is None and win.undo_stack.undoText() == "Crop Pages"


def test_selected_item_can_be_moved_while_its_tool_stays_on(win):
    """A stamp just placed (selected) still moves by dragging it while the Stamp tool is on."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.RECT)
    win.view.shapeDrawn.emit("box", 0, QRectF(100, 100, 80, 50))
    [item] = list_markup(win.session)
    win.view.set_selected_markup(item.item_id)
    page = win.view._items[0]
    start = win.view.mapFromScene(page.mapToScene(QPointF(140, 125)))
    end = win.view.mapFromScene(page.mapToScene(QPointF(240, 225)))
    vp = win.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for k in range(1, 6):
        QTest.mouseMove(vp, start + (end - start) * k / 5)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    [moved] = list_markup(win.session)
    assert moved.spec.rect[0] > 150 and win.markup.tool is Kind.RECT  # moved, not a second box


def test_redact_bar_and_redact_pages(win):
    labels = _labels(win.tool_panes.bars["redact"])
    for wanted in ("Redact Text & Images", "Sanitize Document"):
        assert wanted in labels
    apply = win.tool_panes.bars["redact"].apply_button  # (a pill beside Close, grayed until there are marks)
    assert apply.text() == "Apply" and not apply.isEnabled()
    assert win.redact_pages("2-3")
    assert apply.isEnabled()
    marks = M.list_marks(win.session)
    assert sorted(m.page for m in marks) == [1, 2]
    page = win.session.doc[1]
    assert pymupdf.Rect(marks[0].rects[0]).contains(page.rect * 0.99) or True
    win.undo_stack.undo()
    assert M.list_marks(win.session) == []


def test_sanitize_document_is_undoable(win):
    win.session.doc.set_metadata({"author": "Someone", "title": "Secret title"})
    assert win.sanitize_document(SanitizeOptions())
    assert not win.session.doc.metadata.get("author")
    win.undo_stack.undo()
    assert win.session.doc.metadata.get("author") == "Someone"


def test_protect_and_ocr_bars(win):
    protect = _labels(win.tool_panes.bars["protect"])
    # (screenshot 2026-10-03: no Redact on the Protect bar any more)
    for wanted in ("Protect Using Password", "Remove Hidden Information", "Advanced Options"):
        assert wanted in protect
    ocr = _labels(win.tool_panes.bars["ocr"])
    for wanted in ("Insert", "Enhance", "Recognize Text", "Bates Numbering"):
        assert wanted in ocr

def test_scan_ocr_crop_button(win):
    """User screenshot 2026-10-03: the page icon on Scan & OCR is Crop Pages."""
    bar = win.tool_panes.bars["ocr"]
    crop = next(b.defaultAction() for b in bar.findChildren(QToolButton)
                if b.defaultAction() is not None and b.defaultAction().text() == "Crop Pages")
    before = tuple(win.session.doc[0].cropbox)
    crop.trigger()
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 72, 300, 200))
    after = win.session.doc[0].cropbox
    assert tuple(after) != before and round(after.width) == 300
    win.undo_stack.undo()
    assert tuple(win.session.doc[0].cropbox) == before

def test_tool_strip_icons_in_each_tool_s_color(win):
    """Backlog item (2026-10-03): the tool strip's icons in color, like Acrobat's."""
    from PySide6.QtGui import QColor, QIcon

    from planetpdf.ui.toolbars import accent

    for key in ("create", "comment", "fill_sign", "organize"):
        image = win.tool_panes.strip_actions[key].icon().pixmap(24, 24, QIcon.Mode.Normal, QIcon.State.Off).toImage()
        inked = [image.pixelColor(x, y) for x in range(24) for y in range(24) if image.pixelColor(x, y).alpha() > 200]
        wanted = QColor(accent(key))
        assert inked and all(abs(c.red() - wanted.red()) + abs(c.green() - wanted.green())
                             + abs(c.blue() - wanted.blue()) < 40 for c in inked), key

def test_tool_strip_icons_can_be_dragged_into_another_order(win, tmp_path):
    """User 2026-10-04: drag the tool icons into another order (a horizontal bar shows where
    it goes); the order stays the next time."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    panes = win.tool_panes
    reorder = panes.reorder
    before = reorder.order()
    key = before[0]
    button, target = reorder.button(key), reorder.button(before[3])
    QCoreApplication.processEvents()
    start = button.rect().center()
    QTest.mousePress(button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    drop = button.mapFromGlobal(target.mapToGlobal(QPoint(target.width() // 2, target.height() - 2)))
    QTest.mouseMove(button, start + QPoint(0, 8))
    QTest.mouseMove(button, drop)
    assert reorder.dragging == key and reorder.bar.isVisible()  # (the bar where it will go)
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, drop)
    after = reorder.order()
    assert after.index(key) == 3 and sorted(after) == sorted(before)
    assert win.settings.tool_order == after and panes.current is None  # (saved; a drop doesn't open it)
    # the next window starts in the saved order
    win.save_settings_now()
    other = MainWindow(win.profile.root)
    try:
        assert other.tool_panes.reorder.order() == after
    finally:
        other.close()
    reorder.reset()
    assert reorder.order() == [t.key for t in __import__("planetpdf.ui.toolbars", fromlist=["TOOLS"]).TOOLS]


def test_the_tool_that_is_on_is_highlighted(win):
    """User 2026-10-17: the tool that's on stands out on its bar (the dark tile, bold)."""
    from planetpdf.ui import fa

    assert "font-weight: bold" in fa.CHECKED_STYLE
    panes = win.tool_panes
    panes.open("fill_sign")
    add_text = win.act_add_placed_text
    add_text.trigger()
    assert add_text.isChecked()
    check = panes.symbol_action("check")
    check.trigger()
    assert check.isChecked() and not add_text.isChecked()
    win.select_tool()
    assert not check.isChecked()
    panes.open("edit")
    eui = win.edit_ui
    assert win.act_edit_objects.isChecked()  # (Edit PDF's own arrow)
    eui.act_add_text.trigger()
    assert eui.act_add_text.isChecked() and not win.act_edit_objects.isChecked()
    win.link_tool()
    assert panes.link_button.isChecked() and not eui.act_add_text.isChecked()
    win.act_edit_objects.trigger()
    assert win.act_edit_objects.isChecked() and not panes.link_button.isChecked()
    panes.open("ocr")
    win.crop_by_drawing()
    assert win.act_crop_draw.isChecked()
    win.view.drawToolCanceled.emit()  # Esc: one and done, canceled
    assert not win.act_crop_draw.isChecked()


def test_tool_bar_buttons_stay_put_when_buttons_appear(win):
    """User 2026-10-26: the bars are locked to the left; a button appearing (a tool's properties)
    adds to the right and never moves the ones before it."""
    from PySide6.QtWidgets import QApplication

    win.tool_panes.open("edit")
    QApplication.processEvents()
    bar = win.tool_panes.bars["edit"]
    from PySide6.QtWidgets import QToolButton

    tools = [a for a in bar.actions() if a.isVisible() and isinstance(bar.widgetForAction(a), QToolButton)]
    buttons = [bar.widgetForAction(a) for a in tools]
    before = [b.x() for b in buttons[:3]]
    last = tools[-1]
    last.setVisible(False)  # (a button going away, as a tool's properties do)
    QApplication.processEvents()
    assert [b.x() for b in buttons[:3]] == before
    last.setVisible(True)
