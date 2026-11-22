"""Export To ▸ the formats added in backlog Phase C, and Save as Other ▸ PDF/A·X·E."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from planetpdf.core import ghostscript
from planetpdf.core.export import ExportFormat, ExportOptions
from planetpdf.ui.dialogs.export import FILE_FILTERS, FORMAT_LABELS, ExportDialog
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.open_file(pdf_copy("text.pdf"))
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    w.undo_stack.setClean()
    w.close()


def test_every_format_is_on_the_menus_and_in_the_dialog(win):
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.menu() and a.menu().title() == "&File")
    export_menu = next(a.menu() for a in file_menu.actions() if a.menu() and "Expor" in a.menu().title())

    def texts(menu):
        for a in menu.actions():
            if a.menu():
                yield from texts(a.menu())
            elif a.text():
                yield a.text().replace("&", ""), a.isEnabled()

    items = dict(texts(export_menu))
    for name in ("Word 97-2003 Document", "Microsoft Excel Workbook", "XML Spreadsheet 2003",
                 "Microsoft PowerPoint Presentation", "JPEG2000", "HTML Web Page", "Encapsulated PostScript",
                 "PostScript", "Text (Accessible)", "XML 1.0"):
        assert items.get(name) is True, name
    assert set(FORMAT_LABELS) == set(ExportFormat) and set(FILE_FILTERS) == set(ExportFormat)
    dialog = ExportDialog(3, [], False, True, ExportOptions(), ExportFormat.PPTX)
    assert dialog.dpi.isVisibleTo(dialog) and "editable text boxes" in dialog.note.text()


@pytest.mark.parametrize("fmt", [ExportFormat.HTML, ExportFormat.XLSX, ExportFormat.PPTX, ExportFormat.TXT_ACCESSIBLE])
def test_export_runs_in_the_background(win, tmp_path, fmt):
    files = win.export_to(fmt, [0, 1], tmp_path / f"out{fmt.extension}", ExportOptions(dpi=50))
    assert files and files[0].exists()


@pytest.mark.skipif(ghostscript.find() is None, reason="Ghostscript not installed")
def test_save_as_pdfa(win, tmp_path):
    target = win.save_as_standard("pdfa", tmp_path / "archive.pdf")
    assert target == tmp_path / "archive.pdf" and "PDF/A-2b" in win.messages[-1]
    with pymupdf.open(target) as doc:
        assert "pdfaid" in doc.get_xml_metadata()
    assert win.session.path.name == "text.pdf"  # (the open document is unchanged)


def test_save_as_standard_without_ghostscript_says_so(win, tmp_path, monkeypatch):
    monkeypatch.setattr(ghostscript, "find", lambda: None)
    assert win.save_as_standard("pdfx", tmp_path / "x.pdf") is None
    assert "needs Ghostscript" in win.messages[-1]
