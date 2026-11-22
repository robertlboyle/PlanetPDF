"""Unexpected errors: logged to <profile>/logs/planetpdf.log and shown to the user, so a
failure is never silent (user report 2026-10-01: Add Image did nothing on one file).

``install_excepthook`` catches errors that escape a Qt slot (Qt would otherwise just print
them), shows a message and logs the traceback.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_NAME = "planetpdf.log"
MAX_BYTES = 1_000_000


def log_path(profile_root: Path) -> Path:
    return Path(profile_root) / "logs" / LOG_NAME


def log_exception(profile_root: Path, what: str, exc: BaseException) -> Path:
    path = log_path(profile_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:  # keep it small: start over
            path.replace(path.with_suffix(".old.log"))
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {what}\n{details}")
    except OSError:
        pass
    return path


def _active_main_window():
    from PySide6.QtWidgets import QApplication

    from planetpdf.ui.main_window import MainWindow

    active = QApplication.activeWindow()
    while active is not None and not isinstance(active, MainWindow):
        active = active.parentWidget()
    return active


def install_excepthook(window) -> None:
    """Errors escaping a slot: show and log them instead of failing silently."""
    previous = sys.excepthook

    def hook(kind, exc, tb):
        if issubclass(kind, KeyboardInterrupt):
            previous(kind, exc, tb)
            return
        exc.__traceback__ = tb
        target = _active_main_window() or window  # (several windows: the one in use)
        try:
            target.select_tool()  # put any half-used tool away (no stuck placement ghost)
        except Exception:
            pass
        try:
            target.report_error("That command", exc)
        except Exception:  # never let the error report itself crash the app
            previous(kind, exc, tb)

    sys.excepthook = hook
