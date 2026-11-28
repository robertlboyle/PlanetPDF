"""Where a file came from, as the operating system marks it (Windows' "mark of the web", the Mac's
quarantine flag), carried over to the files PlanetPDF takes out of it.

Security review 2026-11-27: a file attached to a PDF that came from the internet was saved from the
Attachments panel with no mark at all, so Windows opened even a ``.cmd`` without its usual warning.
A saved attachment now gets the PDF's own mark, as Windows' own Extract All does for a zip's files.
A PDF without a mark (the user's own) gives none.

Per system, behind two functions (the cross-platform rule: nothing else knows how it's stored).
Pure Python (no Qt).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_QUARANTINE = "com.apple.quarantine"


def mark_of(path: Path) -> str | None:
    """``path``'s mark as the system keeps it (the text of Windows' Zone.Identifier stream, the
    Mac's quarantine value), or None when it has none."""
    path = Path(path)
    if sys.platform == "win32":
        try:
            with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="replace") as stream:
                text = stream.read()
        except OSError:
            return None
        return text if re.search(r"ZoneId\s*=\s*\d+", text) else None
    if sys.platform == "darwin":
        try:
            done = subprocess.run(["/usr/bin/xattr", "-p", _QUARANTINE, str(path)], capture_output=True, text=True,
                                  timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return (done.stdout.strip() or None) if done.returncode == 0 else None
    return None


def carry_mark(source: Path | None, target: Path) -> bool:
    """Gives ``target`` ``source``'s mark (nothing when ``source`` has none). True when marked."""
    mark = mark_of(source) if source is not None else None
    if not mark:
        return False
    if sys.platform == "win32":
        try:
            with open(f"{Path(target)}:Zone.Identifier", "w", encoding="utf-8", newline="\r\n") as stream:
                stream.write(mark if mark.endswith("\n") else mark + "\n")
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        try:
            return subprocess.run(["/usr/bin/xattr", "-w", _QUARANTINE, mark, str(target)], capture_output=True,
                                  timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False
