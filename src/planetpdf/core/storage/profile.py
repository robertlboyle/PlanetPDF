"""The user's PlanetPDF profile: one directory holding every per-user file.

The UI chooses the base directory (the platform's *roaming* per-user application
data folder, or ``PLANETPDF_PROFILE_DIR`` if an administrator sets it), so the same
preferences, signatures and stamps follow a user between machines, e.g. on terminal
servers with roaming profiles (user decision, see CLAUDE.md).

Layout::

    <profile>/settings.json
    <profile>/signatures/        saved signatures and initials (PNG + index)
    <profile>/stamps/            custom stamps (stamps.json) and seals/ (PNG)
    <profile>/trusted-certificates/   CA certificates trusted for signature checks
    <profile>/text-history.json  text the user has added, offered back as they type
"""

from __future__ import annotations

import logging
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from planetpdf.core.storage.settings import SETTINGS_FILENAME

log = logging.getLogger(__name__)
PROFILE_ENV_VAR = "PLANETPDF_PROFILE_DIR"


@dataclass(frozen=True)
class Profile:
    root: Path

    @property
    def settings_dir(self) -> Path:
        return self.root

    @property
    def signatures_dir(self) -> Path:
        return self.root / "signatures"

    @property
    def stamps_dir(self) -> Path:
        return self.root / "stamps"

    @property
    def trusted_certs_dir(self) -> Path:
        return self.root / "trusted-certificates"

    @property
    def text_history_path(self) -> Path:
        from planetpdf.core.storage.text_history import FILENAME

        return self.root / FILENAME

    @property
    def item_key_path(self) -> Path:
        return self.root / "item-key"

    def item_key(self) -> bytes:
        """This user's key for marking the items PlanetPDF makes (``markup.set_item_key``; security
        review 2026-11-25). Made once and kept in the profile, which only this user can open."""
        try:
            text = self.item_key_path.read_text(encoding="ascii").strip()
            if len(text) == 64:
                return bytes.fromhex(text)
        except (OSError, ValueError):
            pass
        key = secrets.token_bytes(32)
        try:
            self.item_key_path.parent.mkdir(parents=True, exist_ok=True)
            self.item_key_path.write_text(key.hex(), encoding="ascii")
        except OSError:
            log.warning("Couldn't keep the item key in %s", self.item_key_path)
        return key

    def ensure(self) -> Profile:
        for directory in (self.root, self.signatures_dir, self.stamps_dir, self.trusted_certs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def migrate_legacy(profile: Profile, legacy_dirs: list[Path]) -> list[Path]:
    """Copy files from older, non-roaming locations into the profile the first time
    (never overwrites anything already in the profile). Returns what was copied."""
    copied: list[Path] = []
    for legacy in legacy_dirs:
        legacy = Path(legacy)
        if not legacy.is_dir() or legacy.resolve() == profile.root.resolve():
            continue
        for source in legacy.rglob("*"):
            if not source.is_file():
                continue
            target = profile.root / source.relative_to(legacy)
            if target.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(target)
            except OSError as exc:
                log.warning("Couldn't migrate %s: %s", source, exc)
    return copied


__all__ = ["PROFILE_ENV_VAR", "Profile", "SETTINGS_FILENAME", "migrate_legacy"]
