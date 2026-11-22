"""Dialogs for the Document menu: protection, header/footer (page numbers, Bates),
watermark, crop, split, reduce size, properties."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError, Protection
from planetpdf.core.optimize import PRESETS, ReduceOptions
from planetpdf.core.page_marks import POSITIONS, TOKENS, BackgroundSpec, HeaderFooterSpec, WatermarkSpec, image_bytes
from planetpdf.core.pagerange import format_page_ranges
from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit


def _buttons(dialog: QDialog, ok_text: str = "OK") -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class _ColorButton(QPushButton):
    def __init__(self, color: tuple[float, float, float], parent: QWidget) -> None:
        super().__init__(parent)
        self.color = color
        self.clicked.connect(self._pick)
        self._paint()

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor.fromRgbF(*self.color), self, "Color")
        if c.isValid():
            self.color = (c.redF(), c.greenF(), c.blueF())
            self._paint()

    def _paint(self) -> None:
        name = QColor.fromRgbF(*self.color).name()
        self.setText(name.upper())
        self.setStyleSheet(f"background: {name}; color: white;")


# --- protection -------------------------------------------------------------------------------


class ProtectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Protect with Password")
        self.setMinimumWidth(440)
        self.open_pw = QLineEdit(self)
        self.open_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.open_pw2 = QLineEdit(self)
        self.open_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.perm_pw = QLineEdit(self)
        self.perm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.perm_pw2 = QLineEdit(self)
        self.perm_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.allow = {
            "allow_print": QCheckBox("Printing", self),
            "allow_copy": QCheckBox("Copying text and images", self),
            "allow_edit": QCheckBox("Changing the document", self),
            "allow_comments": QCheckBox("Adding comments", self),
            "allow_forms": QCheckBox("Filling in forms", self),
        }
        for box in self.allow.values():
            box.setChecked(True)
        open_group = QGroupBox("Password to open the document", self)
        of = QFormLayout(open_group)
        of.addRow("Password:", self.open_pw)
        of.addRow("Confirm:", self.open_pw2)
        perm_group = QGroupBox("Restrict what others can do (needs a permissions password to change)", self)
        pf = QFormLayout(perm_group)
        pf.addRow("Permissions password:", self.perm_pw)
        pf.addRow("Confirm:", self.perm_pw2)
        allowed = QGridLayout()
        for i, box in enumerate(self.allow.values()):
            allowed.addWidget(box, i // 2, i % 2)
        pf.addRow("Allow:", allowed)
        note = QLabel("AES-256 encryption, applied when you save. Keep the passwords safe: they can't be recovered.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for w in (open_group, perm_group, note, self.error, box):
            layout.addWidget(w)

    def protection(self) -> Protection:
        return Protection(self.open_pw.text(), self.perm_pw.text(), **{k: b.isChecked() for k, b in self.allow.items()})

    def prefill(self, protection: Protection) -> None:
        """Start from the original's protection (a new file made from it)."""
        for edit in (self.open_pw, self.open_pw2):
            edit.setText(protection.open_password)
        for edit in (self.perm_pw, self.perm_pw2):
            edit.setText(protection.permissions_password)
        for key, box in self.allow.items():
            box.setChecked(getattr(protection, key))

    def _accept(self) -> None:
        if self.open_pw.text() != self.open_pw2.text() or self.perm_pw.text() != self.perm_pw2.text():
            self.error.setText("The passwords don't match.")
            return
        restricted = not all(b.isChecked() for b in self.allow.values())
        if restricted and not self.perm_pw.text():
            self.error.setText("Set a permissions password so the restrictions can't simply be switched off.")
            return
        try:
            self.protection().validate()
        except PdfError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


# --- header / footer / page numbers / Bates ---------------------------------------------------------

POSITION_LABELS = {
    "top_left": "Top left", "top_center": "Top center", "top_right": "Top right",
    "bottom_left": "Bottom left", "bottom_center": "Bottom center", "bottom_right": "Bottom right",
}


