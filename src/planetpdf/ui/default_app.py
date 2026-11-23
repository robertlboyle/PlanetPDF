"""Making PlanetPDF the app that opens PDF files (user 2026-10-10).

* **Mac app:** asked once, on the first start of the installed app ("Make PlanetPDF the
  default app for PDF files?"); a yes sets it for this user through Launch Services. A
  Mac installer runs as administrator and can't choose for each user, so the app asks.
* **Windows:** the installer asks (``packaging/PlanetPDF.iss``) and then runs
  ``PlanetPDF.exe --choose-pdf-app`` as the user: Windows' own "How do you want to open .pdf
  files?" chooser (``windows_shell.choose_pdf_app``), where they pick PlanetPDF (user 2026-10-18:
  the Default apps page it used to open had "nothing to click on"). Preferences has the same.
* From source (``python -m planetpdf``) neither is offered: it would make Python the app.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from planetpdf import APP_NAME

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

WINDOWS_SETTINGS = f"ms-settings:defaultapps?registeredAppMachine={APP_NAME}"


def available(platform: str | None = None, frozen: bool | None = None) -> bool:
    """Whether this copy can be made the PDF app (the installed program, on Mac or Windows)."""
    platform = platform or sys.platform
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    return frozen and platform in ("darwin", "win32")


def is_default() -> bool | None:
    """Whether PlanetPDF opens this user's PDF files now (None where it can't be told)."""
    if sys.platform == "win32":
        from planetpdf.ui import windows_shell

        return windows_shell.pdf_app_is_planetpdf()
    if sys.platform != "darwin":
        return None
    from planetpdf.ui import macos_shell

    mine = macos_shell.bundle_identifier()
    current = macos_shell.default_app()
    return bool(mine and current and current.lower() == mine.lower())


def make_default(window: MainWindow, *, tell: bool = True) -> bool:
    """Mac: set it now; Windows: Windows' chooser, where the user confirms (or nothing at all when
    PlanetPDF already opens PDF files: the chooser would offer only "Just once", and a message
    saying so was "annoying to see every upgrade. Just stay silent", user 2026-11-22)."""
    if sys.platform == "win32":
        from planetpdf.ui import windows_shell

        if windows_shell.pdf_app_is_planetpdf():
            return True
        return windows_shell.choose_pdf_app(int(window.winId())) or QDesktopServices.openUrl(QUrl(WINDOWS_SETTINGS))
    from planetpdf.ui import macos_shell

    mine = macos_shell.bundle_identifier()
    ok = bool(mine) and macos_shell.set_default_app(mine)
    if tell:
        if ok:
            window.statusBar().showMessage(f"{APP_NAME} now opens PDF files.", 6000)
        else:
            QMessageBox.warning(window, "Default App", f"macOS didn't make {APP_NAME} the default app for PDF "
                                "files. You can choose it in Finder: select a PDF, choose File ▸ Get Info, "
                                f"pick {APP_NAME} under “Open with” and click Change All.")
    return ok


def offer_at_first_start(window: MainWindow) -> None:
    """Mac app: ask once whether PlanetPDF should open PDF files."""
    settings = window.settings
    if sys.platform != "darwin" or not available() or settings.asked_default_pdf_app:
        return
    settings.asked_default_pdf_app = True
    window.save_settings_now()
    if is_default():
        return
    answer = QMessageBox.question(
        window, "Default App for PDF Files",
        f"Make {APP_NAME} the default app for opening PDF files?\n\nYou can change this later in "
        f"{APP_NAME} ▸ Preferences ▸ General, or in Finder with File ▸ Get Info.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
    if answer == QMessageBox.StandardButton.Yes:
        make_default(window)
