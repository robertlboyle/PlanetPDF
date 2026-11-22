"""Classic-Acrobat menus, toolbars and tool bars (user screenshots 2026-10-01)."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QMimeData, QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from planetpdf.core.markup import Kind
from planetpdf.ui import fa
from planetpdf.ui.main_window import MainWindow, UNDO_STEPS
from planetpdf.ui.menus import NOT_YET


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1200, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def _plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").split("\t")[0]


def _menu(win, *path):
    menu = next(a.menu() for a in win.menuBar().actions() if a.menu() and _plain(a.menu().title()) == path[0])
    for title in path[1:]:
        menu = next(a.menu() for a in menu.actions() if a.menu() and _plain(a.menu().title()) == title)
    return menu


def _texts(menu):
    return [_plain(a.text()) for a in menu.actions() if not a.isSeparator() and a.text()]


def test_file_edit_view_follow_acrobat(window):
    file_items = _texts(_menu(window, "File"))
    for expected in ("Open…", "Reopen PDFs from last session", "Create", "Insert Pages", "Save", "Save As…",
                     "Save as Other", "Export To", "Protect Using Password", "Revert", "Close File", "Properties…",
                     "Print…", "View All Recent Files…", "Exit Application"):
        assert expected in file_items
    assert "Request E-signatures" not in file_items  # no E-sign (user decision)
    edit = _texts(_menu(window, "Edit"))
    # Edit ▸ More is on the Edit menu itself, and Rotate is right there too (user decision)
    for expected in ("Undo", "Redo", "Cut", "Copy", "Paste", "Delete", "Select All", "Deselect All",
                     "Copy File to Clipboard", "Take a Snapshot", "Edit PDF", "Add Text", "Add Image…", "Delete Pages",
                     "Rotate Clockwise", "Rotate Counterclockwise", "Redact Text & Images", "Find…", "Advanced Search",
                     "Preferences…"):
        assert expected in edit
    assert "More" not in edit
    view = _texts(_menu(window, "View"))
    assert "Tools" not in view and "Enable new Acrobat" not in view  # Tools is its own menu now
    assert _texts(_menu(window, "View", "Display Theme")) == ["Follow System Setting", "Light", "Dark"]
    # Tools: each entry opens that tool's bar (user decision), not a submenu
    tools = _texts(_menu(window, "Tools"))
    assert tools[:3] == ["Create PDF", "Edit PDF", "Export PDF"]
    assert {"Organize Pages", "Comment", "Stamp", "Fill & Sign", "Prepare Form", "Protect", "Redact", "Scan & OCR",
            "Compare Files", "Close Tool"} <= set(tools)
    assert not [a for a in _menu(window, "Tools").actions() if a.menu()]


def test_nothing_is_grayed_out_any_more(window):
    """Backlog Phases A-D (2026-10-02) built every Acrobat command that was listed as
    "not available yet"; the mechanism stays for future ones."""
    assert NOT_YET == []
    item = next(a for a in _menu(window, "File", "Create").actions() if _plain(a.text()).startswith("PDF from Web Page"))
    assert item.isEnabled() and item.toolTip() != "Not available in PlanetPDF yet"


def test_main_toolbar_quick_tools(window):
    """User 2026-10-02: sticky note and highlight belong there; sign, delete and turning
    pages don't (Fill & Sign / Edit PDF / Organize work); rotate = the view, both ways."""
    assert set(window.quick_actions) == {"note-sticky", "highlighter", "rotate-left", "rotate-right"}
    assert not hasattr(window, "quick_sign_button")
    window.quick_actions["rotate-right"].trigger()
    assert window.view._view_rotation == 90
    assert window.session.doc[0].rotation == 0 and not window.is_dirty()  # (the file is unchanged)
    window.quick_actions["rotate-left"].trigger()
    window.quick_actions["rotate-left"].trigger()
    assert window.view._view_rotation == 270


