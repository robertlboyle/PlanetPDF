from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.pagerange import format_page_ranges
from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit

LANGUAGE_NAMES = {
    "eng": "English", "spa": "Spanish", "fra": "French", "deu": "German", "ita": "Italian",
    "por": "Portuguese", "nld": "Dutch", "pol": "Polish", "rus": "Russian", "chi_sim": "Chinese (simplified)",
    "chi_tra": "Chinese (traditional)", "jpn": "Japanese", "kor": "Korean", "ara": "Arabic", "heb": "Hebrew",
}


class OcrDialog(QDialog):
    """Choose pages, language and resolution for text recognition."""

    def __init__(
        self,
        page_count: int,
        pages_without_text: list[int],
        selected: list[int],
        languages: list[str],
        language: str,
        dpi: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recognize Text (OCR)")
        self.setMinimumWidth(420)
        self._without_text = pages_without_text
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Adds an invisible text layer to scanned pages, and to pages whose words are drawn as "
            "outlines, so their text can be searched, selected, copied, and redacted. The pages look "
            "exactly the same.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scope = QGroupBox("Pages", self)
        self.rb_missing = QRadioButton(
            f"Pages without searchable text ({len(pages_without_text)}"
            + (f": {format_page_ranges(pages_without_text)}" if pages_without_text else "")
            + ")",
            scope,
        )
        self.rb_range = QRadioButton("These pages:", scope)
        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", scope)
        group = QButtonGroup(self)
        group.addButton(self.rb_missing)
        group.addButton(self.rb_range)
        (self.rb_missing if pages_without_text else self.rb_range).setChecked(True)
        self.rb_missing.setEnabled(bool(pages_without_text))
        warn = QLabel("Pages that already contain text will get a second, duplicate copy.", scope)
        warn.setStyleSheet("color: gray;")
        warn.setWordWrap(True)
        scope_layout = QVBoxLayout(scope)
        for widget in (self.rb_missing, self.rb_range, self.range_edit, warn):
            scope_layout.addWidget(widget)
        layout.addWidget(scope)

        self.language = QComboBox(self)
        for code in languages:
            self.language.addItem(f"{LANGUAGE_NAMES.get(code, code)} ({code})", code)
        index = self.language.findData(language)
        self.language.setCurrentIndex(max(index, 0))
        self.dpi = QComboBox(self)
        for value, text in ((200, "200 dpi (faster)"), (300, "300 dpi (recommended)"), (400, "400 dpi (small print)")):
            self.dpi.addItem(text, value)
        self.dpi.setCurrentIndex(max(self.dpi.findData(dpi), 0))
        form = QFormLayout()
        form.addRow("Language:", self.language)
        form.addRow("Resolution:", self.dpi)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Recognize")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.range_edit.line.textEdited.connect(lambda _t: self.rb_range.setChecked(True))
        self.range_edit.validityChanged.connect(lambda _v: self._update_ok())
        group.buttonToggled.connect(lambda *_a: self._update_ok())
        self._update_ok()

    def _update_ok(self) -> None:
        ok = self.rb_missing.isChecked() or self.range_edit.is_valid()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def pages(self) -> list[int]:
        return list(self._without_text) if self.rb_missing.isChecked() else self.range_edit.pages()

    def language_code(self) -> str:
        return self.language.currentData()

    def dpi_value(self) -> int:
        return int(self.dpi.currentData())
