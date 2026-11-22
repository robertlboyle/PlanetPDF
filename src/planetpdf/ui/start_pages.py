"""Acrobat-style start pages in the middle of the window when a tool opens (user
screenshots 2026-10-01 and 2026-10-03): Create PDF, Export PDF and Prepare Form.

Acrobat's layout: a centered title; the document (picture and name) on the left; the
choices as a list in the middle (plain text, the chosen one in blue); that choice's
options on the right; one big blue button (Create / Export / Start) under it all, and
Help. A choice without an action would be grayed out (none left; a test checks).

Prepare Form shows its page when no document is open, or the document has no form
fields yet (Start then detects them: ``MainWindow.start_form``); a document with fields
goes straight to editing them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core.export import ExportFormat
from planetpdf.ui import fa

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

BLUE = "#1473e6"  # Acrobat's primary button
NOT_YET = "Not available in PlanetPDF yet"


class Choice:
    """One entry of a start page's list. ``build(page)`` makes its options (the right-
    hand side); ``action()`` runs it (the big button)."""

    def __init__(self, key: str, label: str, icon: str, description: str, action: Callable[[], object] | None,
                 button: str, build: Callable[[StartPage], QWidget] | None = None) -> None:
        self.key, self.label, self.icon, self.description = key, label, icon, description
        self.action, self.button, self.build = action, button, build


class StartPage(QWidget):
    """Title · [document] · choices · options · big button · Help."""

    def __init__(self, window: MainWindow, title: str, choices: list[Choice], *, document: bool = False,
                 help_text: str = "") -> None:
        super().__init__(window)
        self.w = window
        self.setObjectName("startPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#startPage { background: palette(base); }")
        self.choices = choices
        self.help_text = help_text
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 36, 48, 36)
        outer.addStretch(1)
        heading = QLabel(title, self)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 22pt;")
        outer.addWidget(heading)
        outer.addSpacing(36)
        row = QHBoxLayout()
        row.setSpacing(28)
        row.addStretch(1)
        self.document_box = QWidget(self)  # (the document's picture and name)
        side = QVBoxLayout(self.document_box)
        side.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel(self.document_box)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFixedSize(190, 240)
        self.thumb.setStyleSheet(f"QLabel {{ background: palette(window); border: 2px solid {BLUE}; }}")
        self.file_label = QLabel(self.document_box)
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setFixedWidth(220)
        self.change_file = QLabel('<a href="change">Change File</a>', self.document_box)
        self.change_file.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.change_file.linkActivated.connect(lambda _l: self.w.open_dialog())
        self.change_file.hide()
        side.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)
        side.addWidget(self.file_label)
        side.addWidget(self.change_file)
        side.addStretch(1)
        self.document_box.setVisible(document)
        row.addWidget(self.document_box)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Choices")
        self.list.setFixedWidth(250)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setStyleSheet(
            f"QListWidget {{ font-size: 14pt; background: transparent; border-right: 1px solid #d4d7dc; }}"
            f"QListWidget::item {{ padding: 9px 14px; color: palette(window-text); }}"
            f"QListWidget::item:selected {{ background: transparent; color: {BLUE}; }}"
            f"QListWidget::item:hover {{ color: {BLUE}; }}")
        for choice in choices:
            item = QListWidgetItem(choice.label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip("" if choice.action is not None else NOT_YET)
            if choice.action is None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        row.addWidget(self.list, 0, Qt.AlignmentFlag.AlignTop)
        self.stack = QStackedWidget(self)
        self.stack.setMinimumWidth(320)
        self.option_pages: dict[str, QWidget] = {}
        for choice in choices:
            page = QWidget(self.stack)
            column = QVBoxLayout(page)
            column.setContentsMargins(4, 6, 0, 0)
            description = QLabel(choice.description, page)
            description.setWordWrap(True)
            description.setStyleSheet("font-size: 11pt; color: palette(window-text);")
            column.addWidget(description)
            if choice.build is not None:
                column.addWidget(choice.build(self))
            column.addStretch(1)
            self.stack.addWidget(page)
            self.option_pages[choice.key] = page
        row.addWidget(self.stack, 0, Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addSpacing(28)
        self.below = QVBoxLayout()  # (page-specific lines under the button, e.g. auto detection)
        self.go = QPushButton(self)
        self.go.setMinimumWidth(170)
        self.go.setStyleSheet(f"QPushButton {{ background: {BLUE}; color: white; border: none; border-radius: 19px;"
                              " padding: 9px 26px; font-size: 12pt; font-weight: 600; }"
                              " QPushButton:disabled { background: #a9c6ee; }")
        self.go.clicked.connect(self.run)
        outer.addWidget(self.go, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(self.below)
        help_link = QLabel('<a href="help">Help</a>', self)
        help_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        help_link.linkActivated.connect(lambda _l: self.show_help())
        outer.addSpacing(10)
        outer.addWidget(help_link)
        outer.addStretch(2)
        self.list.currentRowChanged.connect(self._changed)
        first = next((i for i, c in enumerate(choices) if c.action is not None), 0)
        self.list.setCurrentRow(first)

    def current(self) -> Choice:
        return self.choices[max(self.list.currentRow(), 0)]

    def _changed(self, row: int) -> None:
        choice = self.current()
        self.stack.setCurrentIndex(max(row, 0))
        self.go.setText(choice.button)
        self.go.setEnabled(choice.action is not None)

    def run(self):
        choice = self.current()
        return choice.action() if choice.action is not None else None

    def show_help(self) -> None:
        choice = self.current()
        QMessageBox.information(self, "Help", f"{choice.label}: {choice.description}"
                                + (f"\n\n{self.help_text}" if self.help_text else ""))

    def show_document(self) -> None:
        """The open document's first page and name on the left."""
        from planetpdf.core.render import render_page
        from planetpdf.ui.viewer.qimage import to_pixmap

        session = self.w.session
        if session is None:
            self.thumb.clear()
            self.thumb.setText("No document")
            self.file_label.setText("Open a PDF first.")
            return
        width, height = session.page_size(0)
        scale = min(182 / width, 232 / height)
        self.thumb.setPixmap(to_pixmap(render_page(session, 0, scale)))
        name = session.path.name if session.path else ""
        self.file_label.setText(name if len(name) <= 30 else name[:26] + "…" + name[-4:])
        self.file_label.setToolTip(name)

    def refresh(self) -> None:
        """Called each time the page is shown."""
        if self.document_box.isVisibleTo(self):  # (isVisible() is False until the page is on screen)
            self.show_document()
        self._changed(self.list.currentRow())


