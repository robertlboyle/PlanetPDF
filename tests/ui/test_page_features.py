"""Rotate, export, view layouts and printing (offscreen)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QPageRanges
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from planetpdf.core.export import ExportFormat, ExportOptions
from planetpdf.ui.dialogs.export import ExportDialog
from planetpdf.ui.dialogs.rotate import RotateDialog
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.printing import pages_to_print, print_pages, print_sequence
from planetpdf.ui.viewer.page_view import LayoutMode, ZoomMode
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "config")
    win.resize(1000, 800)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


# --- rotate --------------------------------------------------------------------------


def test_rotate_selected_with_undo(window):
    window.thumbnails.select_pages([0, 2])
    window.rotate_pages(window.target_pages(), 90)
    assert [window.session.doc[i].rotation for i in range(3)] == [90, 0, 90]
    assert window.view._items[0].boundingRect().width() == pytest.approx(g.LETTER.height)
    window.undo_stack.undo()
    assert [window.session.doc[i].rotation for i in range(3)] == [0, 0, 0]
    assert not window.is_dirty()


def test_rotate_dialog_choices(qapp):
    dialog = RotateDialog(10, [1, 2])
    assert dialog.rb_selected.isChecked()
    assert dialog.pages() == [1, 2]
    assert dialog.degrees() == 90  # default: clockwise 90

    dialog.direction.setCurrentIndex(1)
    dialog.angle.setCurrentIndex(2)
    assert dialog.degrees() == -270  # counterclockwise 270

    dialog.rb_all.setChecked(True)
    assert dialog.pages() == list(range(10))

    dialog.rb_range.setChecked(True)
    dialog.range_edit.line.setText("1,5,8-10")
    assert dialog.pages() == [0, 4, 7, 8, 9]
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok.isEnabled()
    dialog.range_edit.line.setText("1,99")
    assert not ok.isEnabled()
    assert "99" in dialog.range_edit.status.text()


def test_rotate_dialog_without_selection_defaults_to_all(qapp):
    dialog = RotateDialog(4, [])
    assert dialog.rb_all.isChecked()
    assert not dialog.rb_selected.isEnabled()


# --- export --------------------------------------------------------------------------


def test_export_dialog_prefills_selection(qapp):
    dialog = ExportDialog(20, [0, 4, 7, 8, 9, 10, 11], False, True, ExportOptions())
    assert dialog.range_edit.line.text() == "1,5,8-12"
    assert dialog.pages() == [0, 4, 7, 8, 9, 10, 11]
    dialog = ExportDialog(20, [], True, True, ExportOptions())
    assert dialog.range_edit.line.text() == "all"
    assert "asked whether to protect the new file" in dialog.note.text()


def test_export_dialog_options_follow_format(qapp):
    dialog = ExportDialog(5, [], False, True, ExportOptions(dpi=200), ExportFormat.JPG)
    dialog.show()
    assert dialog.dpi.isVisible() and dialog.quality.isVisible() and not dialog.ocr.isVisible()
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(ExportFormat.DOCX))
    assert not dialog.dpi.isVisible() and dialog.ocr.isVisible()
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(ExportFormat.IMAGE_PDF))
    dialog.compress.setChecked(False)
    assert dialog.dpi.isVisible() and not dialog.quality.isVisible()
    assert dialog.fmt() is ExportFormat.IMAGE_PDF and dialog.options().dpi == 200
    no_ocr = ExportDialog(5, [], False, False, ExportOptions(ocr_missing_text=True), ExportFormat.TXT)
    assert not no_ocr.ocr.isEnabled() and not no_ocr.options().ocr_missing_text
    dialog.close()


def test_export_images_in_background(window, tmp_path):
    files = window.export_to(ExportFormat.PNG, [0, 2], tmp_path / "shot.png", ExportOptions(dpi=50))
    assert [f.name for f in files] == ["shot_p001.png", "shot_p003.png"]
    assert all(f.exists() for f in files)
    assert not window.is_dirty()


def test_export_word_in_background(window, tmp_path):
    import docx

    [f] = window.export_to(ExportFormat.DOCX, [0], tmp_path / "doc.docx", ExportOptions())
    assert g.SSN in "\n".join(p.text for p in docx.Document(str(f)).paragraphs)


def test_export_pages_from_window(window, tmp_path):
    target = tmp_path / "subset.pdf"
    assert window.export_pages([2, 0], target)
    with pymupdf.open(target) as doc:
        assert doc.page_count == 2
        assert "Page 3" in doc[0].get_text()
        assert "Page 1" in doc[1].get_text()
    assert window.session.page_count == g.TEXT_PAGE_COUNT
    assert not window.is_dirty()  # exporting isn't an edit


# --- view layouts --------------------------------------------------------------------------


def _item_pos(window, index):
    return window.view._items[index].scenePos()


def test_two_up_places_pages_side_by_side(window):
    window.set_layout(LayoutMode.TWO_UP)
    assert _item_pos(window, 0).y() == _item_pos(window, 1).y()
    assert _item_pos(window, 0).x() < 0 < _item_pos(window, 1).x()
    assert _item_pos(window, 2).y() > _item_pos(window, 0).y()
    assert window.settings.view_layout == "two_up"


def test_cover_page_separate(window):
    window.set_layout(LayoutMode.TWO_UP)
    window.act_cover.trigger()
    assert window.view.cover_separate
    # Page 1 alone on the right; pages 2 and 3 share the next row.
    assert _item_pos(window, 0).x() > 0
    assert _item_pos(window, 1).y() == _item_pos(window, 2).y() > _item_pos(window, 0).y()


def test_grid_fits_four_pages(window):
    window.set_layout(LayoutMode.GRID_2X2)
    assert window.view.zoom_mode is ZoomMode.FIT_PAGE
    viewport = window.view.viewport().rect()
    visible = window.view.mapToScene(viewport).boundingRect()
    # First two rows (pages 1-3 here, 4 slots) are fully visible.
    for index in range(3):
        assert visible.contains(window.view._items[index].sceneBoundingRect())


def test_layout_change_keeps_current_page(window):
    window.view.go_to_page(2)
    window.set_layout(LayoutMode.TWO_UP)
    assert window.view.current_page == 2
    window.set_layout(LayoutMode.SINGLE)
    assert window.view.current_page == 2


def test_layout_survives_page_changes(window):
    window.set_layout(LayoutMode.TWO_UP)
    window.insert_blank_page()
    assert window.view.layout_mode is LayoutMode.TWO_UP
    assert len(window.view._items) == g.TEXT_PAGE_COUNT + 1


# --- printing --------------------------------------------------------------------------------


def _quiet_printer() -> QPrinter:
    """A printer object that never asks the default printer's driver: `QPrinter()` does,
    and an unreachable network printer's driver took 50 s or crashed the test run
    (2026-10-03). Built on a printer that's always there, then printing to a PDF file."""
    from PySide6.QtPrintSupport import QPrinterInfo

    names = QPrinterInfo.availablePrinterNames()
    safe = next((n for n in ("Microsoft Print to PDF", "Microsoft XPS Document Writer") if n in names), None)
    if safe is None:
        pytest.skip("needs Microsoft Print to PDF (a printer object would ask the default printer's driver)")
    printer = QPrinter(QPrinterInfo.printerInfo(safe), QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    return printer


def _pdf_printer(path) -> QPrinter:
    printer = _quiet_printer()
    printer.setOutputFileName(str(path))
    printer.setResolution(72)  # keep the test fast
    return printer


def test_pages_to_print_modes(qapp):
    printer = _quiet_printer()
    assert pages_to_print(printer, 5, 2, []) == [0, 1, 2, 3, 4]
    printer.setPrintRange(QPrinter.PrintRange.CurrentPage)
    assert pages_to_print(printer, 5, 2, []) == [2]
    printer.setPrintRange(QPrinter.PrintRange.Selection)
    assert pages_to_print(printer, 5, 2, [3, 1]) == [1, 3]
    printer.setPrintRange(QPrinter.PrintRange.PageRange)
    ranges = QPageRanges()
    ranges.addRange(2, 3)
    ranges.addPage(9)  # beyond the document: dropped
    printer.setPageRanges(ranges)
    assert pages_to_print(printer, 5, 0, []) == [1, 2]


def test_print_sequence_expands_copies_only_when_needed(qapp, monkeypatch):
    printer = _quiet_printer()
    printer.setCopyCount(2)
    monkeypatch.setattr(printer, "supportsMultipleCopies", lambda: False)
    printer.setCollateCopies(True)
    assert print_sequence(printer, [0, 1]) == [0, 1, 0, 1]
    printer.setCollateCopies(False)
    assert print_sequence(printer, [0, 1]) == [0, 0, 1, 1]
    monkeypatch.setattr(printer, "supportsMultipleCopies", lambda: True)
    assert print_sequence(printer, [0, 1]) == [0, 1]


def test_print_to_pdf_file(window, tmp_path, pdf_copy):
    from planetpdf.core.document import PdfSession

    out = tmp_path / "printed.pdf"
    session = PdfSession.open(pdf_copy("mixed_sizes.pdf"))
    calls = []
    assert print_pages(_pdf_printer(out), session, [0, 1, 2], lambda d, t: calls.append(d) or True)
    session.close()
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        # Every printed page is portrait paper, including the landscape source page.
        assert all(page.rect.height > page.rect.width for page in doc)
        assert all(page.get_images() for page in doc)  # something was drawn
    assert calls[-1] == 3


def test_print_can_be_canceled(window, tmp_path):
    out = tmp_path / "canceled.pdf"
    assert not print_pages(_pdf_printer(out), window.session, [0, 1, 2], lambda d, t: d < 1)


def test_a_printer_that_cant_start_gives_a_plain_message(window, tmp_path):
    """User 2026-10-03: "If a printer fails or isn't accessible … give the user a nice
    error message". (A folder as the output file: the printer can't start.)"""
    from planetpdf.ui.printing import PrintError

    with pytest.raises(PrintError, match="unavailable"):
        print_pages(_pdf_printer(tmp_path), window.session, [0])


def test_a_printer_that_isnt_installed_is_reported(qapp):
    from planetpdf.ui.printing import printer_problem, unavailable_text

    problem = printer_problem("PlanetPDF test printer that doesn't exist")
    assert problem == "it isn't installed on this computer"
    text = unavailable_text("Office Printer", problem)
    assert "“Office Printer” is unavailable" in text and "Nothing was printed" in text


@pytest.mark.parametrize("helper, wait, expected", [
    ("import ctypes; ctypes.string_at(0)", 20, "its driver stopped working"),  # (a driver that crashes)
    ("import time; time.sleep(30)", 1, "didn't answer within 1 seconds"),  # (one that hangs)
    ("raise SystemExit(0)", 20, None),
])
def test_a_printer_is_asked_in_a_helper_process_first(monkeypatch, helper, wait, expected):
    """User's Brother printer, unreachable: its driver hung for 50 s or crashed the whole
    program inside QPrinter(). A helper process asks first; whatever it does, PlanetPDF
    keeps running and gets an answer."""
    import sys

    from planetpdf.ui import printer_probe

    monkeypatch.setattr(printer_probe, "_OK", set())
    monkeypatch.setattr(printer_probe, "probe_command", lambda name: [sys.executable, "-c", helper])
    problem = printer_probe.check("Office Printer", timeout=wait)
    assert problem == expected or expected and expected in problem


def test_checking_a_printer_can_be_canceled(monkeypatch):
    import sys

    from planetpdf.ui import printer_probe

    monkeypatch.setattr(printer_probe, "_OK", set())
    monkeypatch.setattr(printer_probe, "probe_command", lambda name: [sys.executable, "-c", "import time; time.sleep(30)"])
    seen = []
    with pytest.raises(printer_probe.Canceled):
        printer_probe.check("Office Printer", timeout=20, progress=lambda s, t: (seen.append((s, t)), False)[1])
    assert seen and seen[0][1] == 20


def test_the_helper_process_reports_a_missing_printer(qapp):
    from planetpdf.ui import printer_probe

    assert printer_probe.check("PlanetPDF test printer that doesn't exist") == "it isn't installed on this computer"


def test_print_says_so_when_no_printer_answers(window, monkeypatch):
    from PySide6.QtPrintSupport import QPrinterInfo

    from planetpdf.ui import printing

    # Whatever this computer has installed (a Mac may have no printers at all).
    monkeypatch.setattr(QPrinterInfo, "availablePrinterNames", staticmethod(lambda: ["Office Printer"]))
    monkeypatch.setattr(printing, "checked_printer", lambda parent, name: "it didn't answer within 30 seconds")
    with pytest.raises(printing.PrintError, match="is unavailable \\(it didn't answer within 30 seconds\\)"):
        printing.print_with_dialog(window, window.session, 0, [], "")


@pytest.mark.parametrize("platform, where, url", [
    ("win32", "Windows Settings (Bluetooth & devices ▸ Printers & scanners)", "ms-settings:printers"),
    ("darwin", "System Settings (Printers & Scanners)",
     "x-apple.systempreferences:com.apple.Print-Scan-Settings.extension"),
    ("linux", "your system's printer settings", None),
])
def test_no_printers_names_this_systems_printer_settings(platform, where, url):
    from planetpdf.ui.printing import no_printers_error

    error = no_printers_error(platform)
    assert f"Add a printer in {where}, then print again." in str(error)
    assert error.settings_url == url


def test_no_printers_message_opens_the_printer_settings(window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtPrintSupport import QPrinterInfo
    from PySide6.QtWidgets import QMessageBox

    from planetpdf.ui import main_window as mw
    from planetpdf.ui.printing import no_printers_error, print_with_dialog

    monkeypatch.setattr(QPrinterInfo, "availablePrinterNames", staticmethod(lambda: []))
    with pytest.raises(mw.PrintError) as raised:
        print_with_dialog(window, window.session, 0, [], "")
    assert raised.value.settings_url == no_printers_error().settings_url

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    box = window.print_error_box(no_printers_error("darwin"))
    [button] = [b for b in box.buttons() if b.text() == "Open Printer Settings"]
    button.click()
    assert opened == ["x-apple.systempreferences:com.apple.Print-Scan-Settings.extension"]
    assert box.defaultButton() is box.button(QMessageBox.StandardButton.Ok)
    box.deleteLater()

    shown = []
    monkeypatch.setattr(mw.MainWindow, "print_error_box",
                        lambda self, exc: type("Box", (), {"exec": lambda _s: shown.append(str(exc))})())
    window.print_dialog()
    [text] = shown
    assert text.startswith("No printers are installed on this computer.")


@pytest.mark.parametrize("failure", ["print error", "driver error"])
def test_print_failures_never_escape_the_print_command(window, monkeypatch, failure):
    """Whatever goes wrong while printing, the user gets a message, never a crash dialog."""
    from PySide6.QtWidgets import QMessageBox

    from planetpdf.ui import main_window as mw
    from planetpdf.ui.printing import PrintError, unavailable_text

    def broken(*_args, **_kw):
        if failure == "print error":
            raise PrintError(unavailable_text("Office Printer", "offline"))
        raise RuntimeError("the driver gave up")

    shown = []
    monkeypatch.setattr(mw, "print_with_dialog", broken)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append((a[1], a[2])))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append((a[1], a[2])))
    window.print_dialog()
    [(title, text)] = shown
    if failure == "print error":
        assert title == "Printer Unavailable" and "Office Printer" in text and "offline" in text
    else:
        assert title == "Printing Didn't Work" and "nothing was printed" in text and "the driver gave up" in text


@pytest.mark.parametrize("options, problem", [
    ("copies=1 printer-state=3 printer-state-reasons=none printer-is-accepting-jobs=true", None),
    ("printer-state=3 printer-state-reasons=offline-report printer-is-accepting-jobs=true", "offline"),
    ("printer-state=3 printer-state-reasons=media-empty-error printer-is-accepting-jobs=true", "it's out of paper"),
    ("printer-state=3 printer-state-reasons=toner-low-warning printer-is-accepting-jobs=true", None),  # (only low)
    ("printer-state=5 printer-state-reasons=paused printer-is-accepting-jobs=true", "it's paused"),
    ("printer-state=3 printer-state-reasons=none printer-is-accepting-jobs=false", "it isn't accepting print jobs"),
    ("printer-info='Office Brother' printer-state=4 printer-state-reasons=door-open-error,media-low-report",
     "its door is open"),
])
def test_mac_and_linux_printers_say_whats_wrong(options, problem):
    """CUPS keeps why a printer can't print (2026-10-14: the Mac got the messages Windows had)."""
    from planetpdf.ui.printing import cups_problem

    assert cups_problem(options) == problem
