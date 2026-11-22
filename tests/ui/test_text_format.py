"""The text format bar (user 2026-10-03): font, size, bold, italic, underline, strike-out
and color for text being added, added text that's selected, and page text in Edit PDF;
the bar under the tool bar shows only while there's text to format."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF
from PySide6.QtWidgets import QApplication

from planetpdf.core import content_edit as E
from planetpdf.core.markup import Kind
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 300), "Page text in Helvetica", fontsize=16)
    doc.save(tmp_path / "page.pdf")
    win = MainWindow(tmp_path / "profile")
    win.resize(1200, 850)
    win.show()
    win.open_file(tmp_path / "page.pdf")
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _texts(win):
    return [i for i in win.markup.items if i.kind is Kind.TEXT]


def _start_typing(win, at=QPointF(100, 100)):
    win.act_add_placed_text.trigger()
    win.view.shapeDrawn.emit("point", 0, QRectF(at, at))
    return win.markup.editor


def test_the_bar_shows_only_while_there_is_text_to_format(window):
    window.text_format.update()
    assert not window.text_format_bar.isVisibleTo(window)
    window.act_add_placed_text.trigger()
    window.text_format.update()
    assert window.text_format_bar.isVisibleTo(window)
    window.markup.set_tool(None)
    window.text_format.update()
    assert not window.text_format_bar.isVisibleTo(window)


def test_font_lists_fill_on_first_use_and_follow_the_preview_preference(window, tmp_path):
    """User 2026-10-07: the lists are filled the first time they're needed (faster start),
    and Preferences ▸ "Show fonts in their own typeface in font lists" is kept between starts."""
    from PySide6.QtCore import Qt

    from planetpdf.core.storage.settings import load_settings
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    bar = window.text_format_widget
    assert window.settings.font_previews
    window.act_add_placed_text.trigger()
    window.text_format.update()
    box = bar.font_box
    assert box.count() > 3 and box.currentText() == "Helvetica"
    helvetica = box.findData("Helvetica")
    assert box.itemData(helvetica, Qt.ItemDataRole.FontRole) is not None  # (in its own typeface)

    dialog = PreferencesDialog(window)
    assert dialog.font_previews.isChecked()
    dialog.font_previews.setChecked(False)
    dialog.check_on_start.setChecked(False)
    dialog.update_days.setValue(30)
    dialog._save()
    assert box.itemData(box.findData("Helvetica"), Qt.ItemDataRole.FontRole) is None  # just the names now
    assert box.currentText() == "Helvetica"
    saved = load_settings(window.profile.settings_dir)
    assert (saved.font_previews, saved.check_updates_on_start, saved.update_every_days) == (False, False, 30)
    again = PreferencesDialog(window)
    assert not again.font_previews.isChecked() and not again.check_on_start.isChecked()
    assert again.update_days.value() == 30
    again.update_days.setValue(0)
    assert again.update_days.text() == "Never"
    window.markup.set_tool(None)


def test_the_format_chosen_before_and_while_typing_is_the_text_s_format(window):
    window.act_add_placed_text.trigger()
    window.text_format.apply({"font": "Noto Serif"})  # (before typing: the next text)
    window.text_format.apply({"font_size": 20})
    editor = _start_typing(window)
    assert window.text_format.target()[0] == "typing"
    editor.setPlainText("Better than Acrobat")
    window.text_format.apply({"bold": True})  # (while typing: the editor shows it at once)
    window.text_format.apply({"underline": True})
    assert editor.font().bold() and editor.font().underline()
    assert editor.font().pixelSize() == 20
    editor.finish()
    [item] = _texts(window)
    style = item.spec.style
    assert (style.font, style.font_size, style.bold, style.underline, style.italic) == ("Noto Serif", 20, True, True, False)
    # the next text starts the same way (Acrobat keeps the last format)
    assert window.settings.text_format["font"] == "Noto Serif" and window.settings.markup_font_size == 20


def test_clicking_the_bar_while_typing_keeps_typing(window, monkeypatch):
    from PySide6.QtWidgets import QApplication as App

    editor = _start_typing(window)
    editor.setPlainText("Still typing")
    monkeypatch.setattr(App, "focusWidget", staticmethod(lambda: window.text_format_widget.font_box))
    editor._finish_unless_formatting()  # (focus went to the font list)
    assert window.markup.editor is editor and not _texts(window)
    monkeypatch.setattr(App, "focusWidget", staticmethod(lambda: window.view))
    editor._finish_unless_formatting()  # (a click on the page finishes it)
    assert [i.spec.text for i in _texts(window)] == ["Still typing"]


def test_selected_text_is_restyled_in_one_undo_step(window):
    editor = _start_typing(window)
    editor.setPlainText("Restyle me")
    editor.finish()
    window.markup.set_tool(None)
    [item] = _texts(window)
    window.markup.select(item.item_id)
    window.text_format.update()
    assert window.text_format.target() == ("placed", [item.item_id])
    assert window.text_format_widget.font_box.currentText() == "Helvetica"
    window.text_format.apply({"font": "Courier"})
    window.text_format.apply({"strikeout": True})
    [item] = _texts(window)
    assert (item.spec.style.font, item.spec.style.strikeout) == ("Courier", True)
    assert window.undo_stack.undoText() == "Format Text"
    window.undo_stack.undo()
    window.undo_stack.undo()
    [item] = _texts(window)
    assert (item.spec.style.font, item.spec.style.strikeout) == ("Helvetica", False)


def test_the_document_s_own_fonts_top_the_list_and_missing_letters_are_asked_about(qapp, tmp_path, monkeypatch):
    """User 2026-10-03: offer the fonts in the document ("in this document"); when the
    text needs letters the document's copy doesn't have, say so and offer the closest."""
    from tests.core.test_fonts import _document_font_pdf

    win = MainWindow(tmp_path / "profile")
    win.resize(1200, 850)
    win.show()
    win.open_file(_document_font_pdf(tmp_path / "own.pdf"))
    QCoreApplication.processEvents()
    try:
        editor = _start_typing(win)
        QCoreApplication.processEvents()  # (the bar follows on the next pass of the event loop)
        box = win.text_format_widget.font_box
        assert box.itemText(0) == "PlanetTest (in this document)" and box.itemData(0) == "PlanetTest"
        editor.setPlainText("pool HOLE")  # (all letters it has: no question)
        monkeypatch.setattr(win.text_format, "ask_missing_letters", lambda *a, **k: pytest.fail("asked"))
        win.text_format.apply({"font": "PlanetTest"})
        assert win.markup.typing_format["font"] == "PlanetTest"
        editor.finish()
        assert [i.spec.style.font for i in _texts(win)] == ["PlanetTest"]
        # other text, then the document's font for it: "Hello pool" needs an "e" it hasn't got
        win.settings.text_format = {"font": "Courier"}
        editor = _start_typing(win, QPointF(100, 300))
        editor.setPlainText("Hello pool")
        editor.finish()
        win.markup.set_tool(None)
        [item] = [i for i in _texts(win) if i.spec.text == "Hello pool"]
        win.markup.select(item.item_id)
        asked = []

        def answer(face, missing, *, can_keep):
            asked.append((missing, can_keep))
            return "keep"  # (the "e" in the closest font)

        monkeypatch.setattr(win.text_format, "ask_missing_letters", answer)
        win.text_format.apply({"font": "PlanetTest"})
        assert asked == [(["e"], True)]
        [item] = [i for i in _texts(win) if i.spec.text == "Hello pool"]
        assert item.spec.style.font == "PlanetTest"
        monkeypatch.setattr(win.text_format, "ask_missing_letters", lambda *a, **k: "closest")
        win.text_format.apply({"font": "Courier"})
        win.text_format.apply({"font": "PlanetTest"})
        [item] = [i for i in _texts(win) if i.spec.text == "Hello pool"]
        assert item.spec.style.font == "Helvetica"  # (PlanetTest's closest font)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_edit_pdf_formats_page_text_but_never_added_text_as_page_text(window):
    editor = _start_typing(window)
    editor.setPlainText("Added, not page text")
    editor.finish()
    window.markup_ui.set_mode("edit")
    QCoreApplication.processEvents()
    lines = [i for i in window.view._markup_items if i.startswith("txt:")]
    assert len(lines) == 1  # (MuPDF also reads annotations' text: it isn't page text)
    window.view.set_group(lines)
    window.text_format.update()
    kind, _what = window.text_format.target()
    assert kind == "lines"
    bar = window.edit_panel.format_bar
    assert bar.font_box.currentText() == "Helvetica" and not bar.buttons["strikeout"].isEnabled()
    window.text_format.apply({"font": "Noto Serif"})
    [line] = E.text_lines(window.session, 0)
    assert line.text == "Page text in Helvetica" and "Noto" in line.font and "Serif" in line.font
    assert window.undo_stack.undoText() == "Format Text"

def _select(editor, start, end):
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def test_bold_and_italic_on_single_words(window):
    """User 2026-10-03: B / I / U / S on the selected words only."""
    editor = _start_typing(window)
    editor.setPlainText("plain bold plain")
    _select(editor, 6, 10)
    window.text_format.apply({"bold": True})
    assert window.text_format.current_format()[0]["bold"]  # (the bar shows the selected words' format)
    _select(editor, 0, 5)
    assert not window.text_format.current_format()[0]["bold"]
    window.text_format.apply({"italic": True})
    editor.finish()
    [item] = _texts(window)
    style = item.spec.style
    assert not style.bold and not style.italic
    assert style.runs == ((0, 5, False, True, False, False), (6, 10, True, False, False, False))
    assert "bold" not in window.settings.text_format  # (words only: not the next text's format)
    # editing it again shows the formats, and keeps them
    window.markup.edit_text(item.item_id)
    editor = window.markup.editor
    assert editor.char_formats()[7] == (True, False, False, False)
    editor.setPlainText(editor.toPlainText())  # (no change of format)
    editor.set_runs((False,) * 4, style.runs)
    editor.finish()
    assert _texts(window)[0].spec.style.runs == style.runs


def test_text_boxes_in_any_font(window):
    """User 2026-10-03: Comment text boxes kept Helvetica."""
    from planetpdf.core import markup as K

    window.markup_ui.set_mode("markup")
    window.markup.set_tool(Kind.TEXTBOX, borderless=False)
    assert window.text_format.target() == ("next", Kind.TEXTBOX)
    window.text_format.apply({"font": "Times Roman"})
    assert window.settings.textbox_format["font"] == "Times Roman" and "font" not in window.settings.text_format
    window.view.shapeDrawn.emit("box", 0, QRectF(100, 400, 200, 40))
    editor = window.markup.editor
    assert window.text_format.target()[0] == "typing"
    editor.setPlainText("Times text box with a bold word")
    _select(editor, 23, 27)
    window.text_format.apply({"bold": True})
    editor.finish()
    [box] = [i for i in window.markup.items if i.kind is Kind.TEXTBOX]
    assert box.spec.style.font == "Times Roman" and box.spec.style.runs == ((23, 27, True, False, False, False),)
    assert window.session.doc.xref_get_key(box.xref, "RC")[0] != "null"
    # selected: the bar formats the whole box (one undo step)
    window.view.set_selected_markup(box.item_id)
    assert window.text_format.target() == ("placed", [box.item_id])
    window.text_format.apply({"italic": True})
    [box] = [i for i in window.markup.items if i.kind is Kind.TEXTBOX]
    assert box.spec.style.italic and box.spec.style.runs == ((23, 27, True, True, False, False),)
    window.undo_stack.undo()
    [box] = [i for i in window.markup.items if i.kind is Kind.TEXTBOX]
    assert not box.spec.style.italic
    window.markup.set_tool(None)
