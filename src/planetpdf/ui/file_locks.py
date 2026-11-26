"""A file is open in one PlanetPDF only (user 2026-10-22, after closing one of two copies of a
file without being asked: the other copy had the changes). Each open file has a lock that
every PlanetPDF of this user can see: a QLockFile named by the file's path, held while any
window of this process has the file open. A lock whose process is gone counts as free.

Every PlanetPDF can also be reached by its process (``single_instance.process_file``), so a copy
that finds a file locked can ask the copy holding it to show it (``show_in_holder``).

Off until ``configure(folder)`` (app.py does it; tests don't, so test processes never lock
the fixtures each other use).
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile

_folder: Path | None = None
_held: dict[str, QLockFile] = {}  # key → lock this process holds


def configure(folder: Path | None) -> None:
    global _folder
    _folder = Path(folder) if folder is not None else None
    if _folder is not None:
        _folder.mkdir(parents=True, exist_ok=True)


def enabled() -> bool:
    return _folder is not None


def key(path: Path) -> str:
    """The same for every spelling of one file (case and separators on Windows)."""
    try:
        text = str(Path(path).resolve())
    except OSError:
        text = str(Path(path).absolute())
    if sys.platform == "win32":
        text = os.path.normcase(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _lock_file(k: str) -> QLockFile:
    lock = QLockFile(str(_folder / f"{k}.lock"))
    lock.setStaleLockTime(0)  # (stale only when its process is gone, never by age)
    return lock


def holder(path: Path) -> int | None:
    """The process ID of the other PlanetPDF that has this file open, else None."""
    if _folder is None:
        return None
    k = key(path)
    if k in _held:
        return None
    lock = _lock_file(k)
    if lock.tryLock(0):
        lock.unlock()
        return None
    pid, _host, _app = lock.getLockInfo()  # (PySide: no "ok"; pid 0 when unreadable)
    if not pid or pid == os.getpid():
        return None
    return pid


def sync(paths: list[Path]) -> None:
    """Hold the locks of exactly these files (every file open in this process)."""
    if _folder is None:
        return
    wanted = {key(p) for p in paths}
    for k in [k for k in _held if k not in wanted]:
        _held.pop(k).unlock()
    for k in wanted - _held.keys():
        lock = _lock_file(k)
        if lock.tryLock(0):
            _held[k] = lock
        # (else another PlanetPDF has it too: one opened before these locks existed. Nothing
        # to hold; ``holder`` keeps refusing new copies.)


def release_all() -> None:
    for lock in _held.values():
        lock.unlock()
    _held.clear()


def show_in_holder(path: Path, pid: int) -> bool:
    """Ask the PlanetPDF that has the file open to show it. True when it answered."""
    from planetpdf.ui.single_instance import send_to_process

    return send_to_process([Path(path)], pid)
