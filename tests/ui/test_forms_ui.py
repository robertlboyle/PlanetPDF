"""Form filling UI (offscreen): editors over fields, Tab order, undo, flatten, XFA."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QListWidget, QMessageBox

from planetpdf.core.forms import fields as F
from planetpdf.core.forms import xfa
from planetpdf.ui.forms_controller import field_key, xfa_values
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _open(qapp, tmp_path, path):
    win = MainWindow(tmp_path / "config")
    win.resize(1100, 850)
    win.show()
    win.open_file(path)
    QCoreApplication.processEvents()
    return win


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = _open(qapp, tmp_path, pdf_copy("acroform.pdf"))
    yield win
    win.undo_stack.setClean()
    win.close()


def _key(win, name, index=0):
    f = next(f for f in win.forms.fields if f.name == name)
    return field_key(name, f.widgets[index].xref)


def _value(win, name):
    return F.get_field(win.session, name).value


def _editor_widget(win):
    proxy = win.view.editor
    return proxy.widget() if proxy is not None else None


def _pick(win, label):
    """A choice from the dropdown's menu (it opens under the field when it's clicked)."""
    menu = win.forms.choices_menu
    assert menu is not None and menu.isVisible()
    next(a for a in menu.actions() if a.text() == label).trigger()
    menu.close()


def test_form_opens_panel_and_overlays(window):
    assert window._forms_dock.isVisible()
    assert window.forms_panel.tree.topLevelItemCount() == 6
    assert len(window.view._field_items) == 7  # the radio group has two buttons


def test_type_into_text_field_then_tab(window):
    window.forms.activate(_key(window, "full_name"))
    editor = _editor_widget(window)
    assert isinstance(editor, QLineEdit)
    editor.setText("Jane Doe")
    QApplication.sendEvent(editor, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
    assert _value(window, "full_name") == "Jane Doe"
    assert window.view.focused_field == _key(window, "subscribe")  # next in tab order
    assert window.is_dirty()
    window.undo_stack.undo()
    assert _value(window, "full_name") == ""
    assert not window.is_dirty()


def test_shift_tab_goes_back_and_wraps(window):
    window.forms.activate(_key(window, "full_name"))
    window.forms.move(forward=False)
    assert window.view.focused_field == _key(window, "start_date")  # wrapped to the last


def test_click_checkbox_and_radio(window):
    window.forms.activate(_key(window, "subscribe"), toggle=True)
    assert _value(window, "subscribe") is True
    window.forms.activate(_key(window, "subscribe"), toggle=True)
    assert _value(window, "subscribe") is False
    window.forms.activate(_key(window, "plan", 1), toggle=True)
    assert _value(window, "plan") == "premium"
    window.forms.activate(_key(window, "plan", 0), toggle=True)
    assert _value(window, "plan") == "basic"
    assert window.undo_stack.count() == 4


def test_space_toggles_focused_checkbox(window):
    window.forms.activate(_key(window, "subscribe"))  # focus via Tab-like activation
    assert _value(window, "subscribe") is False
    QApplication.sendEvent(window.view, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert _value(window, "subscribe") is True


def test_dropdown_and_list(window):
    window.forms.activate(_key(window, "state"), toggle=True)  # (a click: its choices open)
    menu = window.forms.choices_menu
    screen = window.view.field_screen_rect(_key(window, "state"))
    assert menu.pos() == screen.bottomLeft()  # (just under the field, where it really is)
    _pick(window, "CT")
    assert _value(window, "state") == "CT"
    window.forms.activate(_key(window, "state"), toggle=True)
    assert next(a for a in window.forms.choices_menu.actions() if a.isChecked()).text() == "CT"
    _pick(window, "(none)")
    assert _value(window, "state") == ""
    window.forms.activate(_key(window, "services"))
    lst = _editor_widget(window)
    assert isinstance(lst, QListWidget)
    lst.item(2).setSelected(True)
    assert _value(window, "services") == ["TV"]


def _designed(qapp, tmp_path):
    import pymupdf

    from planetpdf.core.document import PdfSession
    from planetpdf.core.forms import design as D

    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    path = tmp_path / "designed.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    D.create_field(s, D.FieldSpec("pick", F.FieldKind.LIST, 0, (72, 72, 272, 132), options=("A", "B", "C")))
    D.create_field(s, D.FieldSpec("many", F.FieldKind.LIST, 0, (72, 150, 272, 210), options=("A", "B", "C"),
                                  multiselect=True))
    D.create_field(s, D.FieldSpec("state", F.FieldKind.COMBO, 0, (72, 230, 272, 250), options=("NY", "NJ")))
    s.save()
    s.close()
    return _open(qapp, tmp_path, path)


def test_list_choices_are_clicked_on_the_page_and_dropdowns_show_an_arrow(qapp, tmp_path):
    """User 2026-10-02: the list editor didn't look like the PDF (other font, a scroll bar),
    and a dropdown had no arrow."""
    from PySide6.QtCore import QPointF

    win = _designed(qapp, tmp_path)
    try:
        assert len(win.view._drop_arrows) == 1  # (the dropdown's ▾, drawn by the viewer)

        def click(name, row):
            key = _key(win, name)
            _page, rect = win.view.field_rect(key)
            win.view.field_click_point = QPointF(rect.left() + 10, rect.top() + 1 + 11 * 1.15 * (row + 0.5))
            win.forms.activate(key, toggle=True)

        click("pick", 1)
        assert _value(win, "pick") == ["B"] and win.view.editor is None  # no editor over it
        click("pick", 2)
        assert _value(win, "pick") == ["C"]
        click("many", 0)
        click("many", 2)
        assert _value(win, "many") == ["A", "C"]
        click("many", 0)  # (toggles off)
        assert _value(win, "many") == ["C"]
        win.forms.activate(_key(win, "pick"))  # from the keyboard (Tab): the editor
        assert isinstance(_editor_widget(win), QListWidget)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_hand_tool_fills_fields_and_empty_dropdowns_say_choose(qapp, tmp_path):
    """User 2026-10-02: "I had to enter into form mode to fill… shouldn't I be able to
    type/select while viewing it?" (Acrobat's Hand tool fills fields too); "there is no
    visible text on the drop down"."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    win = _designed(qapp, tmp_path)
    try:
        assert [a.placeholder for a in win.view._drop_arrows] == ["Choose…"]
        win.act_hand_tool.trigger()
        assert win.view.hand_tool
        key = _key(win, "pick")
        _page, rect = win.view.field_rect(key)
        page = win.view._items[0]
        point = win.view.mapFromScene(page.mapToScene(rect.left() + 10, rect.top() + 1 + 11 * 1.15 * 1.5))
        QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(point))
        assert _value(win, "pick") == ["B"]  # (clicked with the Hand tool: filled, not panned)
        win.forms.set_value("state", "NJ")
        assert [a.placeholder for a in win.view._drop_arrows] == [""]  # (a value now: no hint)
        win.forms.activate(_key(win, "state"), toggle=True)
        assert next(a for a in win.forms.choices_menu.actions() if a.isChecked()).text() == "NJ"
        win.forms.choices_menu.close()
    finally:
        win.undo_stack.setClean()
        win.close()