def test_icons_are_bundled_and_shown(window):
    assert fa.has("folder-open") and fa.has("floppy-disk")
    assert not window.act_open.icon().isNull() and not window.act_print.icon().isNull()
    assert (fa.icons_dir() / "LICENSE.txt").is_file()
    # switched on = reversed: the icon turns white (user: the red highlight hid the gray icon)
    from PySide6.QtGui import QIcon

    on = fa.icon("comment").pixmap(24, 24, QIcon.Mode.Normal, QIcon.State.On).toImage()
    off = fa.icon("comment").pixmap(24, 24, QIcon.Mode.Normal, QIcon.State.Off).toImage()
    lightest = lambda img: max(img.pixelColor(x, y).lightness() for x in range(24) for y in range(24)  # noqa: E731
                               if img.pixelColor(x, y).alpha() > 200)
    assert lightest(on) > 240 and lightest(off) < 140
    assert "QToolButton:checked" in window.tool_panes.strip.styleSheet()


def test_tool_strip_opens_each_tool_bar_and_close_returns(window):
    panes = window.tool_panes
    assert panes.current is None and not any(b.isVisible() for b in panes.bars.values())
    panes.strip_actions["edit"].trigger()
    assert window.edit.mode == "edit" and panes.bars["edit"].isVisible()
    assert window.edit_ui.edit_toolbar is panes.bars["edit"]
    panes.strip_actions["fill_sign"].trigger()
    assert window.edit.mode is None and panes.bars["fill_sign"].isVisible() and not panes.bars["edit"].isVisible()
    panes.bars["fill_sign"].close_button.click()
    assert panes.current is None and not any(b.isVisible() for b in panes.bars.values())
    window.markup_ui.set_mode("markup")  # from the menu: the Comment bar follows
    assert panes.current == "comment" and panes.bars["comment"].isVisible()
    panes.strip_actions["organize"].trigger()
    assert window.markup_ui.mode == "form" and panes.bars["organize"].isVisible()


def test_fill_and_sign_symbols_resize_and_recolor(window, monkeypatch):
    from PySide6.QtGui import QColor

    window.tool_panes.open("fill_sign")
    window.symbol_actions["check"].trigger()
    window.view.shapeDrawn.emit("point", 0, QRectF(QPointF(300, 400), QPointF(300, 400)))
    [item] = [i for i in window.markup.items if i.kind is Kind.SYMBOL]
    assert item.spec.text == "check" and window.view.selected_markup == item.item_id
    bar = window.view.item_bar.widget()
    width = pymupdf.Rect(item.spec.rect).width
    bar.larger.click()
    [item] = [i for i in window.markup.items if i.kind is Kind.SYMBOL]
    assert pymupdf.Rect(item.spec.rect).width == pytest.approx(width * 1.25)
    monkeypatch.setattr(window, "pick_color", lambda *a: QColor("#c0392b"))
    window.view.item_bar.widget().color.click()
    [item] = [i for i in window.markup.items if i.kind is Kind.SYMBOL]
    assert QColor.fromRgbF(*item.spec.style.color).name() == "#c0392b"


def test_copy_paste_items_pictures_and_text(window):
    from planetpdf.core import markup as K

    window.markup.add(K.placed_text(window.session, 0, (100, 500), "Copy me", window.markup.style_for(Kind.TEXT)))
    [item] = [i for i in window.markup.items if i.kind is Kind.TEXT]
    window.markup.select(item.item_id)
    window.act_copy.trigger()
    window.act_paste.trigger()
    copies = [i for i in window.markup.items if i.kind is Kind.TEXT]
    assert len(copies) == 2 and len({i.item_id for i in copies}) == 2
    window.markup.select(copies[1].item_id)
    window.act_cut.trigger()
    assert len([i for i in window.markup.items if i.kind is Kind.TEXT]) == 1
    # a picture copied in another program becomes a movable image item
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    QGuiApplication.clipboard().setImage(image)
    window.act_paste.trigger()
    assert [i for i in window.markup.items if i.kind is Kind.IMAGE]
    QGuiApplication.clipboard().setText("Pasted words")
    window.act_paste.trigger()
    assert any(i.spec.text == "Pasted words" for i in window.markup.items if i.kind is Kind.TEXT)


