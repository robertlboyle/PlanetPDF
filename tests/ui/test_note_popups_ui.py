"""Notes (user 2026-10-04): the Note tool works like the highlighter; the note's box is joined
to the words by a line, moves and resizes on the page, collapses ("–") and opens again with a
click on the highlight."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 120), "Highlight these words please", fontsize=14)
    doc.save(tmp_path / "doc.pdf")
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(tmp_path / "doc.pdf")
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def _notes(w):
    return [i for i in w.markup.items if i.has_note]


def _note_on_words(w, text="Look at this"):
    w.markup_ui.set_mode("markup")
    w.markup.set_tool(Kind.NOTE)
    w.view.shapeDrawn.emit("text", 0, (1, 2))  # ("these words", dragged with the Note tool)
    editor = w.markup.editor
    assert editor is not None and editor.note
    editor.setPlainText(text)
    editor.finish()
    QCoreApplication.processEvents()
    [item] = _notes(w)
    return item


def _viewport_point(w, popup, local: QPointF) -> QPoint:
    return w.view.mapFromScene(popup.parentItem().mapToScene(local))


def test_note_tool_highlights_and_opens_a_box_joined_by_a_line(win):
    item = _note_on_words(win)
    assert item.kind is Kind.HIGHLIGHT and item.text == "Look at this" and item.popup_open
    assert win.undo_stack.undoText().startswith("Add")
    popup = win.view._popup_items[item.item_id]
    assert popup.line.line().length() > 5  # (the line from the box to the words)
    assert "Look at this" == popup.data_.text


def test_move_resize_collapse_and_open_with_the_mouse(win):
    item = _note_on_words(win)
    popup = win.view._popup_items[item.item_id]
    start = QRectF(popup.rect())
    # drag the header: the box moves (one undo step)
    grab = popup.header_rect().center() - QPointF(30, 0)
    QTest.mousePress(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, grab))
    QTest.mouseMove(win.view.viewport(), _viewport_point(win, popup, grab + QPointF(-60, 80)))
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _viewport_point(win, popup, grab + QPointF(-60, 80)))
    assert win.undo_stack.undoText() == "Move Note"
    popup = win.view._popup_items[item.item_id]
    assert popup.rect().top() > start.top() + 40 and popup.rect().width() == pytest.approx(start.width(), abs=1)
    # the corner grip: bigger
    corner = popup.grip_rect().center()
    QTest.mousePress(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, corner))
    QTest.mouseMove(win.view.viewport(), _viewport_point(win, popup, corner + QPointF(40, 30)))
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _viewport_point(win, popup, corner + QPointF(40, 30)))
    assert win.undo_stack.undoText() == "Resize Note"
    assert win.view._popup_items[item.item_id].rect().width() > start.width() + 20
    win.undo_stack.undo()
    win.undo_stack.undo()
    assert win.view._popup_items[item.item_id].rect().top() == pytest.approx(start.top(), abs=1)
    # "–": collapsed (not an edit: no undo step)
    steps = win.undo_stack.count()
    popup = win.view._popup_items[item.item_id]
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, popup.button_rect().center()))
    assert item.item_id not in win.view._popup_items and not _notes(win)[0].popup_open
    assert win.undo_stack.count() == steps
    # outside Comment mode, a click on the highlight opens it again
    win.markup_ui.set_mode("form")
    QCoreApplication.processEvents()
    page = win.view._items[0]
    words = win.view._anchor_items[0].rect().center()
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     win.view.mapFromScene(page.mapToScene(words)))
    assert item.item_id in win.view._popup_items and _notes(win)[0].popup_open


def test_double_click_edits_the_note_in_its_box(win):
    item = _note_on_words(win)
    popup = win.view._popup_items[item.item_id]
    QTest.mouseDClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      _viewport_point(win, popup, popup.body_rect().center()))
    editor = win.markup.editor
    assert editor is not None and editor.toPlainText() == "Look at this"
    assert item.item_id not in win.view._popup_items  # (the box steps aside while typing)
    editor.setPlainText("Changed my mind")
    editor.finish()
    [item] = _notes(win)
    assert item.text == "Changed my mind" and item.popup_open
    assert win.view._popup_items[item.item_id].data_.text == "Changed my mind"


def test_a_click_away_from_text_makes_a_sticky_note(win):
    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.NOTE)
    win.view.shapeDrawn.emit("point", 0, QRectF(400, 500, 0, 0))
    win.markup.editor.setPlainText("A sticky note")
    win.markup.editor.finish()
    [item] = _notes(win)
    assert item.kind is Kind.NOTE and item.popup_open and item.item_id in win.view._popup_items

def test_click_edits_corners_resize_and_the_box_fits_its_text(win):
    """User 2026-10-04: a click in the text edits it, any corner resizes, the edge moves it;
    the box grows with its text; the Note tool's cursor is the I-beam."""
    from planetpdf.ui.viewer.note_popups import fitted_height

    assert Kind.NOTE in __import__("planetpdf.ui.markup_controller", fromlist=["TEXT_TOOLS"]).TEXT_TOOLS
    long_text = "A long note that goes on and on, " * 8
    item = _note_on_words(win, long_text.strip())
    popup = win.view._popup_items[item.item_id]
    assert popup.rect().height() >= fitted_height(popup.rect().width(), item.text) - 0.5  # (all its text shows)
    assert popup.part_at(popup.rect().topLeft() + QPointF(2, 2)) == "tl"
    assert popup.part_at(popup.rect().bottomLeft() + QPointF(2, -2)) == "bl"
    assert popup.part_at(QPointF(popup.rect().left() + 2, popup.rect().center().y())) == "move"  # (the edge)
    assert popup.part_at(popup.body_rect().center()) == "text"
    # the top-left corner: bigger up and to the left, the bottom-right corner stays
    start = QRectF(popup.rect())
    corner = popup.rect().topLeft() + QPointF(2, 2)
    QTest.mousePress(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, corner))
    QTest.mouseMove(win.view.viewport(), _viewport_point(win, popup, corner - QPointF(30, 20)))
    QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       _viewport_point(win, popup, corner - QPointF(30, 20)))
    assert win.undo_stack.undoText() == "Resize Note"
    popup = win.view._popup_items[item.item_id]
    assert popup.rect().left() < start.left() - 20 and popup.rect().right() == pytest.approx(start.right(), abs=1)
    # one click in the text: editing it
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, popup.body_rect().center()))
    assert win.markup.editor is not None and win.markup.editor.toPlainText() == item.text
    win.markup.editor.cancel()

