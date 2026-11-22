"""Redaction panel: mark by area or search, review every mark, set options, apply."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.document import PdfError
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionOptions
from planetpdf.core.redaction.ocg import hidden_ocgs
from planetpdf.core.redaction.patterns import PRESETS
from planetpdf.core.redaction.search import SearchHit, build_pattern, page_has_images_but_no_text, search_pages

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

MAX_RESULTS = 5000
PROGRESS_PAGES = 20  # (a search of fewer pages is over before a progress window would be read)


def _area_key(page: int, rects) -> tuple:
    """Compare areas despite the float precision lost when rects are stored in the PDF."""
    return (page, tuple(round(v, 1) for r in rects for v in r))


def _color_to_hex(color: tuple[float, float, float]) -> str:
    return QColor.fromRgbF(*color).name()


def _hex_to_color(value: str) -> tuple[float, float, float]:
    c = QColor(value)
    if not c.isValid():
        return (0.0, 0.0, 0.0)
    return (c.redF(), c.greenF(), c.blueF())


class RedactionPanel(QWidget):
    areaToolToggled = Signal(bool)
    applyRequested = Signal()

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._hits: list[SearchHit] = []
        self._hit_labels: list[str] = []

        # --- tools ---
        self.area_button = QPushButton("Mark Area", self)
        self.area_button.setCheckable(True)
        self.area_button.setToolTip("Drag a box on the page to mark it for redaction (Esc to stop).")
        self.area_button.toggled.connect(self.areaToolToggled)

        # --- find ---
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Text or pattern to find")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.find_text)
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItems(["Exact text", "Regular expression"])
        self.case_check = QCheckBox("Match case", self)
        self.word_check = QCheckBox("Whole words", self)
        self.find_button = QPushButton("Find", self)
        self.find_button.clicked.connect(self.find_text)

        self.preset_checks: dict[str, QCheckBox] = {}
        preset_grid = QGridLayout()
        for i, preset in enumerate(PRESETS.values()):
            box = QCheckBox(preset.label, self)
            box.setToolTip(f"e.g. {preset.example}")
            self.preset_checks[preset.key] = box
            preset_grid.addWidget(box, i // 2, i % 2)
        self.preset_button = QPushButton("Find Patterns", self)
        self.preset_button.clicked.connect(self.find_presets)

        self.results = QTreeWidget(self)
        self.results.setHeaderLabels(["Page", "Found", "Context"])
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.results.itemDoubleClicked.connect(self._go_to_result)
        self.results_label = QLabel("", self)
        self.mark_results_button = QPushButton("Mark Checked Results", self)
        self.mark_results_button.clicked.connect(self.mark_checked_results)
        self.mark_results_button.setEnabled(False)

        find_box = QGroupBox("Find and mark", self)
        find_layout = QVBoxLayout(find_box)
        row = QHBoxLayout()
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.find_button)
        find_layout.addLayout(row)
        find_layout.addWidget(self.mode_combo)
        row = QHBoxLayout()
        row.addWidget(self.case_check)
        row.addWidget(self.word_check)
        row.addStretch(1)
        find_layout.addLayout(row)
        find_layout.addWidget(QLabel("Common patterns (US formats):", self))
        find_layout.addLayout(preset_grid)
        find_layout.addWidget(self.preset_button)
        find_layout.addWidget(self.results, 1)
        row = QHBoxLayout()
        row.addWidget(self.results_label, 1)
        row.addWidget(self.mark_results_button)
        find_layout.addLayout(row)

        # --- review ---
        self.marks_tree = QTreeWidget(self)
        self.marks_tree.setHeaderLabels(["Page", "Type", "Will remove"])
        self.marks_tree.setRootIsDecorated(False)
        self.marks_tree.setUniformRowHeights(True)
        self.marks_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.marks_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.marks_tree.itemSelectionChanged.connect(self._on_mark_selection)
        self.marks_tree.itemActivated.connect(lambda item, _col: self._show_mark(item))
        self.marks_label = QLabel("No marks yet.", self)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self.remove_selected)
        self.remove_button.setEnabled(False)
        self.remove_all_button = QPushButton("Remove All", self)
        self.remove_all_button.clicked.connect(self.remove_all)
        review_box = QGroupBox("Review marks", self)
        review_layout = QVBoxLayout(review_box)
        review_layout.addWidget(self.marks_tree, 1)
        row = QHBoxLayout()
        row.addWidget(self.marks_label, 1)
        row.addWidget(self.remove_button)
        row.addWidget(self.remove_all_button)
        review_layout.addLayout(row)

        # --- options ---
        settings = window.settings
        self._fill = _hex_to_color(settings.redact_fill)
        self.fill_button = QPushButton(self)
        self.fill_button.setToolTip("Color of the redaction boxes")
        self.fill_button.clicked.connect(self._choose_fill)
        self._update_fill_button()
        self.overlay_check = QCheckBox("Overlay text:", self)
        self.overlay_check.setChecked(settings.redact_overlay_enabled)
        self.overlay_edit = QLineEdit(settings.redact_overlay_text or "REDACTED", self)
        self.overlay_edit.setEnabled(self.overlay_check.isChecked())
        self.overlay_check.toggled.connect(self.overlay_edit.setEnabled)
        self.touching_check = QCheckBox("Also remove shapes that touch the box", self)
        self.touching_check.setChecked(settings.redact_remove_touching)
        self.touching_check.setToolTip(
            "By default only lines and shapes lying entirely inside a box are removed.\n"
            "A shape that is only partly covered keeps its full outline in the file,\n"
            "so the hidden part could be recovered. Tick this to remove such shapes\n"
            "completely (they then disappear outside the box as well)."
        )
        options_box = QGroupBox("Options", self)
        options_layout = QGridLayout(options_box)
        options_layout.addWidget(QLabel("Fill color:", self), 0, 0)
        options_layout.addWidget(self.fill_button, 0, 1)
        options_layout.addWidget(self.overlay_check, 1, 0)
        options_layout.addWidget(self.overlay_edit, 1, 1)
        options_layout.addWidget(self.touching_check, 2, 0, 1, 2)

        self.notes_label = QLabel("", self)
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet("color: #b35900;")
        self.ocr_button = QPushButton("Recognize Text (OCR)…", self)
        self.ocr_button.setToolTip("Make scanned pages searchable so Find can mark their text.")
        self.ocr_button.clicked.connect(window.ocr_dialog)
        self.ocr_button.hide()

        self.apply_button = QPushButton("Apply Redactions…", self)
        self.apply_button.setToolTip("Permanently remove everything under the marks into a new file, then verify it.")
        self.apply_button.clicked.connect(self.applyRequested)
        self.apply_button.setEnabled(False)
        font = self.apply_button.font()
        font.setBold(True)
        self.apply_button.setFont(font)

        inner = QWidget(self)
        layout = QVBoxLayout(inner)
        layout.addWidget(self.area_button)
        layout.addWidget(find_box, 3)
        layout.addWidget(review_box, 2)
        layout.addWidget(options_box)
        layout.addWidget(self.notes_label)
        layout.addWidget(self.ocr_button)
        layout.addWidget(self.apply_button)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # Vertical scrolling only: contents must fit the dock's width.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for tree in (self.results, self.marks_tree):
            tree.setMinimumHeight(120)
            tree.header().setStretchLastSection(True)
            tree.header().setMinimumSectionSize(30)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setMinimumWidth(320)

        self.remove_action = window._action("Remove Selected Marks", self.remove_selected, QKeySequence.StandardKey.Delete)
        self.remove_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.marks_tree.addAction(self.remove_action)
        self._syncing = False

    # --- options -------------------------------------------------------------------

    def options(self) -> RedactionOptions:
        text = self.overlay_edit.text().strip() if self.overlay_check.isChecked() else ""
        return RedactionOptions(fill=self._fill, overlay_text=text, remove_touching_graphics=self.touching_check.isChecked())

    def save_options(self) -> None:
        s = self._window.settings
        s.redact_fill = _color_to_hex(self._fill)
        s.redact_overlay_enabled = self.overlay_check.isChecked()
        s.redact_overlay_text = self.overlay_edit.text()
        s.redact_remove_touching = self.touching_check.isChecked()

    def _choose_fill(self) -> None:
        color = QColorDialog.getColor(QColor.fromRgbF(*self._fill), self, "Redaction Fill Color")
        if color.isValid():
            self._fill = (color.redF(), color.greenF(), color.blueF())
            self._update_fill_button()

    def _update_fill_button(self) -> None:
        name = _color_to_hex(self._fill)
        self.fill_button.setText(name.upper())
        self.fill_button.setStyleSheet(
            f"background: {name}; color: {'white' if QColor(name).lightnessF() < 0.5 else 'black'};"
        )

    # --- search -----------------------------------------------------------------------

    def find_text(self) -> None:
        session = self._window.session
        if session is None:
            return
        term = self.search_edit.text()
        try:
            pattern = build_pattern(
                term,
                regex=self.mode_combo.currentIndex() == 1,
                case_sensitive=self.case_check.isChecked(),
                whole_word=self.word_check.isChecked(),
            )
        except PdfError as exc:
            self.results_label.setText(str(exc))
            return
        label = f'Search: "{term}"' if len(term) <= 30 else "Search"
        found = self._search([(pattern, label)])
        if found is not None:
            self._show_results(*found)

    def find_presets(self) -> None:
        session = self._window.session
        if session is None:
            return
        chosen = [PRESETS[k] for k, box in self.preset_checks.items() if box.isChecked()]
        if not chosen:
            self.results_label.setText("Tick one or more patterns first.")
            return
        found = self._search([(preset.pattern, preset.label) for preset in chosen])
        if found is not None:
            self._show_results(*found)

    def _search(self, patterns: list) -> tuple[list[SearchHit], list[str]] | None:
        """Every (pattern, label) over the whole document; a long one shows "Searching page 37 of
        120" with Cancel (user 2026-10-23). None when canceled."""
        session = self._window.session
        pages = session.page_count

        def work(progress=None) -> tuple[list[SearchHit], list[str]]:
            hits: list[SearchHit] = []
            labels: list[str] = []
            for n, (pattern, label) in enumerate(patterns):
                stage = "Searching page" if len(patterns) == 1 else f"Pattern {n + 1} of {len(patterns)}: searching page"

                def step(done: int, total: int, _stage: str = "", stage=stage) -> bool:
                    return progress is None or progress(done, total, stage)

                found = search_pages(session, pattern, progress=step)
                hits += found
                labels += [label] * len(found)
            return hits, labels

        if pages < PROGRESS_PAGES:
            return work()
        from planetpdf.ui.tasks import TaskCanceled, run_with_progress

        what = "Searching" if len(patterns) == 1 else f"Searching for {len(patterns)} patterns"
        try:
            return run_with_progress(self._window, "Find Text & Redact", what, work)
        except TaskCanceled:
            self.results_label.setText("Search canceled.")
            return None

    def _show_results(self, hits: list[SearchHit], labels: list[str]) -> None:
        if len(labels) == 1 and len(hits) != 1:
            labels = labels * len(hits)
        order = sorted(range(len(hits)), key=lambda i: (hits[i].page, hits[i].rects[0][1], hits[i].rects[0][0]))
        self._hits = [hits[i] for i in order][:MAX_RESULTS]
        self._hit_labels = [labels[i] for i in order][:MAX_RESULTS]
        self.results.clear()
        already = self._marked_areas()
        for hit in self._hits:
            item = QTreeWidgetItem([str(hit.page + 1), hit.text, hit.context])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            is_marked = _area_key(hit.page, hit.rects) in already
            item.setCheckState(0, Qt.CheckState.Unchecked if is_marked else Qt.CheckState.Checked)
            if is_marked:
                item.setToolTip(1, "Already marked")
                item.setForeground(1, QColor("gray"))
            self.results.addTopLevelItem(item)
        total = len(hits)
        shown = f" (showing first {MAX_RESULTS})" if total > MAX_RESULTS else ""
        pages = len({h.page for h in hits})
        self.results_label.setText(
            f"{total} match{'es' if total != 1 else ''} on {pages} page{'s' if pages != 1 else ''}{shown}"
            if total
            else "No matches."
        )
        self.mark_results_button.setEnabled(bool(self._hits))
        self.results.resizeColumnToContents(0)
        self.results.resizeColumnToContents(1)

    def _marked_areas(self) -> set[tuple]:
        session = self._window.session
        if session is None:
            return set()
        return {_area_key(m.page, m.rects) for m in M.list_marks(session)}

    def mark_checked_results(self) -> None:
        specs = []
        for i in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                hit = self._hits[i]
                specs.append(M.make_spec(hit.page, hit.rects, self._hit_labels[i]))
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setForeground(1, QColor("gray"))
        if specs:
            self._window.add_marks(specs)

    def _go_to_result(self, item: QTreeWidgetItem, _column: int) -> None:
        index = self.results.indexOfTopLevelItem(item)
        if 0 <= index < len(self._hits):
            self._window.view.go_to_page(self._hits[index].page)

    # --- review ---------------------------------------------------------------------

    def refresh(self, select: set[str] | None = None) -> None:
        session = self._window.session
        self._syncing = True
        self.marks_tree.clear()
        marks = M.list_marks(session) if session is not None else []
        for mark in marks:
            text = M.mark_text(session, mark) or "(no text: area)"
            item = QTreeWidgetItem([str(mark.page + 1), mark.label, " ".join(text.split())[:200]])
            item.setData(0, Qt.ItemDataRole.UserRole, mark.mark_id)
            self.marks_tree.addTopLevelItem(item)
            if select and mark.mark_id in select:
                item.setSelected(True)
        self._syncing = False
        pages = len({m.page for m in marks})
        self.marks_label.setText(
            f"{len(marks)} mark{'s' if len(marks) != 1 else ''} on {pages} page{'s' if pages != 1 else ''}"
            if marks
            else "No marks yet."
        )
        self.remove_all_button.setEnabled(bool(marks))
        self.apply_button.setEnabled(bool(marks))
        self.marks_tree.resizeColumnToContents(0)
        self.marks_tree.resizeColumnToContents(1)
        self._on_mark_selection()
        self._update_notes()

    def select_marks(self, mark_ids: set[str]) -> None:
        self._syncing = True
        for i in range(self.marks_tree.topLevelItemCount()):
            item = self.marks_tree.topLevelItem(i)
            item.setSelected(item.data(0, Qt.ItemDataRole.UserRole) in mark_ids)
            if item.isSelected():
                self.marks_tree.scrollToItem(item)
        self._syncing = False
        self.remove_button.setEnabled(bool(mark_ids))

    def selected_mark_ids(self) -> list[str]:
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.marks_tree.selectedItems()]

    def _on_mark_selection(self) -> None:
        ids = self.selected_mark_ids()
        self.remove_button.setEnabled(bool(ids))
        if not self._syncing:
            self._window.view.set_selected_marks(set(ids))
            if len(ids) == 1:
                self._show_mark_id(ids[0])

    def _show_mark(self, item: QTreeWidgetItem) -> None:
        self._show_mark_id(item.data(0, Qt.ItemDataRole.UserRole))

    def _show_mark_id(self, mark_id: str) -> None:
        self._window.view.show_mark(mark_id)

    def remove_selected(self) -> None:
        ids = self.selected_mark_ids() or sorted(self._window.view.selected_marks)
        if ids:
            self._window.remove_marks(ids)

    def remove_all(self) -> None:
        session = self._window.session
        if session is not None:
            ids = [m.mark_id for m in M.list_marks(session)]
            if ids:
                self._window.remove_marks(ids)

    def _update_notes(self) -> None:
        session = self._window.session
        notes = []
        if session is not None:
            if hidden_ocgs(session.doc):
                notes.append(
                    "This document has hidden layers. Their text can't be found by search; "
                    "keep “Hidden layers” ticked when applying to remove them."
                )
            scanned = [i + 1 for i in range(session.page_count) if page_has_images_but_no_text(session, i)]
            if scanned:
                listed = ", ".join(map(str, scanned[:8])) + ("…" if len(scanned) > 8 else "")
                notes.append(
                    f"Page(s) {listed} look scanned (images, no text). Search can't find text in "
                    "them until you run text recognition (OCR), or mark areas by hand."
                )
        self.notes_label.setText("\n\n".join(notes))
        self.notes_label.setVisible(bool(notes))
        self.ocr_button.setVisible(session is not None and any("scanned" in n for n in notes))