# --- Create PDF -----------------------------------------------------------------------------------------


SUPPORTED = ("Word, Excel, and PowerPoint files (through Microsoft Office), pictures (JPEG, PNG, TIFF, BMP, GIF, "
             "WebP), text, HTML, XPS, EPUB, FB2, MOBI, CBZ, and SVG.")


class CreatePage(StartPage):
    """Create PDF (screenshot 2026-10-03): Single File (a file tile: Select a File,
    Supported formats, Advanced Settings) · Multiple Files (combine into one / one PDF each
    / a portfolio) · Scanner · Web Page · Clipboard · Blank Page."""

    def __init__(self, window: MainWindow) -> None:
        w = window
        self.chosen_file = None

        def then_show(make):  # a new PDF is open: close Create PDF so it shows
            def run():
                if make():
                    w.tool_panes.close()
            return run

        def single():
            if self.chosen_file is None:
                self._select_file()
                if self.chosen_file is None:
                    return None
            done = w.create_pdf_from_file(self.chosen_file)
            if done:
                if self.ocr_after.isChecked():
                    w.act_ocr.trigger()
                self._set_file(None)
                w.tool_panes.close()
            return done

        def multiple():
            kind = self.multiple_group.checkedId()
            if kind == 1:
                return w.batch.create_multiple()
            if kind == 2:
                return then_show(w.batch.portfolio)()
            return w.combine_dialog(images_only=False)

        super().__init__(w, "Create a PDF from any format", [
            Choice("file", "Single File", "file", "", single, "Create", self._file_tile),
            Choice("multiple", "Multiple Files", "copy", "", multiple, "Next", self._multiple_options),
            Choice("scanner", "Scanner", "scanner-image", "Scan paper documents straight into a PDF.",
                   then_show(w.extras.create_from_scanner), "Scan"),
            Choice("web", "Web Page", "globe", "Turn a web page into a PDF (through Microsoft Edge).",
                   then_show(w.extras.create_from_web), "Create"),
            Choice("clipboard", "Clipboard", "clipboard", "Make a PDF from the picture or text you copied.",
                   then_show(w.create_pdf_from_clipboard), "Create"),
            Choice("blank", "Blank Page", "file-circle-plus", "Start with an empty Letter-size page.",
                   then_show(w.create_blank_pdf), "Create"),
        ], help_text="The new PDF opens in a tab of its own; the original files aren't changed.")

    def _file_tile(self, page: StartPage) -> QWidget:
        box = QWidget(page)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        self.tile = QToolButton(box)
        self.tile.setAccessibleName("No file chosen")
        self.tile.setFixedSize(176, 226)
        self.tile.setIconSize(self.tile.size() * 0.35)
        self.tile.setIcon(fa.icon("file-lines"))
        self.tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.tile.setStyleSheet("QToolButton { border: 1px solid #c8ccd2; background: palette(window); }")
        self.tile.clicked.connect(self._select_file)
        column.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.select_link = QLabel('<a href="select">Select a File</a>', box)
        self.select_link.linkActivated.connect(lambda _l: self._select_file())
        hint = QLabel("Choose from .docx, .xlsx, .txt, etc.", box)
        formats = QLabel('<a href="formats">Supported formats</a>', box)
        formats.linkActivated.connect(lambda _l: QMessageBox.information(self, "Supported Formats", SUPPORTED))
        self.ocr_after = QCheckBox("Advanced: recognize text afterwards (OCR)", box)
        self.ocr_after.setToolTip("For scans and pictures: make their text searchable and copyable")
        for widget in (self.select_link, hint, formats, self.ocr_after):
            widget.setAlignment(Qt.AlignmentFlag.AlignHCenter) if isinstance(widget, QLabel) else None
            column.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    def _select_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from planetpdf.core.create import FILE_FILTER

        name, _ = QFileDialog.getOpenFileName(self, "Select a File", self.w.settings.last_directory, FILE_FILTER)
        if name:
            from pathlib import Path

            self._set_file(Path(name))

    def _set_file(self, path) -> None:
        self.chosen_file = path
        self.tile.setText("" if path is None else (path.name if len(path.name) <= 26 else path.name[:22] + "…"))
        self.tile.setAccessibleName("No file chosen" if path is None else f"File: {path.name}")
        self.tile.setToolTip("" if path is None else str(path))
        self.select_link.setText('<a href="select">Select a File</a>' if path is None
                                 else '<a href="select">Change File</a>')

    def _multiple_options(self, page: StartPage) -> QWidget:
        box = QWidget(page)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        self.multiple_group = QButtonGroup(box)
        for n, (text, tip) in enumerate((
                ("Combine files into one PDF", "PDFs and pictures, in the order you choose"),
                ("Create a PDF from each file", "Pictures, text, and Word, Excel, and PowerPoint files"),
                ("PDF Portfolio", "The files kept together, unchanged, inside one PDF"))):
            radio = QRadioButton(text, box)
            radio.setToolTip(tip)
            radio.setStyleSheet("font-size: 11pt;")
            self.multiple_group.addButton(radio, n)
            column.addWidget(radio)
        self.multiple_group.button(0).setChecked(True)
        return box