def test_the_first_click_away_only_finishes_typing(win):
    """User 2026-10-04: with the Note tool still on, the click that ended the typing started
    a new comment. Now it only finishes the typing; the next click moves the box."""
    win.markup_ui.set_mode("markup")
    win.markup.set_tool(Kind.NOTE)
    win.view.shapeDrawn.emit("text", 0, (1, 2))
    win.markup.editor.setPlainText("Typed")
    page = win.view._items[0]
    away = win.view.mapFromScene(page.mapToScene(QPointF(300, 600)))
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, away)
    assert win.markup.editor is None and [i.text for i in _notes(win)] == ["Typed"]
    assert win.markup.tool is Kind.NOTE  # (the tool stays on: the next click is the tool's)

def test_reply_to_a_note_edit_and_delete_the_reply(win):
    """User 2026-10-04 (Acrobat screenshot): replies to a comment, in its box."""
    item = _note_on_words(win)
    popup = win.view._popup_items[item.item_id]
    assert popup.part_at(popup.reply_rect().center()) == "reply"
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, popup.reply_rect().center()))
    editor = win.markup.editor
    assert editor is not None and editor.placeholderText() == "Type your reply…"
    editor.setPlainText("Agreed")
    editor.finish()
    assert win.undo_stack.undoText() == "Reply"
    [item] = _notes(win)
    assert [r.text for r in item.replies] == ["Agreed"]
    popup = win.view._popup_items[item.item_id]
    reply_id = item.replies[0].item_id
    block = next(r for i, kind, r in popup.blocks() if i == reply_id and kind == "text")
    assert popup.text_target(block.center()) == reply_id and popup.text_target(popup.body_rect().topLeft()) == item.item_id
    # the panel lists it under its comment
    card = win.comments_panel.card(item.item_id)
    assert [w.label.text() for w in card.findChildren(type(card.words)) if w.objectName() == reply_id] == ["Agreed"]
    # a click on the reply's words: edit them
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _viewport_point(win, popup, block.center()))
    assert win.markup.editor.toPlainText() == "Agreed"
    win.markup.editor.setPlainText("Agreed, thanks")
    win.markup.editor.finish()
    assert [r.text for r in _notes(win)[0].replies] == ["Agreed, thanks"]
    win.markup.delete(reply_id)
    assert _notes(win)[0].replies == ()
    win.undo_stack.undo()
    win.undo_stack.undo()
    assert [r.text for r in _notes(win)[0].replies] == ["Agreed"]
    # the comment deleted: its replies go too, and come back with it
    win.markup.delete(item.item_id)
    assert _notes(win) == []
    win.undo_stack.undo()
    assert [r.text for r in _notes(win)[0].replies] == ["Agreed"]

