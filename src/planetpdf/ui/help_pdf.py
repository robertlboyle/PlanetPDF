"""The manual as a PDF (user 2026-10-18: "package a PDF version of the help file … put it in the
install folder … update these files as each new build is built"). Every build writes
``PlanetPDF Manual.pdf`` beside the launcher (``scripts/make_manual.py``); without one (from source,
or a Mac app) it's made once per version into the local app data folder."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from planetpdf import __version__
from planetpdf.core import help as H

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def installed_pdf() -> Path | None:
    """``<install folder>/PlanetPDF Manual.pdf`` (the launcher's folder: versions/<v>/PlanetPDF.exe
    is two levels down)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for folder in (exe.parent.parent.parent, exe.parent):
        candidate = folder / H.PDF_NAME
        if candidate.is_file():
            return candidate
    return None


def generated_pages(window: MainWindow) -> dict[str, str]:
    from planetpdf.ui.help_window import GENERATED

    return {name: make(window) for name, make in GENERATED.items()}


def manual_pdf(window: MainWindow) -> Path | None:
    found = installed_pdf()
    if found is not None:
        return found
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from planetpdf.ui import paths

    target = paths.models_dir().parent / f"PlanetPDF Manual {__version__}.pdf"
    if target.is_file():
        return target
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return H.write_pdf(target, generated_pages(window), version=__version__)
    except Exception:  # (shown as "couldn't be made" by the caller)
        import logging

        logging.getLogger(__name__).exception("The manual's PDF couldn't be made")
        return None
    finally:
        QApplication.restoreOverrideCursor()
