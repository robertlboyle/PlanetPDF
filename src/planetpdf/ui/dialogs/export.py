from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.export import ExportFormat, ExportOptions
from planetpdf.core.pagerange import format_page_ranges
from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit

FORMAT_LABELS = {
    ExportFormat.PDF: "PDF (just these pages)",
    ExportFormat.PNG: "PNG image (one per page)",
    ExportFormat.JPG: "JPG image (one per page)",
    ExportFormat.TIFF: "TIFF image (one per page)",
    ExportFormat.TIFF_MULTI: "TIFF image (all pages in one file)",
    ExportFormat.IMAGE_PDF: "Image-only PDF (pages turned into pictures)",
    ExportFormat.DOCX: "Word document (.docx)",
    ExportFormat.DOC: "Word 97-2003 document (.doc, through Microsoft Word)",
    ExportFormat.XLSX: "Excel workbook (.xlsx)",
    ExportFormat.XML_SPREADSHEET: "XML Spreadsheet 2003 (.xml)",
    ExportFormat.PPTX: "PowerPoint presentation (.pptx)",
    ExportFormat.RTF: "Rich Text (.rtf)",
    ExportFormat.TXT: "Plain text (.txt)",
    ExportFormat.TXT_ACCESSIBLE: "Accessible text (.txt)",
    ExportFormat.HTML: "HTML web page (.html)",
    ExportFormat.XML: "XML 1.0 (.xml)",
    ExportFormat.PS: "PostScript (.ps)",
    ExportFormat.EPS: "Encapsulated PostScript (.eps, one page)",
    ExportFormat.JPX: "JPEG 2000 image (one per page)",
}

FILE_FILTERS = {
    ExportFormat.DOC: "Word 97-2003 documents (*.doc)",
    ExportFormat.XLSX: "Excel workbooks (*.xlsx)",
    ExportFormat.XML_SPREADSHEET: "XML Spreadsheet 2003 (*.xml)",
    ExportFormat.PPTX: "PowerPoint presentations (*.pptx)",
    ExportFormat.TXT_ACCESSIBLE: "Text files (*.txt)",
    ExportFormat.HTML: "Web pages (*.html *.htm)",
    ExportFormat.XML: "XML files (*.xml)",
    ExportFormat.PS: "PostScript files (*.ps)",
    ExportFormat.EPS: "Encapsulated PostScript (*.eps)",
    ExportFormat.JPX: "JPEG 2000 images (*.jp2)",
    ExportFormat.PDF: "PDF files (*.pdf)",
    ExportFormat.PNG: "PNG images (*.png)",
    ExportFormat.JPG: "JPG images (*.jpg *.jpeg)",
    ExportFormat.TIFF: "TIFF images (*.tif *.tiff)",
    ExportFormat.TIFF_MULTI: "TIFF images (*.tif *.tiff)",
    ExportFormat.IMAGE_PDF: "PDF files (*.pdf)",
    ExportFormat.DOCX: "Word documents (*.docx)",
    ExportFormat.RTF: "Rich Text files (*.rtf)",
    ExportFormat.TXT: "Text files (*.txt)",
}


