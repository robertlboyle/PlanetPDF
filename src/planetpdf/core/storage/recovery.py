"""Auto-save for crash recovery.

While a document has unsaved changes, a copy is written to a recovery folder every
few minutes. The copy is removed when the document is saved or closed normally, so
anything left behind after a crash can be offered back on the next start.

The folder is local to the machine (not the roaming profile): whole PDFs would make
roaming profiles slow on terminal servers, and recovery only matters on the machine
that crashed. Locking (so a second running instance doesn't offer the first one's
live copies) is done by the UI with a lock file per entry.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from planetpdf.core.storage.files import remove_quietly, write_bytes_atomic


@dataclass(frozen=True)
class RecoveryEntry:
    entry_id: str
    original: Path  # the file the unsaved changes belong to
    saved_at: datetime
    pdf: Path  # the recovered copy

    @property
    def name(self) -> str:
        return self.original.name


class RecoveryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def _pdf(self, entry_id: str) -> Path:
        return self.root / f"{entry_id}.pdf"

    def _meta(self, entry_id: str) -> Path:
        return self.root / f"{entry_id}.json"

    def lock_path(self, entry_id: str) -> Path:
        return self.root / f"{entry_id}.lock"

    def write(self, entry_id: str, original: Path, data: bytes, now: datetime | None = None) -> RecoveryEntry:
        self.root.mkdir(parents=True, exist_ok=True)
        now = now or datetime.now()
        write_bytes_atomic(self._pdf(entry_id), data)
        meta = {"original": str(original), "saved_at": now.isoformat(timespec="seconds")}
        write_bytes_atomic(self._meta(entry_id), json.dumps(meta).encode("utf-8"))
        return RecoveryEntry(entry_id, Path(original), now, self._pdf(entry_id))

    def entries(self) -> list[RecoveryEntry]:
        """Newest first; incomplete or unreadable leftovers are cleaned up."""
        if not self.root.is_dir():
            return []
        out = []
        for meta in self.root.glob("*.json"):
            entry_id = meta.stem
            pdf = self._pdf(entry_id)
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                entry = RecoveryEntry(entry_id, Path(info["original"]), datetime.fromisoformat(info["saved_at"]), pdf)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not pdf.is_file():
                remove_quietly(meta)
                continue
            out.append(entry)
        return sorted(out, key=lambda e: e.saved_at, reverse=True)

    def discard(self, entry_id: str) -> None:
        for path in (self._pdf(entry_id), self._meta(entry_id)):
            remove_quietly(path)

    def restore_to(self, entry: RecoveryEntry, fallback_dir: Path, target: Path | None = None) -> Path:
        """Copy the recovered document next to the original as "<name> (recovered).pdf" (or into
        ``fallback_dir`` if that folder is gone, or is a temporary folder whose files are deleted
        without asking: security review 2026-11-27), or to ``target`` when one was chosen. The
        original is never touched."""
        from planetpdf.core.temp_folders import is_temporary

        original = entry.original.parent
        folder = original if original.is_dir() and not is_temporary(original) else Path(fallback_dir)
        stem = entry.original.stem or "Document"
        if target is None:
            target = folder / f"{stem} (recovered).pdf"
            n = 2
            while target.exists():
                target = folder / f"{stem} (recovered {n}).pdf"
                n += 1
        write_bytes_atomic(target, entry.pdf.read_bytes())
        self.discard(entry.entry_id)
        return target
