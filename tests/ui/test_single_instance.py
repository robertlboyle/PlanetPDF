"""Nobody else can stand in for the running PlanetPDF (security list 2026-11-26, item 2): its
pipe has a name made at random for the run, said only in the user's own folder, and each end
proves it read that file before anything is handed over."""

from __future__ import annotations

import ctypes
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

import planetpdf
from planetpdf.core import local_proof as P
from planetpdf.ui import single_instance as S


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "instance"


def _env():
    return dict(os.environ, PYTHONPATH=str(Path(planetpdf.__file__).parents[1]))


def _second_start(folder: Path, files: list[Path]) -> str:
    """A later start in a process of its own, while this one answers. What it printed."""
    code = ("from pathlib import Path; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from planetpdf.ui import single_instance as S; "
            f"print(S.send_to_running({[str(f) for f in files]!r}, S.instance_file(Path({str(folder)!r}))))")
    second = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=_env())
    end = time.time() + 30
    while second.poll() is None and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return second.stdout.read().strip()


def _pump(until, seconds: float = 5.0) -> None:
    end = time.time() + seconds
    while not until() and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def test_each_run_listens_on_a_name_nobody_can_work_out(qapp, folder):
    first, second = S.InstanceServer(None, folder), S.InstanceServer(None, folder, shared=False)
    try:
        assert first.listening and second.listening
        assert first.name != second.name and len(first.name) >= len("PlanetPDF-") + 32
        assert getpass.getuser().lower() not in first.name.lower()
        said = json.loads(S.instance_file(folder).read_text(encoding="utf-8"))
        assert said == {"name": first.name, "key": first.key, "pid": os.getpid()}  # (the shared copy's)
        assert json.loads(S.process_file(folder).read_text(encoding="utf-8"))["name"] in (first.name, second.name)
    finally:
        first.close()
        second.close()


@pytest.mark.skipif(sys.platform == "win32", reason="the Mac and Linux: the file itself is closed to others")
def test_the_address_file_is_closed_to_other_users(qapp, folder):
    server = S.InstanceServer(None, folder)
    try:
        assert S.instance_file(folder).stat().st_mode & 0o077 == 0
        assert folder.stat().st_mode & 0o077 == 0
    finally:
        server.close()


def test_a_later_start_hands_its_files_over(qapp, folder, tmp_path):
    received = []
    server = S.InstanceServer(received.append, folder)
    try:
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        assert _second_start(folder, [pdf]) == "True"
        _pump(lambda: received)
        assert received == [[pdf.resolve()]]
    finally:
        server.close()


def test_nothing_is_sent_to_a_pipe_that_cannot_prove_it_is_planetpdf(qapp, folder, tmp_path):
    """Someone else's pipe under the name the file gives (an old name taken after a crash): the
    later start asks for proof, gets none, and keeps its file names to itself."""
    name, heard = P.new_name("PlanetPDF-test-squat-"), bytearray()
    squatter = QLocalServer()
    assert squatter.listen(name)

    def connection() -> None:
        socket = squatter.nextPendingConnection()

        def read() -> None:
            heard.extend(bytes(socket.readAll()))
            socket.write(b'{"proof": "' + b"0" * 64 + b'", "nonce": "1234"}\n')  # (a guess)
            socket.flush()

        socket.readyRead.connect(read)
        if socket.bytesAvailable():
            read()

    squatter.newConnection.connect(connection)
    try:
        P.write_private(S.instance_file(P.private_folder(folder)), {"name": name, "key": P.new_key(), "pid": 1})
        secret = tmp_path / "payroll-2026.pdf"
        assert _second_start(folder, [secret]) == "False"
        assert b"hello" in heard and b"payroll" not in heard and b"files" not in heard
    finally:
        squatter.close()


def _talk(server: S.InstanceServer, lines: list[bytes]) -> bytes:
    """Send these lines to the server from a plain socket; everything it answered."""
    socket, answers = QLocalSocket(), bytearray()
    socket.connectToServer(server.name)
    assert socket.waitForConnected(2000)
    for line in lines:
        socket.write(line)
        socket.flush()
        before = len(answers)

        def more() -> bool:
            answers.extend(bytes(socket.readAll()))
            return len(answers) > before

        _pump(more, 2.0)
    socket.abort()
    return bytes(answers)


