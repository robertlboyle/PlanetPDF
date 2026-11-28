"""A small client for PlanetPDF's local API (Python 3.9+, standard library only). Copy this file
into your program or plugin.

    from planetpdf_client import PlanetPDF

    with PlanetPDF("Invoice Mailer", publisher="Accounting", token_file="planetpdf.token") as pdf:
        print(pdf.call("info", file="invoices/1001.pdf"))
        pdf.call("compress", file="big.pdf", out="small.pdf",
                 on_progress=lambda p: print(p["text"]))

A plugin started by PlanetPDF uses ``PlanetPDF.from_environment()`` (PlanetPDF gives it the
address and its token). ``pdf.call("planetpdf.commands")`` lists every command and parameter.
Errors come back as ``PlanetPDFError`` (``.code``: -32002 signing not approved, -32003 blocked,
-32005 too many requests, -32602 bad parameters, -32000 the work failed).

Before it says who it is, the client makes sure it is talking to PlanetPDF: ``api.json`` (a file
only you can read) gives the address and a key, and the client has the other end prove it holds
that key (``planetpdf.prove``). On a computer several people use, a program of someone else's
that took PlanetPDF's place can't, and gets nothing (-32097). On Windows the pipe is also opened so
that the other end can learn who is calling but can't act as you.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import os
import secrets
import socket
import sys
from pathlib import Path


class PlanetPDFError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message} ({code})")
        self.code = code
        self.message = message


def default_server() -> tuple[str, str]:
    """Where the running PlanetPDF (or ``planetpdf serve``) listens and the key it proves itself
    with, from its api.json ("" for the key: a PlanetPDF from before 1.0.8)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    info = base / "Planet Networks" / "PlanetPDF" / "api.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        return data["address"], str(data.get("key") or "")
    except (OSError, ValueError, KeyError, TypeError):
        raise PlanetPDFError(-32099, "PlanetPDF isn't running (start it, or run: planetpdf serve).") from None


def default_address() -> str:
    return default_server()[0]


def proof(key: str, nonce: str) -> str:
    """What PlanetPDF answers to ``nonce`` when it holds ``key``."""
    return hmac.new(key.encode("utf-8"), f"PlanetPDF server|{nonce}".encode("utf-8"), hashlib.sha256).hexdigest()


def _open_windows_pipe(address: str):
    """The pipe as a file, opened so the other end can identify the caller but not act as them
    (SECURITY_IDENTIFICATION; Python's own open allows impersonation)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    generic_read_write, open_existing = 0x80000000 | 0x40000000, 3
    sqos_present, identification, pipe_busy = 0x00100000, 0x00010000, 231
    invalid = wintypes.HANDLE(-1).value
    for _ in range(20):
        handle = kernel32.CreateFileW(address, generic_read_write, 0, None, open_existing,
                                      sqos_present | identification, None)
        if handle not in (None, 0, invalid):
            return os.fdopen(msvcrt.open_osfhandle(handle, os.O_BINARY), "r+b", buffering=0)
        error = ctypes.get_last_error()
        if error != pipe_busy:
            raise PlanetPDFError(-32099, "PlanetPDF isn't running (start it, or run: planetpdf serve).")
        kernel32.WaitNamedPipeW(address, 500)  # (every connection is taken: wait for a free one)
    raise PlanetPDFError(-32099, "PlanetPDF is busy: try again in a moment.")


class _Pipe:
    """A Windows named pipe, or a Unix socket, read a line at a time."""

    def __init__(self, address: str) -> None:
        if sys.platform == "win32":
            self._file = _open_windows_pipe(address)
            self._read, self._write = self._file.read, self._file.write
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self._sock.connect(address)
            except OSError:
                self._sock.close()
                raise PlanetPDFError(-32099, "PlanetPDF isn't running (start it, or run: planetpdf serve).") \
                    from None
            self._read, self._write = (lambda n: self._sock.recv(n)), self._sock.sendall
        self._buffer = b""

    def send(self, message: dict) -> None:
        self._write((json.dumps(message) + "\n").encode("utf-8"))

    def receive(self) -> dict:
        while b"\n" not in self._buffer:
            chunk = self._read(65536)
            if not chunk:
                raise PlanetPDFError(-32098, "PlanetPDF closed the connection.")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return json.loads(line)

    def close(self) -> None:
        (self._file if sys.platform == "win32" else self._sock).close()


class PlanetPDF:
    def __init__(self, name: str, *, publisher: str = "", token: str | None = None,
                 token_file: str | os.PathLike | None = None, address: str | None = None,
                 key: str | None = None) -> None:
        self.name, self.publisher = name, publisher
        self.token_file = Path(token_file) if token_file else None
        if token is None and self.token_file and self.token_file.is_file():
            token = self.token_file.read_text(encoding="utf-8").strip()
        self.token = token
        if address is None:
            address, known_key = default_server()
            key = known_key if key is None else key
        self._pipe = _Pipe(address)
        self._ids = itertools.count(1)
        if not key:
            # (without a key nothing proves this is PlanetPDF, and the token would go to whatever
            # answered: security review 2026-11-27, the client sent its token unchecked)
            self._pipe.close()
            raise PlanetPDFError(-32098, "There's no key to check that it is PlanetPDF at that address, so nothing "
                                         "was sent. Use the address and key in PlanetPDF's api.json (PlanetPDF "
                                         "1.0.8 or later), or PLANETPDF_API_KEY for a plugin.")
        self._prove(key)  # (first of all: is this PlanetPDF? Nothing else is said until it has proved it)
        hello = self.call("planetpdf.hello", token=token) if token else \
            self.call("planetpdf.hello", name=name, publisher=publisher)
        if "token" in hello:
            self.token = hello["token"]
            if self.token_file:
                self.token_file.write_text(self.token, encoding="utf-8")
        self.version = hello.get("version")

    def _prove(self, key: str) -> None:
        nonce = secrets.token_hex(16)
        try:
            answer = self.call("planetpdf.prove", nonce=nonce).get("proof")
        except (PlanetPDFError, ValueError, AttributeError, OSError):
            answer = None
        if not isinstance(answer, str) or not hmac.compare_digest(answer, proof(key, nonce)):
            self._pipe.close()
            raise PlanetPDFError(-32097, "What answered at PlanetPDF's address couldn't prove it is PlanetPDF, "
                                         "so nothing was sent to it. Start PlanetPDF again.")

    @classmethod
    def from_environment(cls) -> PlanetPDF:
        """A plugin PlanetPDF started: its address, key and token are in the environment."""
        return cls(os.environ.get("PLANETPDF_PLUGIN_NAME", "plugin"), token=os.environ["PLANETPDF_API_TOKEN"],
                   address=os.environ["PLANETPDF_API_ADDRESS"], key=os.environ.get("PLANETPDF_API_KEY", ""))

    def call(self, method: str, on_progress=None, **params):
        rid = next(self._ids)
        self._pipe.send({"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": {k: v for k, v in params.items() if v is not None}})
        while True:
            message = self._pipe.receive()
            if message.get("method") == "progress":
                if on_progress is not None and message["params"].get("id") == rid:
                    on_progress(message["params"])
                continue
            if message.get("id") != rid:
                continue
            if "error" in message:
                raise PlanetPDFError(message["error"]["code"], message["error"]["message"])
            return message["result"]

    def close(self) -> None:
        self._pipe.close()

    def __enter__(self) -> PlanetPDF:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
