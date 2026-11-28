"""Printouts from the PlanetPDF printer (user 2026-10-18). The Windows port monitor
(printer\\) and the Mac's "Open in PlanetPDF" put each printout in PlanetPDF's inbox (local
app data + "printouts", a folder per job, the file named after the printed document) and open
it with PlanetPDF, which then saves it where it belongs:

* a **default folder** (Preferences, else the environment variable ``PLANETPDF_PRINT_FOLDER``):
  saved there at once, named by the **name template** (Preferences, else
  ``PLANETPDF_PRINT_NAME``, else ``{document}``), never overwriting ("Quote (2).pdf");
* otherwise (most people) a **Save dialog** asks for the folder and name, starting in the folder
  used last, the name from the template.

No Qt here: the rules (ui/printouts.py shows the dialog).
"""

from __future__ import annotations

import getpass
import os
import platform
import re
from datetime import datetime
from pathlib import Path

ENV_FOLDER = "PLANETPDF_PRINT_FOLDER"
ENV_NAME = "PLANETPDF_PRINT_NAME"
DEFAULT_TEMPLATE = "{document}"
TOKENS = {"document": "the printed document's name", "date": "today (year-month-day)",
          "time": "now (hours-minutes-seconds)", "user": "your sign-in name", "computer": "this computer's name"}
FALLBACK = "Printout"
MAX_CHARS = 120
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?$", re.IGNORECASE)


def is_printout(path: Path, inbox: Path) -> bool:
    """Is ``path`` a printout waiting in the inbox?"""
    try:
        return Path(path).resolve().is_relative_to(Path(inbox).resolve()) and Path(path).suffix.lower() == ".pdf"
    except (OSError, ValueError):
        return False


def waiting(inbox: Path) -> list[Path]:
    """Printouts still in the inbox (e.g. PlanetPDF wasn't running), oldest first."""
    if not Path(inbox).is_dir():
        return []
    found = [p for p in Path(inbox).rglob("*.pdf") if p.is_file()]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def _from_environment(name: str) -> bool:
    """Whether the environment may give this setting: not when IT's policy decides it (security review
    2026-11-27, finding 14: a user's own PLANETPDF_PRINT_FOLDER beat a policy that said "ask")."""
    from planetpdf.core import policy

    return not policy.current().locks(name)


def default_folder(setting: str) -> Path | None:
    """Where printouts go without asking: the Preferences folder, else the environment's (unless
    the policy decides it)."""
    text = (setting or "").strip() or (os.environ.get(ENV_FOLDER, "").strip() if _from_environment("print_folder")
                                       else "")
    if not text:
        return None
    return Path(os.path.expandvars(os.path.expanduser(text)))


def template(setting: str) -> str:
    from_env = os.environ.get(ENV_NAME, "").strip() if _from_environment("print_name_template") else ""
    return (setting or "").strip() or from_env or DEFAULT_TEMPLATE


def safe_name(text: str) -> str:
    """A file name (without .pdf): no characters Windows or the Mac refuse, at most 120."""
    name = " ".join(_BAD.sub(" ", text).split())[:MAX_CHARS].strip(" .")
    return FALLBACK if not name or _RESERVED.match(name) else name


def file_name(template_text: str, document: str, *, now: datetime | None = None, user: str | None = None,
              computer: str | None = None) -> str:
    """The template filled in ({document}, {date}, {time}, {user}, {computer}; anything else is
    kept as written), made safe; without .pdf."""
    now = now or datetime.now()
    values = {
        "document": document or FALLBACK,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "user": user if user is not None else _user(),
        "computer": computer if computer is not None else platform.node(),
    }
    filled = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1).lower(), m.group(0)), template_text)
    return safe_name(filled)


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # (no sign-in name to be found)
        return ""


def unique_path(folder: Path, name: str) -> Path:
    """``folder``/name.pdf, or "name (2).pdf"… when that's taken."""
    first = Path(folder) / f"{name}.pdf"
    if not first.exists():
        return first
    n = 2
    while (Path(folder) / f"{name} ({n}).pdf").exists():
        n += 1
    return Path(folder) / f"{name} ({n}).pdf"


def done_with(path: Path, inbox: Path) -> None:
    """The printout has left the inbox: its job folder goes when empty."""
    folder = Path(path).parent
    try:
        if folder != Path(inbox) and folder.is_relative_to(Path(inbox)) and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
