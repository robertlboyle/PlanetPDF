"""One running PlanetPDF per user (user 2026-10-02: "this should all be in one copy of
the program unless I pop out a document"). A second start (double-clicking another PDF
in Explorer, the Start menu, the command line) hands its files to the running copy, which
opens them as tabs and comes to the front, and then exits.

The two talk over a QLocalServer (a named pipe on Windows, a socket file elsewhere). On a
terminal server every user and session has their own PlanetPDF. ``--new-instance`` skips all this
(testing).

**Nobody else can stand in for the running copy** (security list 2026-11-26, item 2). The pipe
used to be named after the user and session, which another user on the same server can work out,
and make first: later starts then handed their file names to that user's program. Now every
PlanetPDF listens on a name made at random for that run and says where in a file in the user's own
folder (``instance_file`` for the copy later starts hand over to, ``process_file`` for each
process), with a key. A start reads the file, connects (on Windows letting the other end learn who
it is but not act as it), and asks for proof that the other end read the same file
(``core/local_proof.py``) before it sends anything; the running copy asks the same of the start.

**No second copy while the first is still starting** (user 2026-10-22: two copies had the
same file open; closing one asked nothing, while the other held the changes). The first copy
takes ``instance_lock()`` and listens at once, holding files handed to it until its window can
take them (``InstanceServer.set_handler``); a later start that finds the lock taken keeps trying to
hand over its files (``send_to_running(wait=...)``) instead of giving up after half a second.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from planetpdf.core import local_proof as P

CONNECT_MS = 500
REPLY_MS = 5000
WAIT_SECONDS = 20  # a later start waits this long for a copy that is still starting
LISTEN_TRIES = 3

_folder: Path | None = None  # where this user's PlanetPDFs say how to reach them (configure)


def configure(folder: Path | None) -> None:
    """The user's own folder for the lock and the address files (``paths.instance_dir()``)."""
    global _folder
    _folder = P.private_folder(folder) if folder is not None else None


def session_tag() -> str:
    """"-<Windows session>" (each session of a user on a terminal server has its own PlanetPDF)."""
    if sys.platform == "win32":
        from planetpdf.ui.windows_shell import session_id

        return f"-{session_id()}"
    return ""


def instance_file(folder: Path) -> Path:
    """How to reach the copy later starts hand their files to."""
    return Path(folder) / f"instance{session_tag()}.json"


def process_file(folder: Path, pid: int | None = None) -> Path:
    """How to reach one PlanetPDF process (``file_locks``: "show the file you have open")."""
    return Path(folder) / f"process-{pid or os.getpid()}.json"


def instance_lock(folder: Path | None = None) -> QLockFile:
    """Held by the running PlanetPDF for as long as it runs (``tryLock`` it)."""
    folder = P.private_folder(folder if folder is not None else _folder)
    lock = QLockFile(str(folder / f"instance{session_tag()}.lock"))
    lock.setStaleLockTime(0)  # (free only once its process is gone)
    return lock


def send_to_running(files: list[Path], where: Path | None = None, wait: float = 0,
                    lock: QLockFile | None = None) -> bool:
    """Give the files to a PlanetPDF that is already running (the one ``where`` says how to reach:
    by default the copy later starts hand over to). True when it took them (this start then just
    exits); False when none is running. With ``wait``, keep trying that many seconds (the running
    copy may still be starting), but stop as soon as ``lock`` can be taken: then no copy is running
    and this one becomes it."""
    if where is None:
        if _folder is None:
            return False
        where = instance_file(_folder)
    deadline = time.monotonic() + wait
    while True:
        left = deadline - time.monotonic()
        if _send(files, where, max(REPLY_MS, int(left * 1000))):  # (answered once its window is ready)
            return True
        if time.monotonic() >= deadline or (lock is not None and lock.tryLock(0)):
            return False
        time.sleep(0.2)


def send_to_process(files: list[Path], pid: int) -> bool:
    """Ask one PlanetPDF process to show these files. True when it answered."""
    return _folder is not None and send_to_running(files, process_file(_folder, pid))


def _connect(name: str) -> QLocalSocket | None:
    socket = QLocalSocket()
    if sys.platform == "win32":
        # Opened by hand: Qt's own open lets the other end act as this user (impersonation)
        # until it's shown to be PlanetPDF; this one only lets it learn who is calling.
        from planetpdf.ui.windows_shell import close_handle, open_pipe

        handle = open_pipe(name, CONNECT_MS)
        if handle is None:
            return None
        if not socket.setSocketDescriptor(handle):
            close_handle(handle)
            return None
        return socket
    socket.connectToServer(name)
    return socket if socket.waitForConnected(CONNECT_MS) else None


def _read_line(socket: QLocalSocket, wait_ms: int) -> bytes | None:
    """One line from the other end (None: it didn't come in time, or the connection ended)."""
    end = time.monotonic() + wait_ms / 1000
    buffer = bytearray()
    while b"\n" not in buffer:
        left = int((end - time.monotonic()) * 1000)
        if left <= 0 or not socket.waitForReadyRead(left):
            if socket.bytesAvailable():
                buffer.extend(bytes(socket.readAll()))
                continue
            return None
        buffer.extend(bytes(socket.readAll()))
    return bytes(buffer).partition(b"\n")[0]


