"""Prepare Form: a field's properties (name, tooltip, required, choices, ...)."""

from __future__ import annotations

import re
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError
from planetpdf.core.forms.design import FieldSpec
from planetpdf.core.forms.fields import FieldKind

DATE_FORMATS = ["mm/dd/yyyy", "m/d/yyyy", "mm/dd/yy", "dd/mm/yyyy", "yyyy-mm-dd", "mmm d, yyyy", "d mmmm yyyy"]
KIND_LABELS = {
    FieldKind.TEXT: "Text field", FieldKind.DATE: "Date field", FieldKind.CHECKBOX: "Checkbox",
    FieldKind.RADIO: "Radio button group", FieldKind.COMBO: "Dropdown", FieldKind.LIST: "List box",
    FieldKind.SIGNATURE: "Signature field", FieldKind.BUTTON: "Button",
}


class FieldPropertiesDialog(QDialog):
    def __init__(self, spec: FieldSpec, parent: QWidget | None = None, field_names: list[str] | tuple = ()) -> None:
        """``field_names``: the form's other fields that can be filled in (a barcode's choice)."""
        super().__init__(parent)
        self._spec = spec
        kind = spec.kind
        title = "Barcode" if kind is FieldKind.BUTTON and spec.action == "barcode" else KIND_LABELS.get(kind, "Field")
        self.setWindowTitle(f"{title} Properties")
        form = QFormLayout()
        self.name = QLineEdit(spec.name)
        form.addRow("&Name:", self.name)
        self.tooltip = QLineEdit(spec.tooltip)
        self.tooltip.setPlaceholderText("Shown when the pointer is over the field")
        form.addRow("&Tooltip:", self.tooltip)
        self.required = QCheckBox("Required")
        self.required.setChecked(spec.required)
        self.read_only = QCheckBox("Read-only")
        self.read_only.setChecked(spec.read_only)
        form.addRow("", self.required)
        form.addRow("", self.read_only)

        self.multiline = self.max_length = self.font_size = self.date_format = None
        self.choices = self.editable = self.multiselect = self.buttons = None
        self.caption = self.action = self.address = None
        self.symbology = self.every_field = self.encoded = self.with_names = None
        if kind is FieldKind.BUTTON and spec.action == "image":
            form.addRow("", QLabel("An image field: whoever fills in the form clicks it to choose a picture."))
        elif kind is FieldKind.BUTTON and spec.action == "barcode":
            self._barcode_rows(form, spec, list(field_names))
        elif kind is FieldKind.BUTTON:
            from planetpdf.core.forms.buttons import ACTIONS

            self.caption = QLineEdit(spec.caption)
            form.addRow("&Caption:", self.caption)
            self.action = QComboBox()
            for key, text in ACTIONS.items():
                self.action.addItem(text, key)
            if spec.action == "other":
                self.action.addItem("Its own action (from another program)", "other")
            current = "url" if spec.action.startswith("url:") else spec.action
            self.action.setCurrentIndex(max(0, self.action.findData(current)))
            form.addRow("&When clicked:", self.action)
            self.address = QLineEdit(spec.action[4:] if spec.action.startswith("url:") else "")
            self.address.setPlaceholderText("https://…")
            form.addRow("&Web address:", self.address)
            self.action.currentIndexChanged.connect(lambda _i: self.address.setEnabled(self.action.currentData() == "url"))
            self.address.setEnabled(current == "url")
        self.check_text = None
        if kind is FieldKind.CHECKBOX:
            # the text shown beside the box (user 2026-10-04): typed here, paragraphs kept
            self.check_text = QPlainTextEdit(spec.labels[0] if spec.labels else "")
            self.check_text.setPlaceholderText("Shown next to the box (clicking it ticks the box too)")
            self.check_text.setMaximumHeight(70)
            form.addRow("Te&xt:", self.check_text)
        if kind in (FieldKind.TEXT, FieldKind.DATE, FieldKind.COMBO, FieldKind.LIST, FieldKind.RADIO, FieldKind.CHECKBOX):
            labeled = kind in (FieldKind.RADIO, FieldKind.CHECKBOX)
            self.font_size = QDoubleSpinBox()
            self.font_size.setRange(0, 72)
            self.font_size.setSpecialValueText("Normal (10 pt)" if labeled else "Auto")
            self.font_size.setSuffix(" pt")
            self.font_size.setValue(spec.font_size)
            form.addRow("&Text size:" if labeled else "&Font size:", self.font_size)
        if kind is FieldKind.TEXT:
            self.multiline = QCheckBox("Multiple lines")
            self.multiline.setChecked(spec.multiline)
            form.addRow("", self.multiline)
        if kind in (FieldKind.TEXT, FieldKind.DATE):
            self.max_length = QSpinBox()
            self.max_length.setRange(0, 10000)
            self.max_length.setSpecialValueText("No limit")
            self.max_length.setValue(spec.max_length)
            form.addRow("&Maximum length:", self.max_length)
        if kind is FieldKind.DATE:
            self.date_format = QComboBox()
            self.date_format.setEditable(True)
            self.date_format.addItems(DATE_FORMATS)
            self.date_format.setCurrentText(spec.date_format or DATE_FORMATS[0])
            form.addRow("&Date format:", self.date_format)
        if kind is FieldKind.RADIO:
            # a row per button: its text (paragraphs welcome, pasted or typed), its value
            self.buttons = RadioButtonsEditor(spec, self)
            form.addRow("&Buttons:", self.buttons)
        elif kind in (FieldKind.COMBO, FieldKind.LIST):
            self.choices = QPlainTextEdit("\n".join(spec.options))
            self.choices.setMinimumHeight(110)
            form.addRow("&Choices:", self.choices)
            form.addRow("", QLabel("One choice per line."))
        if kind is FieldKind.COMBO:
            self.editable = QCheckBox("Allow typing a value that isn't in the list")
            self.editable.setChecked(spec.editable_combo)
            form.addRow("", self.editable)
        if kind is FieldKind.LIST:
            self.multiselect = QCheckBox("Allow choosing several")
            self.multiselect.setChecked(spec.multiselect)
            form.addRow("", self.multiselect)

        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020;")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.name.selectAll()
        self.name.setFocus()

    def _barcode_rows(self, form: QFormLayout, spec: FieldSpec, names: list[str]) -> None:
        """A barcode's kind, the fields it holds and whether their names come first."""
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QRadioButton

        from planetpdf.core.forms.barcodes import SYMBOLOGIES, BarcodeSettings

        current = spec.barcode or BarcodeSettings()
        self.symbology = QComboBox()
        for key, text in SYMBOLOGIES.items():
            self.symbology.addItem(text, key)
        self.symbology.setCurrentIndex(max(0, self.symbology.findData(current.symbology)))
        form.addRow("&Barcode:", self.symbology)
        self.every_field = QRadioButton("Every field in the form")
        self.these_fields = chosen = QRadioButton("These fields:")
        self.every_field.setChecked(not current.fields)
        chosen.setChecked(bool(current.fields))
        form.addRow("&Holds:", self.every_field)
        form.addRow("", chosen)
        self.encoded = QListWidget()
        self.encoded.setMaximumHeight(140)
        for name in [*current.fields, *(n for n in names if n not in current.fields)]:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in current.fields else Qt.CheckState.Unchecked)
            self.encoded.addItem(item)
        self.encoded.setEnabled(chosen.isChecked())
        chosen.toggled.connect(self.encoded.setEnabled)
        form.addRow("", self.encoded)
        self.with_names = QCheckBox("Field names first (a line of names, then a line of values)")
        self.with_names.setChecked(current.names)
        form.addRow("", self.with_names)
        note = QLabel("PlanetPDF draws the barcode from the form's values whenever they change. Other PDF "
                      "programs show the last one drawn.")
        note.setWordWrap(True)
        form.addRow("", note)

    def spec(self) -> FieldSpec:
        s = self._spec
        changes: dict = dict(name=self.name.text().strip(), tooltip=self.tooltip.text().strip(),
                             required=self.required.isChecked(), read_only=self.read_only.isChecked())
        if self.font_size is not None:
            changes["font_size"] = float(self.font_size.value())
        if self.check_text is not None:
            text = self.check_text.toPlainText().strip()
            if s.labels or text:  # (another program's checkbox becomes ours once it has text)
                changes["labels"] = (text,)
            elif not s.labels:
                changes["font_size"] = s.font_size  # (others' checkbox, no text: as it was)
        if self.multiline is not None:
            changes["multiline"] = self.multiline.isChecked()
        if self.max_length is not None:
            changes["max_length"] = self.max_length.value()
        if self.date_format is not None:
            changes["date_format"] = self.date_format.currentText().strip()
        if self.editable is not None:
            changes["editable_combo"] = self.editable.isChecked()
        if self.multiselect is not None:
            changes["multiselect"] = self.multiselect.isChecked()
        if self.symbology is not None:
            from planetpdf.core.forms.barcodes import BarcodeSettings

            fields = () if self.every_field.isChecked() else tuple(
                self.encoded.item(n).text() for n in range(self.encoded.count())
                if self.encoded.item(n).checkState() == Qt.CheckState.Checked)
            changes["barcode"] = BarcodeSettings(self.symbology.currentData(), fields, self.with_names.isChecked())
        if self.caption is not None:
            action = self.action.currentData()
            changes.update(caption=self.caption.text().strip(),
                           action="url:" + self.address.text().strip() if action == "url" else action)
        if self.buttons is not None:
            options, labels, rects = self.buttons.result()
            changes.update(options=options, labels=labels, option_rects=rects,
                           value=s.value if s.value in options else None)
        if self.choices is not None:
            options = tuple(line.strip() for line in self.choices.toPlainText().splitlines() if line.strip())
            changes["options"] = options
            if s.kind is FieldKind.COMBO:
                changes["value"] = s.value if s.value in options or changes.get("editable_combo") else ""
            elif s.kind is FieldKind.LIST:
                changes["value"] = [v for v in (s.value or []) if v in options]
        return replace(s, **changes)

    def _accept(self) -> None:
        try:
            spec = self.spec()
            spec.validate()
            if self.symbology is not None and not self.every_field.isChecked() and not spec.barcode.fields:
                raise PdfError("Tick the fields the barcode holds, or choose every field.")
        except PdfError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


