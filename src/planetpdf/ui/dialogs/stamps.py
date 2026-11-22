"""Stamp library manager and the text-stamp editor (with live preview)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError
from planetpdf.core.resources import STAMP_FONTS, font_label
from planetpdf.core.stamps import BLUE, BORDERS, TOKENS, StampSpec, render_text_stamp, resolve_lines
from planetpdf.ui.viewer.qimage import pdf_preview

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _resolve_kwargs(window: MainWindow) -> dict:
    s = window.settings
    return {"user": s.user_name or "Your Name", "company": s.company_name or "Your Company", "now": datetime.now(), "date_format": s.date_format, "time_format": s.time_format}


class StampEditorDialog(QDialog):
    """Edit a text stamp's text and style; ``spec()`` gives the result."""

    def __init__(self, window: MainWindow, spec: StampSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self._base = spec
        self._color = spec.color
        self.setWindowTitle("Text Stamp")
        self.name = QLineEdit(spec.name, self)
        self.line1 = QLineEdit(spec.lines[0] if spec.lines else "", self)
        self.line2 = QLineEdit(spec.lines[1] if len(spec.lines) > 1 else "", self)
        self.line2.setPlaceholderText("Optional second line, e.g. {date}")
        tokens = QHBoxLayout()
        for token, meaning in TOKENS.items():
            button = QPushButton(token, self)
            button.setToolTip(f"Insert {meaning}")
            button.clicked.connect(lambda _c=False, t=token: self.line2.insert(t))
            tokens.addWidget(button)
        tokens.addStretch(1)
        self.color_button = QPushButton(self)
        self.color_button.clicked.connect(self._pick_color)
        self.style = QComboBox(self)
        self.style.setAccessibleName("Style")
        self.style.addItem("Acrobat style (shaded tile)", "tile")
        self.style.addItem("Outlined text with a border", "classic")
        self.style.setCurrentIndex(max(self.style.findData(spec.style if spec.style in ("tile", "classic") else "classic"), 0))
        self.font = QComboBox(self)
        for key in STAMP_FONTS:
            self.font.addItem(font_label(key), key)
        self.font.setCurrentIndex(max(self.font.findData(spec.font), 0))
        self.bold = QCheckBox("Bold", self)
        self.bold.setChecked(spec.bold)
        self.border = QComboBox(self)
        for b in BORDERS:
            self.border.addItem(b.title(), b)
        self.border.setCurrentIndex(max(self.border.findData(spec.border), 0))
        self.rotation = QSpinBox(self)
        self.rotation.setRange(-90, 90)
        self.rotation.setSuffix("°")
        self.rotation.setValue(int(spec.rotation))
        self.rotation.setToolTip("Positive tilts counterclockwise")
        self.opacity = QSpinBox(self)
        self.opacity.setRange(10, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(int(round(spec.opacity * 100)))
        self.preview = QLabel(self)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 140)
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Main text:", self.line1)
        form.addRow("Second line:", self.line2)
        form.addRow("", tokens)
        form.addRow("Color:", self.color_button)
        form.addRow("Style:", self.style)
        font_row = QHBoxLayout()
        font_row.addWidget(self.font)
        font_row.addWidget(self.bold)
        form.addRow("Font:", font_row)
        form.addRow("Border:", self.border)
        self._classic_only = (self.font, self.bold, self.border)
        form.addRow("Rotation:", self.rotation)
        form.addRow("Opacity:", self.opacity)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        for widget in (self.line1, self.line2):
            widget.textChanged.connect(self._update)
        for widget in (self.font, self.border, self.style):
            widget.currentIndexChanged.connect(self._update)
        self.bold.toggled.connect(self._update)
        self.rotation.valueChanged.connect(self._update)
        self.opacity.valueChanged.connect(self._update)
        self._update()

    def spec(self) -> StampSpec:
        lines = tuple(t for t in (self.line1.text().strip(), self.line2.text().strip()) if t)
        style = self.style.currentData()
        return replace(
            self._base,
            name=self.name.text().strip() or (lines[0].title() if lines else "Stamp"),
            lines=lines,
            color=self._color,
            style=style,
            # a copied built-in keeps its exact Acrobat colors until you pick another color
            tint=self._base.tint if self._color == self._base.color else "",
            big=min(self._base.big, max(len(lines) - 1, 0)),
            font="stamp" if style == "tile" else (self.font.currentData() if self.font.currentData() else "sans"),
            bold=self.bold.isChecked(),
            border=self.border.currentData(),
            rotation=float(self.rotation.value()),
            opacity=self.opacity.value() / 100,
        )

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor.fromRgbF(*self._color), self, "Stamp Color")
        if color.isValid():
            self._color = (color.redF(), color.greenF(), color.blueF())
            self._update()

    def _update(self) -> None:
        name = QColor.fromRgbF(*self._color).name()
        self.color_button.setText(name.upper())
        self.color_button.setStyleSheet(f"background: {name}; color: white;")
        for widget in self._classic_only:
            widget.setEnabled(self.style.currentData() == "classic")
        try:
            spec = self.spec()
            pdf = render_text_stamp(spec, resolve_lines(spec, **_resolve_kwargs(self._window)))
            self.preview.setPixmap(pdf_preview(pdf, 320, 140))
            self.error.clear()
        except PdfError as exc:
            self.preview.clear()
            self.error.setText(str(exc))

    def _accept(self) -> None:
        if not self.line1.text().strip():
            self.error.setText("Enter the stamp's main text.")
            return
        self.accept()