def test_a_dropdown_pick_sticks(qapp, tmp_path):
    """User bugs 2026-10-02 / 10-04 ("it always says choose"): a QComboBox on the page
    opened its list in the wrong place on a zoomed, scaled page, so a real click on a choice
    landed on the page ("clicked away") and nothing was picked. The choices are now a menu
    of the window's own under the field; no editor stays open on the page."""
    win = _designed(qapp, tmp_path)
    try:
        win.forms.activate(_key(win, "state"), toggle=True)
        assert win.view.editor is None
        _pick(win, "NJ")
        assert _value(win, "state") == "NJ"
        assert [a.placeholder for a in win.view._drop_arrows] == [""]  # ("Choose…" gone)
        win.forms.activate(_key(win, "state"))  # (Tab: no menu until Space / Enter / Down)
        win.forms.choices_menu.close()
        win.forms.choices_menu = None
        win.forms.activate(_key(win, "state"))
        assert win.forms.choices_menu is None
        QApplication.sendEvent(win.view, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        assert win.forms.choices_menu is not None and win.forms.choices_menu.isVisible()
        win.forms.choices_menu.close()
    finally:
        win.undo_stack.setClean()
        win.close()

def test_editors_use_the_fields_own_text_size_and_signature_fields_look_final(qapp, tmp_path):
    """User 2026-10-02: "check this for all types of fields": editors looked different from
    the PDF (smaller, another font), and signature fields showed MuPDF's red tag."""
    import pymupdf

    from planetpdf.core.document import PdfSession
    from planetpdf.core.forms import design as D
    from planetpdf.ui.forms_controller import appearance_text_size
    from planetpdf.ui.viewer.overlays import SignatureLook

    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    path = tmp_path / "sizes.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    D.create_field(s, D.FieldSpec("name", F.FieldKind.TEXT, 0, (72, 72, 272, 92), value="Jane Doe"))
    D.create_field(s, D.FieldSpec("sig", F.FieldKind.SIGNATURE, 0, (72, 120, 272, 160)))
    s.save()
    s.close()
    win = _open(qapp, tmp_path, path)
    try:
        key = _key(win, "name")
        _page, rect = win.view.field_rect(key)
        from planetpdf.ui.forms_controller import split_key

        drawn = appearance_text_size(win.session.doc, split_key(key)[1], rect)
        assert drawn > 8  # (MuPDF's automatic size for a 20 pt box)
        win.forms.activate(key)
        editor = _editor_widget(win)
        assert f"font-size: {drawn:.2f}px" in editor.styleSheet() and "Arial" in editor.styleSheet()
        assert any(isinstance(item, SignatureLook) for item in win.view._drop_arrows)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_date_field_validates_and_reformats(window):
    window.forms.activate(_key(window, "start_date"))
    container = _editor_widget(window)
    edit = container.findChild(QLineEdit)
    edit.setText("2026-12-25")  # typed in another format
    window.forms.commit()
    assert _value(window, "start_date") == "12/25/2026"
    window.forms.activate(_key(window, "start_date"))
    edit = _editor_widget(window).findChild(QLineEdit)
    edit.setText("next tuesday")
    window.forms.commit()
    assert _value(window, "start_date") == "12/25/2026"  # unchanged
    assert "isn't a date" in window.statusBar().currentMessage()


def test_panel_shows_values_and_activates(window):
    window.forms.set_value("full_name", "Panel Test")
    item = window.forms_panel.tree.topLevelItem(0)
    assert item.text(2) == "Panel Test"
    window.forms_panel._activate(window.forms_panel.tree.topLevelItem(3))
    assert window.view.focused_field == _key(window, "state")


def test_clear_form_is_one_undo_step(window):
    window.forms.set_value("full_name", "A")
    window.forms.set_value("subscribe", True)
    window.forms.clear_form()
    assert _value(window, "full_name") == "" and _value(window, "subscribe") is False
    window.undo_stack.undo()
    assert _value(window, "full_name") == "A" and _value(window, "subscribe") is True


def test_values_survive_save(window):
    window.forms.set_value("full_name", "Saved Name")
    assert window.save()
    assert _value(window, "full_name") == "Saved Name"
    window.forms.activate(_key(window, "full_name"))  # keys still valid after renumbering
    assert window.view.focused_field is not None


def test_flatten_can_be_undone(window, monkeypatch):
    monkeypatch.setattr(window, "ask", lambda *a: True)
    window.forms.set_value("full_name", "Flat Name")
    assert window.forms.flatten()
    assert window.forms.fields == []
    assert window.undo_stack.undoText() == "Flatten Form" and window.is_dirty()
    assert "Flat Name" in window.session.doc[0].get_text()
    assert not window.forms_panel.flatten_button.isEnabled()
    window.undo_stack.undo()  # the fields are back, still filled in
    assert {f.name: f.value for f in window.forms.fields}["full_name"] == "Flat Name"
    assert window.forms_panel.flatten_button.isEnabled()
    window.undo_stack.redo()
    assert window.forms.fields == []


def test_read_only_field_cannot_be_activated(qapp, tmp_path, pdf_copy):
    win = _open(qapp, tmp_path, pdf_copy("form_extra.pdf"))
    try:
        win.forms.activate(_key(win, g.READ_ONLY_FIELD))
        assert win.view.focused_field is None
        assert "read-only" in win.statusBar().currentMessage()
        assert [split for split in win.forms._stops if g.READ_ONLY_FIELD in split] == []
    finally:
        win.undo_stack.setClean()
        win.close()


def test_pure_xfa_shows_message(qapp, tmp_path, pdf_copy, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    win = _open(qapp, tmp_path, pdf_copy("xfa_pure.pdf"))
    try:
        assert shown == ["XFA Form Not Supported"]
        assert "can't display or fill" in win.forms_panel.note.text()
    finally:
        win.close()


@pytest.mark.parametrize("keep", [True, False])
def test_hybrid_xfa_asks_on_save(qapp, tmp_path, pdf_copy, keep):
    win = _open(qapp, tmp_path, pdf_copy("xfa_hybrid.pdf"))
    asked = []
    win.ask_three = lambda *a: (asked.append(a[0]), keep)[1]
    try:
        win.forms.set_value("full_name", "Hybrid Jane")
        assert win.save()
        assert win.save()  # asked only once per document
        assert asked == ["Form Submission"]
        with pymupdf.open(win.session.path) as doc:
            kind = xfa.xfa_kind(doc)
            if keep:
                packets = dict(xfa._PACKET.findall(doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")[1]))
                assert b"<full_name>Hybrid Jane</full_name>" in doc.xref_stream(int(packets["datasets"]))
        assert kind is (xfa.XfaKind.HYBRID if keep else xfa.XfaKind.NONE)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_hybrid_save_cancel_does_not_save(qapp, tmp_path, pdf_copy):
    win = _open(qapp, tmp_path, pdf_copy("xfa_hybrid.pdf"))
    win.ask_three = lambda *a: None
    try:
        win.forms.set_value("full_name", "X")
        before = win.session.path.read_bytes()
        assert not win.save()
        assert win.session.path.read_bytes() == before
        assert win.is_dirty()
    finally:
        win.undo_stack.setClean()
        win.close()


def test_xfa_values_formatting(window):
    window.forms.set_value("subscribe", True)
    window.forms.set_value("plan", "basic")
    values = xfa_values(window.forms.fields)
    assert values["subscribe"] == "1" and values["plan"] == "basic" and values["full_name"] == ""