def _send(files: list[Path], where: Path, reply_ms: int = REPLY_MS) -> bool:
    info = P.read_private(where)
    if info is None or not isinstance(info.get("name"), str) or not isinstance(info.get("key"), str):
        return False  # (nobody said how to reach them)
    socket = _connect(info["name"])
    if socket is None:
        return False
    try:
        nonce = P.new_nonce()
        socket.write((json.dumps({"hello": nonce}) + "\n").encode("utf-8"))
        socket.flush()
        line = _read_line(socket, reply_ms)
        try:
            answer = json.loads(line) if line else {}
        except ValueError:
            answer = {}
        if not isinstance(answer, dict) or not P.proves(info["key"], P.SERVER, nonce, answer.get("proof")):
            return False  # (not this user's PlanetPDF: nothing is sent to it)
        if sys.platform == "win32":
            from planetpdf.ui.windows_shell import allow_foreground

            allow_foreground()  # (Windows lets the running copy come to the front)
        message = {"files": [str(Path(f).resolve()) for f in files],
                   "proof": P.proof(info["key"], P.CLIENT, str(answer.get("nonce", "")))}
        socket.write((json.dumps(message) + "\n").encode("utf-8"))
        socket.flush()
        line = _read_line(socket, reply_ms)
        return line is not None and line.strip() == b"ok"  # (no answer: start normally)
    finally:
        socket.abort()


class InstanceServer(QObject):
    """Listens for later starts; ``handler(files)`` opens them (an empty list: just come
    to the front). Without a handler yet (the window is still being built), what comes in
    waits for ``set_handler``. ``shared``: this is also the copy later starts hand over to (it
    holds ``instance_lock``); otherwise it can only be reached as a process."""

    def __init__(self, handler: Callable[[list[Path]], None] | None, folder: Path | None = None, *,
                 shared: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._handler = handler
        self._waiting: list[list[Path]] = []
        self._states: dict[QLocalSocket, dict] = {}  # each connection's unread bytes and its nonce
        self.folder = P.private_folder(folder if folder is not None else _folder)
        self.key = P.new_key()
        self.name = ""
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connection)
        self.listening = False
        for _ in range(LISTEN_TRIES):  # (a random name is free; another try if it somehow isn't)
            self.name = P.new_name()
            if self.server.listen(self.name):
                self.listening = True
                break
        self.files = [process_file(self.folder)] + ([instance_file(self.folder)] if shared else [])
        if self.listening:
            for path in self.files:
                P.write_private(path, {"name": self.name, "key": self.key, "pid": os.getpid()})

    # The sockets' signals go to methods of this object, never to functions made here that hold
    # it: such a function, kept by the socket, made a ring Python's collector took apart in its
    # own order, and a connection the other end had just dropped then crashed the program.
    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._states[socket] = {"buffer": bytearray(), "nonce": ""}
            socket.readyRead.connect(self._ready)
            socket.disconnected.connect(self._gone)
            if socket.bytesAvailable():
                self._read(socket)

    def _ready(self) -> None:
        self._read(self.sender())

    def _gone(self) -> None:
        socket = self.sender()
        self._states.pop(socket, None)
        socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        state = self._states.get(socket)
        if state is None:
            return
        state["buffer"].extend(bytes(socket.readAll()))
        while b"\n" in state["buffer"]:
            line, _, rest = bytes(state["buffer"]).partition(b"\n")
            state["buffer"][:] = rest
            self._line(socket, state, line)

    def _line(self, socket: QLocalSocket, state: dict, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            message = None
        if not isinstance(message, dict):
            self._answer(socket, b"no\n", close=True)
        elif "hello" in message:  # (prove this is PlanetPDF, and ask the same of the start)
            state["nonce"] = P.new_nonce()
            reply = {"proof": P.proof(self.key, P.SERVER, str(message["hello"])), "nonce": state["nonce"]}
            self._answer(socket, (json.dumps(reply) + "\n").encode("utf-8"))
        elif state["nonce"] and P.proves(self.key, P.CLIENT, state["nonce"], message.get("proof")):
            state["nonce"] = ""  # (one hand-over per proof)
            listed = message.get("files")
            files = [Path(f) for f in listed if isinstance(f, str)] if isinstance(listed, list) else []
            self._answer(socket, b"ok\n", close=True)
            self._waiting.append(files)
            QTimer.singleShot(0, self._deliver)  # (after answering)
        else:
            self._answer(socket, b"no\n", close=True)

    @staticmethod
    def _answer(socket: QLocalSocket, data: bytes, close: bool = False) -> None:
        socket.write(data)
        socket.flush()
        if close:
            socket.disconnectFromServer()

    def _deliver(self) -> None:
        """Hand what came in to the window (once there is one: until then it waits)."""
        if self._handler is None:
            return
        waiting, self._waiting = self._waiting, []
        for files in waiting:
            self._handler(files)

    def set_handler(self, handler: Callable[[list[Path]], None]) -> None:
        self._handler = handler
        self._deliver()

    def close(self) -> None:
        """Stop answering (PlanetPDF is quitting: a later start must not hand files to it)."""
        self.server.close()
        self.listening = False
        for socket in list(self._states):  # (connections still open end here)
            socket.abort()
        self._states.clear()
        for path in self.files:  # (only what still says this process)
            info = P.read_private(path)
            if info is not None and info.get("name") == self.name:
                try:
                    path.unlink()
                except OSError:
                    pass


def forget_stale(folder: Path | None = None) -> int:
    """Remove the address files of PlanetPDF processes that are gone (left by a crash). How many."""
    from planetpdf.ui.peer_process import program_of

    folder = folder if folder is not None else _folder
    removed = 0
    if folder is None:
        return 0
    for path in Path(folder).glob("process-*.json"):
        try:
            pid = int(path.stem.partition("-")[2])
        except ValueError:
            continue
        if pid != os.getpid() and not program_of(pid):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