def create_pdf_page(window: MainWindow) -> StartPage:
    return CreatePage(window)


# --- Export PDF -------------------------------------------------------------------------------------------


class ExportPage(StartPage):
    """Export PDF (screenshot 2026-10-03): the document · the kinds of file · that kind's
    formats (⚙ = its settings) · Export."""

    GROUPS = [
        ("word", "Microsoft Word", [("Word Document", ExportFormat.DOCX), ("Word 97-2003 Document", ExportFormat.DOC)]),
        ("spreadsheet", "Spreadsheet", [("Microsoft Excel Workbook", ExportFormat.XLSX),
                                        ("XML Spreadsheet 2003", ExportFormat.XML_SPREADSHEET)]),
        ("ppt", "Microsoft PowerPoint", [("PowerPoint Presentation", ExportFormat.PPTX)]),
        ("image", "Image", [("JPEG", ExportFormat.JPG), ("JPEG 2000", ExportFormat.JPX), ("PNG", ExportFormat.PNG),
                            ("TIFF", ExportFormat.TIFF), ("Multi-page TIFF", ExportFormat.TIFF_MULTI)]),
        ("html", "HTML Web Page", [("HTML Web Page", ExportFormat.HTML)]),
        ("more", "More Formats", [("Rich Text Format", ExportFormat.RTF), ("Text (Plain)", ExportFormat.TXT),
                                  ("Text (Accessible)", ExportFormat.TXT_ACCESSIBLE), ("XML 1.0", ExportFormat.XML),
                                  ("PostScript", ExportFormat.PS), ("Encapsulated PostScript", ExportFormat.EPS),
                                  ("Image-only PDF", ExportFormat.IMAGE_PDF)]),
    ]
    DESCRIPTIONS = {
        "word": "Text, pictures, and layout you can edit in Word.",
        "spreadsheet": "A sheet per page, tables cell for cell.",
        "ppt": "A slide per page, its text as editable text boxes.",
        "image": "Every page as a picture.",
        "html": "A web page: headings, paragraphs, and pictures.",
        "more": "Other formats: rich text, plain or accessible text, XML, PostScript, or a PDF of pictures.",
    }

    def __init__(self, window: MainWindow) -> None:
        self.groups: dict[str, QButtonGroup] = {}
        choices = [Choice(key, label, "file", self.DESCRIPTIONS[key], lambda k=key: window.export_dialog(self.format(k)),
                          "Export", lambda page, k=key, f=formats: self._formats(page, k, f))
                   for key, label, formats in self.GROUPS]
        super().__init__(window, "Export your PDF to any format", choices, document=True,
                         help_text="Export makes a new file; the PDF itself isn't changed. ⚙ opens a format's settings.")

    def _formats(self, page: StartPage, key: str, formats) -> QWidget:
        box = QWidget(page)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 10, 0, 0)
        group = QButtonGroup(box)
        for n, (label, fmt) in enumerate(formats):
            row = QHBoxLayout()
            radio = QRadioButton(label, box)
            radio.setStyleSheet("font-size: 11pt;")
            group.addButton(radio, n)
            row.addWidget(radio)
            gear = QToolButton(box)
            gear.setIcon(fa.icon("gear"))
            gear.setAutoRaise(True)
            gear.setToolTip(f"{label}: settings")
            gear.clicked.connect(lambda _c=False, f=fmt: self.w.export_dialog(f))
            row.addWidget(gear)
            row.addStretch(1)
            column.addLayout(row)
        group.button(0).setChecked(True)
        self.groups[key] = group
        return box

    def format(self, key: str) -> ExportFormat:
        formats = dict((k, f) for k, _label, f in self.GROUPS)[key]
        return formats[max(self.groups[key].checkedId(), 0)][1]

    def choose(self, category: str, option: str) -> None:
        """Select a kind of file and one of its formats by their labels (tests, links)."""
        labels = [c.label for c in self.choices]
        self.list.setCurrentRow(labels.index(category))
        key = self.current().key
        formats = dict((k, f) for k, _label, f in self.GROUPS)[key]
        self.groups[key].button([label for label, _f in formats].index(option)).setChecked(True)

    def _changed(self, row: int) -> None:
        super()._changed(row)
        self.go.setEnabled(self.current().action is not None and self.w.session is not None)


