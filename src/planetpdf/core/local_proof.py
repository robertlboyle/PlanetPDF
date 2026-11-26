"""Knowing that the other end of a local connection is this user's PlanetPDF (security list
2026-11-26, item 2).

PlanetPDF's own pipes (a second start handing its files over, "show the file you have open", the
local API) used names anyone could work out from the user's name and session. On a computer several
people use, another user could make a pipe of that name first: Qt's listener then joins it, and
what was meant for PlanetPDF reaches the other user's program (tried: a second start connected to
the pipe made first).

So each running PlanetPDF now listens on a name made at random for that run, and says where in a
file only the user can read (``write_private``), with a key made the same way. Whoever connects
asks for proof first: it sends a number, and only a program that read the key from that file can
answer with the right keyed hash (``proof``). A pipe someone else made under an old name (left in
the file by a crash) can't answer, so nothing is sent to it. The listener asks the same of whoever
connects.

Pure Python, no Qt: the local API (``core/api.py``) and the window's own pipes share it; the
Python client PlanetPDF ships (``resources/sdk``) carries its own copy of ``proof``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

SERVER, CLIENT = "server", "client"


def new_name(prefix: str = "PlanetPDF-") -> str:
    """A pipe / socket name nobody can guess (128 random bits)."""
    return prefix + secrets.token_hex(16)


def new_key() -> str:
    return secrets.token_hex(32)


def new_nonce() -> str:
    return secrets.token_hex(16)


def proof(key: str, role: str, nonce: str) -> str:
    """What ``role`` (SERVER or CLIENT) answers to ``nonce``. The role is part of it, so one side's
    answer can never be handed back as the other's."""
    message = f"PlanetPDF {role}|{nonce}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def proves(key: str, role: str, nonce: str, given: object) -> bool:
    return isinstance(given, str) and bool(key) and hmac.compare_digest(proof(key, role, nonce), given)


def private_folder(folder: Path) -> Path:
    """Make ``folder`` (the user's own: on the Mac and Linux, closed to everyone else)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    return folder


def write_private(path: Path, data: dict) -> bool:
    """Write ``data`` as JSON where only this user can read it: the file is made closed (0600) on
    the Mac and Linux; on Windows the user's own app data folder already is. Written whole, then
    put in place, so a reader never sees half of it."""
    path = Path(path)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd = os.open(temp, flags, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(data, indent=2).encode("utf-8"))
        os.replace(temp, path)
        return True
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return False


def read_private(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
