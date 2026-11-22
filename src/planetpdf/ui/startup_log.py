"""Startup diagnostics (user report 2026-10-01: "the UI never appears").

Every start writes ``<profile>/logs/startup.log`` (overwritten each time) and echoes the
same lines to the console when there is one:

* each startup step with the time since launch (imports, profile, window, file, …);
* versions, platform, the screens, the saved window position, and where the window
  actually ended up (on which screen, visible or not, minimized, any dialog on top);
* a watchdog: while startup hasn't finished, a stack trace of every thread is written
  every ``WATCHDOG_SECONDS``, so a hang shows exactly where it is stuck.

Until the profile folder is known, lines are kept in memory and written once it is.
"""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_NAME = "startup.log"
WATCHDOG_SECONDS = 15

_start = time.perf_counter()
previous_start_failed = False  # set by attach(): the last start never reached the event loop
_lines: list[str] = []
_file = None


def log(message: str) -> None:
    line = f"[{time.perf_counter() - _start:7.2f}s] {message}"
    if _file is not None:
        try:
            _file.write(line + "\n")
            _file.flush()
        except (OSError, ValueError):
            pass
    else:
        _lines.append(line)
    stream = sys.stderr or sys.__stderr__
    if stream is not None:  # (none under pythonw / a windowed exe)
        try:
            print("PlanetPDF " + line, file=stream, flush=True)
        except (OSError, ValueError):
            pass


def begin() -> None:
    log(f"PlanetPDF starting {datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"Python {sys.version.split()[0]} on {platform.platform()}; executable {sys.executable}")
    log(f"Arguments: {sys.argv}")
    for name in ("QT_QPA_PLATFORM", "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_QPA_FONTDIR", "PLANETPDF_PROFILE_DIR"):
        if os.environ.get(name):
            log(f"Environment {name}={os.environ[name]}")


def attach(profile_root: Path) -> Path | None:
    """Start writing to ``<profile>/logs/startup.log`` (with the lines so far) and arm
    the watchdog."""
    global _file, previous_start_failed
    path = Path(profile_root) / "logs" / LOG_NAME
    try:  # the last start never got going (crashed or hung)? Then play it safe this time
        previous = path.read_text(encoding="utf-8", errors="replace")
        previous_start_failed = "Startup finished" not in previous and "PlanetPDF starting" in previous
    except OSError:
        previous_start_failed = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _file = path.open("w", encoding="utf-8")
        _file.write("\n".join(_lines) + ("\n" if _lines else ""))
        _file.flush()
        _lines.clear()
        faulthandler.enable(_file, all_threads=True)  # a hard crash also lands in the log
        faulthandler.dump_traceback_later(WATCHDOG_SECONDS, repeat=True, file=_file)
    except OSError as exc:
        log(f"Couldn't write {path}: {exc}")
        return None
    log(f"Startup log: {path}")
    return path


def finished() -> None:
    """Startup is over (the event loop is running): stop the watchdog."""
    faulthandler.cancel_dump_traceback_later()
    log("Startup finished; the event loop is running.")


def describe_screens(app) -> None:
    for n, screen in enumerate(app.screens()):
        g, a = screen.geometry(), screen.availableGeometry()
        log(f"Screen {n} {screen.name()!r}: {g.width()}x{g.height()} at ({g.x()},{g.y()}), "
            f"available {a.width()}x{a.height()} at ({a.x()},{a.y()}), scale {screen.devicePixelRatio()}")
    primary = app.primaryScreen()
    log(f"Primary screen: {primary.name() if primary else 'none'}; platform plugin: {app.platformName()}")


def describe_window(window, label: str = "Window") -> None:
    from PySide6.QtWidgets import QApplication

    frame = window.frameGeometry()
    screen = QApplication.screenAt(frame.center())
    state = []
    if window.isMinimized():
        state.append("minimized")
    if window.isMaximized():
        state.append("maximized")
    if window.isFullScreen():
        state.append("full screen")
    log(f"{label}: visible={window.isVisible()} {' '.join(state)} frame {frame.width()}x{frame.height()} at "
        f"({frame.x()},{frame.y()}); on screen: {screen.name() if screen else 'NONE (off every screen)'}; "
        f"opacity {window.windowOpacity()}")
    modal = QApplication.activeModalWidget()
    if modal is not None:
        g = modal.frameGeometry()
        log(f"A dialog is open on top: {modal.windowTitle()!r} ({type(modal).__name__}) at ({g.x()},{g.y()}) "
            f"{g.width()}x{g.height()}, visible={modal.isVisible()}")
