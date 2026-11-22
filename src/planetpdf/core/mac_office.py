"""Microsoft Office for Mac, when installed (2026-10-14): Word 97-2003 export and Word / Excel /
PowerPoint files to PDF, through AppleScript (``osascript``), as ``windows_office`` does through COM.

* **macOS asks once** whether PlanetPDF may control each Office app (System Settings ▸ Privacy &
  Security ▸ Automation); a "no" is reported with where to change it.
* **Office for Mac is sandboxed**: for a file outside its own folders each app shows "Grant File
  Access", a folder picker (it stopped the first try: Word waited on it). So every file goes
  through Office's shared folder (``WORK``, in its group container), which Word, Excel and
  PowerPoint can always read and write; macOS asks the user once to let PlanetPDF use another
  app's data ("Allow"), and that's all.
* Office is quit afterwards only when PlanetPDF started it (never the user's open copy and their
  work). Unlike on Windows, it shows while it works: Office for Mac has no hidden mode.
* **Word for Mac can't close a document by script** (16.113, 2026-10-14: ``close`` is refused for
  documents and windows in every form, even the raw event). When PlanetPDF started Word, quitting
  it closes the copy; when Word was already open, the copy stays open there, named
  "<name> (PlanetPDF copy)" so the user knows to close it. Excel and PowerPoint close theirs.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.office import WORD_97_2003, OfficeBlocked, app_for

APPS = {"word": "Microsoft Word", "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint"}
WORK = Path.home() / "Library" / "Group Containers" / "UBF8T346G9.Office" / "PlanetPDF"  # (Office's shared folder)
TIMEOUT = 600  # seconds for one file (a long document, Office starting up)
NOT_ALLOWED = "-1743"  # errAEEventNotPermitted: the user said no to controlling the app


def _app_path(app: str) -> Path | None:
    for base in (Path("/Applications"), Path.home() / "Applications"):
        path = base / f"{APPS[app]}.app"
        if path.is_dir():
            return path
    return None


def available(app: str) -> bool:
    return app in APPS and _app_path(app) is not None


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _running(app: str) -> bool:
    return subprocess.run(["pgrep", "-x", APPS[app]], capture_output=True).returncode == 0


def _run(app: str, script: str) -> None:
    was_running = _running(app)
    try:
        done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise PdfError(f"{APPS[app]} didn't finish within {TIMEOUT // 60} minutes.") from exc
    finally:
        if not was_running:
            subprocess.run(["osascript", "-e", f'tell application "{APPS[app]}" to quit saving no'],
                           capture_output=True, timeout=60)
    if done.returncode != 0:
        message = done.stderr.strip()
        if NOT_ALLOWED in message:
            raise PdfError(f"macOS didn't let PlanetPDF use {APPS[app]}. Allow it in System Settings ▸ "
                           f"Privacy & Security ▸ Automation ▸ PlanetPDF ▸ {APPS[app]}, then try again.")
        raise PdfError(f"{APPS[app]} couldn't convert the file: {message.split('execution error: ')[-1]}")


def _workspace() -> Path:
    folder = WORK / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _script(app: str, source: Path, out: Path, kind: str) -> str:
    """AppleScript that opens ``source`` (read-only where Office allows), saves ``out``, closes."""
    src, dst = _quoted(str(source)), _quoted(str(out))
    if app == "word":
        fmt = "format PDF" if kind == "pdf" else "format document97"
        return f'''with timeout of {TIMEOUT} seconds
tell application "Microsoft Word"
    open file name {src} read only true confirm conversions false add to recent files false
    set docName to name of active document  -- (Word's open returns nothing; "active document"
    set theDoc to document docName          -- is re-read each time, so hold the document by name)
    save as theDoc file name ((POSIX file {dst}) as text) file format {fmt}
    -- (no close: Word 16.113 for Mac can't close a document by script; see _convert)
end tell
end timeout'''
    if app == "excel":
        return f'''with timeout of {TIMEOUT} seconds
tell application "Microsoft Excel"
    set theBook to open workbook workbook file name {src} read only true
    save workbook as theBook filename ((POSIX file {dst}) as text) file format PDF file format
    close theBook saving no
end tell
end timeout'''
    return f'''with timeout of {TIMEOUT} seconds
tell application "Microsoft PowerPoint"
    open (POSIX file {src})
    set thePresentation to active presentation
    -- (PowerPoint wants the target as a classic Mac path; a POSIX file gave error -9074)
    save thePresentation in ((POSIX file {dst}) as text) as save as PDF
    close thePresentation saving no
end tell
end timeout'''


def _convert(app: str, source: Path, target: Path, kind: str) -> None:
    if not available(app):
        raise PdfError(f"This needs {APPS[app]} on this Mac.")
    work = _workspace()
    try:
        name = Path(source)
        inside = work / (f"{name.stem} (PlanetPDF copy){name.suffix}" if app == "word" else name.name)
        shutil.copy2(source, inside)
        out = work / (Path(source).stem + (".pdf" if kind == "pdf" else ".doc"))
        try:
            _run(app, _script(app, inside, out, kind))
        except PdfError as exc:
            if kind == "doc97" and "block" in str(exc).lower():
                raise OfficeBlocked("Microsoft Word on this Mac isn't allowed to save Word 97-2003 files "
                                    "(its File Block settings, often an organization's policy). Export to "
                                    "Word (.docx) instead, or ask IT to allow it.") from exc
            raise
        if not out.is_file():
            raise PdfError(f"{APPS[app]} didn't save the file.")
        shutil.move(str(out), target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def to_pdf(source: Path, target: Path) -> None:
    source, target = Path(source).resolve(), Path(target).resolve()
    app = app_for(source)
    if app is None:
        raise PdfError(f"{source.name} isn't a Word, Excel, or PowerPoint file.")
    _convert(app, source, target, "pdf")


def word_save_as(source: Path, target: Path, file_format: int = WORD_97_2003) -> None:
    if file_format != WORD_97_2003:
        raise PdfError("Only Word 97-2003 is saved through Word on a Mac.")
    _convert("word", Path(source).resolve(), Path(target).resolve(), "doc97")
