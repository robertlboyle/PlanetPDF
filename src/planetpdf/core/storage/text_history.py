"""Text the user has added, remembered in their profile and offered back as they type
(user 2026-11-16: "text should be remembered and stored in the profile as an auto
complete field").

``<profile>/text-history.json`` = {"version": 1, "entries": [newest first]}. Nothing
leaves the computer: it's the user's own list, shown in Preferences, where it can be
switched off or cleared.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from planetpdf.core.storage.files import write_bytes_atomic

log = logging.getLogger(__name__)
FILENAME = "text-history.json"
MAX_ENTRIES = 500
MAX_LENGTH = 500  # characters: longer text (a pasted paragraph) isn't worth offering back


class TextHistory:
    def __init__(self, path: Path | None, entries: list[str] | None = None) -> None:
        self.path = path  # None: kept in memory only (tests, a profile that can't be written)
        self.entries: list[str] = list(entries or [])

    @classmethod
    def load(cls, path: Path) -> TextHistory:
        entries: list[str] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("entries", []) if isinstance(data, dict) else []
            entries = [e for e in raw if isinstance(e, str) and e.strip()][:MAX_ENTRIES]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Couldn't read the remembered text (%s): %s", path, exc)
        return cls(path, entries)

    def remember(self, text: str) -> bool:
        """Put ``text`` first (moving it up if it's there already). False when it isn't kept."""
        text = text.strip()
        if not text or len(text) > MAX_LENGTH:
            return False
        self.entries = [text] + [e for e in self.entries if e != text]
        del self.entries[MAX_ENTRIES:]
        self.save()
        return True

    def suggestions(self, typed: str) -> list[str]:
        """Remembered text that starts with what's typed (any case), most recent first,
        longer than it (nothing to add otherwise)."""
        if not typed.strip():
            return []
        lowered = typed.casefold()
        return [e for e in self.entries if len(e) > len(typed) and e.casefold().startswith(lowered)]

    def forget(self, text: str) -> None:
        if text in self.entries:
            self.entries.remove(text)
            self.save()

    def clear(self) -> None:
        self.entries = []
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        data = json.dumps({"version": 1, "entries": self.entries}, ensure_ascii=False, indent=1)
        try:
            write_bytes_atomic(self.path, data.encode("utf-8"))
        except OSError as exc:
            log.warning("Couldn't save the remembered text (%s): %s", self.path, exc)