# --- Prepare Form -------------------------------------------------------------------------------------------


class PrepareFormPage(StartPage):
    """Prepare Form (screenshot 2026-10-03): Single File (the open document, Change File) /
    Scanner / Create New · "This document requires signatures" · Start · "Form field auto
    detection is ON. Change"."""

    def __init__(self, window: MainWindow) -> None:
        w = window

        def open_file():  # (user 2026-10-04: an option to use the file that's already open)
            if w.session is not None:
                w.start_form(detect=w.settings.form_auto_detect, signatures=self.signatures.isChecked())

        def single_file():
            before = w.session
            w.open_dialog()
            if w.session is not None and w.session is not before:
                w.start_form(detect=w.settings.form_auto_detect, signatures=self.signatures.isChecked())

        def scanned():
            if w.extras.create_from_scanner():
                w.start_form(detect=w.settings.form_auto_detect, signatures=self.signatures.isChecked())

        def blank():
            if w.create_blank_pdf():
                w.start_form(detect=False, signatures=False)

        # (Acrobat's "Create a Web Form" is its online service: left out with the other cloud items)
        self.signatures = QCheckBox("This document requires signatures")
        self.signatures.setToolTip("Also make signature fields where the form has lines to sign on")
        self.signatures.setStyleSheet("font-size: 11pt; margin-top: 14px;")
        super().__init__(w, "To begin select a file, scan a document, or start from scratch", [
            Choice("current", "Open File", "file-check", "The file that's open now: its blanks, boxes, and lines "
                   "become fields.", open_file, "Start"),
            Choice("file", "Single File", "file", "Choose a PDF: its blanks, boxes, and lines become fields.",
                   single_file, "Choose File"),
            Choice("scanner", "Scanner", "scanner-image", "Scan a paper form; its blanks and boxes become fields.",
                   scanned, "Start"),
            Choice("new", "Create New", "file-circle-plus", "Start a form on an empty page and add the fields "
                   "yourself.", blank, "Start"),
        ], document=True, help_text="Fields can be moved, resized, and changed afterwards; the Fields list on the "
                                    "right shows them all.")
        self.change_file.show()
        self.signatures.setParent(self)  # (for every choice: under the button)
        self.below.addWidget(self.signatures, 0, Qt.AlignmentFlag.AlignHCenter)
        self.detect_line = QLabel(self)
        self.detect_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detect_line.linkActivated.connect(lambda _l: self._toggle_detection())
        self.below.addSpacing(10)
        self.below.addWidget(self.detect_line)
        self._show_detection()
        self.refresh()

    def refresh(self) -> None:
        """"Open File" only while a file is open (and then it's the first choice)."""
        current = next(i for i, c in enumerate(self.choices) if c.key == "current")
        has_file = self.w.session is not None
        self.list.item(current).setHidden(not has_file)
        if has_file and getattr(self, "_had_file", None) is not True:
            self.list.setCurrentRow(current)
        elif not has_file and self.list.currentRow() == current:
            self.list.setCurrentRow(current + 1)
        self._had_file = has_file
        super().refresh()

    def _toggle_detection(self) -> None:
        self.w.settings.form_auto_detect = not self.w.settings.form_auto_detect
        self._show_detection()

    def _show_detection(self) -> None:
        state = "ON" if self.w.settings.form_auto_detect else "OFF"
        self.detect_line.setText(f'Form field auto detection is {state}. <a href="change">Change</a>')
        self.detect_line.setToolTip("ON: blanks, boxes, and lines on the pages become form fields when you start")

    def _changed(self, row: int) -> None:
        super()._changed(row)
        self.document_box.setVisible(self.current().key == "current")


def prepare_form_page(window: MainWindow) -> StartPage:
    return PrepareFormPage(window)
