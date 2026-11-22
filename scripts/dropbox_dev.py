"""Where the Dropbox ``Dev`` folder is on this computer (2026-10-14).

The project is a Git clone outside Dropbox on every computer; Dropbox keeps only what isn't code:
the Font Awesome Pro package (``Dev/fontawesome``) and the secrets (``Dev/PlanetPDF-Key``: the
update-signing key and the upload login; ``Dev/Apple``). The scripts find them here:

1. ``PLANETPDF_DROPBOX_DEV`` (the Dev folder itself), if set;
2. Dropbox's own record of where it keeps its folder (``info.json``: ``%LOCALAPPDATA%\\Dropbox`` /
   ``%APPDATA%\\Dropbox`` on Windows, ``~/.dropbox`` on the Mac and Linux; the business account
   first, then a personal one). A team account's path is the member folder ("…/Robert Boyle"),
   which has a personal Dev folder of its own: the team's ``Dev`` is at the top, one level up, and
   comes first;
3. the usual places: macOS File Provider (``~/Library/CloudStorage/Dropbox*``), ``~/Dropbox*``;
4. the folder beside the project (an old copy still inside Dropbox).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _info_files() -> list[Path]:
    files = [Path.home() / ".dropbox" / "info.json"]
    for env in ("LOCALAPPDATA", "APPDATA"):
        if os.environ.get(env):
            files.append(Path(os.environ[env]) / "Dropbox" / "info.json")
    return files


def candidates() -> list[Path]:
    found: list[Path] = []
    if os.environ.get("PLANETPDF_DROPBOX_DEV"):
        found.append(Path(os.environ["PLANETPDF_DROPBOX_DEV"]))
    for info in _info_files():
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for account in ("business", "personal"):
            path = (data.get(account) or {}).get("path")
            if path:
                found += [Path(path).parent / "Dev", Path(path) / "Dev"]
    home = Path.home()
    found += sorted((home / "Library" / "CloudStorage").glob("Dropbox*/Dev"))
    found += sorted(home.glob("Dropbox*/Dev"))
    found.append(ROOT.parent)
    return found


MARKERS = ("PlanetPDF-Key", "fontawesome")  # (what tells the shared Dev folder from another one)


def dev_folder(needs: str = "") -> Path | None:
    """The Dev folder (holding ``needs``, e.g. "fontawesome", when given), or None."""
    wanted = (needs,) if needs else MARKERS
    return next((d for d in candidates() if d.is_dir() and any((d / w).exists() for w in wanted)), None)


def dev_path(*parts: str) -> Path:
    """``Dev/<parts>`` where it exists, else where it would be in the first Dev folder found."""
    found = dev_folder(parts[0]) or dev_folder() or ROOT.parent
    return found.joinpath(*parts)
