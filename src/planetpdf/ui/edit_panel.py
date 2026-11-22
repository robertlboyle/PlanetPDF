"""Edit PDF's right-hand panel (Acrobat, user screenshot 2026-10-01):

* FORMAT: the text format bar (`ui.text_format`): font, size, bold, italic, underline,
  color of the selected page text or added text.
* OBJECTS: flip, rotate, replace or delete the selected image.
* SCANNED DOCUMENTS: recognize text (OCR) so scanned pages can be edited.
* Show bounding boxes: outline every editable line and image.

Every change is one undo step (``EditController.restyle_selected`` /
``transform_selected``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.ui import fa
from planetpdf.ui.item_bar import color_swatch

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _heading(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet("font-weight: 700; letter-spacing: 1px; color: palette(mid); padding-top: 10px;")
    return label


class EditPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 10)

        # FORMAT
        layout.addWidget(_heading("FORMAT", self))
        self.format_hint = QLabel("Select text (one line, several with a box or Shift+click, or text you added) "
                                  "to change its format.", self)
        self.format_hint.setWordWrap(True)
        layout.addWidget(self.format_hint)
        from planetpdf.ui.text_format import TextFormatBar

        self.format_bar = TextFormatBar(self, rows=2)  # (the same as the bar under the tool bar)
        layout.addWidget(self.format_bar)
        window.text_format.add_bar(self.format_bar)

        # OBJECTS
        layout.addWidget(_heading("OBJECTS", self))
        self.objects_hint = QLabel("Select images, lines, or boxes to flip, rotate, or delete them (Delete works on anything selected).", self)
        self.objects_hint.setWordWrap(True)
        layout.addWidget(self.objects_hint)
        grid = QGridLayout()
        self.object_buttons: dict[str, QPushButton] = {}
        for n, (key, icon, text) in enumerate([
            ("flip_v", "arrows-up-down", "Flip Vertical"), ("flip_h", "arrows-left-right", "Flip Horizontal"),
            ("rotate_ccw", "rotate-left", "Rotate Left"), ("rotate_cw", "rotate-right", "Rotate Right"),
            ("replace", "image", "Replace Image…"), ("delete", "trash", "Delete"),
        ]):
            button = QPushButton(fa.icon(icon), text, self)
            button.setStyleSheet("text-align: left; padding: 4px 6px;")
            if key == "replace":
                button.clicked.connect(lambda: self._with_image(lambda item: window.edit.replace_image(item)))
            elif key == "delete":
                button.clicked.connect(self._delete)
            else:
                button.clicked.connect(lambda _c=False, k=key: (window.edit.transform_selected(k), self.update_from_selection()))
            grid.addWidget(button, n // 2, n % 2)
            self.object_buttons[key] = button
        layout.addLayout(grid)
        self.color = QPushButton("Shape Color…", self)
        self.color.setToolTip("Color of the selected lines and boxes")
        self.color.setStyleSheet("text-align: left; padding: 4px 6px;")
        self.color.clicked.connect(self._pick_color)
        layout.addWidget(self.color)

        # SCANNED DOCUMENTS
        layout.addWidget(_heading("SCANNED DOCUMENTS", self))
        ocr = QPushButton(fa.icon("wand-magic-sparkles"), "Recognize Text (OCR)…", self)
        ocr.setToolTip("Scanned pages are pictures: recognize the text first so it can be found and copied")
        ocr.setStyleSheet("text-align: left; padding: 4px 6px;")
        ocr.clicked.connect(window.act_ocr.trigger)
        layout.addWidget(ocr)

        self.bounds = QCheckBox("Show bounding boxes", self)
        self.bounds.setToolTip("Outline every line of text and image you can edit")
        self.bounds.toggled.connect(window.view.set_bounding_boxes)
        layout.addWidget(self.bounds)
        layout.addStretch(1)

        window.edit.selectionChanged.connect(lambda _id: self.update_from_selection())
        window.edit.modeChanged.connect(lambda _m: self.update_from_selection())
        self.update_from_selection()

    def _with_image(self, run) -> None:
        item = self.w.view.selected_markup
        if item and item.startswith("img:"):
            run(item)
            self.update_from_selection()

    def update_from_selection(self) -> None:
        """Enable what applies to the selection: FORMAT for selected text (its color also
        for shapes), flip / rotate for images and shapes, Delete for anything."""
        edit = self.w.edit
        active = edit.mode == "edit" and self.w.session is not None
        lines, images, shapes = edit.selected_objects() if active else ([], [], [])
        anything = bool(self.w.view.selected_group) if active else False
        self._updating = True
        try:
            self.w.text_format.update()  # (FORMAT: the text format bar follows the selection)
            self.format_hint.setVisible(self.w.text_format.target()[0] not in ("placed", "lines", "typing"))
            self.color.setEnabled(bool(shapes))
            if shapes:
                path = shapes[0].path
                self.color.setIcon(color_swatch(QColor.fromRgbF(*(path.get("color") or path.get("fill") or (0, 0, 0)))))
            for key, button in self.object_buttons.items():
                if key == "delete":
                    button.setEnabled(anything)
                elif key == "replace":
                    button.setEnabled(len(images) == 1 and len(self.w.view.selected_group) == 1)
                else:
                    button.setEnabled(bool(images or shapes))
            self.objects_hint.setVisible(not anything)
        finally:
            self._updating = False

    def _delete(self) -> None:
        self.w.edit.delete()
        self.update_from_selection()

    def _pick_color(self) -> None:
        _lines, _images, shapes = self.w.edit.selected_objects()
        if not shapes:
            return
        current = shapes[0].path.get("color") or shapes[0].path.get("fill") or (0, 0, 0)
        color = self.w.pick_color(QColor.fromRgbF(*current), "Shape Color")
        if color is not None:
            self.w.edit.recolor_selected((color.redF(), color.greenF(), color.blueF()))
            self.update_from_selection()