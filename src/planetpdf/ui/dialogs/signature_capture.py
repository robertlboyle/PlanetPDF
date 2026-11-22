"""Create a signature or initials: draw, type (bundled script fonts) or import an image."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError
from planetpdf.core.resources import SIGNATURE_FONTS, font_label
from planetpdf.core.signing.visual import INK_BLACK, INK_BLUE, recolor_ink, render_typed, trim_and_clear_background
from planetpdf.ui.fonts import family
from planetpdf.ui.viewer.qimage import png_preview

INKS = {"Blue": INK_BLUE, "Black": INK_BLACK}
CUSTOM = "Custom…"


def _qcolor(rgb) -> QColor:
    return QColor.fromRgbF(*rgb)


class DrawPad(QWidget):
    """Sign with the mouse, a pen or a finger."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(460, 170)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.strokes: list[QPainterPath] = []
        self._current: QPainterPath | None = None
        self.ink = QColor(_qcolor(INK_BLUE))

    def clear(self) -> None:
        self.strokes = []
        self.update()
        self.changed.emit()

    def is_empty(self) -> bool:
        return not any(p.elementCount() > 1 for p in self.strokes)

    def mousePressEvent(self, event) -> None:
        self._current = QPainterPath(event.position())
        self.strokes.append(self._current)

    def mouseMoveEvent(self, event) -> None:
        if self._current is not None:
            self._current.lineTo(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._current is not None and self._current.elementCount() == 1:
            p = event.position()
            self._current.lineTo(QPointF(p.x() + 0.5, p.y() + 0.5))  # a dot
        self._current = None
        self.update()
        self.changed.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        painter.setPen(QPen(QColor(200, 200, 200), 1, Qt.PenStyle.DashLine))
        baseline = self.height() * 0.72
        painter.drawLine(20, int(baseline), self.width() - 20, int(baseline))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.ink, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for path in self.strokes:
            painter.drawPath(path)

    def to_png(self, scale: float = 3.0, ink: QColor | None = None) -> bytes:
        if self.is_empty():
            raise PdfError("Draw your signature first.")
        image = QImage(int(self.width() * scale), int(self.height() * scale), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        painter.setPen(QPen(ink or self.ink, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for path in self.strokes:
            painter.drawPath(path)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return trim_and_clear_background(bytes(buffer.data()), threshold=250)


class SignatureCaptureDialog(QDialog):
    """``png()`` returns the transparent PNG after the dialog is accepted."""

    def __init__(self, kind: str = "signature", default_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle("New Initials" if kind == "initials" else "New Signature")
        self._png: bytes | None = None
        self._imported: bytes | None = None  # trimmed, in its own colors; shown and saved in the ink color
        self._ink = INK_BLUE
        self._custom: tuple | None = None

        self.ink = QComboBox(self)
        self.ink.addItems([*INKS, CUSTOM])
        self.ink.activated.connect(self._ink_picked)

        # Draw
        self.pad = DrawPad(self)
        clear = QPushButton("Clear", self)
        clear.clicked.connect(self.pad.clear)
        draw_tab = QWidget(self)
        draw_layout = QVBoxLayout(draw_tab)
        draw_layout.addWidget(QLabel("Sign in the box with your mouse, pen, or finger.", draw_tab))
        draw_layout.addWidget(self.pad, 1)
        draw_layout.addWidget(clear, 0, Qt.AlignmentFlag.AlignRight)

        # Type
        self.typed = QLineEdit(default_name if kind == "signature" else _initials(default_name), self)
        self.typed.setPlaceholderText("Your initials" if kind == "initials" else "Your full name")
        self.font_buttons = QButtonGroup(self)
        type_tab = QWidget(self)
        type_layout = QVBoxLayout(type_tab)
        form = QFormLayout()
        form.addRow("Type:", self.typed)
        type_layout.addLayout(form)
        self.font_previews: dict[str, QRadioButton] = {}
        for i, key in enumerate(SIGNATURE_FONTS):
            button = QRadioButton(self)
            button.setToolTip(font_label(key))
            self.font_buttons.addButton(button, i)
            self.font_previews[key] = button
            type_layout.addWidget(button)
        self.font_buttons.button(0).setChecked(True)
        self.typed.textChanged.connect(self._update_type_previews)
        self._update_type_previews()

        # Import
        self.import_preview = QLabel("No image chosen.", self)
        self.import_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.import_preview.setMinimumHeight(140)
        choose = QPushButton("Choose Image…", self)
        choose.clicked.connect(self._choose_image)
        import_tab = QWidget(self)
        import_layout = QVBoxLayout(import_tab)
        import_layout.addWidget(QLabel("A photo or scan of your signature (PNG or JPG). The white background is removed "
                                       "and the ink is shown in the color chosen below.", import_tab))
        import_layout.addWidget(self.import_preview, 1)
        import_layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignLeft)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(draw_tab, "Draw")
        self.tabs.addTab(type_tab, "Type")
        self.tabs.addTab(import_tab, "Import")
        if default_name:
            self.tabs.setCurrentIndex(1)

        self.name = QLineEdit("Initials" if kind == "initials" else "My signature", self)
        bottom = QFormLayout()
        bottom.addRow("Ink color:", self.ink)
        bottom.addRow("Save as:", self.name)

        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(bottom)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.resize(560, 420)

    def _ink_rgb(self):
        return self._ink

    def _ink_picked(self, index: int) -> None:
        text = self.ink.itemText(index)
        if text in INKS:
            self.set_ink(INKS[text])
            return
        color = QColorDialog.getColor(_qcolor(self._custom or self._ink), self, "Ink Color")
        if color.isValid():
            self.set_ink((color.redF(), color.greenF(), color.blueF()))
        else:  # canceled: back to what it was
            self.set_ink(self._ink)

    def set_ink(self, rgb) -> None:
        """Every tab shows (and saves) the signature in this color."""
        self._ink = tuple(rgb)
        named = next((name for name, value in INKS.items() if value == self._ink), None)
        self.ink.blockSignals(True)
        if named is None:
            self._custom = self._ink
            self.ink.setItemText(self.ink.count() - 1, f"Custom ({_qcolor(self._ink).name()})")
            self.ink.setCurrentIndex(self.ink.count() - 1)
        else:
            self.ink.setCurrentText(named)
        self.ink.blockSignals(False)
        self.pad.ink = _qcolor(self._ink)
        self.pad.update()
        self._update_type_previews()
        self._update_import_preview()

    def _update_type_previews(self) -> None:
        text = self.typed.text().strip() or ("RB" if self.kind == "initials" else "Your Name")
        for key, button in self.font_previews.items():
            button.setText(text)
            font = QFont(family(key))
            font.setPointSize(26)
            button.setFont(font)
            button.setStyleSheet(f"color: {_qcolor(self._ink_rgb()).name()};")

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Signature Image", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.use_image(Path(path).read_bytes())

    def use_image(self, data: bytes) -> None:
        try:
            self._imported = trim_and_clear_background(data)
        except PdfError as exc:
            self.error.setText(str(exc))
            return
        self._update_import_preview()
        self.error.clear()

    def _update_import_preview(self) -> None:
        if self._imported is not None:
            self.import_preview.setPixmap(png_preview(recolor_ink(self._imported, self._ink), 440, 150))

    def build_png(self) -> bytes:
        tab = self.tabs.currentIndex()
        # Drawn/typed in black (full-strength transparency from darkness), then given
        # the ink color, so light colors work as well as dark ones.
        if tab == 0:
            return recolor_ink(self.pad.to_png(ink=QColor("black")), self._ink)
        if tab == 1:
            key = SIGNATURE_FONTS[max(self.font_buttons.checkedId(), 0)]
            return recolor_ink(render_typed(self.typed.text(), key, INK_BLACK), self._ink)
        if self._imported is None:
            raise PdfError("Choose an image first.")
        return recolor_ink(self._imported, self._ink)

    def _accept(self) -> None:
        try:
            self._png = self.build_png()
        except PdfError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def png(self) -> bytes | None:
        return self._png

    def label(self) -> str:
        return self.name.text().strip() or self.kind.title()


def _initials(name: str) -> str:
    return "".join(part[0].upper() for part in name.split() if part and part[0].isalpha())
