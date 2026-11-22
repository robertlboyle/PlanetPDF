"""Per-user storage location. The only module that uses QStandardPaths.

Everything per-user lives in ONE profile directory in the *roaming* application data
folder (it follows the user between machines / terminal-server sessions):

* Windows: the Roaming application data folder, e.g. ...\\Roaming\\Planet Networks\\PlanetPDF
* macOS: ~/Library/Application Support/...
* Linux: ~/.local/share/...

An administrator can redirect it with the PLANETPDF_PROFILE_DIR environment variable.

Auto-save recovery copies are the exception: they're whole PDFs, only useful on the
machine that crashed, so they go in the *local* application data folder.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from planetpdf.core.storage.profile import PROFILE_ENV_VAR, Profile, migrate_legacy


def profile() -> Profile:
    override = os.environ.get(PROFILE_ENV_VAR, "").strip()
    if override:
        root = Path(os.path.expandvars(os.path.expanduser(override)))
    else:
        # AppDataLocation is the roaming folder on Windows (AppConfigLocation is not).
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    prof = Profile(root).ensure()
    # Earlier builds kept settings in the local (non-roaming) config folder.
    legacy = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    migrate_legacy(prof, [legacy])
    return prof


def recovery_dir() -> Path:
    # AppLocalDataLocation is the local (non-roaming) folder on Windows; on macOS and
    # Linux it's the same folder as AppDataLocation.
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "recovery"


def open_files_dir() -> Path:
    """The locks of the files open in PlanetPDF (``ui/file_locks.py``: local, per user)."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "open-files"


def updates_dir() -> Path:
    """Downloaded updates wait here (local, per user: a zip is only useful on this machine)."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "updates"


def models_dir() -> Path:
    """Read Out Loud's downloaded voices (2026-10-14): this computer's local app data, never the
    roaming profile (354 MB) and never inside the app (updates replace it)."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "models"


def api_info_path() -> Path:
    """Where the local API says it's listening (2026-10-18): this computer's local app data, where
    the Python client (resources/sdk) looks for it."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "api.json"


def printouts_dir() -> Path:
    """The PlanetPDF printer's inbox (local, per user): the Windows port monitor and the Mac's
    "Open in PlanetPDF" put printouts here (printer/src/naming.rs INBOX, core/mac_pdf_service.py),
    and PlanetPDF saves them where they belong (ui/printouts.py)."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "printouts"


def documents_dir() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))