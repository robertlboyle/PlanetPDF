"""One running PlanetPDF per user (user 2026-10-02: "this should all be in one copy of
the program unless I pop out a document"). A second start (double-clicking another PDF
in Explorer, the Start menu, the command line) hands its files to the running copy, which
opens them as tabs and comes to the front, and then exits.

The two talk over a QLocalServer (a named pipe on Windows) whose name includes the
user and their Windows session, so on a terminal server every user gets their own
PlanetPDF and two people never share one. ``--new-instance`` skips all this (testing).

**No second copy while the first is still starting** (user 2026-10-22: two copies had the
same file open; closing one asked nothing, while the other held the changes). The first copy
used to listen only once its window was built, seconds after it started, so a file
double-clicked meanwhile started a copy of its own. Now the first copy takes
``instance_lock()`` and listens at once, holding files handed to it until its window can take
them (``InstanceServer.set_handler``); a later start that finds the lock taken keeps trying to
hand over its files (``send_to_running(wait=...)``) instead of giving up after half a second.
"""

from __future__ import annotations

import getpass
import json
import re
import sys
from pathlib import Path
from typing import Callable

import time

from PySide6.QtCore import QDir, QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CONNECT_MS = 500
REPLY_MS = 5000
WAIT_SECONDS = 20  # a later start waits this long for a copy that is still starting


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # (no user name available)
        user = "user"
    session = ""
    if sys.platform == "win32":
        from planetpdf.ui.windows_shell import session_id

        session = f"-{session_id()}"
    return "PlanetPDF-" + re.sub(r"[^A-Za-z0-9_.-]", "_", f"{user}{session}")


def instance_lock(name: str | None = None) -> QLockFile:
    """Held by the running PlanetPDF for as long as it runs (``tryLock`` it)."""
    lock = QLockFile(str(Path(QDir.tempPath()) / f"{name or server_name()}.lock"))
    lock.setStaleLockTime(0)  # (free only once its process is gone)
    return lock


def send_to_running(files: list[Path], name: str | None = None, wait: float = 0,
                    lock: QLockFile | None = None) -> bool:
    """Give the files to a PlanetPDF that is already running. True when it took them
    (this start then just exits); False when none is running. With ``wait``, keep trying
    that many seconds (the running copy may still be starting), but stop as soon as
    ``lock`` can be taken: then no copy is running and this one becomes it."""
    deadline = time.monotonic() + wait
    while True:
        left = deadline - time.monotonic()
        if _send(files, name, max(REPLY_MS, int(left * 1000))):  # (answered once its window is ready)
            return True
        if time.monotonic() >= deadline or (lock is not None and lock.tryLock(0)):
            return False
        time.sleep(0.2)


def _send(files: list[Path], name: str | None, reply_ms: int = REPLY_MS) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(CONNECT_MS):
        return False
    if sys.platform == "win32":
        from planetpdf.ui.windows_shell import allow_foreground

        allow_foreground()  # (Windows lets the running copy come to the front)
    message = json.dumps({"files": [str(Path(f).resolve()) for f in files]}) + "\n"
    socket.write(message.encode("utf-8"))
    socket.flush()
    if not socket.waitForReadyRead(reply_ms):
        socket.abort()
        return False  # (it didn't answer: start normally)
    reply = bytes(socket.readAll()).decode("utf-8", "replace").strip()
    socket.disconnectFromServer()
    return reply == "ok"


class InstanceServer(QObject):
    """Listens for later starts; ``handler(files)`` opens them (an empty list: just come
    to the front). Without a handler yet (the window is still being built), what comes in
    waits for ``set_handler``."""

    def __init__(self, handler: Callable[[list[Path]], None] | None, name: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._handler = handler
        self._waiting: list[list[Path]] = []
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connection)
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)  # (left behind by a crash, on Linux / macOS)
            self.listening = self.server.listen(self.name)
        else:
            self.listening = True

    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(sock=socket, buf=buffer) -> None:
                buf.extend(bytes(sock.readAll()))
                if not buf.endswith(b"\n"):
                    return
                try:
                    files = [Path(f) for f in json.loads(buf.decode("utf-8")).get("files", [])]
                except (ValueError, AttributeError):
                    files = []
                sock.write(b"ok\n")
                sock.flush()
                sock.disconnectFromServer()
                QTimer.singleShot(0, lambda: self._deliver(files))  # (after answering)

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                read()

    def _deliver(self, files: list[Path]) -> None:
        if self._handler is None:
            self._waiting.append(files)
        else:
            self._handler(files)

    def set_handler(self, handler: Callable[[list[Path]], None]) -> None:
        self._handler = handler
        waiting, self._waiting = self._waiting, []
        for files in waiting:
            handler(files)

    def close(self) -> None:
        """Stop answering (PlanetPDF is quitting: a later start must not hand files to it)."""
        self.server.close()