class StampManagerDialog(QDialog):
    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Stamps and Seals")
        self.resize(560, 460)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Custom stamps")
        self.list.setIconSize(QSize(180, 70))
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _i: self._place())
        self.new_button = QPushButton("New Text Stamp…", self)
        self.new_button.clicked.connect(self._new)
        self.seal_button = QPushButton("Import Seal Image…", self)
        self.seal_button.clicked.connect(self._import_seal)
        self.edit_button = QPushButton("Edit…", self)
        self.edit_button.clicked.connect(self._edit)
        self.copy_button = QPushButton("Duplicate", self)
        self.copy_button.clicked.connect(self._duplicate)
        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self._delete)
        self.place_button = QPushButton("Place on Page", self)
        self.place_button.clicked.connect(self._place)
        where = QLabel(f"Custom stamps are saved in your profile: {window.profile.stamps_dir}", self)
        where.setWordWrap(True)
        where.setStyleSheet("color: gray;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        side = QVBoxLayout()
        for button in (self.new_button, self.seal_button, self.edit_button, self.copy_button, self.delete_button):
            side.addWidget(button)
        side.addStretch(1)
        side.addWidget(self.place_button)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side)
        layout = QVBoxLayout(self)
        layout.addLayout(row, 1)
        layout.addWidget(where)
        layout.addWidget(buttons)
        self.populate()

    def populate(self, select: str | None = None) -> None:
        library = self._window.stamp_library
        self.list.clear()
        for spec in library.all:
            try:
                icon = QIcon(pdf_preview(library.render(spec, **_resolve_kwargs(self._window)), 180, 70))
            except (PdfError, OSError):
                icon = QIcon()
            row = QListWidgetItem(icon, spec.name + ("" if not spec.builtin else "  (built-in)"))
            row.setData(Qt.ItemDataRole.UserRole, spec.stamp_id)
            self.list.addItem(row)
            if spec.stamp_id == select:
                row.setSelected(True)
        self._update_buttons()

    def _selected(self) -> StampSpec | None:
        rows = self.list.selectedItems()
        if not rows:
            return None
        return self._window.stamp_library.get(rows[0].data(Qt.ItemDataRole.UserRole))

    def _update_buttons(self) -> None:
        spec = self._selected()
        self.edit_button.setEnabled(spec is not None and not spec.builtin and spec.kind == "text")
        self.delete_button.setEnabled(spec is not None and not spec.builtin)
        self.copy_button.setEnabled(spec is not None and spec.kind == "text")
        self.place_button.setEnabled(spec is not None and self._window.session is not None)

    def _new(self) -> StampSpec | None:
        base = StampSpec(stamp_id="new", name="", lines=("CHECKED", "{date}"), color=BLUE, style="tile", font="stamp")
        dialog = StampEditorDialog(self._window, base, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        s = dialog.spec()
        new = self._window.stamp_library.add_text_stamp(
            s.name, list(s.lines), color=s.color, font=s.font, bold=s.bold, border=s.border, rotation=s.rotation,
            opacity=s.opacity, style=s.style, tint=s.tint, big=s.big,
        )
        self.populate(select=new.stamp_id)
        return new

    new_text_stamp = _new  # Stamp ▾ ▸ Custom Stamps ▸ Create Text Stamp…

    def _edit(self) -> None:
        spec = self._selected()
        if spec is None or spec.builtin:
            return
        dialog = StampEditorDialog(self._window, spec, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._window.stamp_library.update(dialog.spec())
            self.populate(select=spec.stamp_id)

    def _duplicate(self) -> None:
        spec = self._selected()
        if spec is not None:
            new = self._window.stamp_library.copy_of(spec)
            self.populate(select=new.stamp_id)

    def _delete(self) -> None:
        spec = self._selected()
        if spec is not None and not spec.builtin and self._window.ask("Delete Stamp", f"Delete “{spec.name}” from your stamps?"):
            self._window.stamp_library.remove(spec.stamp_id)
            self.populate()

    def _import_seal(self) -> StampSpec | None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Seal Image", "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return None
        name, ok = QInputDialog.getText(self, "Seal Name", "Name for this seal:", text=Path(path).stem)
        if not ok:
            return None
        try:
            new = self._window.stamp_library.add_seal(name.strip() or Path(path).stem, Path(path).read_bytes())
        except (PdfError, OSError) as exc:
            self._window.show_message("Import Seal", str(exc))
            return None
        self.populate(select=new.stamp_id)
        return new

    import_seal = _import_seal  # Stamp ▾ ▸ Custom Stamps ▸ Create from Image…

    def _place(self) -> None:
        spec = self._selected()
        if spec is not None and self._window.session is not None:
            self.accept()
            self._window.markup.place_stamp(spec.stamp_id)
