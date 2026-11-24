"""Create ▸ Create Multiple PDF Files, Create ▸ PDF Portfolio, Scan & OCR ▸ Recognize Text
▸ In Multiple Files, Scan & OCR ▸ Enhance, and the Attachments panel (backlog Phase C,
built 2026-10-02)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import batch as B
from planetpdf.core.document import PdfError

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _series(items: list[str]) -> str:
    """"Word", "Word and Excel", "Word, Excel, and PowerPoint" (always the Oxford comma)."""
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


class FileListDialog(QDialog):
    """A list of files (Add Files / Add Folder / Remove / Up / Down), an output folder,
    and room for options."""

    def __init__(self, window: MainWindow, title: str, intro: str, *, file_filter: str = "All files (*)",
                 folder: bool = True, ok_text: str = "Start") -> None:
        super().__init__(window)
        self._window = window
        self._filter = file_filter
        self.setWindowTitle(title)
        self.setMinimumSize(560, 420)
        label = QLabel(intro, self)
        label.setWordWrap(True)
        self.list = QListWidget(self)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        buttons_row = QHBoxLayout()
        for text, slot in (("Add Files…", self._add_files), ("Add Folder…", self._add_folder),
                           ("Remove", self._remove), ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))):
            b = QPushButton(text, self)
            b.clicked.connect(slot)
            buttons_row.addWidget(b)
        buttons_row.addStretch(1)
        self.form = QFormLayout()
        self.folder = QLineEdit(window.settings.last_directory, self)
        self.folder.setAccessibleName("Save in folder")
        self._folder_chosen = False  # (until the user picks one, it follows the first file's folder)
        self.folder.textEdited.connect(lambda _text: setattr(self, "_folder_chosen", True))
        if folder:
            browse = QPushButton("Browse…", self)
            browse.clicked.connect(self._choose_folder)
            row = QHBoxLayout()
            row.addWidget(self.folder, 1)
            row.addWidget(browse)
            self.form.addRow("Save in:", row)
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c0392b;")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons_row)
        layout.addLayout(self.form)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)

    def add_paths(self, paths) -> None:
        existing = set(self.files())
        was_empty = not existing
        for path in paths:
            path = Path(path)
            if path.is_file() and path not in existing:
                item = QListWidgetItem(path.name, self.list)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                existing.add(path)
        files = self.files()
        if was_empty and files and not self._folder_chosen:
            # beside the files (user 2026-11-24: "it should default to saving the file in the same path")
            self.folder.setText(str(self._window.new_pdf_folder(files[0])))
            self._window.remember_source_folder(files[0])

    def files(self) -> list[Path]:
        return [Path(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())]

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Files", self._window.settings.last_directory, self._filter)
        self.add_paths(paths)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Folder", self._window.settings.last_directory)
        if folder:
            self.add_paths(sorted(p for p in Path(folder).iterdir() if p.is_file()))

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        if 0 <= row + step < self.list.count() and row >= 0:
            item = self.list.takeItem(row)
            self.list.insertItem(row + step, item)
            self.list.setCurrentRow(row + step)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save In", self.folder.text())
        if folder:
            self.folder.setText(folder)
            self._folder_chosen = True

    def _accept(self) -> None:
        if not self.files():
            self.error.setText("Add the files first.")
            return
        self.accept()


class BatchUi:
    def __init__(self, window: MainWindow) -> None:
        self.w = window

    # --- Create Multiple PDF Files -----------------------------------------------------------

    def create_multiple(self, files=None, folder: Path | None = None) -> list:
        from planetpdf.core import office
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        w = self.w
        if files is None:
            apps = [name for app, name in (("word", "Word"), ("excel", "Excel"), ("powerpoint", "PowerPoint"))
                    if office.available(app)]
            dialog = FileListDialog(
                w, "Create Multiple PDF Files",
                "Each file becomes a PDF of its own: pictures, text, XPS, and EPUB files."
                + (f" {_series(apps)} files are converted through Microsoft Office." if apps else ""),
                ok_text="Create PDFs")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            files, folder = dialog.files(), Path(dialog.folder.text())
        try:
            results = run_in_background(w, "Create Multiple PDF Files", "Creating PDFs",
                                        lambda progress: B.create_multiple(files, folder, progress))
        except (TaskCanceled, B.BatchCanceled):
            w.statusBar().showMessage("Canceled.", 4000)
            return []
        self._summary("Create Multiple PDF Files", results, folder)
        return results

    def _summary(self, title: str, results, folder) -> None:
        made = [r for r in results if r.output is not None]
        failed = [r for r in results if r.output is None]
        text = f"{len(made)} of {len(results)} file{'s' if len(results) != 1 else ''} done" + (
            f", in {folder}." if folder else ".")
        if failed:
            text += "\n\nNot done:\n" + "\n".join(f"• {r.source.name}: {r.error}" for r in failed[:12])
        notes = [r for r in made if r.error]
        if notes:
            text += "\n\n" + "\n".join(f"• {r.source.name}: {r.error}" for r in notes[:12])
        self.w.show_message(title, text)

    # --- PDF Portfolio -------------------------------------------------------------------------

    def portfolio(self, files=None, target: Path | None = None) -> Path | None:
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        w = self.w
        if files is None:
            dialog = FileListDialog(w, "PDF Portfolio",
                                    "The files are kept together, unchanged, inside one PDF (any kind of file). "
                                    "A cover page lists them; open them from the Attachments panel.",
                                    folder=False, ok_text="Create Portfolio…")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            files = dialog.files()
        if target is None:
            path, _ = QFileDialog.getSaveFileName(w, "Save Portfolio As", str(Path(w.settings.last_directory) / "Portfolio.pdf"),
                                                  "PDF files (*.pdf)")
            if not path:
                return None
            target = Path(path)
        try:
            run_in_background(w, "PDF Portfolio", f"Creating {target.name}",
                              lambda progress: B.make_portfolio(files, target, progress))
        except (TaskCanceled, B.BatchCanceled):
            return None
        except (PdfError, OSError) as exc:
            w.show_message("PDF Portfolio", str(exc))
            return None
        w.open_paths([target])
        return target

    # --- Recognize Text in Multiple Files ------------------------------------------------------

    def ocr_multiple(self, files=None, folder: Path | None = None, language: str | None = None) -> list:
        from planetpdf.core import ocr
        from planetpdf.ui.tasks import TaskCanceled, run_in_background

        w = self.w
        languages = ocr.available_languages()
        if not languages:
            w.show_message("Recognize Text", "Text recognition needs Tesseract OCR, which wasn't found on this computer.")
            return []
        if files is None:
            dialog = FileListDialog(w, "Recognize Text in Multiple Files",
                                    "Every scanned page in these PDFs gets invisible, searchable text. Pages that "
                                    "already have text are left alone.", file_filter="PDF files (*.pdf)",
                                    ok_text="Recognize Text")
            language_box = QComboBox(dialog)
            language_box.addItems(languages)
            language_box.setCurrentText(w.settings.ocr_language if w.settings.ocr_language in languages else languages[0])
            in_place = QCheckBox("Replace the original files (otherwise new copies go in the folder)", dialog)
            dialog.form.addRow("Language:", language_box)
            dialog.form.addRow("", in_place)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            files, language = dialog.files(), language_box.currentText()
            folder = None if in_place.isChecked() else Path(dialog.folder.text())
        try:
            results = run_in_background(
                w, "Recognize Text", "Recognizing text in multiple files",
                lambda progress: B.ocr_files(files, folder, language=language or "eng", dpi=w.settings.ocr_dpi,
                                             progress=progress))
        except (TaskCanceled, B.BatchCanceled):
            w.statusBar().showMessage("Canceled. Files finished before that are done.", 6000)
            return []
        except PdfError as exc:
            w.show_message("Recognize Text", str(exc))
            return []
        self._summary("Recognize Text", results, folder)
        return results

    # --- Enhance -------------------------------------------------------------------------------

    def enhance(self, mode: str, pages: list[int] | None = None, color: bool | None = None) -> bool:
        """Scan & OCR ▸ Enhance ▸ Scanned Document ("scan") / Camera Image ("camera")."""
        from planetpdf.core import enhance as E
        from planetpdf.core.pagerange import format_page_ranges
        from planetpdf.ui.commands import PageContentCommand
        from planetpdf.ui.dialogs.page_range_edit import PageRangeEdit
        from planetpdf.ui.tasks import TaskCanceled, run_with_progress

        w = self.w
        title = "Enhance Scanned Document" if mode == "scan" else "Enhance Camera Image"
        if pages is None:
            dialog = QDialog(w)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(440)
            range_edit = PageRangeEdit(w.session.page_count, format_page_ranges(w.selected_pages()) or "all", dialog)
            keep_color = QCheckBox("Keep the colors (otherwise black and white text on white, the clearest)", dialog)
            note = QLabel("Straightens the page, makes the paper white, and sharpens the text"
                          + (", after flattening the photo to the sheet of paper." if mode == "camera" else ".")
                          + " The page becomes the cleaned-up picture: run Recognize Text afterwards for "
                            "searchable text. Undo puts it back.", dialog)
            note.setWordWrap(True)
            note.setStyleSheet("color: gray;")
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
            box.button(QDialogButtonBox.StandardButton.Ok).setText("Enhance")
            box.accepted.connect(dialog.accept)
            box.rejected.connect(dialog.reject)
            form = QFormLayout(dialog)
            form.addRow("Pages:", range_edit)
            form.addRow("", keep_color)
            form.addRow(note)
            form.addRow(box)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            pages, color = range_edit.pages(), keep_color.isChecked()
        holder = {}

        def work(progress):
            holder["command"] = PageContentCommand.applied(
                w, pages, lambda s: E.enhance_pages(s, pages, mode, color=bool(color), progress=progress), title)

        try:
            run_with_progress(w, title, title, work)
        except TaskCanceled:
            if "command" in holder:  # (pages enhanced before Cancel: keep them, as one undo step)
                w.push_command(holder["command"])
            return False
        except PdfError as exc:
            w.show_message(title, str(exc))
            return False
        w.push_command(holder["command"])
        return True


class AttachmentsPanel(QWidget):
    """View ▸ Show/Hide ▸ Navigation Panes ▸ Attachments: the files inside the PDF (a
    portfolio's files, or any attachments). Save copies out; add or remove (undoable)."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.list = QListWidget(self)
        self.list.setAccessibleName("Attachments")
        self.list.itemDoubleClicked.connect(lambda _i: self.save_selected())
        save = QPushButton("Save As…", self)
        save.setToolTip("Save a copy of the attached file (PlanetPDF never runs attachments)")
        save.clicked.connect(self.save_selected)
        add = QPushButton("Add…", self)
        add.clicked.connect(self.add_files)
        remove = QPushButton("Remove", self)
        remove.clicked.connect(self.remove_selected)
        row = QHBoxLayout()
        for b in (save, add, remove):
            row.addWidget(b)
        layout = QVBoxLayout(self)
        self.heading = QLabel(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)

    def refresh(self) -> None:
        self.list.clear()
        session = self._window.session
        if session is None:
            self.heading.setText("")
            return
        items = B.attachments(session.doc)
        self.heading.setText(("PDF Portfolio: " if B.is_portfolio(session.doc) else "")
                             + f"{len(items)} attached file{'s' if len(items) != 1 else ''}")
        for name, size in items:
            item = QListWidgetItem(f"{name}   ({size / 1024:,.0f} KB)", self.list)
            item.setData(Qt.ItemDataRole.UserRole, name)

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def save_selected(self) -> None:
        name = self._selected()
        if name is not None:
            self._window.save_attachment(name, self._window.session.doc.embfile_get(name))

    def add_files(self, paths=None) -> None:
        from planetpdf.ui.commands import DocumentSnapshotCommand

        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(self, "Attach Files", self._window.settings.last_directory, "All files (*)")
        if not paths:
            return

        def attach(session):
            for path in paths:
                session.doc.embfile_add(Path(path).name, Path(path).read_bytes(), filename=Path(path).name)
            session.mark_modified()

        self._window.push_command(DocumentSnapshotCommand.applied(self._window, attach, "Attach Files"))
        self.refresh()

    def remove_selected(self) -> None:
        from planetpdf.ui.commands import DocumentSnapshotCommand

        name = self._selected()
        if name is None:
            return

        def remove(session):
            session.doc.embfile_del(name)
            session.mark_modified()

        self._window.push_command(DocumentSnapshotCommand.applied(self._window, remove, f"Remove {name}"))
        self.refresh()
