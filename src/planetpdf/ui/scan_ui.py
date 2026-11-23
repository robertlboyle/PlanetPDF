"""The Optimize Scans tool (user 2026-10-03): every scan clean-up step in one place, each
undoable, with a before/after preview (``core.scan_cleanup``).

Bar: Optimize… (all steps, chosen in a dialog, optionally removing blank pages and
recognizing text afterwards) · Black & White · Straighten & Clean · Fix Page Direction ·
Remove Blank Pages…. Work runs on a copy in the background (the document's password
kept), then comes back as one undo step. Recognize Text asks to straighten and clean
first when the scans look crooked or specked (``ScanTools.suggest_cleanup``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import scan_cleanup as S

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

COLOR_MODES = [("auto", "Automatic (black and white for text pages)"), ("bw", "Black and white"),
               ("gray", "Grayscale"), ("keep", "Keep the colors as scanned")]
RESOLUTIONS = [(300, "300 dpi (best for text)"), (200, "200 dpi"), (150, "150 dpi (smallest)"),
               (None, "Keep the scan's resolution")]


def _pixmap(array) -> QPixmap:
    import cv2

    if array.ndim == 2:
        h, w = array.shape
        image = QImage(array.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
    else:
        rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        image = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


STEP_TIPS = {
    "direction": "Pages scanned sideways or upside down turned the right way up",
    "straighten": "Crooked pages straightened",
    "borders": "Black edges from a copier or scanner lid made white",
    "clean": "White paper (no shadows or yellowing), no specks or dust",
    "bleed_through": "Faint text showing through from the back of thin paper removed",
    "color": "Black and white (crisp, smallest), grayscale, or the colors as scanned",
}
SIMPLE_LABELS = {
    "direction": "Turn pages scanned sideways or upside down the right way up",
    "straighten": "Straighten crooked pages",
    "borders": "Remove dark borders at the edges (from a copier or scanner lid)",
    "clean": "Clean up: white paper (no shadows or yellowing), no specks or dust",
    "bleed_through": "Remove bleed-through from the back of thin paper",
}


class _StepPanel(QWidget):
    """Advanced view: Off / Automatic / Manual for one step, and its manual setting."""

    def __init__(self, step: str, parent: QWidget, changed) -> None:
        super().__init__(parent)
        self.step = step
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tip = QLabel(STEP_TIPS[step], self)
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.modes: dict[str, QRadioButton] = {}
        row = QHBoxLayout()
        group = QButtonGroup(self)
        names = [("off", "Off"), ("auto", "Automatic"), ("manual", "Manual")]
        for key, text in names:
            button = QRadioButton(text, self)
            group.addButton(button)
            button.toggled.connect(lambda on, k=key: on and changed())
            self.modes[key] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.manual = QWidget(self)
        form = QFormLayout(self.manual)
        form.setContentsMargins(16, 0, 0, 0)
        self.value = None
        if step == "direction":
            self.value = QComboBox(self.manual)
            for turn, text in ((90, "90° clockwise"), (180, "180° (upside down)"), (270, "90° counterclockwise")):
                self.value.addItem(text, turn)
            self.value.currentIndexChanged.connect(lambda _i: changed())
            form.addRow("Turn every page:", self.value)
        elif step == "straighten":
            self.value = QDoubleSpinBox(self.manual)
            self.value.setRange(-10, 10)
            self.value.setSingleStep(0.1)
            self.value.setDecimals(1)
            self.value.setSuffix("°")
            self.value.setToolTip("Positive turns the picture counterclockwise")
            self.value.valueChanged.connect(lambda _v: changed())
            form.addRow("Turn the picture by:", self.value)
        elif step == "borders":
            self.value = QDoubleSpinBox(self.manual)
            self.value.setRange(0, 50)
            self.value.setSingleStep(1)
            self.value.setSuffix(" mm")
            self.value.setValue(5)
            self.value.valueChanged.connect(lambda _v: changed())
            form.addRow("Clear at every edge:", self.value)
        elif step == "clean":
            self.value = QDoubleSpinBox(self.manual)
            self.value.setRange(0.05, 2.0)
            self.value.setSingleStep(0.05)
            self.value.setDecimals(2)
            self.value.setSuffix(" mm")
            self.value.setValue(0.3)
            self.value.valueChanged.connect(lambda _v: changed())
            form.addRow("Remove specks up to:", self.value)
        elif step == "bleed_through":
            self.value = QSlider(Qt.Orientation.Horizontal, self.manual)
            self.value.setRange(180, 250)
            self.value.setValue(222)
            self.value.setInvertedAppearance(True)  # (left: removes less; right: more)
            self.value.setToolTip("Further right removes darker marks too (and faint pencil or stamps)")
            self.value.valueChanged.connect(lambda _v: changed())
            strength = QHBoxLayout()
            strength.addWidget(QLabel("Less", self.manual))
            strength.addWidget(self.value, 1)
            strength.addWidget(QLabel("More", self.manual))
            form.addRow("Remove:", strength)
        elif step == "color":
            self.value = QSlider(Qt.Orientation.Horizontal, self.manual)
            self.value.setRange(20, 235)
            self.value.setValue(160)
            self.value.setToolTip("Black and white: further right keeps fainter writing (text gets bolder)")
            self.value.valueChanged.connect(lambda _v: changed())
            threshold = QHBoxLayout()
            threshold.addWidget(QLabel("Lighter", self.manual))
            threshold.addWidget(self.value, 1)
            threshold.addWidget(QLabel("Darker", self.manual))
            form.addRow("Black and white:", threshold)
            self.modes["off"].hide()  # (a picture always has some color mode)
            self.modes["manual"].setText("Manual threshold")
        layout.addWidget(self.manual)
        layout.addStretch(1)
        self.set_mode("auto")

    def mode(self) -> str:
        return next(k for k, b in self.modes.items() if b.isChecked())

    def set_mode(self, mode: str) -> None:
        self.modes[mode].setChecked(True)
        self.manual.setEnabled(mode == "manual")


class OptimizeScansDialog(QDialog):
    """Simple view (default): steps on or off, all automatic, the whole page before and
    after. Advanced: each step Off / Automatic / Manual, with a before / after preview
    of that step (user 2026-10-03)."""

    def __init__(self, window: MainWindow, scanned: list[int], current: int, *, ocr_available: bool) -> None:
        super().__init__(window)
        self.w = window
        self.scanned = scanned
        self.known: dict[int, int] = {}  # (automatic page direction found per page: reading it is slow)
        self.shown: S.Preview | None = None
        self._syncing = False
        self.setWindowTitle("Optimize Scans")
        self._timer = QTimer(self, singleShot=True, interval=300)  # (first: the step panels call back as they're built)
        self._timer.timeout.connect(self.update_preview)

        pages_box = QGroupBox("Pages", self)
        pages_layout = QHBoxLayout(pages_box)
        self.all_pages = QRadioButton(f"All {len(scanned)} scanned page{'s' if len(scanned) != 1 else ''}", pages_box)
        self.some_pages = QRadioButton("Pages:", pages_box)
        self.page_range = QLineEdit(pages_box)
        self.page_range.setPlaceholderText("e.g. 1-5, 8")
        self.all_pages.setChecked(True)
        self.page_range.textEdited.connect(lambda _t: self.some_pages.setChecked(True))
        pages_layout.addWidget(self.all_pages)
        pages_layout.addWidget(self.some_pages)
        pages_layout.addWidget(self.page_range, 1)

        # the steps: simple (check boxes) and advanced (a list, and the chosen step's panel)
        self.steps_box = QGroupBox("Clean-up", self)
        steps_layout = QVBoxLayout(self.steps_box)
        self.simple = QWidget(self.steps_box)
        simple_layout = QVBoxLayout(self.simple)
        simple_layout.setContentsMargins(0, 0, 0, 0)
        self.checks: dict[str, QCheckBox] = {}
        for step, text in SIMPLE_LABELS.items():
            box = QCheckBox(text, self.simple)
            box.setChecked(True)
            box.toggled.connect(lambda on, s=step: self._simple_toggled(s, on))
            simple_layout.addWidget(box)
            self.checks[step] = box
        self.advanced = QWidget(self.steps_box)
        advanced_layout = QHBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.step_list = QListWidget(self.advanced)
        self.step_list.setMinimumWidth(230)
        self.panels: dict[str, _StepPanel] = {}
        self.panel_stack = QStackedWidget(self.advanced)
        for step in S.STEPS:
            self.step_list.addItem(S.STEP_NAMES[step])
            panel = _StepPanel(step, self.panel_stack, self._advanced_changed)
            self.panels[step] = panel
            self.panel_stack.addWidget(panel)
        self.step_list.currentRowChanged.connect(self._step_chosen)
        advanced_layout.addWidget(self.step_list)
        advanced_layout.addWidget(self.panel_stack, 1)
        self.advanced.hide()
        steps_layout.addWidget(self.simple)
        steps_layout.addWidget(self.advanced)
        if not ocr_available:
            self.checks["direction"].setChecked(False)
            self.checks["direction"].setEnabled(False)
            self.checks["direction"].setToolTip("Needs text recognition (Tesseract), which isn't installed")
            self.panels["direction"].modes["auto"].setEnabled(False)

        result = QGroupBox("Result", self)
        result_form = QFormLayout(result)
        self.color = QComboBox(result)
        for key, text in COLOR_MODES:
            self.color.addItem(text, key)
        self.color.currentIndexChanged.connect(lambda _i: self._changed())
        self.resolution = QComboBox(result)
        for dpi, text in RESOLUTIONS:
            self.resolution.addItem(text, dpi)
        result_form.addRow("Color:", self.color)
        result_form.addRow("Resolution:", self.resolution)
        self.blanks = QCheckBox("Remove blank pages (you'll choose which)", result)
        self.blanks.setChecked(True)
        self.ocr = QCheckBox("Recognize text afterwards (OCR), so it can be searched and copied", result)
        self.ocr.setEnabled(ocr_available)
        result_form.addRow(self.blanks)
        result_form.addRow(self.ocr)

        preview = QGroupBox("Preview", self)
        preview_layout = QVBoxLayout(preview)
        chooser = QHBoxLayout()
        # ◀ Page 4 (scan 2 of 3) ▶: through the scanned pages only, left = back, right = on
        # (user 2026-10-04: up / down arrows, and the box counted every page of the document)
        self._scans = list(scanned) or [current]
        self._scan_at = self._scans.index(current) if current in self._scans else 0
        self.prev_scan, self.next_scan = QToolButton(preview), QToolButton(preview)
        for button, arrow, tip, step in ((self.prev_scan, Qt.ArrowType.LeftArrow, "Previous scanned page", -1),
                                         (self.next_scan, Qt.ArrowType.RightArrow, "Next scanned page", 1)):
            button.setArrowType(arrow)
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, s=step: self.step_scan(s))
        self.scan_label = QLabel(preview)
        chooser.addWidget(self.prev_scan)
        chooser.addWidget(self.scan_label)
        chooser.addWidget(self.next_scan)
        self._show_scan_position()
        self.whole = QCheckBox("Whole clean-up", preview)
        self.whole.setToolTip("Show the page as scanned and as it will be, instead of the chosen step")
        self.whole.toggled.connect(lambda _on: self._show())
        chooser.addStretch(1)
        chooser.addWidget(self.whole)
        preview_layout.addLayout(chooser)
        pictures = QHBoxLayout()
        self.before, self.after = QLabel(preview), QLabel(preview)
        self.before_title, self.after_title = QLabel("Before", preview), QLabel("After", preview)
        for label, title in ((self.before, self.before_title), (self.after, self.after_title)):
            column = QVBoxLayout()
            column.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
            label.setFixedSize(270, 350)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("background: #808080; color: white;")
            column.addWidget(label)
            pictures.addLayout(column)
        preview_layout.addLayout(pictures)
        self.detail = QLabel(preview)
        self.detail.setWordWrap(True)
        preview_layout.addWidget(self.detail)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Optimize")
        self.advanced_button = buttons.addButton("Advanced…", QDialogButtonBox.ButtonRole.ResetRole)
        self.advanced_button.setCheckable(True)
        self.advanced_button.setToolTip("Each step Off, Automatic, or Manual, with a preview of that step")
        self.advanced_button.toggled.connect(self.set_advanced)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        left = QVBoxLayout()
        for widget in (pages_box, self.steps_box, result):
            left.addWidget(widget)
        left.addStretch(1)
        main = QHBoxLayout()
        main.addLayout(left, 1)
        main.addWidget(preview)
        layout = QVBoxLayout(self)
        layout.addLayout(main)
        layout.addWidget(buttons)

        self.step_list.setCurrentRow(0)
        self.setMinimumSize(1020, 620)
        self.set_advanced(False)  # (simple, all automatic: user 2026-10-03)

    # --- simple and advanced -----------------------------------------------------------------------

    @property
    def is_advanced(self) -> bool:
        return self.advanced_button.isChecked()

    def set_advanced(self, on: bool) -> None:
        if self.advanced_button.isChecked() != on:
            self.advanced_button.setChecked(on)  # (calls back here)
            return
        self.simple.setVisible(not on)
        self.advanced.setVisible(on)
        self.whole.setChecked(not on)
        self.whole.setVisible(on)
        self.advanced_button.setText("Simple…" if on else "Advanced…")
        if not on:  # (back to simple: every step that's on is automatic again)
            for step, box in self.checks.items():
                if self.panels[step].mode() == "manual":
                    self.panels[step].set_mode("auto")
        self._changed()

    def _simple_toggled(self, step: str, on: bool) -> None:
        if not self._syncing:
            self.panels[step].set_mode("auto" if on else "off")
            self._changed()

    def _advanced_changed(self) -> None:
        self._syncing = True
        try:
            for step, panel in self.panels.items():
                panel.manual.setEnabled(panel.mode() == "manual")
                if step in self.checks:
                    self.checks[step].setChecked(panel.mode() != "off")
        finally:
            self._syncing = False
        self._changed()

    def _step_chosen(self, row: int) -> None:
        if row >= 0:
            self.panel_stack.setCurrentIndex(row)
            self._show()

    def _changed(self) -> None:
        self._timer.start()

    # --- the options -------------------------------------------------------------------------------

    def options(self) -> S.CleanupOptions:
        p = self.panels
        color = self.color.currentData()
        return S.CleanupOptions(
            direction=p["direction"].mode(), turn=int(p["direction"].value.currentData()),
            straighten=p["straighten"].mode(), angle=float(p["straighten"].value.value()),
            borders=p["borders"].mode(), border_mm=float(p["borders"].value.value()),
            clean=p["clean"].mode(), speck_mm=float(p["clean"].value.value()),
            bleed_through=p["bleed_through"].mode(), bleed_cutoff=int(p["bleed_through"].value.value()),
            color=color,
            bw_threshold=int(p["color"].value.value()) if p["color"].mode() == "manual" else None,
            dpi=self.resolution.currentData(), language=self.w.settings.ocr_language or "eng",
        )

    def pages(self) -> list[int]:
        if self.all_pages.isChecked():
            return list(self.scanned)
        from planetpdf.core.pagerange import parse_page_ranges

        return parse_page_ranges(self.page_range.text(), self.w.session.page_count)

    # --- the preview -------------------------------------------------------------------------------

    @property
    def preview_index(self) -> int:
        """The document page being previewed (0-based)."""
        return self._scans[self._scan_at]

    def step_scan(self, step: int) -> None:
        """◀ / ▶: the previous / next scanned page."""
        at = max(0, min(len(self._scans) - 1, self._scan_at + step))
        if at != self._scan_at:
            self._scan_at = at
            self._show_scan_position()
            self._timer.start()

    def _show_scan_position(self) -> None:
        self.scan_label.setText(f"Page {self.preview_index + 1}  (scan {self._scan_at + 1} of {len(self._scans)})")
        self.prev_scan.setEnabled(self._scan_at > 0)
        self.next_scan.setEnabled(self._scan_at < len(self._scans) - 1)

    def update_preview(self) -> None:
        index = self.preview_index
        try:
            self.shown = S.preview(self.w.session.doc, index, self.options(), width=540, known=self.known)
        except Exception as exc:  # (never stop the dialog over a preview)
            self.shown = None
            self.after.setText(f"No preview:\n{exc}")
            return
        if self.shown is not None:  # (what each step did, in the advanced list)
            for row, step in enumerate(S.STEPS):
                view = self.shown.step(step)
                mode = self.panels[step].mode()
                state = {"off": "off", "auto": "automatic", "manual": "manual"}[mode]
                text = f"{S.STEP_NAMES[step]}: {state}"
                if view is not None and mode != "off":
                    text += f"\n   {view.detail}"
                self.step_list.item(row).setText(text)
        self._show()

    def _show(self) -> None:
        if self.shown is None:
            self.before.setText("Not a scanned page")
            self.after.clear()
            self.detail.clear()
            return
        step = S.STEPS[max(self.step_list.currentRow(), 0)]
        view = None if (self.whole.isChecked() or not self.is_advanced) else self.shown.step(step)
        if view is None:
            before, after = self.shown.original, self.shown.final
            self.before_title.setText("As scanned")
            self.after_title.setText("After the clean-up")
            self.detail.setText("; ".join(f"{S.STEP_NAMES[s.step]}: {s.detail}" for s in self.shown.steps))
        else:
            before, after = view.before, view.after
            self.before_title.setText(f"Before: {S.STEP_NAMES[step].lower()}")
            self.after_title.setText(f"After: {S.STEP_NAMES[step].lower()}")
            self.detail.setText(view.detail)
        if self.is_advanced and view is None and not self.whole.isChecked():
            self.detail.setText(f"{S.STEP_NAMES[step]} is off.")
        for label, array in ((self.before, before), (self.after, after)):
            label.setPixmap(_pixmap(array).scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))


class BlankPagesDialog(QDialog):
    """The pages that look blank, each with a small picture: untick any to keep it."""

    def __init__(self, window: MainWindow, blank: list[int]) -> None:
        super().__init__(window)
        self.setWindowTitle("Remove Blank Pages")
        layout = QVBoxLayout(self)
        note = QLabel("These pages look blank (nothing on them but specks or show-through from the back). "
                      "Untick any you want to keep. Undo brings deleted pages back.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget(self)
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(110, 140))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMinimumSize(560, 330)
        from planetpdf.ui.viewer.thumbnails import PageFrameDelegate

        self.list.setItemDelegate(PageFrameDelegate(self.list, checked=True))  # ticked = framed in blue
        for index in blank:
            pix = window.session.doc[index].get_pixmap(dpi=18)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), f"Page {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.delete_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.itemChanged.connect(lambda _i: self._count())
        layout.addWidget(buttons)
        self._count()

    def chosen(self) -> list[int]:
        return [self.list.item(n).data(Qt.ItemDataRole.UserRole) for n in range(self.list.count())
                if self.list.item(n).checkState() == Qt.CheckState.Checked]

    def _count(self) -> None:
        n = len(self.chosen())
        self.delete_button.setText(f"Delete {n} Page{'s' if n != 1 else ''}")
        self.delete_button.setEnabled(n > 0)


class ScanTools:
    def __init__(self, window: MainWindow) -> None:
        self.w = window

    # --- the commands --------------------------------------------------------------------------------

    def optimize_dialog(self) -> None:
        from planetpdf.core.ocr import find_tessdata

        w = self.w
        if w.session is None:
            return
        scanned = S.scanned_pages(w.session.doc)
        if not scanned:
            w.show_message("Optimize Scans", "This document has no scanned pages (pages that are one picture "
                                             "of the whole page), so there's nothing to optimize.")
            return
        dialog = OptimizeScansDialog(w, scanned, max(w.view.current_page, 0), ocr_available=find_tessdata() is not None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.run(dialog.options(), dialog.pages(), remove_blanks=dialog.blanks.isChecked(), ocr=dialog.ocr.isChecked())

    def black_white(self) -> None:
        self._quick(S.CleanupOptions(color="bw", straighten=False, borders=False, bleed_through=False,
                                     direction=False, dpi=None), "Black & White")

    def straighten_clean(self) -> None:
        self._quick(S.CleanupOptions(color="keep", direction=False, dpi=None), "Straighten & Clean")

    def fix_direction(self) -> None:
        self._quick(S.CleanupOptions(color="keep", straighten=False, borders=False, clean=False,
                                     bleed_through=False, direction=True, dpi=None), "Fix Page Direction")

    def _quick(self, options: S.CleanupOptions, label: str) -> None:
        if self.w.session is None:
            return
        scanned = S.scanned_pages(self.w.session.doc)
        if not scanned:
            self.w.show_message(label, "This document has no scanned pages (pages that are one picture of the "
                                       "whole page).")
            return
        options.language = self.w.settings.ocr_language or "eng"
        self.run(options, scanned, label=label)

    def remove_blank_pages(self, pages: list[int] | None = None) -> int:
        """Find empty pages, show which, and delete them on a yes (one undo step)."""
        from planetpdf.ui.commands import DeletePagesCommand
        from planetpdf.ui.tasks import TaskCanceled, run_with_progress

        w = self.w
        if w.session is None:
            return 0
        pages = list(range(w.session.page_count)) if pages is None else pages
        try:
            blank = run_with_progress(w, "Remove Blank Pages", "Looking for blank pages",
                                      lambda progress: S.find_blank_pages(w.session.doc, pages, progress))
        except TaskCanceled:
            return 0
        if not blank:
            w.statusBar().showMessage("No blank pages found.", 5000)
            return 0
        if len(blank) >= w.session.page_count:
            w.show_message("Remove Blank Pages", "Every page looks blank, so none were deleted.")
            return 0
        chosen = self.choose_blank_pages(blank)
        if not chosen:
            return 0
        w.push_command(DeletePagesCommand(w, chosen))
        w.statusBar().showMessage(f"Deleted {len(chosen)} blank page{'s' if len(chosen) != 1 else ''}.", 6000)
        return len(chosen)

    def choose_blank_pages(self, blank: list[int]) -> list[int]:
        """The pages found, with pictures: the ones left ticked are deleted ([] = none)."""
        dialog = BlankPagesDialog(self.w, blank)
        return dialog.chosen() if dialog.exec() == QDialog.DialogCode.Accepted else []

    # --- the work ------------------------------------------------------------------------------------

    def run(self, options: S.CleanupOptions, pages: list[int], *, remove_blanks: bool = False, ocr: bool = False,
            label: str = "Optimize Scans") -> S.CleanupReport | None:
        import pymupdf

        from planetpdf.core import ocr as O
        from planetpdf.ui.commands import DocumentSnapshotCommand
        from planetpdf.ui.tasks import TaskCanceled, page_progress_text, run_in_background

        w = self.w
        if w.session is None or not pages or not w._confirm_signature_change():
            return None
        session = w.session
        snapshot = session.snapshot()

        def work(progress):
            doc = session.open_snapshot(snapshot)
            report = S.cleanup_pages(doc, pages, options, progress)
            return report, doc.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)

        try:
            report, data = run_in_background(w, label, f"{label}: {session.path.name}", work,
                                             describe=page_progress_text(label, pages, verb="Optimizing page"))
        except TaskCanceled:
            w.statusBar().showMessage(f"{label} canceled; nothing was changed.", 5000)
            return None
        except Exception as exc:
            QMessageBox.critical(w, label, str(exc))
            return None
        if report.changed or report.turned:
            straightened = sorted(report.straightened)

            def apply(s) -> None:
                s.restore_snapshot(data)
                if straightened:  # (their recognized text no longer lines up: it goes)
                    O.remove_ocr_text(s, [p for p in straightened if O.page_has_ocr(s.doc, p)])

            try:
                w.push_command(DocumentSnapshotCommand.applied(w, apply, label))
            except Exception as exc:
                w.report_error(label, exc)
                return None
        w.statusBar().showMessage(self.summary(report), 12000)
        if remove_blanks:
            self.remove_blank_pages(pages)
        if ocr:
            remaining = [p for p in range(w.session.page_count) if O.page_needs_ocr(w.session.doc, p)]
            if remaining:
                w.run_ocr(remaining, w.settings.ocr_language or "eng", w.settings.ocr_dpi)
        return report

    @staticmethod
    def summary(report: S.CleanupReport) -> str:
        parts = []
        if report.changed:
            parts.append(f"cleaned {len(report.changed)} scanned page{'s' if len(report.changed) != 1 else ''}")
        if report.turned:
            parts.append(f"turned {len(report.turned)} the right way up")
        if report.straightened:
            parts.append(f"straightened {len(report.straightened)}")
        bw = sum(1 for m in report.modes.values() if m == "bw")
        if bw:
            parts.append(f"{bw} in black and white")
        text = ", ".join(parts).capitalize() + "." if parts else "Nothing needed changing."
        if report.not_scanned:
            text += (f" {len(report.not_scanned)} page{'s' if len(report.not_scanned) != 1 else ''} "
                     "aren't scans and were left alone.")
        return text

    # --- Recognize Text: clean up first? ----------------------------------------------------------------

    def suggest_cleanup(self, pages: list[int]) -> str | None:
        """Before OCR: "clean" (straighten and clean first, done), "as_is", or None
        (canceled). Asked only when the scans look crooked or specked."""
        w = self.w
        try:
            rough = S.looks_rough(w.session.doc, pages)
        except Exception:
            rough = None
        if rough is None:
            return "as_is"
        answer = self.ask_cleanup_first(*rough)
        if answer != "clean":
            return answer
        options = S.CleanupOptions(color="keep", dpi=None, language=w.settings.ocr_language or "eng")
        scanned = [p for p in pages if S.scan_picture(w.session.doc, p)]
        return "clean" if self.run(options, scanned, label="Straighten & Clean") is not None else None

    def ask_cleanup_first(self, tilt: float, specks: float) -> str | None:
        """"clean", "as_is" or None (canceled)."""
        w = self.w
        why = []
        if tilt >= 0.5:
            why.append(f"crooked (up to {tilt:.1f}° off level)")
        if specks >= 300:
            why.append("full of specks")
        box = QMessageBox(QMessageBox.Icon.Question, "Recognize Text",
                          f"These scans look {' and '.join(why)}. Text is recognized better on straight, clean "
                          "pages.\n\nStraighten and clean them first?", parent=w)
        clean = box.addButton("Straighten and Clean First", QMessageBox.ButtonRole.AcceptRole)
        as_is = box.addButton("Recognize As They Are", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(clean)
        box.exec()
        if box.clickedButton() is as_is:
            return "as_is"
        return "clean" if box.clickedButton() is clean else None
