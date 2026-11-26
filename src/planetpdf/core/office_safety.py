"""Office files are turned into PDFs from a copy that can't reach the network (security review
2026-11-25: a .docx naming a template on a server made Word, driven by PlanetPDF, fetch it and
sign in to that server; Word itself would have opened the downloaded file in Protected View).

- Word, Excel, and PowerPoint files in the Office Open XML formats (.docx, .xlsx, .pptx and their
  kin) are copied with every link to an outside file pointed at a place that doesn't exist on this
  computer: attached templates, linked pictures, linked objects, external data. Hyperlinks stay:
  nothing opens them while converting.
- The older formats (.doc, .xls, .ppt, .rtf …) can't be cleaned that way. When one came from the
  internet or an e-mail (Windows' "mark of the web", the Mac's quarantine flag), PlanetPDF refuses
  it and says how to convert it safely; a file of the user's own is converted as it is.

Pure Python (no Qt).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from planetpdf.core.document import PdfError

OOXML = {".docx", ".docm", ".dotx", ".dotm", ".xlsx", ".xlsm", ".xltx", ".xltm",
         ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm"}
NEWER = {".doc": ".docx", ".rtf": ".docx", ".wpd": ".docx", ".odt": ".docx", ".xls": ".xlsx", ".ods": ".xlsx",
         ".csv": ".xlsx", ".ppt": ".pptx", ".odp": ".pptx"}
REMOVED_TARGET = "file:///C:/PlanetPDF-removed-link" if sys.platform == "win32" else "file:///PlanetPDF-removed-link"
_RELATIONSHIP = re.compile(r"<Relationship\b[^>]*>", re.IGNORECASE)
_TARGET = re.compile(r"""(\sTarget=)(["']).*?\2""", re.IGNORECASE | re.DOTALL)


class OfficeRefused(PdfError):
    """An Office file PlanetPDF won't hand to Office (it could reach the network)."""


def downloaded(path: Path) -> bool:
    """Whether ``path`` came from the internet or an e-mail, as the operating system marked it."""
    path = Path(path)
    if sys.platform == "win32":
        try:
            with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="replace") as marks:
                found = re.search(r"ZoneId\s*=\s*(\d+)", marks.read())
            return bool(found and int(found.group(1)) >= 3)  # (3 = internet, 4 = restricted)
        except OSError:
            return False
    if sys.platform == "darwin":
        try:
            return subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)],
                                  capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def unlinked_rels(text: str) -> tuple[str, int]:
    """A ``.rels`` part with every outside link (``TargetMode="External"``) but hyperlinks pointed
    nowhere. Returns (the part, how many were changed)."""
    count = 0

    def fix(match: re.Match) -> str:
        nonlocal count
        tag = match.group(0)
        if not re.search(r"""TargetMode=["']External["']""", tag, re.IGNORECASE):
            return tag
        kind = re.search(r"""\sType=["']([^"']*)["']""", tag, re.IGNORECASE)
        if kind and kind.group(1).lower().endswith("/hyperlink"):
            return tag
        count += 1
        return _TARGET.sub(lambda t: f"{t.group(1)}{t.group(2)}{REMOVED_TARGET}{t.group(2)}", tag, count=1)

    return _RELATIONSHIP.sub(fix, text), count


def _unlinked_copy(source: Path, target: Path) -> int:
    changed = 0
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
            for info in original.infolist():
                data = original.read(info)
                if info.filename.lower().endswith(".rels"):
                    text, count = unlinked_rels(data.decode("utf-8", errors="replace"))
                    if count:
                        data, changed = text.encode("utf-8"), changed + count
                copy.writestr(info, data)
    except zipfile.BadZipFile as exc:
        raise PdfError(f"{source.name} isn't a readable Office file ({exc}).") from exc
    return changed


def safe_copy(source: Path, folder: Path) -> Path:
    """The copy of ``source`` in ``folder`` (a private one) that Office is given."""
    source = Path(source)
    target = Path(folder) / source.name
    suffix = source.suffix.lower()
    if suffix in OOXML:
        _unlinked_copy(source, target)
        return target
    if downloaded(source):
        newer = NEWER.get(suffix, "a newer format")
        raise OfficeRefused(
            f"{source.name} came from the internet or an e-mail, and in this older format PlanetPDF can't take out "
            f"its links to other places before Office opens it. Open it in Office yourself (it opens in Protected "
            f"View), save it as {newer}, and make the PDF from that copy.")
    shutil.copyfile(source, target)
    return target
