"""The fonts installed on Windows, from the registry (fast: no font file is opened).

Machine-wide fonts are listed under HKLM, fonts a user installed only for themselves
under HKCU (with full paths). An entry reads like "Arial Bold Italic (TrueType)" =
"arialbi.ttf". Bitmap fonts are left out; font collections (.ttc / .otc: "Cambria & Cambria
Math (TrueType)" = "cambria.ttc") are listed by ``collection_files`` and read face by face in
``fonts`` (their entry names don't say which face is which).
"""

from __future__ import annotations

import os
import re
import winreg
from pathlib import Path

_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
_STYLES = [(" bold italic", True, True), (" bold oblique", True, True), (" italic", False, True),
           (" oblique", False, True), (" bold", True, False), (" regular", False, False)]


def split_name(entry: str) -> tuple[str, bool, bool]:
    """"Arial Bold Italic (TrueType)" -> ("Arial", True, True)."""
    name = re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", entry, flags=re.IGNORECASE).strip()
    lower = name.lower()
    for suffix, bold, italic in _STYLES:
        if lower.endswith(suffix):
            return name[: -len(suffix)].strip(), bold, italic
    return name, False, False


def _entries():
    """(registry entry name, font file) for every installed font, machine-wide then the user's."""
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, _KEY)
        except OSError:
            continue
        with key:
            n = 0
            while True:
                try:
                    entry, value, _kind = winreg.EnumValue(key, n)
                except OSError:
                    break
                n += 1
                if isinstance(value, str):
                    yield entry, Path(value) if os.path.isabs(value) else fonts_dir / value


def installed_fonts() -> dict[str, dict[tuple[bool, bool], Path]]:
    found: dict[str, dict[tuple[bool, bool], Path]] = {}
    for entry, path in _entries():
        if "&" in entry or path.suffix.lower() not in (".ttf", ".otf") or not path.is_file():
            continue
        family, bold, italic = split_name(entry)
        if family:
            found.setdefault(family, {}).setdefault((bold, italic), path)
    return found


def collection_files() -> list[Path]:
    """Every installed font collection (each file once)."""
    seen: dict[str, Path] = {}
    for _entry, path in _entries():
        if path.suffix.lower() in (".ttc", ".otc") and path.is_file():
            seen.setdefault(str(path).lower(), path)
    return list(seen.values())
