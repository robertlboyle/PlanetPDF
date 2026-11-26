"""Serving PlanetPDF's local API (``core/api.py``) over a QLocalServer: a named pipe on Windows,
a socket file on the Mac and Linux, both open to this user only (UserAccessOption), never a
network port. Its name is made at random for each run (security list 2026-11-26, item 2: a name
built from the user's name could be taken first by another user of the same computer, who then
received what programs sent). Where it is goes into ``api.json`` in the user's local app data
folder for programs to find, with a key: a program asks the API to prove it read that file
(``planetpdf.prove``) before it says anything else.

Requests run in worker threads (a long export doesn't hold up the window or other requests);
replies and progress are written from the GUI thread, and the signing question is asked there.
Hosted by the PlanetPDF window (``attach``), or without one by ``planetpdf serve`` (``serve``).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from planetpdf.core import api
from planetpdf.core import local_proof as P

READ_LIMIT = 4 * 1024 * 1024  # a request line longer than this is refused
LISTEN_TRIES = 3
PROBE_MS = 1500


def server_name() -> str:
    return P.new_name("PlanetPDF-API-")


def info_path() -> Path:
    from planetpdf.ui import paths

    return paths.api_info_path()


class _Gui(QObject):
    """Runs a function on the GUI thread for a worker thread and hands back its result."""

    call = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job) -> None:
        fn, holder, done = job
        try:
            holder["result"] = fn()
        except BaseException as e:  # (raised again in the worker)
            holder["error"] = e
        done.set()

    def run(self, fn: Callable[[], object]):
        holder, done = {}, threading.Event()
        self.call.emit((fn, holder, done))
        done.wait()
        if "error" in holder:
            raise holder["error"]
        return holder.get("result")


class ApiServer(QObject):
    _write = Signal(object, bytes)

    def __init__(self, profile_root: Path, *, window_methods: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.gui = _Gui(self)
        self.service = api.ApiService(profile_root, ask_to_sign=self._ask_to_sign, extra=window_methods or {},
                                      ask_to_connect=self._ask_to_connect)
        self._write.connect(self._send, Qt.ConnectionType.QueuedConnection)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connection)
        self.key = self.service.pipe_key = P.new_key()
        self.name = ""
        self.listening = False
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._connections: dict[QLocalSocket, api.Connection] = {}
        if served_elsewhere():  # (one API per user: PlanetPDF's, or a planetpdf serve)
            return
        for _ in range(LISTEN_TRIES):  # (a random name is free; another try if it somehow isn't)
            self.name = server_name()
            if self.server.listen(self.name):
                self.listening = True
                break
        if self.listening:
            self._write_info()

    # --- where programs find it ---
    def _write_info(self) -> None:
        P.write_private(info_path(), {"address": self.server.fullServerName(), "protocol": api.PROTOCOL,
                                      "version": api.PROTOCOL_VERSION, "pid": os.getpid(), "key": self.key})

    def close(self) -> None:
        if self.listening:
            try:
                data = json.loads(info_path().read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    info_path().unlink()
            except (OSError, ValueError):
                pass
        self.server.close()
        self.listening = False

    @property
    def address(self) -> str:
        return self.server.fullServerName()

    # --- connections ---
    # (the sockets' signals go to methods of this object, never to functions that hold it:
    # see ui/single_instance.py)
    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._buffers[socket] = bytearray()
            from planetpdf.ui.peer_process import peer_program

            self._connections[socket] = api.Connection(program=peer_program(socket))
            socket.readyRead.connect(self._ready)
            socket.disconnected.connect(self._dropped)

    def _ready(self) -> None:
        self._read(self.sender())

    def _dropped(self) -> None:
        self._gone(self.sender())

    def _gone(self, socket: QLocalSocket) -> None:
        conn = self._connections.pop(socket, None)
        self._buffers.pop(socket, None)
        if conn is not None:  # (its running jobs stop)
            for event in list(conn.jobs.values()):
                event.set()
        import shiboken6

        if shiboken6.isValid(socket):  # (closing the server deletes its sockets itself)
            socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        while b"\n" in buffer:
            line, _, rest = bytes(buffer).partition(b"\n")
            buffer[:] = rest
            if line.strip():
                self._start(socket, line)
        if len(buffer) > READ_LIMIT:
            self._send(socket, json.dumps(api._error(None, api.INVALID_REQUEST, "Request too long.")).encode())
            socket.disconnectFromServer()

    def _start(self, socket: QLocalSocket, line: bytes) -> None:
        conn = self._connections[socket]

        def send(message: dict) -> None:
            self._write.emit(socket, (json.dumps(message, default=str) + "\n").encode("utf-8"))

        threading.Thread(target=self.service.handle_line, args=(conn, line, send), daemon=True,
                         name="PlanetPDF API request").start()

    @Slot(object, bytes)
    def _send(self, socket: QLocalSocket, data: bytes) -> None:
        if socket in self._connections and socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            socket.write(data)
            socket.flush()

    # --- the questions the API asks ---
    def _ask_to_connect(self, name: str, publisher: str, program: str, before: str) -> bool:
        from planetpdf.ui.approval import ask_to_connect

        return self.gui.run(lambda: ask_to_connect(name, publisher, program, before))

    def _ask_to_sign(self, client: api.Client, description: str):
        from planetpdf.ui.approval import ask_to_sign

        who = f"The program “{client.label}”" + (f" ({client.program})" if client.program else "")
        return self.gui.run(lambda: ask_to_sign(who, description, remember=True))


def served_elsewhere() -> bool:
    """Whether another process of this user already serves the API (what ``api.json`` names
    answers and proves it holds that file's key)."""
    info = P.read_private(info_path())
    if info is None or info.get("pid") == os.getpid():
        return False
    address, key = info.get("address"), info.get("key")
    if not isinstance(address, str) or not isinstance(key, str):
        return False
    from planetpdf.ui.single_instance import _connect, _read_line

    socket = _connect(address)
    if socket is None:
        return False
    try:
        nonce = P.new_nonce()
        request = {"jsonrpc": "2.0", "id": 1, "method": "planetpdf.prove", "params": {"nonce": nonce}}
        socket.write((json.dumps(request) + "\n").encode("utf-8"))
        socket.flush()
        line = _read_line(socket, PROBE_MS)
        try:
            result = json.loads(line).get("result", {}) if line else {}
        except (ValueError, AttributeError):
            result = {}
        return isinstance(result, dict) and P.proves(key, P.SERVER, nonce, result.get("proof"))
    finally:
        socket.abort()


def window_methods(server_gui: _Gui) -> dict:
    """What the PlanetPDF window adds to the API: open files in it, and which document is open."""

    def open_files(conn, params):
        files = params.get("files") or []
        if isinstance(files, str):
            files = [files]
        paths = [Path(f) for f in files]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise api.ApiError(api.BAD_PARAMS, f"There's no file {missing[0]}.")

        def go():
            from planetpdf.ui.main_window import MainWindow

            window = MainWindow.last_active_window()
            if window is None:
                raise api.ApiError(api.FAILED, "PlanetPDF has no window open.")
            window.open_paths(paths)
            window.bring_forward()
            return {"opened": [str(p) for p in paths]}

        return server_gui.run(go)

    def current_document(conn, params):
        def go():
            from planetpdf.ui.main_window import MainWindow

            window = MainWindow.last_active_window()
            session = window.session if window is not None else None
            if session is None:
                return {"document": None}
            return {"document": str(session.path), "page": window.view.current_page + 1,
                    "pages": session.page_count, "modified": session.is_modified}

        return server_gui.run(go)

    return {"ui.open": (api.commands.READ, open_files), "ui.current_document": (api.commands.READ, current_document)}


def attach(window, profile_root: Path) -> ApiServer | None:
    """Start the API for the running program, when Preferences turned it on (off by default:
    security review 2026-11-25). Already running: that one."""
    if not getattr(window.settings, "api_enabled", False):
        return None
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if getattr(app, "planetpdf_api", None) is not None:
        return app.planetpdf_api
    server = ApiServer(profile_root, parent=app)
    server.service.extra.update(window_methods(server.gui))
    app.planetpdf_api = server  # (plugins_ui starts plugins with it)
    return server


def serve(profile_root: Path, log: Callable[[str], None] = print) -> int:
    """``planetpdf serve``: the API without a window, until Ctrl+C."""
    import signal

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from planetpdf import APP_NAME, ORG_NAME

    app = QApplication.instance() or QApplication([APP_NAME])
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    server = ApiServer(profile_root)
    if not server.listening:
        log("planetpdf: the API is already being served (PlanetPDF, or another planetpdf serve, is running).")
        return 1
    log(f"PlanetPDF's API is listening at {server.address} (Ctrl+C stops it).")
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    ticker = QTimer()
    ticker.timeout.connect(lambda: None)  # (lets Python see Ctrl+C while Qt waits)
    ticker.start(200)
    try:
        return app.exec()
    finally:
        server.close()