def test_the_author_changes_only_when_the_words_are_edited(win):
    """User 2026-10-04: "only change the author if I have edited the comment. If I add to
    it, it should keep whatever previous attribution even if blank"."""
    from planetpdf.core import markup as K

    item = _note_on_words(win, "Original")
    spec = K.replace(item.spec, author="")  # (a comment with no author)
    win.markup._window.push_command(__import__("planetpdf.ui.commands", fromlist=["x"]).ReplaceMarkupCommand(
        win, item.spec, spec, "Clear author"))
    [item] = _notes(win)
    assert item.author == ""
    win.markup.reply_to(item.item_id)  # a reply: the comment keeps its (blank) author
    win.markup.editor.setPlainText("A reply")
    win.markup.editor.finish()
    [item] = _notes(win)
    assert item.author == "" and item.replies[0].author == win.settings.author()
    popup = win.view._popup_items[item.item_id]  # moved: still blank
    win.markup.move_popup(item.item_id, popup.rect().translated(0, 40))
    assert _notes(win)[0].author == ""
    win.markup.set_text(item.item_id, "Edited words")  # its words edited: signed by me
    assert _notes(win)[0].author == win.settings.author()
    win.undo_stack.undo()
    assert _notes(win)[0].author == "" and _notes(win)[0].text == "Original"

def test_comments_in_the_panel_and_jumping_both_ways(win):
    """User 2026-10-04 (Acrobat screenshots): comments shown in the panel on the right or in
    boxes on the page; a click on the highlight goes to its comment, a click on the comment
    goes to the highlight in the document."""
    item = _note_on_words(win, "In the panel")
    win.markup.set_comments_in_panel(True)
    assert win.view._popup_items == {} and win._comments_dock.isVisible()
    panel = win.comments_panel
    card = panel.card(item.item_id)
    assert card is not None and "In the panel" in card.words.label.text()
    # a click on the highlight (outside Comment mode): its card, selected, with a reply box
    win.markup_ui.set_mode("form")
    QCoreApplication.processEvents()
    page = win.view._items[0]
    QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     win.view.mapFromScene(page.mapToScene(win.view._anchor_items[0].rect().center())))
    assert panel.selected_id() == item.item_id and panel.card(item.item_id).reply_box.isVisibleTo(panel)
    # Post a reply from the card
    card = panel.card(item.item_id)
    card.reply_text.setPlainText("Panel reply")
    card.post.click()
    assert [r.text for r in _notes(win)[0].replies] == ["Panel reply"]
    # a click on the card: the document goes to the highlight and marks it
    shown = []
    win.view.show_area = lambda page_index, rect, **kw: shown.append((page_index, rect))
    panel.card_clicked(item.item_id)
    assert shown and shown[0][0] == 0
    # back to boxes on the page
    win.markup.set_comments_in_panel(False)
    win.markup.jump_to(item.item_id)
    assert item.item_id in win.view._popup_items
