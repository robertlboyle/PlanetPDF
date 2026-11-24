"""Folders the system or another program empties without asking (user 2026-11-24: "I opened a file
from a temporary location and when the temp location was closed, it deleted the file I had been
working on. Detect saving into a ~ temp file and don't allow it. Force a save outside the temp
folder").

Files opened from a zip in Windows Explorer (``%TEMP%\\<id>_<name>.zip.<n>\\``), e-mail attachments
(Outlook's ``Content.Outlook`` folder, the new Outlook's ``Olk``), browser "Open" downloads and the
like live in such folders and vanish when the zip window or the program closes. PlanetPDF won't
save a document there: Save asks where to keep it instead, and Save As refuses those folders.

Pure Python (no Qt). ``PLANETPDF_ALLOW_TEMP_SAVES=1`` lets tests save in pytest's temporary folders.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ALLOW_ENV = "PLANETPDF_ALLOW_TEMP_SAVES"

# (part of the path, what to call it): folders other programs empty, inside the user's profile
_KNOWN = (
    (("inetcache", "content.outlook"), "Outlook's folder for opened attachments"),
    (("temporary internet files", "content.outlook"), "Outlook's folder for opened attachments"),
    (("microsoft", "olk"), "Outlook's folder for opened attachments"),
    (("microsoft", "windows", "inetcache"), "the web browser's temporary files"),
)


def _norm(path: Path | str) -> str:
    try:
        text = os.path.realpath(path)  # (Windows: also spells out short names like ROBERT~1)
    except (OSError, ValueError):
        text = os.path.abspath(path)
    return os.path.normcase(text).rstrip("\\/")


def temp_roots() -> list[str]:
    """The system's temporary folders, spelled out (normcase, no trailing separator)."""
    roots = {tempfile.gettempdir()}
    for name in ("TEMP", "TMP", "TMPDIR"):
        if os.environ.get(name):
            roots.add(os.environ[name])
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.add(os.path.join(local, "Temp"))
        windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if windir:
            roots.add(os.path.join(windir, "Temp"))
    else:
        roots.update({"/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp"})
        if sys.platform == "darwin":
            roots.add("/private/var/folders")  # (each user's $TMPDIR lives under it)
    return sorted({_norm(r) for r in roots if r})


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def temporary_reason(path: Path | str) -> str | None:
    """None when ``path`` is somewhere lasting; else what kind of temporary folder it is in,
    for the message ("a temporary folder", "Outlook's folder for opened attachments")."""
    if os.environ.get(ALLOW_ENV) == "1":
        return None
    text = _norm(path)
    parts = [p.lower() for p in Path(text).parts]
    for needle, what in _KNOWN:
        n = len(needle)
        if any(tuple(parts[i:i + n]) == needle for i in range(len(parts) - n + 1)):
            return what
    for root in temp_roots():
        if _inside(text, root):
            rest = Path(text[len(root):].lstrip("\\/")).parts
            if rest and ".zip." in rest[0].lower():
                return "the temporary folder Windows uses while a zip file is open"
            return "a temporary folder"
    return None


def is_temporary(path: Path | str) -> bool:
    return temporary_reason(path) is not None


def lasting_folder(*candidates: Path | str | None) -> Path:
    """The first candidate folder that exists and isn't temporary, else Documents, else home."""
    home = Path.home()
    for folder in (*candidates, home / "Documents", home):
        if folder and Path(folder).is_dir() and not is_temporary(folder):
            return Path(folder)
    return home