def test_a_start_that_cannot_prove_itself_hands_nothing_over(qapp, folder):
    received = []
    server = S.InstanceServer(received.append, folder)
    try:
        assert _talk(server, [b'{"files": ["C:/evil.pdf"]}\n']).strip() == b"no"  # (no hello first)
        answers = _talk(server, [b'{"hello": "abc"}\n', b'{"files": ["C:/evil.pdf"], "proof": "wrong"}\n'])
        assert answers.strip().endswith(b"no")
        assert _talk(server, [b"not json\n"]).strip() == b"no"
        _pump(lambda: False, 0.2)
        assert received == []
        # the right proof is taken once: a second hand-over on the same connection needs a new hello
        hello = json.loads(_talk(server, [b'{"hello": "abc"}\n']))
        assert P.proves(server.key, P.SERVER, "abc", hello["proof"])
    finally:
        server.close()


def test_closing_takes_the_address_files_away_and_stale_ones_are_forgotten(qapp, folder):
    server = S.InstanceServer(None, folder)
    assert S.instance_file(folder).is_file() and S.process_file(folder).is_file()
    gone = S.process_file(folder, 4_000_000_000 if sys.platform == "win32" else 2_000_000_000)  # (no such process)
    P.write_private(gone, {"name": "PlanetPDF-old", "key": "k", "pid": 1})
    assert S.forget_stale(folder) == 1 and not gone.exists() and S.process_file(folder).is_file()
    server.close()
    assert not S.instance_file(folder).exists() and not S.process_file(folder).exists()
    assert not S.send_to_running([], S.instance_file(folder))  # (nobody says how to reach them)


def test_another_copys_address_file_is_left_alone_on_close(qapp, folder):
    first = S.InstanceServer(None, folder)
    second = S.InstanceServer(None, folder)  # (took over the shared file: the newer copy)
    first.close()
    assert json.loads(S.instance_file(folder).read_text(encoding="utf-8"))["name"] == second.name
    second.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows: how a named pipe is opened")
def test_the_pipe_is_opened_so_the_other_end_cannot_act_as_this_user(tmp_path):
    """``open_pipe`` connects with identification only: a program at the other end can learn who
    is calling, but Windows won't let it act as them (the level Qt's and Python's opens allow)."""
    from ctypes import wintypes

    from planetpdf.ui.windows_shell import PIPE_PREFIX, close_handle, open_pipe

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    k.CreateNamedPipeW.restype = wintypes.HANDLE
    k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                           ctypes.c_void_p]
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                            ctypes.c_void_p]
    k.GetCurrentThread.restype = wintypes.HANDLE
    a.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
    a.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    name = P.new_name("PlanetPDF-test-level-")
    assert open_pipe(name, 50) is None  # (nothing listens there)
    server = k.CreateNamedPipeW(PIPE_PREFIX + name, 0x3, 0, 1, 4096, 4096, 0, None)  # (duplex, one instance)
    assert server not in (None, wintypes.HANDLE(-1).value)
    client = open_pipe(name, 500, overlapped=False)
    assert client is not None
    try:
        done = wintypes.DWORD()
        assert k.WriteFile(client, b"x", 1, ctypes.byref(done), None)
        k.ConnectNamedPipe(server, None)  # (already connected)
        buffer = ctypes.create_string_buffer(1)
        assert k.ReadFile(server, buffer, 1, ctypes.byref(done), None)
        assert a.ImpersonateNamedPipeClient(server)
        try:
            token, level, size = wintypes.HANDLE(), wintypes.DWORD(99), wintypes.DWORD()
            opened = a.OpenThreadToken(k.GetCurrentThread(), 0x0008, True, ctypes.byref(token))  # TOKEN_QUERY
            if opened:
                assert a.GetTokenInformation(token, 9, ctypes.byref(level), ctypes.sizeof(level), ctypes.byref(size))
                close_handle(token.value)
        finally:
            a.RevertToSelf()
        assert opened and level.value == 1  # SecurityIdentification (2 = impersonation: Qt's own open)
    finally:
        close_handle(client)
        close_handle(server)
