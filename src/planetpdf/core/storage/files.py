"""Safe file replacement.

Sync clients (Dropbox, OneDrive) and antivirus scanners briefly lock files on
Windows, so replace/remove operations retry for a short time before giving up.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.5)


def replace_with_retry(src: Path, dst: Path) -> None:
    """Atomically move ``src`` over ``dst``, retrying on transient locks."""
    for delay in (*_RETRY_DELAYS, None):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def remove_quietly(path: Path) -> None:
    """Delete ``path`` if it exists; ignore failures (used for temp-file cleanup)."""
    for delay in (*_RETRY_DELAYS, None):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if delay is None:
                return
            time.sleep(delay)


def temp_path_beside(target: Path, suffix: str = ".pdf") -> Path:
    """Reserve a unique temp file in the same directory as ``target``.

    Same directory means the final ``os.replace`` stays on one volume, so it is atomic.
    """
    fd, name = tempfile.mkstemp(prefix=".~planetpdf-", suffix=suffix, dir=target.parent)
    os.close(fd)
    return Path(name)


def write_bytes_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path_beside(target, suffix=".tmp")
    try:
        tmp.write_bytes(data)
        replace_with_retry(tmp, target)
    finally:
        remove_quietly(tmp)
