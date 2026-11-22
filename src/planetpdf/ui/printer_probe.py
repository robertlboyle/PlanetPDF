"""Ask a printer's driver for its settings in a separate, short-lived process.

Some printer drivers crash the whole program when the printer can't be reached (user's
Brother HL-L3280CDW over WSD with the Microsoft IPP class driver, 2026-10-03: an access
violation inside ``QPrinter()``, while Windows still lists the printer as "Normal"). A
native crash can't be caught in Python, so before PlanetPDF lets Qt talk to a printer's
driver, a helper process does it first (``planetpdf --probe-printer NAME``). If that helper
crashes, hangs or fails, the printer is reported as unavailable and PlanetPDF keeps
running (user: "handle printing issues gracefully").
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

PROBE_FLAG = "--probe-printer"
PROBE_SECONDS = 30

_OK: set[str] = set()  # (printers whose driver answered once in this run: asked only once)


def probe_command(name: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, PROBE_FLAG, name]
    return [sys.executable, "-m", "planetpdf", PROBE_FLAG, name]


class Canceled(Exception):
    pass


def known_good(name: str) -> bool:
    return name in _OK


def check(name: str, timeout: float = PROBE_SECONDS, progress=None) -> str | None:
    """None when the printer's driver answers, else why it can't be used.
    ``progress(seconds, timeout)`` is called about twice a second; False cancels
    (raises ``Canceled``)."""
    if name in _OK:
        return None
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        helper = subprocess.Popen(probe_command(name), env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError:
        return None  # (the check itself couldn't run: don't block printing over that)
    start = time.monotonic()
    try:
        while True:
            try:
                code = helper.wait(0.5)
                break
            except subprocess.TimeoutExpired:
                waited = time.monotonic() - start
                if waited >= timeout:
                    return "it didn't answer within %d seconds" % timeout
                if progress is not None and progress(int(waited), int(timeout)) is False:
                    raise Canceled()
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait()
    if code == 0:
        _OK.add(name)
        return None
    if code == 2:
        return "it isn't installed on this computer"
    return "its driver stopped working when PlanetPDF asked for its settings"


def probe_main(argv: list[str]) -> int:
    """The helper process: open the printer's driver the way printing does."""
    name = argv[argv.index(PROBE_FLAG) + 1] if argv.index(PROBE_FLAG) + 1 < len(argv) else ""
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    from PySide6.QtWidgets import QApplication

    app = QApplication([argv[0]])  # noqa: F841 (QPrinter needs one)
    info = QPrinterInfo.printerInfo(name)
    if info.isNull():
        return 2
    printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    printer.pageLayout().paintRectPixels(printer.resolution())
    info.supportedPageSizes()
    info.defaultPageSize()
    return 0 if printer.isValid() else 3
