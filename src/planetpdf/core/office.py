"""Microsoft Office, wherever PlanetPDF runs (2026-10-14): Word 97-2003 export and Word / Excel /
PowerPoint files to PDF. Windows drives Office through COM (``core/windows_office.py``), the Mac
through AppleScript (``core/mac_office.py``); elsewhere these features say Office is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from planetpdf.core.document import PdfError

WORD_97_2003 = 0  # (Word's own number for the format, the same on Windows and the Mac)
SUFFIXES = {
    "word": {".doc", ".docx", ".docm", ".rtf", ".odt", ".wpd"},
    "excel": {".xls", ".xlsx", ".xlsm", ".ods", ".csv"},
    "powerpoint": {".ppt", ".pptx", ".pptm", ".odp"},
}


class OfficeBlocked(PdfError):
    """Office refuses the format: its File Block settings (often a company policy)."""


def _backend():
    if sys.platform == "win32":
        from planetpdf.core import windows_office

        return windows_office
    if sys.platform == "darwin":
        from planetpdf.core import mac_office

        return mac_office
    return None


def app_for(path: Path) -> str | None:
    suffix = Path(path).suffix.lower()
    return next((app for app, suffixes in SUFFIXES.items() if suffix in suffixes), None)


def available(app: str) -> bool:
    """Is Word / Excel / PowerPoint installed here?"""
    backend = _backend()
    return backend is not None and backend.available(app)


def _turned_off() -> str:
    """Why PlanetPDF doesn't start Office on this computer ("" = it may): an administrator's
    policy (``core/policy.py``). Said when a conversion is asked for, so the reason is the one shown."""
    from planetpdf.core import policy

    return policy.reason("office_conversion")


def to_pdf(source: Path, target: Path) -> None:
    """A Word, Excel or PowerPoint file as PDF (Office's own "Save as PDF"), made from a copy
    whose links to outside files are taken out (``office_safety``: security review 2026-11-25)."""
    from planetpdf.core.document import private_folder
    from planetpdf.core.office_safety import safe_copy

    if _turned_off():
        raise PdfError(_turned_off())
    backend = _backend()
    if backend is None:
        raise PdfError("Turning Office files into PDFs needs Microsoft Office (Windows or Mac).")
    with private_folder() as folder:
        backend.to_pdf(safe_copy(Path(source), folder), target)


def word_save_as(source: Path, target: Path, file_format: int = WORD_97_2003) -> None:
    if _turned_off():
        raise PdfError(_turned_off())
    backend = _backend()
    if backend is None:
        raise PdfError("This needs Microsoft Word (Windows or Mac).")
    backend.word_save_as(source, target, file_format)
