"""Screen readers (user 2026-10-25: "Make sure visually impaired or blind people can use the app").

Every control in the main window, every tool bar and panel, and every dialog has a name a
screen reader can say (``ui/accessibility.py``).
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QToolButton

import planetpdf.ui as ui_package
from planetpdf.ui import accessibility as A
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    window = MainWindow(tmp_path / "profile")
    window.resize(1300, 900)
    window.show()
    window.open_file(g.make_text_pdf(tmp_path / "text.pdf"))
    yield window
    for document in window.documents:  # (a test may open a second file)
        document.undo_stack.setClean()
    window.close()


def test_plain_names():
    assert A.plain("&Find…") == "Find"
    assert A.plain("Zoom In (Ctrl+=)") == "Zoom In"
    assert A.plain("Save && Close") == "Save & Close"
    assert A.plain("<b>Bold</b>\nmore") == "Bold"


def test_a_control_without_a_name_gets_one_when_shown(win, qapp):
    button = QToolButton(win.statusBar())
    button.setToolTip("Do Something (Ctrl+K)")
    edit = QLineEdit(win.statusBar())
    edit.setPlaceholderText("Search comments")
    button.show()
    edit.show()
    qapp.processEvents()
    assert A.spoken_name(button) == "Do Something"
    assert A.spoken_name(edit) == "Search comments"


def test_every_control_in_the_window_has_a_name(win, qapp):
    for key in list(win.tool_panes.bars.keys()):
        win.tool_panes.ensure(key)
    for dock in win.tool_panel_docks():
        dock.show()
    qapp.processEvents()
    A.name_all(win)  # (what showing each one does)
    assert [A.describe(w) for w in A.unnamed(win)] == []


# Arguments for building each dialog, by parameter name; dialogs needing real data
# (a comparison, a field spec…) are built in ``SPECIAL``.
def _arguments(win):
    return {"window": win, "parent": win, "page_count": 3, "selected": [0], "encrypted": False, "signed": False,
            "has_bookmarks": False, "ocr_available": False, "can_open": False, "current_size": 100_000,
            "file_size": 100_000, "metadata": {}, "facts": {}, "mark_count": 0, "pages_without_text": [0],
            "languages": ["eng"], "rules": [], "start_dir": "", "link": None, "title": "Files", "intro": "Choose",
            "blank": [1], "scanned": [0], "current": 0, "existing": 0, "sections": [], "certificates": [],
            "scanners": [], "entries": [], "path": Path(g.__file__), "error": "", "language": "eng", "dpi": 300}


def _special(win):
    from planetpdf.core import properties as PR
    from planetpdf.core.forms.design import FieldSpec
    from planetpdf.core.forms.fields import FieldKind
    from planetpdf.core.stamps import BUILTIN_STAMPS

    return {
        "FieldPropertiesDialog": lambda c: c(FieldSpec("Text1", FieldKind.TEXT, 0, (0, 0, 100, 20)), win),
        "StampEditorDialog": lambda c: c(win, BUILTIN_STAMPS[0], win),
        "PoliciesDialog": lambda c: c(win.security_ui),
        "PropertiesDialog": lambda c: c({}, PR.facts(win.session), win),
        "StampsPalette": lambda c: c(win.stamp_ui),
    }


# dialogs that can't be made in a test, and why
NOT_BUILT = {
    "CompareDialog": "needs two compared documents (its lists are named from their labels)",
    "SpellingDialog": "needs a spelling checker run", "DictionaryDialog": "needs a spelling checker run",
    "CorrectOcrDialog": "needs an OCR word list", "RecoveryDialog": "needs an auto-saver",
    "VerificationReportDialog": "needs a redaction result", "SpaceUsageDialog": "needs a space-usage report",
    "ExportDialog": "needs the export options", "SanitizeDialog": "needs the sanitize options",
    "ReduceSizeDialog": "needs the optimizer's preview",
}


def _dialog_classes():
    classes = {}
    for info in pkgutil.walk_packages(ui_package.__path__, "planetpdf.ui."):
        if "windows" in info.name or "mac" in info.name:
            continue
        module = importlib.import_module(info.name)
        for name, cls in vars(module).items():
            if inspect.isclass(cls) and issubclass(cls, QDialog) and cls.__module__ == module.__name__:
                classes[name] = cls
    return classes


def _built_dialogs(win, problems):
    """(name, dialog) for every dialog class that can be made here; the ones that can't are
    reported in ``problems``."""
    arguments, special = _arguments(win), _special(win)
    for name, cls in sorted(_dialog_classes().items()):
        if name in NOT_BUILT:
            continue
        try:
            if name in special:
                dialog = special[name](cls)
            else:
                params = [p for p in inspect.signature(cls.__init__).parameters.values() if p.name != "self"]
                values = {p.name: arguments[p.name] for p in params
                          if p.default is inspect.Parameter.empty or p.name in arguments}
                dialog = cls(**values)
        except Exception as exc:  # noqa: BLE001 (reported)
            problems.append(f"{name} couldn't be made ({exc!r}): add it to SPECIAL or NOT_BUILT")
            continue
        yield name, dialog
        dialog.deleteLater()


def test_every_dialog_has_names_for_its_controls(win, qapp):
    problems, built = [], 0
    for name, dialog in _built_dialogs(win, problems):
        built += 1
        A.name_all(dialog)
        problems += [f"{name}: {A.describe(w)}" for w in A.unnamed(dialog)]
    assert problems == [], "\n".join(problems)
    assert built >= 30


def tab_order_problems(dialog) -> list[str]:
    """Places where Tab goes back up and to the left (a jump a keyboard user can't follow);
    going up and to the right is a new column, which is fine."""
    from PySide6.QtCore import QPoint, Qt

    chain, widget = [], dialog.nextInFocusChain()
    while widget is not None and widget is not dialog and len(chain) < 500:
        if (widget.focusPolicy() & Qt.FocusPolicy.TabFocus and widget.isVisibleTo(dialog) and widget.isEnabled()
                and dialog.isAncestorOf(widget) and widget.width() > 0):
            chain.append(widget)
        widget = widget.nextInFocusChain()
    out = []
    for before, after in zip(chain, chain[1:]):
        if before.isAncestorOf(after) or after.isAncestorOf(before):
            continue
        a = before.mapTo(dialog, QPoint(0, 0))
        b = after.mapTo(dialog, QPoint(0, 0))
        if b.y() < a.y() - 4 and b.x() < a.x() - 4:  # (its top above, and to the left)
            out.append(f"{A.describe(before)} -> {A.describe(after)}")
    return out


def test_tab_goes_through_every_dialog_in_reading_order(win, qapp):
    problems = []
    for name, dialog in _built_dialogs(win, problems):
        dialog.show()
        qapp.processEvents()
        problems += [f"{name}: {p}" for p in tab_order_problems(dialog)]
        dialog.hide()
    assert problems == [], "\n".join(problems)


def test_thumbnails_say_which_page(win, qapp):
    from PySide6.QtCore import Qt

    thumbs = win.thumbnails
    assert thumbs.count() >= 1
    assert thumbs.item(0).data(Qt.ItemDataRole.AccessibleTextRole) == f"Page 1 of {thumbs.count()}"


def _headed_pdf(path):
    import pymupdf

    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 90), f"Chapter {n + 1}", fontsize=22)
        page.insert_textbox(pymupdf.Rect(72, 120, 540, 300), "This is the body text of the page. " * 6, fontsize=11)
    page = doc.new_page(width=612, height=792)  # a page with no text (a scan, say)
    page.insert_image(pymupdf.Rect(72, 72, 272, 272), pixmap=pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0))
    doc.save(path)
    return path


def test_the_page_area_says_which_page_and_turns_are_announced(win, qapp, tmp_path):
    A.ANNOUNCED.clear()
    win.open_file(_headed_pdf(tmp_path / "headed.pdf"))
    assert A.ANNOUNCED[-1] == "headed.pdf, 3 pages, page 1"
    assert A.spoken_name(win.view) == "Page 1 of 3"
    assert "Ctrl+4" in win.view.accessibleDescription()
    win.view.go_to_page(2)
    win._announce_page()  # (what the settle timer does)
    assert A.spoken_name(win.view) == "Page 3 of 3"
    assert A.ANNOUNCED[-1] == "Page 3 of 3"


def test_reflow_reads_headings_paragraphs_and_pictures(win, qapp, tmp_path):
    from planetpdf.ui.viewer.zoom_tools import reading_order

    win.open_file(_headed_pdf(tmp_path / "headed.pdf"))
    paragraphs = reading_order(win.session.doc[0])
    assert paragraphs[0] == ("h2", "Chapter 1")
    assert paragraphs[1][0] == "p" and paragraphs[1][1].startswith("This is the body text")
    assert reading_order(win.session.doc[2]) == [("p", "[Picture]")]
    win.act_reflow.trigger()
    reflow = win.central.currentWidget()
    assert reflow.hasFocus() or not win.isActiveWindow()
    assert A.spoken_name(reflow) == "Text of page 1 of 3"
    assert "<h2" in reflow.toHtml()
    win.view.go_to_page(2)
    assert A.spoken_name(reflow) == "Text of page 3 of 3"
    assert "Recognize Text" in reflow.toPlainText()  # (no words: says how to get them)
    win.act_reflow.trigger()


def test_a_form_can_be_filled_from_the_panel_with_the_keyboard(win, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win.open_file(g.make_acroform_pdf(tmp_path / "form.pdf"))
    panel = win.forms_panel
    names = [panel.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(panel.tree.topLevelItemCount())]
    values = lambda: {f.name: f.value for f in win.forms.fields}  # noqa: E731

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("full_name")))
    assert panel.editor() is panel.text_edit
    assert A.spoken_name(panel.text_edit) == "full_name, Text"
    panel.text_edit.setText("Ada Lovelace")
    QTest.keyClick(panel.text_edit, Qt.Key.Key_Return)  # saves, then the next field
    assert values()["full_name"] == "Ada Lovelace"
    assert panel.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == names[names.index("full_name") + 1]

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("subscribe")))
    assert panel.editor() is panel.check
    panel.check.setChecked(True)
    assert values()["subscribe"] is True

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("plan")))
    assert panel.editor() is panel.choice
    choice = panel.choice.itemData(2)
    panel.choice.setCurrentIndex(2)
    panel.choice.activated.emit(2)
    assert values()["plan"] == choice

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("state")))
    panel.choice.setCurrentIndex(1)
    panel.choice.activated.emit(1)
    assert values()["state"] == g.FORM_STATE_CHOICES[0]

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("services")))
    service = win.forms._by_name["services"]
    if service.multiselect:
        panel.list.item(1).setSelected(True)
        assert values()["services"] == [g.FORM_SERVICE_CHOICES[1]]
    else:
        panel.choice.setCurrentIndex(2)
        panel.choice.activated.emit(2)
        assert values()["services"] == [g.FORM_SERVICE_CHOICES[1]]

    win.undo_stack.undo()  # (each value is one undo step; the panel shows the old one again)
    assert values()["services"] in ([], None)

    panel.tree.setCurrentItem(panel.tree.topLevelItem(names.index("start_date")))
    assert g.FORM_DATE_FORMAT in panel.text_edit.placeholderText()


def test_status_messages_and_progress_are_spoken(win, qapp, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog

    A.ANNOUNCED.clear()
    win.statusBar().showMessage("Saved report.pdf", 3000)
    qapp.processEvents()
    assert A.ANNOUNCED[-1] == "Saved report.pdf"

    clock = [100.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    dialog = QProgressDialog(win)
    A.ANNOUNCED.clear()
    A.say_progress(dialog, "Exporting to Word\nReading page 1 of 10…")
    clock[0] += 1
    A.say_progress(dialog, "Exporting to Word\nReading page 2 of 10…")  # (too soon: not said)
    clock[0] += A.PROGRESS_EVERY
    A.say_progress(dialog, "Exporting to Word\nReading page 7 of 10…")
    assert list(A.ANNOUNCED) == ["Exporting to Word. Reading page 1 of 10", "Exporting to Word. Reading page 7 of 10"]
    dialog.deleteLater()


def test_f6_goes_round_the_parts_of_the_window(win, qapp):
    from planetpdf.ui import keyboard_nav as K

    win.activateWindow()
    names = [name for name, _w in win._panes.regions()]
    assert names[0] == "Main toolbar" and "Page" in names and names[-1] == "Tools"
    win.view.setFocus()
    seen = []
    for _ in range(len(names)):
        A.ANNOUNCED.clear()
        win.act_next_pane.trigger()
        seen.append(A.ANNOUNCED[-1])
    assert seen[-1] == "Page" and set(seen) == set(names)  # (all the way round, back to the page)
    win.act_previous_pane.trigger()
    assert A.ANNOUNCED[-1] == names[names.index("Page") - 1]
    assert K.first_focusable(win.view) is win.view


def test_arrow_keys_inside_a_toolbar_and_the_focus_ring(win, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolBar

    from planetpdf.ui import keyboard_nav as K

    win.activateWindow()
    qapp.processEvents()  # (active before the keyboard moves)
    bar = win.findChild(QToolBar, "mainToolbar")
    buttons = K.toolbar_buttons(bar)
    buttons[0].setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    ring = qapp._planetpdf_keys.ring(win)
    assert buttons[0].hasFocus()
    assert ring.isVisible() and ring.target is buttons[0]
    QTest.keyClick(buttons[0], Qt.Key.Key_Right)
    assert buttons[1].hasFocus() and ring.target is buttons[1]
    QTest.keyClick(buttons[1], Qt.Key.Key_End)
    assert buttons[-1].hasFocus()
    QTest.keyClick(buttons[-1], Qt.Key.Key_Escape)
    assert win.view.hasFocus()


def test_a_contrast_theme_wins_and_icons_follow_its_colors(qapp, monkeypatch):
    from PySide6.QtGui import QPalette

    from planetpdf.ui import fa, theme

    started = qapp.style().name()
    monkeypatch.setattr(theme, "high_contrast", lambda: True)
    try:
        theme.apply_theme("dark")  # (chosen, but the contrast theme wins)
        assert qapp.style().name().lower() == theme._original["style"].lower()
        assert fa._default_color[0] == qapp.palette().color(QPalette.ColorRole.WindowText).name()
        assert "palette(highlight)" in fa.checked_style(contrast=True)
    finally:
        monkeypatch.setattr(theme, "high_contrast", lambda: False)
        theme.apply_theme("system")
    assert fa._default_color[0] is None and qapp.style().name() == started


def test_text_size(qapp):
    from planetpdf.ui import theme

    normal = qapp.font().pointSizeF()
    try:
        theme.apply_text_size(150)
        assert qapp.font().pointSizeF() == pytest.approx(normal * 1.5)
    finally:
        theme.apply_text_size(100)
    assert qapp.font().pointSizeF() == pytest.approx(normal)


def test_comments_by_keyboard(win, qapp):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtTest import QTest

    from planetpdf.core import markup as K

    win.activateWindow()
    qapp.processEvents()
    win.settings.user_name = "Ada"
    win.markup_ui.act_mode_markup.trigger()  # (the Comment tool: its panel opens)
    win.markup.set_tool(K.Kind.RECT)
    for top in (100, 300):
        win.view.shapeDrawn.emit("rect", 0, QRectF(100, top, 80, 50))
    win.markup.set_tool(None)
    panel = win.comments_panel
    first_id, second_id = list(panel._cards)
    win.markup.set_text(first_id, "Check this number")
    qapp.processEvents()
    first = panel.card(first_id)
    assert "by Ada, page 1" in A.spoken_name(first) and "Check this number" in A.spoken_name(first)

    panel.search.setFocus()
    QTest.keyClick(panel.search, Qt.Key.Key_Down)
    assert win.focusWidget() is panel.card(first_id) and panel.selected_id() == first_id
    QTest.keyClick(win.focusWidget(), Qt.Key.Key_Down)
    assert win.focusWidget() is panel.card(second_id) and panel.selected_id() == second_id

    card = win.focusWidget()
    QTest.keyClick(card, Qt.Key.Key_Return)  # edit the words in the card
    editor = card.words.editor
    assert editor is not None and win.focusWidget() is editor
    editor.setPlainText("Second note")
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert {i.item_id: i.text for i in K.list_markup(win.session)}[second_id] == "Second note"
    assert win.focusWidget() is panel.card(second_id)  # (still on that card after the rebuild)

    QTest.keyClick(win.focusWidget(), Qt.Key.Key_Delete)
    qapp.processEvents()
    assert [i.item_id for i in K.list_markup(win.session)] == [first_id]
    assert win.focusWidget() is panel.card(first_id)


def test_stamps_and_text_are_placed_with_enter_and_moved_with_arrows(win, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from planetpdf.core import markup as K
    from planetpdf.core.stamps import BUILTIN_STAMPS

    win.activateWindow()
    qapp.processEvents()
    win.tool_panes.open("stamp")  # (the Stamp tool: what's placed stays selected, to move)
    win.markup.place_stamp(BUILTIN_STAMPS[0].stamp_id)
    assert win.focusWidget() is win.view
    A.ANNOUNCED.clear()
    QTest.keyClick(win.view, Qt.Key.Key_Return)
    [stamp] = [i for i in K.list_markup(win.session) if i.kind in (K.Kind.STAMP, K.Kind.SEAL)]
    assert stamp.page == win.view.current_page and A.ANNOUNCED[-1].startswith("Placed on page 1")
    before = stamp.rect
    assert win.view.selected_markup == stamp.item_id
    QTest.keyClick(win.view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    moved = next(i for i in K.list_markup(win.session) if i.item_id == stamp.item_id)
    assert moved.rect[0] > before[0] and "inches from the left" in A.ANNOUNCED[-1]

    win.markup.set_tool(K.Kind.TEXT)  # Add Text: Enter opens the text editor in the middle of the page
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Return)
    assert win.view.editor is not None


def test_the_tab_order_check_catches_a_jump_back(qapp):
    from PySide6.QtWidgets import QDialog, QGridLayout, QLineEdit

    dialog = QDialog()
    grid = QGridLayout(dialog)
    first, second = QLineEdit(dialog), QLineEdit(dialog)  # (Tab goes in the order they were made)
    grid.addWidget(first, 1, 1)  # bottom right
    grid.addWidget(second, 0, 0)  # then back up to the top left
    dialog.show()
    qapp.processEvents()
    assert tab_order_problems(dialog)
    dialog.deleteLater()


# --- nothing changes for people who don't need it (user 2026-10-25) -------------------------


def test_enter_that_finishes_typed_text_places_nothing_more(win, qapp):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtTest import QTest

    from planetpdf.core import markup as K

    win.activateWindow()
    qapp.processEvents()
    win.markup.set_tool(K.Kind.TEXT)
    win.view.shapeDrawn.emit("point", 0, QRectF(200, 200, 0, 0))  # (a click)
    QTest.keyClicks(qapp.focusWidget(), "Hello")
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Return)  # finishes the text, as before
    qapp.processEvents()
    assert win.view.editor is None
    assert [i.text for i in K.list_markup(win.session) if i.kind is K.Kind.TEXT] == ["Hello"]

    win.markup_ui.act_mode_markup.trigger()
    win.markup.set_tool(K.Kind.TEXTBOX)
    win.view.shapeDrawn.emit("textbox", 0, QRectF(200, 300, 150, 50))
    QTest.keyClicks(qapp.focusWidget(), "Line")
    QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Return)  # a new line in the box, as before
    assert win.view.editor is not None and not [i for i in K.list_markup(win.session) if i.kind is K.Kind.TEXTBOX]
    win.markup.finish_editing()  # (the box is kept: "Line" and the new line)
    assert [i.text for i in K.list_markup(win.session) if i.kind is K.Kind.TEXTBOX] == ["Line"]


def test_enter_leaves_box_tools_and_select_text_alone(win, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    before = win.session.doc[0].cropbox
    win.crop_by_drawing()  # Crop Pages by drawing: Enter must not crop to a box in the middle
    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key.Key_Return)
    assert win.session.doc[0].cropbox == before
    win.markup.cancel_pick_box()
    win.text_tools.act_select.setChecked(True)  # Select Text: Enter isn't taken
    assert not win.view.place_by_keyboard()
    win.text_tools.act_select.setChecked(False)


def test_mouse_use_of_the_panels_is_unchanged(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtTest import QTest

    from planetpdf.core import markup as K

    # Fill Form panel: a double-click opens the field on the page, as before
    win.open_file(g.make_acroform_pdf(tmp_path / "form.pdf"))
    panel = win.forms_panel
    item = panel.tree.topLevelItem(0)
    opened = []
    monkeypatch.setattr(win.forms, "activate", lambda key, **kw: opened.append(key))
    panel.tree.itemDoubleClicked.emit(item, 0)
    assert opened

    # Comments panel: a click on a card jumps to its comment once
    win.markup_ui.act_mode_markup.trigger()
    win.markup.set_tool(K.Kind.RECT)
    win.view.shapeDrawn.emit("rect", 0, QRectF(100, 100, 80, 50))
    win.markup.set_tool(None)
    qapp.processEvents()
    jumps = []
    monkeypatch.setattr(win.markup, "jump_to", lambda item_id: jumps.append(item_id))
    card = next(iter(win.comments_panel._cards.values()))
    win.comments_panel._selected = None
    win.comments_panel._show_selection()
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert len(jumps) == 1


def test_reflow_without_a_screen_reader_has_no_caret(win, qapp):
    from PySide6.QtCore import Qt

    win.act_reflow.trigger()
    reflow = win.central.currentWidget()
    assert not reflow.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    win.act_reflow.trigger()