def radio_rects(rects: tuple, count: int) -> tuple:
    """Keep the existing buttons' places; new buttons go below the last one."""
    rects = list(rects[:count])
    while len(rects) < count:
        x0, y0, x1, y1 = rects[-1]
        step = (y1 - y0) + 8
        rects.append((x0, y0 + step, x1, y1 + step))
    return tuple(rects)


def value_from_label(label: str, number: int, taken: set[str]) -> str:
    """A button's value in the form data, made from its text: its first words (a PDF
    name can't hold a paragraph), unique in the group; "ChoiceN" when it has no text."""
    first = next((line for line in label.splitlines() if line.strip()), "")
    words = re.sub(r"[^\w .,'&-]+", " ", first).split()
    value = ""
    for word in words:
        if len(value) + len(word) + 1 > 40:
            break
        value = f"{value} {word}".strip()
    value = value or f"Choice{number}"
    unique, n = value, 2
    while unique in taken:
        unique, n = f"{value} {n}", n + 1
    return unique


class _ButtonRow(QWidget):
    def __init__(self, editor: RadioButtonsEditor, label: str, value: str, rect, manual: bool) -> None:
        super().__init__(editor.rows_box)
        self.rect = rect  # this button's place on the page (None: new, placed below the last)
        self.manual = manual  # (the value was typed: not made from the text any more)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.number = QLabel(self)
        self.number.setFixedWidth(18)
        self.number.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.number)
        self.text = QPlainTextEdit(label, self)
        self.text.setPlaceholderText("Text shown next to the button (paste or type; Enter starts a new line)")
        self.text.setFixedHeight(64)
        self.text.setTabChangesFocus(True)
        layout.addWidget(self.text, 1)
        side = QVBoxLayout()
        self.value = QLineEdit(value, self)
        self.value.setPlaceholderText("Value")
        self.value.setToolTip("What the form data says when this button is chosen (made from the text unless "
                              "you type one)")
        self.value.setFixedWidth(150)
        self.value.textEdited.connect(lambda _t: setattr(self, "manual", True))
        side.addWidget(self.value)
        self.remove = QToolButton(self)
        self.remove.setText("Remove")
        self.remove.clicked.connect(lambda: editor.remove_row(self))
        side.addWidget(self.remove, 0, Qt.AlignmentFlag.AlignLeft)
        side.addStretch(1)
        layout.addLayout(side)
        self.text.textChanged.connect(editor.refresh_values)


