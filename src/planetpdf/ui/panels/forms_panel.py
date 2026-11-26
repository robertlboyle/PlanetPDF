"""Forms panel: every field in tab order with its value; highlight, clear, flatten."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.forms import fields as F
from planetpdf.core.forms.xfa import XfaKind
from planetpdf.ui import plain

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

KIND_LABELS = {
    F.FieldKind.TEXT: "Text",
    F.FieldKind.DATE: "Date",
    F.FieldKind.CHECKBOX: "Checkbox",
    F.FieldKind.RADIO: "Choice (radio)",
    F.FieldKind.COMBO: "Dropdown",
    F.FieldKind.LIST: "List",
    F.FieldKind.SIGNATURE: "Signature",
    F.FieldKind.BUTTON: "Button",
}


def display_value(f: F.FormField) -> str:
    if f.kind is F.FieldKind.CHECKBOX:
        return "☑ checked" if f.value else "☐"
    if f.kind is F.FieldKind.RADIO:
        return f.value or "—"
    if f.kind is F.FieldKind.LIST:
        labels = dict(zip(f.options, f.labels or f.options))
        return ", ".join(labels.get(v, v) for v in f.value)
    if f.kind is F.FieldKind.COMBO:
        labels = dict(zip(f.options, f.labels or f.options))
        return labels.get(f.value, str(f.value))
    if f.kind in (F.FieldKind.SIGNATURE, F.FieldKind.BUTTON):
        return ""
    return str(f.value)


class FormsPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.tree = QTreeWidget(self)
        self.tree.setAccessibleName("Form fields")
        self.tree.setHeaderLabels(["Field", "Type", "Value"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        # a click opens the field on the page; the keyboard (and a screen reader) fill it in
        # the box below the list, with standard controls (user 2026-10-25: blind users)
        self.tree.itemClicked.connect(self._activate)
        self.tree.itemDoubleClicked.connect(self._activate)  # (the mouse: on the page, as before)
        self.tree.installEventFilter(self)  # (Enter: the box below)
        self.tree.currentItemChanged.connect(lambda item, _old: self._show_field(item))
        self.highlight = QCheckBox("Highlight fields", self)
        self.highlight.setChecked(window.settings.highlight_fields)
        self.highlight.toggled.connect(self._toggle_highlight)
        self.status = plain.plain(QLabel(self))
        self.status.setWordWrap(True)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #b35900;")
        self.clear_button = QPushButton("Clear Form", self)
        self.clear_button.clicked.connect(lambda: window.forms.clear_form())
        self.flatten_button = QPushButton("Flatten Form…", self)
        self.flatten_button.setToolTip("Turn the filled-in fields into ordinary page content (Edit ▸ Undo brings them back).")
        self.flatten_button.clicked.connect(lambda: window.forms.flatten())

        self._make_editors()

        layout = QVBoxLayout(self)
        layout.addWidget(self.highlight)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.value_label)
        layout.addWidget(self.editors)
        layout.addWidget(self.status)
        layout.addWidget(self.note)
        row = QHBoxLayout()
        row.addWidget(self.clear_button)
        row.addWidget(self.flatten_button)
        layout.addLayout(row)
        self.setMinimumWidth(280)
        self._syncing = False
        self._fields: dict[str, F.FormField] = {}
        self._field: F.FormField | None = None
        self._loading = False

    # --- filling a field here (standard controls a screen reader can use) ---------------

    def _make_editors(self) -> None:
        self.value_label = plain.plain(QLabel(self), "Choose a field above to fill it in.")  # (field names)
        self.value_label.setWordWrap(True)
        self.editors = QStackedWidget(self)
        self.text_edit = QLineEdit(self)
        self.text_edit.returnPressed.connect(lambda: self.save_value(next_field=True))
        self.text_edit.editingFinished.connect(self.save_value)
        self.multiline_edit = QPlainTextEdit(self)
        self.multiline_edit.setTabChangesFocus(True)
        self.multiline_edit.setMaximumHeight(90)
        self.multiline_edit.installEventFilter(self)  # (saved when the focus leaves it)
        self.check = QCheckBox(self)
        self.check.toggled.connect(lambda _on: self.save_value())
        self.choice = QComboBox(self)
        self.choice.activated.connect(lambda _i: self.save_value())
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.list.setMaximumHeight(110)
        self.list.itemSelectionChanged.connect(self.save_value)
        self.no_edit = QLabel(self)
        self.no_edit.setWordWrap(True)
        for editor in (self.text_edit, self.multiline_edit, self.check, self.choice, self.list, self.no_edit):
            self.editors.addWidget(editor)
            editor.setAccessibleName("Field value")  # (each takes its field's name when chosen)
        self.editors.setCurrentWidget(self.no_edit)
        self.editors.setVisible(False)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        if watched is self.tree and event.type() == QEvent.Type.KeyPress                 and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._field is not None:
            self.focus_editor()
            return True
        if event.type() == QEvent.Type.FocusOut and (
                watched is self.multiline_edit or (self.choice.isEditable() and watched is self.choice.lineEdit())):
            self.save_value()
        return False

    def editor(self) -> QWidget:
        """The control filling the chosen field."""
        return self.editors.currentWidget()

    def focus_editor(self) -> None:
        if self._field is not None and self.editors.isVisible():
            self.editor().setFocus()

    def _show_field(self, item: QTreeWidgetItem | None, *, scroll: bool = True) -> None:
        """The field chosen in the list: its control below, and the page scrolled to it."""
        f = self._fields.get(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None
        self._field = f
        self.editors.setVisible(f is not None)
        if f is None:
            self.value_label.setText("Choose a field above to fill it in.")
            return
        kind = KIND_LABELS[f.kind]
        required = ", required" if f.required else ""
        self.value_label.setText(f"{f.name} ({kind.lower()}{required}):")
        self._loading = True
        try:
            editor = self._load(f)
        finally:
            self._loading = False
        self.editors.setCurrentWidget(editor)
        self.value_label.setBuddy(editor)
        editor.setAccessibleName(f"{f.name}, {kind}{required}")
        if scroll:
            self._scroll_to(f)

    def _load(self, f: F.FormField) -> QWidget:
        """Puts the field's value into the control that fills it, and returns that control."""
        if f.read_only or not f.kind.fillable:
            if f.kind is F.FieldKind.SIGNATURE:
                self.no_edit.setText("A signature field: sign it with Fill & Sign (Sign Yourself) or Digitally Sign.")
            else:
                self.no_edit.setText("Read-only: this field can't be changed." if f.read_only
                                     else "This field can't be filled here.")
            return self.no_edit
        if f.kind is F.FieldKind.CHECKBOX:
            self.check.setText(f"{f.name} checked")
            self.check.setChecked(bool(f.value))
            return self.check
        if f.kind is F.FieldKind.RADIO:
            self._set_editable(False)
            self.choice.clear()
            self.choice.addItem("(none)", None)
            for ref in f.widgets:
                if ref.on_state and self.choice.findData(ref.on_state) < 0:
                    self.choice.addItem(ref.label or ref.on_state, ref.on_state)
            self.choice.setCurrentIndex(max(0, self.choice.findData(f.value)))
            return self.choice
        labels = dict(zip(f.options, f.labels or f.options))
        if f.kind is F.FieldKind.COMBO or (f.kind is F.FieldKind.LIST and not f.multiselect):
            self._set_editable(f.editable_combo)
            self.choice.clear()
            self.choice.addItem("(none)", "")
            for value in f.options:
                self.choice.addItem(labels.get(value, value), value)
            current = f.value[0] if isinstance(f.value, list) and f.value else f.value
            index = self.choice.findData(current)
            if index >= 0:
                self.choice.setCurrentIndex(index)
            elif f.editable_combo:
                self.choice.setEditText(str(current or ""))
            return self.choice
        if f.kind is F.FieldKind.LIST:
            self.list.clear()
            for value in f.options:
                item = QListWidgetItem(labels.get(value, value), self.list)
                item.setData(Qt.ItemDataRole.UserRole, value)
                item.setSelected(value in (f.value or []))
            return self.list
        if f.multiline:
            self.multiline_edit.setPlainText(str(f.value or ""))
            return self.multiline_edit
        self.text_edit.setText(str(f.value or ""))
        self.text_edit.setMaxLength(f.max_length or 32767)
        dated = f.kind is F.FieldKind.DATE and f.date_format
        self.text_edit.setPlaceholderText(f"Date, {f.date_format}" if dated else "")
        return self.text_edit

    def _set_editable(self, on: bool) -> None:
        if self.choice.isEditable() == on:
            return
        self.choice.setEditable(on)
        if on:  # (typed text is saved when Enter is pressed or the focus leaves)
            self.choice.lineEdit().returnPressed.connect(lambda: self.save_value(next_field=True))
            self.choice.lineEdit().installEventFilter(self)

    def value(self):
        """The value the control holds now, as the field stores it."""
        f, editor = self._field, self.editor()
        if editor is self.check:
            return self.check.isChecked()
        if editor is self.choice:
            if f.kind is F.FieldKind.RADIO:
                return self.choice.currentData()
            chosen = self.choice.currentIndex()
            typed = self.choice.isEditable() and self.choice.currentText() != self.choice.itemText(chosen)
            text = self.choice.currentText() if typed or chosen < 0 else self.choice.itemData(chosen)
            if f.kind is F.FieldKind.LIST:
                return [text] if text else []
            return text or ""
        if editor is self.list:
            return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]
        if editor is self.multiline_edit:
            return self.multiline_edit.toPlainText()
        return self.text_edit.text()

    def save_value(self, *, next_field: bool = False) -> None:
        """Writes the control's value into the field (one undo step), and with ``next_field``
        moves on to the next field that can be filled."""
        f = self._field
        if self._loading or f is None or self.editor() is self.no_edit:
            return
        value = self.value()
        if value != f.value:
            self._window.forms.set_value(f.name, value)
        if next_field:
            self.next_field()

    def next_field(self) -> None:
        current = self.tree.currentItem()
        start = self.tree.indexOfTopLevelItem(current) if current is not None else -1
        for i in range(start + 1, self.tree.topLevelItemCount()):
            f = self._fields.get(self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole))
            if f is not None and f.kind.fillable and not f.read_only:
                self.tree.setCurrentItem(self.tree.topLevelItem(i))
                self.focus_editor()
                return
        self._window.statusBar().showMessage("That was the last field.", 3000)

    def _scroll_to(self, f: F.FormField) -> None:
        session = self._window.session
        if session is None or not f.widgets:
            return
        ref = f.widgets[0]
        shown = pymupdf.Rect(ref.rect) * session.doc[ref.page].rotation_matrix
        self._window.view.show_area(ref.page, QRectF(shown.x0, shown.y0, shown.width, shown.height), flash=False)

    def _toggle_highlight(self, on: bool) -> None:
        self._window.settings.highlight_fields = on
        self._window.view.set_fields_highlighted(on)

    def populate(self, fields: list[F.FormField], kind: XfaKind) -> None:
        self._fields = {f.name: f for f in fields}
        self._field = None
        self.tree.clear()
        for f in fields:
            item = QTreeWidgetItem([f.name + (" *" if f.required else ""), KIND_LABELS[f.kind], display_value(f)])
            item.setData(0, Qt.ItemDataRole.UserRole, f.name)
            if f.read_only or not f.kind.fillable:
                for col in range(3):
                    item.setForeground(col, QColor("gray"))
                item.setToolTip(0, "Read-only" if f.read_only else "Not fillable here")
            self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        has_fields = bool(fields)
        self.clear_button.setEnabled(has_fields)
        self.flatten_button.setEnabled(has_fields)
        self._update_status(fields)
        notes = {
            XfaKind.PURE: "This is a dynamic XFA form, which PlanetPDF can't display or fill. Use Adobe Acrobat or Reader.",
            XfaKind.HYBRID: "This form also has an XFA copy. When you save, you'll be asked whether it will be submitted electronically.",
        }
        self.note.setText(notes.get(kind, ""))
        self.note.setVisible(kind is not XfaKind.NONE)

    def update_values(self, fields: list[F.FormField]) -> None:
        by_name = {f.name: f for f in fields}
        self._fields = by_name
        if self._field is not None:
            self._field = by_name.get(self._field.name)
            focus = self.focusWidget()
            if self._field is not None and not (focus is not None and self.editors.isAncestorOf(focus)):
                self._loading = True  # (changed elsewhere: undo, on the page)
                try:
                    self._load(self._field)
                finally:
                    self._loading = False
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            f = by_name.get(item.data(0, Qt.ItemDataRole.UserRole))
            if f is not None:
                item.setText(2, display_value(f))
        self._update_status(fields)

    def _update_status(self, fields: list[F.FormField]) -> None:
        if not fields:
            self.status.setText("This document has no form fields.")
            return
        empty_required = [f.name for f in fields if f.required and f.value in ("", None, [], False)]
        text = (f"{len(fields)} field{'s' if len(fields) != 1 else ''}. Click a field on the page or here to fill it; "
                "Tab moves to the next. From the keyboard: choose one in the list, then Enter fills it below.")
        if empty_required:
            text += f"\nRequired (*) and still empty: {len(empty_required)}."
        self.status.setText(text)

    def select_field(self, name: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == name:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(item)
                self.tree.blockSignals(False)
                self.tree.scrollToItem(item)
                self._show_field(item, scroll=False)  # (chosen on the page: the page is there)
                return

    def _activate(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        forms = self._window.forms
        f = next((f for f in forms.fields if f.name == name), None)
        if f is None or not f.widgets:
            return
        from planetpdf.ui.forms_controller import field_key

        forms.activate(field_key(name, f.widgets[0].xref))
