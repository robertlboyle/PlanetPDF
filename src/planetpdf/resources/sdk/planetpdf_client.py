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
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import sys
from pathlib import Path


class PlanetPDFError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message} ({code})")
        self.code = code
        self.message = message


def default_address() -> str:
    """Where the running PlanetPDF (or ``planetpdf serve``) listens, from its api.json."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    info = base / "Planet Networks" / "PlanetPDF" / "api.json"
    try:
        return json.loads(info.read_text(encoding="utf-8"))["address"]
    except (OSError, ValueError, KeyError):
        raise PlanetPDFError(-32099, "PlanetPDF isn't running (start it, or run: planetpdf serve).") from None


class _Pipe:
    """A Windows named pipe, or a Unix socket, read a line at a time."""

    def __init__(self, address: str) -> None:
        if sys.platform == "win32":
            self._file = open(address, "r+b", buffering=0)
            self._read, self._write = self._file.read, self._file.write
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(address)
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
                 token_file: str | os.PathLike | None = None, address: str | None = None) -> None:
        self.name, self.publisher = name, publisher
        self.token_file = Path(token_file) if token_file else None
        if token is None and self.token_file and self.token_file.is_file():
            token = self.token_file.read_text(encoding="utf-8").strip()
        self.token = token
        self._pipe = _Pipe(address or default_address())
        self._ids = itertools.count(1)
        hello = self.call("planetpdf.hello", token=token) if token else \
            self.call("planetpdf.hello", name=name, publisher=publisher)
        if "token" in hello:
            self.token = hello["token"]
            if self.token_file:
                self.token_file.write_text(self.token, encoding="utf-8")
        self.version = hello.get("version")

    @classmethod
    def from_environment(cls) -> PlanetPDF:
        """A plugin PlanetPDF started: its address and token are in the environment."""
        return cls(os.environ.get("PLANETPDF_PLUGIN_NAME", "plugin"), token=os.environ["PLANETPDF_API_TOKEN"],
                   address=os.environ["PLANETPDF_API_ADDRESS"])

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