def test_select_all_snapshot_and_copy_file(window):
    assert window.clipboard_tools.select_all()
    assert "SSN" in window.text_tools.selected_text()
    window.act_deselect_all.trigger()
    assert window.text_tools.selected_text() == ""
    png = window.clipboard_tools.snapshot_area(0, (72, 60, 300, 120))
    assert Image.open(io.BytesIO(png)).width > 300  # 150 dpi
    assert not QGuiApplication.clipboard().image().isNull()
    window.clipboard_tools.copy_file()
    assert QGuiApplication.clipboard().mimeData().urls()[0].toLocalFile().endswith("text.pdf")


def test_create_and_insert_from_clipboard_and_blank(window, tmp_path, monkeypatch):
    target = tmp_path / "new.pdf"
    monkeypatch.setattr("planetpdf.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    QGuiApplication.clipboard().setText("Text from the clipboard")
    assert window.create_pdf_from_clipboard()
    assert window.session.path == target and "Text from the clipboard" in window.session.doc[0].get_text()
    pages = window.session.page_count
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    QGuiApplication.clipboard().setImage(image)
    assert window.insert_pages_from_clipboard()
    assert window.session.page_count == pages + 1
    window.undo_stack.undo()
    assert window.session.page_count == pages
    window.undo_stack.setClean()
    blank = tmp_path / "blank.pdf"
    monkeypatch.setattr("planetpdf.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(blank), ""))
    assert window.create_blank_pdf() and window.session.page_count == 1


def test_history_reopen_revert_and_recent(window, tmp_path, pdf_copy):
    view = window.view
    assert not window.act_back.isEnabled()
    view.go_to_page(2)
    assert window.act_back.isEnabled()
    window.act_back.trigger()
    assert view.current_page == 0 and window.act_forward.isEnabled()
    window.act_forward.trigger()
    assert view.current_page == 2
    # Revert
    window.act_rotate_cw.trigger()
    assert window.is_dirty()
    window.ask = lambda *a: True
    assert window.revert() and not window.is_dirty() and window.session.doc[0].rotation == 0
    # recent files on the File menu, numbered like Acrobat
    menu = _menu(window, "File")
    menu.aboutToShow.emit()
    assert any(_plain(a.text()).startswith("1 ") for a in menu.actions())
    path = window.session.path
    window.settings.last_session = [str(path)]
    window.close_document()
    assert window.reopen_last_session() and window.session.path == path


def test_undo_history_panel(window):
    assert window.undo_stack.undoLimit() == UNDO_STEPS >= 20
    for _ in range(3):
        window.act_rotate_cw.trigger()
    assert window.undo_view.stack() is window.undo_stack and window.undo_stack.count() == 3
    window.undo_stack.setIndex(0)  # what clicking the first row of the history does
    assert window.session.doc[0].rotation == 0
    assert "Undo History" in _texts(_menu(window, "Edit"))


def test_theme_and_hand_tool(window):
    from planetpdf.ui.theme import is_dark

    window.theme_actions["dark"].trigger()
    assert is_dark() and window.settings.theme == "dark"
    window.theme_actions["light"].trigger()
    assert not is_dark()
    window.theme_actions["system"].trigger()
    assert window.settings.theme == "system"
    window.act_hand_tool.trigger()
    assert window.view.hand_tool and window.view.dragMode() == window.view.DragMode.ScrollHandDrag
    window.act_add_placed_text.trigger()  # another tool puts the Hand away
    assert not window.view.hand_tool and not window.act_hand_tool.isChecked()
