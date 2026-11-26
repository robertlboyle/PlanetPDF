"""Where a new file is saved: every Save dialog goes through ``choose_save_path`` (security review
2026-11-25). It starts in a folder that lasts, never a temporary one (an opened zip file's,
Outlook's attachments, the temporary folder: they're emptied without asking), and refuses one
chosen there. Save / Save As of the document itself check the same (``MainWindow.save``)."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

PDF_FILTER = "PDF files (*.pdf)"

_DEVICES = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def plain_file_name(name: object, default: str = "attachment") -> str:
    """A file name from a document (an attachment's), made safe to offer in a Save dialog: its last
    part only (no folders, no \\\\server\\share: security review 2026-11-25, the dialog opened on the
    share a PDF named, and Windows signed in to it), no drive or stream (":"), no characters Windows
    refuses, not a device name (CON, NUL, COM1…)."""
    text = re.split(r"[\\/]", str(name or ""))[-1].replace(":", "_")
    text = "".join(ch for ch in text if ch >= " " and ch not in '<>"|?*').strip().strip(".")
    if text.split(".")[0].strip().lower() in _DEVICES:
        text = "_" + text
    return text or default


def refuse_temporary(parent: QWidget | None, target: Path) -> bool:
    """True (after saying so) when ``target`` is in a folder that gets emptied by itself."""
    from planetpdf.core.temp_folders import temporary_reason

    reason = temporary_reason(target)
    if reason is None:
        return False
    QMessageBox.warning(
        parent, "Temporary Folder",
        f"That folder is {reason}. Files saved there are deleted without asking, so "
        "PlanetPDF won't save your document there.\n\nPlease choose another folder, such as Documents.")
    return True


def safe_start(suggested: Path | str, last_directory: Path | str | None = None) -> Path:
    """``suggested``, moved to a lasting folder (the one used last, else Documents) when it's in a
    temporary one."""
    from planetpdf.core.temp_folders import lasting_folder, temporary_reason

    start = Path(suggested)
    if temporary_reason(start) is not None:
        start = lasting_folder(last_directory) / start.name
    return start


def choose_save_path(parent: QWidget | None, title: str, suggested: Path | str, file_filter: str = PDF_FILTER,
                     *, suffix: str | None = ".pdf", last_directory: Path | str | None = None) -> Path | None:
    """The Save dialog: a path the user chose (``suffix`` added when missing), never in a
    temporary folder; None when canceled."""
    from planetpdf.core.temp_folders import lasting_folder

    if last_directory is None:
        settings = getattr(parent, "settings", None)
        last_directory = getattr(settings, "last_directory", None)
    start = safe_start(suggested, last_directory)
    while True:
        path, _kind = QFileDialog.getSaveFileName(parent, title, str(start), file_filter)
        if not path:
            return None
        target = Path(path)
        if suffix and target.suffix.lower() != suffix.lower():
            target = target.with_name(target.name + suffix)
        if not refuse_temporary(parent, target):
            return target
        start = lasting_folder(last_directory) / target.name
