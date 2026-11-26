"""A printout from the PlanetPDF printer arrives (user 2026-10-18: "Unless there is a default
output folder set in the settings or set via an environment variable, it should prompt for the
output folder and name … Default to using the last output folder used"). core/printouts.py has
the rules; here: the Save dialog, moving the file out of the inbox, opening it."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox

from planetpdf.core import printouts as P
from planetpdf.ui import paths

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def inbox() -> Path:
    return paths.printouts_dir()


def is_printout(path: Path) -> bool:
    return P.is_printout(path, inbox())


def ask_open(window: MainWindow, document: str) -> tuple[bool, bool]:
    """Before the Save dialog (user 2026-10-18: "Ask before opening the print dialog and have a box
    that asks to save that setting. It is a setting that can be changed later in the preferences"):
    (open it?, remember the answer?). Tests replace this."""
    box = QMessageBox(QMessageBox.Icon.Question, "Print to PlanetPDF",
                      f"Open “{document}” in PlanetPDF after it's saved?", parent=window)
    yes = box.addButton("Open It", QMessageBox.ButtonRole.YesRole)
    box.addButton("Don't Open It", QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes)
    remember = QCheckBox("Remember my answer (Preferences ▸ Documents can change it)", box)
    box.setCheckBox(remember)
    if sys.platform == "win32" and QGuiApplication.platformName() == "windows":  # (the question in front too)
        from planetpdf.ui import windows_shell

        QTimer.singleShot(0, lambda: windows_shell.force_foreground(int(box.winId())))
    box.exec()
    return box.clickedButton() is yes, remember.isChecked()


def ask_where(window: MainWindow, suggested: Path) -> Path | None:
    """The system's own Save dialog (Windows' and the Mac's: their places, long names). None =
    canceled. Tests replace this."""
    from planetpdf.ui.save_path import choose_save_path

    return choose_save_path(window, "Print to PlanetPDF", suggested)  # (never a temporary folder)


def receive(window: MainWindow, path: Path) -> tuple[Path, bool] | None:
    """Saves the printout where it belongs. Returns (where, open it?); None: canceled or failed."""
    s = window.settings
    path = Path(path)
    document = path.stem
    # In front before asking anything (user 2026-11-22: the question "is hidden in the background
    # and I was waiting for something to happen"): the print came from another program, so Windows
    # wouldn't otherwise let PlanetPDF come forward.
    window.bring_forward(force=True)
    name = P.file_name(P.template(s.print_name_template), document)
    folder = P.default_folder(s.print_folder)
    open_after = s.print_open_after
    if s.print_open_ask:  # (asked first, then where)
        open_after, remember = ask_open(window, document)
        if remember:
            s.print_open_ask, s.print_open_after = False, open_after
            window.save_settings_now()
    if folder is not None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = P.unique_path(folder, name)
        except OSError as exc:
            window.show_message("PlanetPDF Printer", f"The printout couldn't go to {folder}:\n\n{exc}\n\n"
                                                     "Choose where to save it instead.")
            folder = None
    if folder is None:
        start = Path(s.print_last_folder) if s.print_last_folder and Path(s.print_last_folder).is_dir() \
            else paths.documents_dir()
        target = ask_where(window, start / f"{name}.pdf")
        if target is None:
            _discard(path)
            window.statusBar().showMessage(f"The printout “{document}” wasn't saved.", 6000)
            return None
        s.print_last_folder = str(target.parent)
        window.save_settings_now()
    from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

    tmp = temp_path_beside(target)
    try:
        # A copy beside the target, then put in its place in one step: a file being replaced (the
        # Save dialog asked) is never gone before the printout is there (security review 2026-11-25)
        shutil.copyfile(path, tmp)
        replace_with_retry(tmp, target)
    except OSError as exc:
        remove_quietly(tmp)
        window.show_message("PlanetPDF Printer", f"The printout couldn't be saved as {target}:\n\n{exc}")
        return None
    remove_quietly(path)
    P.done_with(path, inbox())
    if not open_after:
        window.statusBar().showMessage(f"Printout saved: {target}", 8000)
    return target, open_after


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
    P.done_with(path, inbox())


def open_waiting(window: MainWindow) -> None:
    """At start: printouts that arrived while PlanetPDF wasn't running."""
    waiting = P.waiting(inbox())
    if waiting:
        window.open_paths(waiting)