class HeaderFooterDialog(QDialog):
    """``preset``: "header" (blank), "page_numbers", or "bates"."""

    def __init__(self, page_count: int, selected: list[int], preset: str = "header", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle({"page_numbers": "Add Page Numbers", "bates": "Bates Numbering"}.get(preset, "Header & Footer"))
        self.setMinimumWidth(560)
        self.fields: dict[str, QLineEdit] = {}
        grid = QGridLayout()
        for i, position in enumerate(POSITIONS):
            edit = QLineEdit(self)
            edit.setPlaceholderText(POSITION_LABELS[position])
            self.fields[position] = edit
            grid.addWidget(QLabel(POSITION_LABELS[position] + ":", self), i // 3 * 2, i % 3)
            grid.addWidget(edit, i // 3 * 2 + 1, i % 3)
        if preset == "page_numbers":
            self.fields["bottom_center"].setText("Page {page} of {pages}")
        elif preset == "bates":
            self.fields["bottom_right"].setText("{bates}")
        tokens = QLabel("Fields: " + ", ".join(f"{t} = {m}" for t, m in TOKENS.items()), self)
        tokens.setWordWrap(True)
        tokens.setStyleSheet("color: gray;")

        self.font = QComboBox(self)
        self.font.setAccessibleName("Font")
        self.font.addItem("Helvetica (sans)", "sans")
        self.font.addItem("Times (serif)", "serif")
        self.bold = QCheckBox("Bold", self)
        self.size = QDoubleSpinBox(self)
        self.size.setRange(5, 36)
        self.size.setValue(10)
        self.color = _ColorButton((0, 0, 0), self)
        self.margin = QDoubleSpinBox(self)
        self.margin.setRange(0.1, 3)
        self.margin.setSingleStep(0.1)
        self.margin.setSuffix(" in")
        self.margin.setValue(0.5)
        self.first_number = QSpinBox(self)
        self.first_number.setRange(0, 1_000_000)
        self.first_number.setValue(1)
        style = QFormLayout()
        font_row = QHBoxLayout()
        font_row.addWidget(self.font)
        font_row.addWidget(self.bold)
        style.addRow("Font:", font_row)
        style.addRow("Size:", self.size)
        style.addRow("Color:", self.color)
        style.addRow("Distance from edge:", self.margin)
        style.addRow("First page number:", self.first_number)

        self.bates_prefix = QLineEdit("PN-" if preset == "bates" else "", self)
        self.bates_suffix = QLineEdit(self)
        self.bates_start = QSpinBox(self)
        self.bates_start.setRange(0, 999_999_999)
        self.bates_start.setValue(1)
        self.bates_digits = QSpinBox(self)
        self.bates_digits.setRange(1, 12)
        self.bates_digits.setValue(6)
        bates = QGroupBox("Bates numbering ({bates})", self)
        bf = QFormLayout(bates)
        bf.addRow("Prefix:", self.bates_prefix)
        bf.addRow("Suffix:", self.bates_suffix)
        bf.addRow("Start at:", self.bates_start)
        bf.addRow("Digits:", self.bates_digits)

        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        rf = QFormLayout()
        rf.addRow("Pages:", self.range_edit)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(tokens)
        row = QHBoxLayout()
        row.addLayout(style, 1)
        row.addWidget(bates, 1)
        layout.addLayout(row)
        layout.addLayout(rf)
        note = QLabel("Added as part of the page (every viewer and printer shows it). Undo removes it.", self)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)
        self.buttons = _buttons(self, "Add")
        self.range_edit.validityChanged.connect(self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        layout.addWidget(self.buttons)

    def pages(self) -> list[int]:
        return self.range_edit.pages()

    def load(self, spec: HeaderFooterSpec) -> None:
        """Fill in the settings of an existing header/footer (… ▸ Update)."""
        for position, edit in self.fields.items():
            edit.setText(spec.texts.get(position, ""))
        self.font.setCurrentIndex(max(0, self.font.findData(spec.font)))
        self.bold.setChecked(spec.bold)
        self.size.setValue(spec.size)
        self.color.color = tuple(spec.color)
        self.color._paint()
        self.margin.setValue(spec.margin / 72)
        self.first_number.setValue(spec.first_number)
        self.bates_prefix.setText(spec.bates_prefix)
        self.bates_suffix.setText(spec.bates_suffix)
        self.bates_start.setValue(spec.bates_start)
        self.bates_digits.setValue(spec.bates_digits)

    def spec(self) -> HeaderFooterSpec:
        return HeaderFooterSpec(
            texts={p: e.text() for p, e in self.fields.items() if e.text().strip()},
            font=self.font.currentData(),
            bold=self.bold.isChecked(),
            size=self.size.value(),
            color=self.color.color,
            margin=self.margin.value() * 72,
            first_number=self.first_number.value(),
            bates_prefix=self.bates_prefix.text(),
            bates_suffix=self.bates_suffix.text(),
            bates_start=self.bates_start.value(),
            bates_digits=self.bates_digits.value(),
        )


# --- watermark ----------------------------------------------------------------------------------


class WatermarkDialog(QDialog):
    def __init__(self, page_count: int, selected: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Watermark")
        self.setMinimumWidth(460)
        self._image: bytes | None = None
        self.use_text = QRadioButton("Text:", self)
        self.use_image = QRadioButton("Image:", self)
        group = QButtonGroup(self)
        group.addButton(self.use_text)
        group.addButton(self.use_image)
        self.use_text.setChecked(True)
        self.text = QLineEdit("CONFIDENTIAL", self)
        self.text.setAccessibleName("Watermark text")
        self.image_label = QLabel("(none)", self)
        choose = QPushButton("Choose…", self)
        choose.clicked.connect(self._choose)
        self.color = _ColorButton((0.8, 0.1, 0.1), self)
        self.opacity = QSpinBox(self)
        self.opacity.setRange(5, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(25)
        self.rotation = QSpinBox(self)
        self.rotation.setRange(-90, 90)
        self.rotation.setSuffix("°")
        self.rotation.setValue(45)
        self.scale = QSpinBox(self)
        self.scale.setRange(10, 100)
        self.scale.setSuffix(" % of page width")
        self.scale.setValue(60)
        self.behind = QCheckBox("Behind the page content (uncheck to put it on top)", self)
        self.behind.setChecked(True)  # a watermark is an underlay (user); use a Stamp for on top
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        form = QFormLayout()
        text_row = QHBoxLayout()
        text_row.addWidget(self.text)
        form.addRow(self.use_text, text_row)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_label, 1)
        image_row.addWidget(choose)
        form.addRow(self.use_image, image_row)
        form.addRow("Color:", self.color)
        form.addRow("Opacity:", self.opacity)
        form.addRow("Rotation:", self.rotation)
        form.addRow("Size:", self.scale)
        form.addRow("", self.behind)
        form.addRow("Pages:", self.range_edit)
        self.buttons = _buttons(self, "Add")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Watermark Image", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.use_image_file(Path(path))

    def use_image_file(self, path: Path) -> None:
        self._image = image_bytes(path)
        self.image_label.setText(path.name)
        self.use_image.setChecked(True)

    def pages(self) -> list[int]:
        return self.range_edit.pages()

    def load(self, spec: WatermarkSpec) -> None:
        """Fill in the settings of an existing watermark (Watermark ▸ Update)."""
        self.text.setText(spec.text)
        self._image = spec.image
        if spec.image:
            self.image_label.setText("(the current picture)")
        (self.use_image if spec.image else self.use_text).setChecked(True)
        self.color.color = tuple(spec.color)
        self.color._paint()
        self.opacity.setValue(round(spec.opacity * 100))
        self.rotation.setValue(round(spec.rotation))
        self.scale.setValue(round(spec.scale * 100))
        self.behind.setChecked(spec.behind)

    def spec(self) -> WatermarkSpec:
        return WatermarkSpec(
            text=self.text.text(),
            image=self._image if self.use_image.isChecked() else None,
            color=self.color.color,
            opacity=self.opacity.value() / 100,
            rotation=float(self.rotation.value()),
            scale=self.scale.value() / 100,
            behind=self.behind.isChecked(),
        )


# --- background -------------------------------------------------------------------------------------


class BackgroundDialog(QDialog):
    """Edit PDF ▸ More ▸ Background (Acrobat): a color or a picture under the pages."""

    def __init__(self, page_count: int, selected: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Background")
        self.setMinimumWidth(440)
        self._image: bytes | None = None
        self.use_color = QRadioButton("Color:", self)
        self.use_image = QRadioButton("Picture:", self)
        group = QButtonGroup(self)
        group.addButton(self.use_color)
        group.addButton(self.use_image)
        self.use_color.setChecked(True)
        self.color = _ColorButton((1.0, 1.0, 0.85), self)
        self.image_label = QLabel("(none)", self)
        choose = QPushButton("Choose…", self)
        choose.clicked.connect(self._choose)
        self.opacity = QSpinBox(self)
        self.opacity.setRange(5, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(100)
        self.scale = QSpinBox(self)
        self.scale.setRange(5, 100)
        self.scale.setSuffix(" % of the page")
        self.scale.setValue(100)
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        form = QFormLayout()
        form.addRow(self.use_color, self.color)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_label, 1)
        image_row.addWidget(choose)
        form.addRow(self.use_image, image_row)
        form.addRow("Picture size:", self.scale)
        form.addRow("Opacity:", self.opacity)
        form.addRow("Pages:", self.range_edit)
        note = QLabel("Goes under everything on the page. Undo, or Background ▸ Remove, takes it off.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        self.buttons = _buttons(self, "Add")
        self.range_edit.validityChanged.connect(self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.buttons)

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Background Picture", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.use_image_file(Path(path))

    def use_image_file(self, path: Path) -> None:
        self._image = image_bytes(path)
        self.image_label.setText(path.name)
        self.use_image.setChecked(True)

    def pages(self) -> list[int]:
        return self.range_edit.pages()

    def load(self, spec: BackgroundSpec) -> None:
        if spec.color is not None:
            self.color.color = tuple(spec.color)
            self.color._paint()
        self._image = spec.image
        if spec.image:
            self.image_label.setText("(the current picture)")
        (self.use_image if spec.image else self.use_color).setChecked(True)
        self.opacity.setValue(round(spec.opacity * 100))
        self.scale.setValue(round(spec.scale * 100))

    def spec(self) -> BackgroundSpec:
        image = self._image if self.use_image.isChecked() else None
        return BackgroundSpec(color=None if image else self.color.color, image=image,
                              opacity=self.opacity.value() / 100, scale=self.scale.value() / 100)


# --- crop -------------------------------------------------------------------------------------------


class CropDialog(QDialog):
    DRAW = 2  # result code: the user wants to draw the crop box instead

    def __init__(self, page_count: int, selected: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crop Pages")
        self.margins = {}
        form = QFormLayout()
        for side in ("left", "top", "right", "bottom"):
            spin = QDoubleSpinBox(self)
            spin.setRange(0, 10)
            spin.setSingleStep(0.1)
            spin.setSuffix(" in")
            self.margins[side] = spin
            form.addRow(f"Trim {side}:", spin)
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        form.addRow("Pages:", self.range_edit)
        note = QLabel("Cropping only hides the edges; nothing is deleted, and it can be undone.", self)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        draw = QPushButton("Draw Crop Box on the Page Instead…", self)
        draw.clicked.connect(lambda: self.done(self.DRAW))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(draw)
        layout.addWidget(_buttons(self, "Crop"))

    def values(self) -> tuple[float, float, float, float]:
        return tuple(self.margins[s].value() * 72 for s in ("left", "top", "right", "bottom"))

    def pages(self) -> list[int]:
        return self.range_edit.pages()


# --- split ---------------------------------------------------------------------------------------------


class SplitDialog(QDialog):
    def __init__(self, page_count: int, has_bookmarks: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Split Document")
        self.every = QRadioButton("Every", self)
        self.every_n = QSpinBox(self)
        self.every_n.setAccessibleName("Pages in each file")
        self.every_n.setRange(1, max(1, page_count))
        self.every_n.setValue(1)
        self.ranges = QRadioButton("These groups:", self)
        self.ranges_edit = QLineEdit(self)
        self.ranges_edit.setPlaceholderText("e.g. 1-3; 4-10; 11-")
        self.bookmarks = QRadioButton("At each top-level bookmark", self)
        self.bookmarks.setEnabled(has_bookmarks)
        group = QButtonGroup(self)
        for b in (self.every, self.ranges, self.bookmarks):
            group.addButton(b)
        self.every.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.every)
        row.addWidget(self.every_n)
        row.addWidget(QLabel("page(s) per file", self))
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        r2 = QHBoxLayout()
        r2.addWidget(self.ranges)
        r2.addWidget(self.ranges_edit, 1)
        layout.addLayout(r2)
        layout.addWidget(self.bookmarks)
        note = QLabel("You'll choose a folder next; the files are named after this document.", self)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Choose Folder…"))


# --- reduce size ----------------------------------------------------------------------------------------


class ReduceSizeDialog(QDialog):
    """Compress PDF: how much, what to do with scanned pages, and where the result goes
    (this document, one undo step; or a smaller copy as a new file)."""

    def __init__(self, current_size: int, parent: QWidget | None = None, *, pages: int = 0, scanned: int = 0,
                 in_place: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compress PDF")
        self.preset = QComboBox(self)
        self.preset.addItem("Smallest (for email and screens, 96 dpi pictures)", "screen")
        self.preset.addItem("Balanced (150 dpi pictures)", "standard")
        self.preset.addItem("High quality (300 dpi pictures, for printing)", "print")
        self.preset.addItem("Keep pictures as they are (compact the rest)", "none")
        self.preset.setCurrentIndex(1)
        form = QFormLayout()
        form.addRow("Current size:", QLabel(f"{current_size / 1_048_576:.2f} MB", self))
        form.addRow("Quality:", self.preset)
        scans = QGroupBox("Scanned pages", self)
        scan_layout = QVBoxLayout(scans)
        if scanned:
            found = QLabel(f"{scanned} of {pages} page{'s' if pages != 1 else ''} "
                           f"{'are' if scanned != 1 else 'is'} scanned (a picture of the whole page).", scans)
        else:
            found = QLabel("No scanned pages found in this document.", scans)
        found.setWordWrap(True)
        scan_layout.addWidget(found)
        self.black_white = QCheckBox("Store text-only scans in black and white (much smaller)", scans)
        self.black_white.setToolTip("Pages that are only dark text on paper: the paper's tint, specks, and shading "
                                    "of the scan go; pages with color, photos, or shaded boxes are left in color "
                                    "or gray")
        self.black_white.setChecked(True)
        self.gray = QCheckBox("Store scans without real color in gray", scans)
        self.gray.setToolTip("Pages that look gray anyway (faint paper color only): a third of the size, "
                             "same look")
        self.gray.setChecked(True)
        scan_layout.addWidget(self.black_white)
        scan_layout.addWidget(self.gray)
        scans.setEnabled(bool(scanned))
        where = QGroupBox("Result", self)
        where_layout = QVBoxLayout(where)
        self.in_place = QRadioButton("Make this document smaller (Save it afterwards; Undo puts it back)", where)
        self.copy = QRadioButton("Save a smaller copy as a new file (this document isn't changed)", where)
        (self.in_place if in_place else self.copy).setChecked(True)
        where_layout.addWidget(self.in_place)
        where_layout.addWidget(self.copy)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(scans)
        layout.addWidget(where)
        layout.addWidget(_buttons(self, "Compress"))

    def options(self) -> ReduceOptions:
        key = self.preset.currentData()
        scans = dict(scans_black_white=self.black_white.isChecked(), scans_gray=self.gray.isChecked())
        if key == "none":
            return ReduceOptions(image_dpi=None, **scans)
        dpi, quality = PRESETS[key]
        return ReduceOptions(image_dpi=dpi, jpeg_quality=quality, **scans)


class SpaceUsageDialog(QDialog):
    """What takes the room in the file (Acrobat's Audit Space Usage)."""

    def __init__(self, usage, file_size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Space Usage")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        for n, (what, size, percent) in enumerate(usage.rows()):
            grid.addWidget(QLabel(what, self), n, 0)
            bar = QProgressBar(self)
            bar.setRange(0, 100)
            bar.setValue(round(percent))
            bar.setTextVisible(False)
            bar.setMaximumHeight(12)
            grid.addWidget(bar, n, 1)
            grid.addWidget(QLabel(_size(size), self), n, 2, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(QLabel(f"{percent:.0f}%", self), n, 3, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(grid)
        note = f"File size: {_size(file_size)}."
        if usage.scanned_pages:
            note += (f" {usage.scanned_pages} page{'s are' if usage.scanned_pages != 1 else ' is'} scanned: "
                     "Compress… stores text-only scans in black and white.")
        label = QLabel(note, self)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _size(size: int) -> str:
    if size >= 1_048_576:
        return f"{size / 1_048_576:.2f} MB"
    return f"{max(size, 1) / 1024:.0f} KB"


# --- properties -----------------------------------------------------------------------------------------


class PropertiesDialog(QDialog):
    def __init__(self, metadata: dict[str, str], facts, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.setMinimumWidth(520)
        self.edits: dict[str, QLineEdit] = {}
        desc = QGroupBox("Description", self)
        df = QFormLayout(desc)
        for key in ("title", "author", "subject", "keywords"):
            edit = QLineEdit(metadata.get(key, ""), self)
            self.edits[key] = edit
            df.addRow(key.title() + ":", edit)
        info = QGroupBox("File", self)
        inf = QFormLayout(info)
        rows = [
            ("Location", facts.file),
            ("Size", f"{facts.file_size / 1_048_576:.2f} MB ({facts.file_size:,} bytes)"),
            ("Pages", str(facts.pages)),
            ("Page size", facts.page_size),
            ("PDF version", facts.pdf_version),
            ("Created", facts.created),
            ("Modified", facts.modified),
            ("Created with", facts.creator),
            ("PDF producer", facts.producer),
            ("Password protected", "Yes" if facts.protected else "No"),
            ("Digitally signed", "Yes" if facts.signed else "No"),
            ("Form fields", "Yes" if facts.has_forms else "No"),
            ("Fonts", ", ".join(facts.fonts[:12]) + ("…" if len(facts.fonts) > 12 else "") or "None"),
        ]
        for label, value in rows:
            text = QLabel(value, self)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            inf.addRow(label + ":", text)
        layout = QVBoxLayout(self)
        layout.addWidget(desc)
        layout.addWidget(info)
        layout.addWidget(_buttons(self, "Save Changes"))

    def values(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self.edits.items()}