class ExportDialog(QDialog):
    """Pages + format + options. The file name is chosen after OK."""

    def __init__(
        self,
        page_count: int,
        selected: list[int],
        encrypted: bool,
        ocr_available: bool,
        options: ExportOptions,
        fmt: ExportFormat = ExportFormat.PDF,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setMinimumWidth(440)
        self._encrypted = encrypted

        self.format_combo = QComboBox(self)
        for f, text in FORMAT_LABELS.items():
            self.format_combo.addItem(text, f)
        self.format_combo.setCurrentIndex(max(self.format_combo.findData(fmt), 0))

        self.range_edit = PageRangeEdit(page_count, format_page_ranges(selected) or "all", self)
        pages_hint = QLabel("For example <b>1,5,8-12</b>. Pages are exported in this order. Unsaved changes are included.", self)
        pages_hint.setWordWrap(True)
        pages_hint.setStyleSheet("color: gray;")

        self.dpi = QSpinBox(self)
        self.dpi.setRange(36, 1200)
        self.dpi.setSingleStep(50)
        self.dpi.setSuffix(" dpi")
        self.dpi.setValue(options.dpi)
        self.quality = QSpinBox(self)
        self.quality.setRange(10, 100)
        self.quality.setSuffix(" %")
        self.quality.setValue(options.jpeg_quality)
        self.compress = QCheckBox("Compress pictures as JPEG (much smaller file)", self)
        self.compress.setChecked(options.compress_images)
        self.ocr = QCheckBox("Recognize text on scanned pages (OCR)", self)
        self.ocr.setChecked(options.ocr_missing_text and ocr_available)
        self.ocr.setEnabled(ocr_available)
        if not ocr_available:
            self.ocr.setToolTip("Tesseract OCR language data wasn't found.")
        # AI text reading (Unlimited-OCR; optional, an NVIDIA card: core/ai_ocr.py)
        from planetpdf.core import ai_ocr

        ai = ai_ocr.status()
        self.ai = QCheckBox("Read the pages with AI (Unlimited-OCR): best for scans", self)
        self.ai.setEnabled(ai.ready)
        self.ai.setChecked(options.ai_reading and ai.ready)
        self.ai.setToolTip(("Reads the whole document in one pass, so paragraphs, headings, and tables flow "
                            "across pages. " + ai.reason) if ai.ready else ai.reason)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #b35900;")

        form = QFormLayout()
        form.addRow("Format:", self.format_combo)
        form.addRow("Pages:", self.range_edit)
        form.addRow("", pages_hint)
        self._dpi_label = QLabel("Resolution:", self)
        form.addRow(self._dpi_label, self.dpi)
        self._quality_label = QLabel("JPG quality:", self)
        form.addRow(self._quality_label, self.quality)
        form.addRow("", self.compress)
        form.addRow("", self.ocr)
        form.addRow("", self.ai)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(self.buttons)

        self.format_combo.currentIndexChanged.connect(lambda _i: self._update())
        self.compress.toggled.connect(lambda _c: self._update())
        self.ai.toggled.connect(lambda _c: self._update())
        self.range_edit.validityChanged.connect(lambda _v: self._update())
        self._update()

    def fmt(self) -> ExportFormat:
        return self.format_combo.currentData()

    def pages(self) -> list[int]:
        return self.range_edit.pages()

    def options(self) -> ExportOptions:
        return ExportOptions(
            dpi=self.dpi.value(),
            jpeg_quality=self.quality.value(),
            compress_images=self.compress.isChecked(),
            ocr_missing_text=self.ocr.isChecked() and self.ocr.isEnabled(),
            ai_reading=self._ai_applies(),
        )

    def _ai_applies(self) -> bool:
        """The AI reading is chosen, can run here, and the format takes it (whether the dialog is shown or not)."""
        from planetpdf.core.export import AI_FORMATS

        return self.ai.isChecked() and self.ai.isEnabled() and self.fmt().value in AI_FORMATS

    def _update(self) -> None:
        f = self.fmt()
        from planetpdf.core import ghostscript

        raster_ps = f in (ExportFormat.PS, ExportFormat.EPS) and ghostscript.find() is None
        image = f.is_image or f is ExportFormat.PPTX or raster_ps  # (PowerPoint: the slide backgrounds)
        for widget in (self.dpi, self._dpi_label):
            widget.setVisible(image)
        uses_quality = f in (ExportFormat.JPG, ExportFormat.JPX) or (f is ExportFormat.IMAGE_PDF and self.compress.isChecked())
        for widget in (self.quality, self._quality_label):
            widget.setVisible(uses_quality)
        self.compress.setVisible(f is ExportFormat.IMAGE_PDF)
        from planetpdf.core.export import AI_FORMATS

        self.ai.setVisible(f.value in AI_FORMATS)
        self.ocr.setVisible(f.is_document and not self._ai_applies())
        notes = []
        if f is ExportFormat.IMAGE_PDF:
            notes.append("Pages become pictures: text can't be searched or copied, and links, forms, and layers are dropped.")
        if self._ai_applies():
            notes.append("The AI reads every page as a picture and writes one flowing document: headings, paragraphs, "
                         "lists, and tables in reading order. Pictures and the page layout aren't kept.")
        elif f is ExportFormat.DOCX:
            notes.append("Layout, tables, and images are converted as closely as possible; complex pages may need touching up in Word.")
        if f is ExportFormat.RTF:
            notes.append("Text is reflowed into paragraphs with fonts, sizes, bold/italic, and pictures; page layout isn't kept.")
        if f is ExportFormat.DOC:
            notes.append("Converted to Word (.docx) first, then Microsoft Word saves it in the old format.")
        if f in (ExportFormat.XLSX, ExportFormat.XML_SPREADSHEET):
            notes.append("One sheet per page: tables cell for cell (numbers as numbers), other text one line per row.")
        if f is ExportFormat.PPTX:
            notes.append("One slide per page: the page's pictures and shapes as the background, its text as editable text boxes.")
        if f is ExportFormat.TXT_ACCESSIBLE:
            notes.append("Paragraphs in reading order (not the page's line breaks), with a heading per page and pictures marked.")
        if raster_ps:
            notes.append("Ghostscript isn't installed: the pages will be pictures in the PostScript file.")
        if f is ExportFormat.EPS and self.range_edit.is_valid() and len(self.pages()) != 1:
            notes.append("Encapsulated PostScript holds one page: choose a single page.")
        if self._encrypted and f in (ExportFormat.PDF, ExportFormat.IMAGE_PDF):
            notes.append("This document is password protected: you'll be asked whether to protect the new file too.")
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.range_edit.is_valid())