class RadioButtonsEditor(QWidget):
    """Prepare Form ▸ radio group properties: one row per button (user 2026-10-02: each
    button its own entry, paragraphs pasted in with their line breaks)."""

    def __init__(self, spec: FieldSpec, parent: QWidget) -> None:
        super().__init__(parent)
        self._spec = spec
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.rows_box = QWidget(self)
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows_box)
        scroll.setMinimumHeight(240)
        scroll.setMinimumWidth(520)
        outer.addWidget(scroll, 1)
        add = QPushButton("Add Button", self)
        add.clicked.connect(lambda: self.add_row())
        hint = QLabel("New buttons go below the last one; drag them into place afterwards.", self)
        hint.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(add)
        bottom.addWidget(hint, 1)
        outer.addLayout(bottom)
        self.rows: list[_ButtonRow] = []
        labels = spec.labels or ("",) * len(spec.options)
        for n, (value, label) in enumerate(zip(spec.options, labels)):
            rect = spec.option_rects[n] if n < len(spec.option_rects) else None
            # a value made from the text keeps following it; any other (typed, or from another
            # program's form) is kept as it is
            made = bool(label) and (value_from_label(label, n + 1, set()) == value or value == f"Choice{n + 1}")
            self.add_row(label, value, rect, manual=not made)

    def add_row(self, label: str = "", value: str = "", rect=None, *, manual: bool = False) -> _ButtonRow:
        row = _ButtonRow(self, label, value, rect, manual)
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.refresh_values()
        if not label:
            row.text.setFocus()
        return row

    def remove_row(self, row: _ButtonRow) -> None:
        if len(self.rows) <= 2:
            return  # (a radio group needs two buttons)
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.refresh_values()

    def refresh_values(self) -> None:
        """Number the rows; values not typed by hand follow their text."""
        taken = {r.value.text().strip() for r in self.rows if r.manual and r.value.text().strip()}
        for n, row in enumerate(self.rows, 1):
            row.number.setText(f"{n}.")
            row.remove.setEnabled(len(self.rows) > 2)
            if not row.manual:
                value = value_from_label(row.text.toPlainText(), n, taken)
                row.value.setText(value)
            taken.add(row.value.text().strip())

    def result(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple]:
        """(values, texts, places) of the buttons, in order."""
        options = tuple(r.value.text().strip() for r in self.rows)
        labels = tuple(r.text.toPlainText().strip("\n") for r in self.rows)
        rects: list = []
        for row in self.rows:
            if row.rect is not None:
                rects.append(row.rect)
            else:
                rects = list(radio_rects(tuple(rects) or self._spec.option_rects[:1], len(rects) + 1))
        return options, labels, tuple(rects)
