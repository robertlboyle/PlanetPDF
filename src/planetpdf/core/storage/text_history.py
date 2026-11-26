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
import os
import re
import sys
from pathlib import Path

from planetpdf.core.storage.files import write_bytes_atomic

log = logging.getLogger(__name__)
FILENAME = "text-history.json"
MAX_ENTRIES = 500
MAX_LENGTH = 500  # characters: longer text (a pasted paragraph) isn't worth offering back

# What's never remembered (security review 2026-11-25): Social Security numbers, card numbers, and
# account numbers, alone or within the text (dates and phone numbers are still remembered)
_PRIVATE = (
    re.compile(r"(?<!\d)\d{3}([- ]?)\d{2}\1\d{4}(?!\d)"),  # SSN: 123-45-6789, 123 45 6789, 123456789
    re.compile(r"(?<!\d)\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}(?!\d)"),  # card: 4111 1111 1111 1111
    re.compile(r"(?<!\d)\d{4}([ -])\d{6}\1\d{5}(?!\d)"),  # card: 3782 822463 10005
    re.compile(r"(?<!\d)\d{8,19}(?!\d)"),  # account or card number, digits only
)


def private(text: str) -> bool:
    """True when ``text`` holds what looks like an SSN, a card number, or an account number."""
    return any(pattern.search(text) for pattern in _PRIVATE)


class TextHistory:
    def __init__(self, path: Path | None, entries: list[str] | None = None) -> None:
        self.path = path  # None: kept in memory only (tests, a profile that can't be written)
        self.entries: list[str] = list(entries or [])

    @classmethod
    def load(cls, path: Path, keep_private: bool = False) -> TextHistory:
        """``keep_private``: also text holding private numbers (Preferences: the user's choice)."""
        entries: list[str] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("entries", []) if isinstance(data, dict) else []
            entries = [e for e in raw if isinstance(e, str) and e.strip() and (keep_private or not private(e))]
            entries = entries[:MAX_ENTRIES]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Couldn't read the remembered text (%s): %s", path, exc)
        return cls(path, entries)

    def remember(self, text: str, allow_private: bool = False) -> bool:
        """Put ``text`` first (moving it up if it's there already). False when it isn't kept: too
        long, or holding a private number when ``allow_private`` is off (the default)."""
        text = text.strip()
        if not text or len(text) > MAX_LENGTH or (private(text) and not allow_private):
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

    def forget_private(self) -> int:
        """Forgets every entry holding a private number (the option was switched off). Returns how many."""
        kept = [e for e in self.entries if not private(e)]
        gone = len(self.entries) - len(kept)
        if gone:
            self.entries = kept
            self.save()
        return gone

    def clear(self) -> None:
        self.entries = []
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        data = json.dumps({"version": 1, "entries": self.entries}, ensure_ascii=False, indent=1)
        try:
            write_bytes_atomic(self.path, data.encode("utf-8"))
            if sys.platform != "win32":  # (Windows: the profile folder is the user's own already)
                os.chmod(self.path, 0o600)  # (readable by this user only)
        except OSError as exc:
            log.warning("Couldn't save the remembered text (%s): %s", self.path, exc)
