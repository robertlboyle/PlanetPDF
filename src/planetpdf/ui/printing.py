"""Printing to any system printer through Qt's print support (cross-platform).

Each PDF page is rendered at up to MAX_PRINT_DPI, scaled to fit the printable area
(aspect preserved, centered) and rotated 90° when its orientation doesn't match the
paper's. Copies are left to the driver when it supports them; otherwise we repeat
the pages ourselves (honoring collation).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QProgressDialog, QWidget

from planetpdf.core.document import PdfSession
from planetpdf.core.render import render_page
from planetpdf.ui import accessibility

MAX_PRINT_DPI = 300


class PrintError(Exception):
    def __init__(self, text: str, settings_url: str | None = None):
        super().__init__(text)
        self.settings_url = settings_url  # offered as "Open Printer Settings" when set


def printer_settings(platform: str | None = None) -> tuple[str, str | None]:
    """Where printers are added on this system, as words for a message, and the address
    that opens that place (None where there's no standard one)."""
    import sys

    platform = platform or sys.platform
    if platform == "win32":
        return "Windows Settings (Bluetooth & devices ▸ Printers & scanners)", "ms-settings:printers"
    if platform == "darwin":
        return ("System Settings (Printers & Scanners)",
                "x-apple.systempreferences:com.apple.Print-Scan-Settings.extension")
    return "your system's printer settings", None


def no_printers_error(platform: str | None = None) -> PrintError:
    where, url = printer_settings(platform)
    return PrintError(f"No printers are installed on this computer.\n\nAdd a printer in {where}, "
                      "then print again.", url)


def unavailable_text(name: str, reason: str = "") -> str:
    """The message shown when a printer can't be used (user 2026-10-03: "give the user a
    nice error message stating that the printer is unavailable")."""
    who = f"The printer “{name}”" if name else "The printer"
    why = f" ({reason})" if reason else ""
    return (f"{who} is unavailable{why}.\n\nCheck that it is turned on and connected to this computer "
            "or the network, or choose another printer. Nothing was printed.")


# Windows: PRINTER_INFO_2 status bits and the "Use Printer Offline" attribute
_WIN_OFFLINE_STATUS = {0x80: "offline", 0x2: "it reports an error", 0x8: "it's out of paper",
                       0x40000: "its door is open", 0x400000: "it needs attention"}
_WIN_WORK_OFFLINE = 0x400


def printer_problem(name: str) -> str | None:
    """Why a printer can't print right now, as far as the system knows, or None. Network
    printers often look fine until a job is sent: then printing itself reports it."""
    from PySide6.QtPrintSupport import QPrinterInfo

    info = QPrinterInfo.printerInfo(name) if name else QPrinterInfo.defaultPrinter()
    if info.isNull():
        return "it isn't installed on this computer" if name else "no printer is installed"
    if info.state() == QPrinter.PrinterState.Error:
        return "it reports an error"
    import sys

    if sys.platform == "win32":
        return _windows_printer_problem(info.printerName())
    return _cups_printer_problem(info.printerName())  # (macOS and Linux print through CUPS)


# CUPS' printer-state-reasons (RFC 2911 / PWG 5100.9) -> what the message says; a reason may end
# in "-report" / "-warning" / "-error": only errors and offline stop printing
_CUPS_REASONS = {"offline": "offline", "media-empty": "it's out of paper", "media-needed": "it's out of paper",
                 "media-jam": "paper is jammed", "door-open": "its door is open", "cover-open": "its cover is open",
                 "toner-empty": "it's out of toner", "marker-supply-empty": "it's out of toner or ink",
                 "input-tray-missing": "a paper tray is missing", "paused": "it's paused",
                 "stopped": "it's stopped", "shutdown": "it's switched off", "connecting-to-device": "it isn't answering"}


def cups_problem(options: str) -> str | None:
    """Why CUPS can't print to a printer, from ``lpoptions -p NAME``'s output, or None."""
    import shlex

    try:
        values = dict(item.split("=", 1) for item in shlex.split(options) if "=" in item)
    except ValueError:
        return None
    if values.get("printer-state") == "5":  # (stopped: paused in System Settings / cupsdisable)
        return "it's paused"
    if values.get("printer-is-accepting-jobs") == "false":
        return "it isn't accepting print jobs"
    for reason in values.get("printer-state-reasons", "none").split(","):
        base = reason
        for suffix in ("-error", "-report", "-warning"):
            base = base.removesuffix(suffix)
        stops = base == "offline" or reason.endswith("-error") or base in ("paused", "stopped", "shutdown")
        if stops and base in _CUPS_REASONS:
            return _CUPS_REASONS[base]
    return None


def _cups_printer_problem(name: str) -> str | None:
    import shutil
    import subprocess

    lpoptions = shutil.which("lpoptions")
    if not lpoptions or not name:
        return None
    try:
        done = subprocess.run([lpoptions, "-p", name], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cups_problem(done.stdout) if done.returncode == 0 else None


def _windows_printer_problem(name: str) -> str | None:
    import ctypes
    from ctypes import wintypes

    try:
        winspool = ctypes.WinDLL("winspool.drv")
    except OSError:
        return None
    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(name, ctypes.byref(handle), None):
        return "Windows can't open it"
    try:
        needed = wintypes.DWORD()
        winspool.GetPrinterW(handle, 2, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if not winspool.GetPrinterW(handle, 2, buffer, needed, ctypes.byref(needed)):
            return None
        # PRINTER_INFO_2: 13 pointers, then Attributes, Priority, DefaultPriority,
        # StartTime, UntilTime, Status, cJobs, AveragePPM (DWORDs)
        offset = 13 * ctypes.sizeof(ctypes.c_void_p)
        attributes = ctypes.c_uint32.from_buffer(buffer, offset).value
        status = ctypes.c_uint32.from_buffer(buffer, offset + 5 * 4).value
    finally:
        winspool.ClosePrinter(handle)
    if attributes & _WIN_WORK_OFFLINE:
        return "it is set to “Use Printer Offline”"
    for bit, reason in _WIN_OFFLINE_STATUS.items():
        if status & bit:
            return reason
    return None


def pages_to_print(printer: QPrinter, page_count: int, current: int, selection: list[int]) -> list[int]:
    """0-based pages chosen in the print dialog."""
    mode = printer.printRange()
    if mode == QPrinter.PrintRange.CurrentPage:
        pages = [current]
    elif mode == QPrinter.PrintRange.Selection:
        pages = sorted(selection)
    elif mode == QPrinter.PrintRange.PageRange:
        pages = []
        for r in printer.pageRanges().toRangeList():
            pages.extend(range(r.from_ - 1, r.to))
        if not pages and printer.fromPage() > 0:
            pages = list(range(printer.fromPage() - 1, printer.toPage()))
    else:
        pages = list(range(page_count))
    return [p for p in pages if 0 <= p < page_count]


def print_sequence(printer: QPrinter, pages: list[int]) -> list[int]:
    """Pages in output order, expanding copies when the driver can't do them."""
    copies = printer.copyCount()
    if copies <= 1 or printer.supportsMultipleCopies():
        return list(pages)
    if printer.collateCopies():
        return list(pages) * copies
    return [p for p in pages for _ in range(copies)]


def print_pages(
    printer: QPrinter,
    session: PdfSession,
    pages: list[int],
    progress: Callable[[int, int], bool] | None = None,
) -> bool:
    """Print ``pages`` in order. ``progress(done, total)`` returns False to cancel.
    Returns False if canceled."""
    sequence = print_sequence(printer, pages)
    if not sequence:
        raise PrintError("No pages to print.")
    name = printer.printerName() if printer.outputFormat() == QPrinter.OutputFormat.NativeFormat else ""
    painter = QPainter()
    if not painter.begin(printer):
        raise PrintError(unavailable_text(name, "it couldn't be started"))
    try:
        dpi = min(printer.resolution(), MAX_PRINT_DPI)
        paint = printer.pageLayout().paintRectPixels(printer.resolution())
        area = QRectF(0, 0, paint.width(), paint.height())  # painter origin = printable area
        for n, index in enumerate(sequence):
            if progress is not None and not progress(n, len(sequence)):
                printer.abort()
                return False
            if n and not printer.newPage():
                raise PrintError(_stopped_text(name, n, len(sequence)))
            _draw_page(painter, area, session, index, dpi)
            if printer.printerState() in (QPrinter.PrinterState.Error, QPrinter.PrinterState.Aborted):
                raise PrintError(_stopped_text(name, n, len(sequence)))
        if progress is not None:
            progress(len(sequence), len(sequence))
        painter.end()
        if printer.printerState() == QPrinter.PrinterState.Error:
            raise PrintError(_stopped_text(name, len(sequence), len(sequence)))
        return True
    except PrintError:
        if painter.isActive():
            printer.abort()
        raise
    finally:
        if painter.isActive():
            painter.end()


def _stopped_text(name: str, done: int, total: int) -> str:
    who = f"The printer “{name}”" if name else "The printer"
    return (f"{who} stopped responding after {done} of {total} pages were sent.\n\nCheck that it is turned on "
            "and connected, then print again. Pages already sent may still come out.")


def _draw_page(painter: QPainter, area: QRectF, session: PdfSession, index: int, dpi: int) -> None:
    width, height = session.page_size(index)
    rotate = (width > height) != (area.width() > area.height())
    fit_w, fit_h = (height, width) if rotate else (width, height)
    scale = min(area.width() / fit_w, area.height() / fit_h)

    rendered = render_page(session, index, dpi / 72.0)
    image = QImage(
        rendered.samples, rendered.width, rendered.height, rendered.stride, QImage.Format.Format_RGB888
    )
    target = QRectF(-width * scale / 2, -height * scale / 2, width * scale, height * scale)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(area.center())
    if rotate:
        painter.rotate(90)
    painter.drawImage(target, image)
    painter.restore()


SAFE_PRINTERS = ("Microsoft Print to PDF", "Microsoft XPS Document Writer")
_CANCELED = "canceled"


def checked_printer(parent: QWidget | None, name: str) -> str | None:
    """Have a helper process ask the printer's driver first (`printer_probe.check`), with
    a counted "Please wait" dialog. None = fine, ``_CANCELED``, or why it can't be used."""
    from planetpdf.ui import printer_probe
    from planetpdf.ui.tasks import TaskCanceled, run_in_background

    if printer_probe.known_good(name):
        return None

    def describe(done: int, total: int) -> str:
        return (f"Checking that the printer “{name}” is ready: {done} of at most {total} "
                "seconds…\nPlease wait…")

    def work(progress):
        return printer_probe.check(name, progress=lambda s, t: progress(s, t))

    try:
        return run_in_background(parent, "Print", f"Checking the printer “{name}”", work,
                                 describe=describe)
    except (TaskCanceled, printer_probe.Canceled):
        return _CANCELED


def print_with_dialog(
    parent: QWidget, session: PdfSession, current: int, selection: list[int], printer_name: str = ""
) -> str | None:
    """Show the print dialog and print. Returns the printer name used, or None if
    nothing was printed."""
    from PySide6.QtPrintSupport import QPrinterInfo
    from PySide6.QtWidgets import QMessageBox

    names = QPrinterInfo.availablePrinterNames()
    if not names:
        raise no_printers_error()
    # Never let Qt touch a printer's driver before a helper process has (printer_probe):
    # an unreachable printer's driver can take the whole program down.
    start = printer_name if printer_name in names else QPrinterInfo.defaultPrinterName() or names[0]
    problem = checked_printer(parent, start)
    if problem is _CANCELED:
        return None
    if problem:
        fallback = next((n for n in SAFE_PRINTERS if n in names and n != start), None)
        if fallback is None or checked_printer(parent, fallback):
            raise PrintError(unavailable_text(start, problem))
        QMessageBox.information(
            parent, "Printer Unavailable",
            f"The printer “{start}” is unavailable ({problem}).\n\nThe print dialog opens with "
            f"“{fallback}” instead. If you choose another printer there, PlanetPDF checks it first.",
        )
        start = fallback
    printer = QPrinter(QPrinterInfo.printerInfo(start), QPrinter.PrinterMode.HighResolution)
    printer.setDocName(session.path.name)
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle("Print")
    options = (
        QAbstractPrintDialog.PrintDialogOption.PrintPageRange
        | QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage
        | QAbstractPrintDialog.PrintDialogOption.PrintCollateCopies
        | QAbstractPrintDialog.PrintDialogOption.PrintShowPageSize
    )
    if len(selection) > 1:
        options |= QAbstractPrintDialog.PrintDialogOption.PrintSelection
    dialog.setOptions(options)
    dialog.setMinMax(1, session.page_count)
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return None

    pages = pages_to_print(printer, session.page_count, current, selection)
    if not pages:
        raise PrintError("The selected page range doesn't contain any pages.")
    if printer.outputFormat() == QPrinter.OutputFormat.NativeFormat:
        if printer.printerName() != start:  # (another printer chosen in the dialog)
            problem = checked_printer(parent, printer.printerName())
            if problem is _CANCELED:
                return None
            if problem:
                raise PrintError(unavailable_text(printer.printerName(), problem))
        problem = printer_problem(printer.printerName())
        if problem:
            raise PrintError(unavailable_text(printer.printerName(), problem))
    total = len(print_sequence(printer, pages))
    bar = QProgressDialog("Printing…", "Cancel", 0, total, parent)
    bar.setWindowTitle("Print")
    bar.setWindowModality(Qt.WindowModality.WindowModal)
    bar.setMinimumDuration(400)

    def progress(done: int, total_: int) -> bool:
        bar.setValue(done)
        bar.setLabelText(f"Printing page {min(done + 1, total_)} of {total_}…")
        accessibility.say_progress(bar, bar.labelText())
        return not bar.wasCanceled()

    try:
        completed = print_pages(printer, session, pages, progress)
    finally:
        bar.close()
    return printer.printerName() if completed else None
