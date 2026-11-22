"""The Mac's PlanetPDF printer (user 2026-10-18): "Open in PlanetPDF" in every print dialog's
PDF menu. macOS lists what's in ~/Library/PDF Services there and runs a script with three
arguments: the document's title, the print options, the PDF's path. Ours puts the PDF in
PlanetPDF's inbox for printouts (a folder per printout, the file named after the document) and
opens it with PlanetPDF, which asks where to save it (or uses the folder set in Preferences),
the same as the Windows printer (printer/, ui/printouts.py).

No Qt. The app writes the script when it starts (``install``), so it is always this version's.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

SERVICE_NAME = "Open in PlanetPDF"
# Qt's AppLocalDataLocation on a Mac + "printouts" (ui/paths.printouts_dir)
INBOX = "Library/Application Support/Planet Networks/PlanetPDF/printouts"

SCRIPT = r"""#!/bin/sh
# "Open in PlanetPDF" in the print dialog's PDF menu, made by PlanetPDF (it is rewritten when
# PlanetPDF starts). macOS passes: the document's title, the print options, the PDF's path.
title=$1
source=$3
[ -f "$source" ] || exit 1
folder="$HOME/__INBOX__/job-$$-$(date +%s)"
mkdir -p "$folder" || exit 1
# A file name from the title: no / : or control characters, no "Microsoft Word - " in front,
# the document's own extension dropped, at most 120 characters.
name=$(printf '%s' "$title" | tr -d '\000-\037' | tr '/:\\"<>|?*' '         ' |
    sed -E -e 's/^(Microsoft (Word|Excel|PowerPoint|Outlook) - )//' \
        -e 's/\.(docx|docm|doc|xlsx|xlsm|xls|pptx|ppt|txt|rtf|html?|pdf|pages|numbers|key|csv|odt)$//' \
        -e 's/  +/ /g' -e 's/^[ .]+//' -e 's/[ .]+$//' | cut -c1-120)
[ -n "$name" ] || name="Printout"
target="$folder/$name.pdf"
cp "$source" "$target" || exit 1
open -b "__BUNDLE__" "$target"
"""


def service_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "PDF Services" / SERVICE_NAME


def script(bundle_id: str) -> str:
    return SCRIPT.replace("__INBOX__", INBOX).replace("__BUNDLE__", bundle_id)


def install(bundle_id: str, home: Path | None = None) -> Path:
    """Puts (or updates) the PDF Service for this user; returns where it is."""
    path = service_path(home)
    text = script(bundle_id)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    mode = path.stat().st_mode
    wanted = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRUSR
    if mode != wanted:
        os.chmod(path, wanted)
    return path


def remove(home: Path | None = None) -> bool:
    path = service_path(home)
    if path.exists():
        path.unlink()
        return True
    return False
