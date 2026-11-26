"""PlanetPDF's local API (2026-10-18): other programs and plugins use PlanetPDF's functions.

**Protocol:** JSON-RPC 2.0, one JSON object per line (UTF-8, ending in "\\n"), over a local
connection only: a named pipe on Windows, a socket file on the Mac and Linux, open to this user
alone (``ui/api_server.py``; never a network port). Where it is: ``api.json`` in PlanetPDF's local
app data folder (``{"address": …, "key": …, "protocol": "jsonrpc-lines", "version": 1}``), or
``PLANETPDF_API_ADDRESS`` (and ``PLANETPDF_API_KEY``) in a plugin's environment. The address is
made at random for each run, and a program can have the API prove it holds the key before saying
anything else (``planetpdf.prove`` with a nonce → the keyed hash of ``core/local_proof.py``), so
nothing another user of the computer puts in PlanetPDF's place is taken for it.

**Who is calling:** a program first calls ``planetpdf.hello`` with its name (and publisher); the
first time it gets a token, which it keeps and sends with every later ``hello``. Every program is
listed in Preferences ▸ Scripting & Plugins, where it can be blocked. **A new program is asked
about** (security review 2026-11-21: any program running as the user had full use with no prompt,
and its name was only what it said): the server looks up which program is really at the other end
of the connection (``Connection.program``, from the operating system) and the user allows or
refuses it once; its key then works only for that program (another program using it is asked
about again). **Signing** asks as well: the first time a program signs, and it can be let sign
from then on ("Always Allow"), revocable. Plugins are started by PlanetPDF and were turned on by
the user, so they aren't asked about.

**Methods:** ``planetpdf.hello``, ``planetpdf.commands`` (what ``core.commands`` offers, with each
parameter), ``planetpdf.cancel`` ({"id": request id}), every command by its name (``"export"``,
``"redact"``, …) with its parameters as named params, and what the program hosting the API adds
(``ui.open``, ``ui.current_document``…). Long work sends ``progress`` notifications
(``{"id", "done", "total", "stage", "text"}``) before its result.

**Limits:** ``RATE`` requests a second per program (bursts of ``BURST``), ``JOBS`` at once; every
call is written to the audit log (``<profile>/logs/api-audit.log``: time, program, method, the
files named, how it ended; never passwords).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from planetpdf import __version__
from planetpdf.core import commands, local_proof
from planetpdf.core.document import PdfError

log = logging.getLogger(__name__)

PROTOCOL, PROTOCOL_VERSION = "jsonrpc-lines", 1
RATE, BURST, JOBS = 10.0, 30, 2

# JSON-RPC errors (the -32000 range is ours)
PARSE_ERROR, INVALID_REQUEST, NO_METHOD, BAD_PARAMS = -32700, -32600, -32601, -32602
FAILED, NOT_IDENTIFIED, NOT_APPROVED, BLOCKED, NOT_ALLOWED, TOO_MANY, CANCELED = (
    -32000, -32001, -32002, -32003, -32004, -32005, -32006)
DENIED = -32007  # the user didn't allow this program


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- the programs that have called -----------------------------------------------------------------


@dataclass
class Client:
    id: str
    name: str
    publisher: str = ""
    token_hash: str = ""
    first_seen: str = ""
    last_used: str = ""
    can_sign: bool = False  # the user chose "Always Allow" for signing
    blocked: bool = False
    plugin: str = ""  # a plugin's id: its token is made by PlanetPDF when it starts the plugin
    permissions: list[str] = field(default_factory=list)  # a plugin's (from its manifest); programs: all
    program: str = ""  # the program file the user allowed (as the operating system named it)

    def may(self, permission: str) -> bool:
        return not self.plugin or permission in self.permissions

    @property
    def label(self) -> str:
        return f"{self.name} ({self.publisher})" if self.publisher else self.name


class ClientStore:
    """``<profile>/api/clients.json``: every program, with its token's hash (never the token),
    sealed with a key the operating system keeps for the user (``storage.user_key``), so a program
    can't add itself by writing the file (security review 2026-11-25). A list whose seal doesn't
    match is ignored (and said so in the log): every program is asked about again."""

    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / "clients.json"
        self._lock = threading.Lock()
        self._key: bytes | None = None

    def _seal(self, items: list[dict]) -> str:
        import hmac

        if self._key is None:
            from planetpdf.core.storage.user_key import user_key

            self._key = user_key(self.path.parent, "api-seal")
        text = json.dumps(items, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()

    def load(self) -> list[Client]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("clients", []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not secrets.compare_digest(str(data.get("seal", "")), self._seal(items)):
            if items:
                log.warning("The list of programs allowed to use PlanetPDF was changed outside PlanetPDF: it's ignored")
            return []
        known = set(Client.__dataclass_fields__)
        return [Client(**{k: v for k, v in item.items() if k in known}) for item in items if isinstance(item, dict)]

    def save(self, clients: list[Client]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = [asdict(c) for c in clients]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"clients": items, "seal": self._seal(items)}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def change(self, client_id: str, **values) -> Client | None:
        with self._lock:
            clients = self.load()
            for c in clients:
                if c.id == client_id:
                    for k, v in values.items():
                        setattr(c, k, v)
                    self.save(clients)
                    return c
        return None

    def remove(self, client_id: str) -> None:
        with self._lock:
            self.save([c for c in self.load() if c.id != client_id])

    def by_token(self, token: str) -> Client | None:
        wanted = _hash(token)
        return next((c for c in self.load() if secrets.compare_digest(c.token_hash, wanted)), None)

    def register(self, name: str, publisher: str = "", *, plugin: str = "",
                 permissions: list[str] | None = None, program: str = "") -> tuple[Client, str]:
        """A new program: its record and its token (returned once, never stored)."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            clients = self.load()
            if plugin:  # (a plugin keeps one record: a new token each time PlanetPDF starts it)
                existing = next((c for c in clients if c.plugin == plugin), None)
                if existing is not None:
                    existing.token_hash, existing.name, existing.publisher = _hash(token), name, publisher
                    existing.permissions = list(permissions or [])
                    self.save(clients)
                    return existing, token
            client = Client(id=secrets.token_hex(8), name=name[:100], publisher=publisher[:100],
                            token_hash=_hash(token), first_seen=now_text(), plugin=plugin,
                            permissions=list(permissions or []), program=program)
            clients.append(client)
            self.save(clients)
        return client, token


class AuditLog:
    """One JSON line per call. Never passwords: only the parameters that name files."""

    MAX = 5 * 1024 * 1024

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, **entry) -> None:
        entry = {"time": now_text(), **entry}
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.MAX:
                    self.path.replace(self.path.with_suffix(".old.log"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except OSError:
                pass

    def tail(self, count: int = 200) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-count:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


class RateLimit:
    def __init__(self, rate: float = RATE, burst: int = BURST) -> None:
        self.rate, self.burst = rate, burst
        self.tokens, self.at = float(burst), time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


# --- a connection ------------------------------------------------------------------------------------


@dataclass
class Connection:
    """One program's connection: who it is (after hello), its limits, its running jobs."""

    client: Client | None = None
    program: str = ""  # the program at the other end, from the operating system ("" = unknown)
    limit: RateLimit = field(default_factory=RateLimit)
    jobs: dict = field(default_factory=dict)  # request id -> threading.Event (set = cancel)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _files_named(cmd: commands.Command | None, params: dict) -> list[str]:
    if cmd is None or not isinstance(params, dict):
        return []
    out = []
    for p in cmd.params:
        value = params.get(p.name, params.get(p.name.replace("_", "-")))
        if value is None or p.kind not in ("file", "files", "out", "folder"):
            continue
        out.extend(str(v) for v in (value if isinstance(value, list) else [value]))
    return out


def same_program(a: str, b: str) -> bool:
    """The same program file? (Windows' paths ignore case.)"""
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class ApiService:
    """Answers requests. ``ask_to_sign(client, description)`` asks the user (True, False or
    "always"); ``ask_to_connect(name, publisher, program, before)`` asks whether a new program (or,
    with ``before``, a known program's key used by another program file) may use PlanetPDF (True /
    False; None = allowed without asking: tests); ``extra`` adds methods of the program hosting the
    API (``name -> fn(conn, params)``, each with the permission it needs)."""

    def __init__(self, profile_root: Path, ask_to_sign: Callable[[Client, str], bool | str] | None = None,
                 extra: dict[str, tuple[str, Callable[[Connection, dict], object]]] | None = None,
                 ask_to_connect: Callable[[str, str, str, str], bool] | None = None) -> None:
        self.profile_root = Path(profile_root)
        self.clients = ClientStore(self.profile_root / "api")
        self.audit = AuditLog(self.profile_root / "logs" / "api-audit.log")
        self.ask_to_sign = ask_to_sign
        self.ask_to_connect = ask_to_connect
        self.extra = dict(extra or {})
        self.pipe_key = ""  # (whoever serves the API sets it: the key its api.json gives; planetpdf.prove)
        self._refused: set[str] = set()  # (programs refused while this runs: not asked again)
        self._asking = threading.Lock()  # (one question at a time)

    # (one line in, the replies out through ``send``; may block for a long job: call it off the GUI thread)
    def handle_line(self, conn: Connection, line: bytes | str, send: Callable[[dict], None]) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            send(_error(None, PARSE_ERROR, "That isn't JSON."))
            return
        if isinstance(message, list):  # (batches aren't supported: one request per line)
            send(_error(None, INVALID_REQUEST, "Send one request per line."))
            return
        reply = self.handle(conn, message, send)
        if reply is not None:
            send(reply)

    def handle(self, conn: Connection, message: dict, send: Callable[[dict], None]) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST,
                          'A request needs "jsonrpc": "2.0" and a "method".')
        rid, method, params = message.get("id"), message["method"], message.get("params") or {}
        if not isinstance(params, dict):
            return _error(rid, BAD_PARAMS, "Params must be an object (named parameters).")
        started = time.monotonic()
        cmd = commands.COMMANDS.get(method)
        try:
            result = self._dispatch(conn, rid, method, params, cmd, send)
        except ApiError as e:
            self._log(conn, method, cmd, params, f"refused: {e}", started)
            return None if rid is None else _error(rid, e.code, str(e))
        except commands.NotApproved as e:
            self._log(conn, method, cmd, params, "signing not approved", started)
            return None if rid is None else _error(rid, NOT_APPROVED, str(e))
        except commands.CommandError as e:
            self._log(conn, method, cmd, params, f"bad parameters: {e}", started)
            return None if rid is None else _error(rid, BAD_PARAMS, str(e))
        except (PdfError, OSError, ValueError) as e:
            self._log(conn, method, cmd, params, f"failed: {e}", started)
            return None if rid is None else _error(rid, FAILED, str(e))
        if method not in ("planetpdf.hello", "planetpdf.prove"):
            self._log(conn, method, cmd, params, "ok", started)
        return None if rid is None else {"jsonrpc": "2.0", "id": rid, "result": result}

    def _log(self, conn, method, cmd, params, outcome, started) -> None:
        self.audit.write(program=conn.client.label if conn.client else "(not identified)",
                         program_id=conn.client.id if conn.client else "", method=method,
                         files=_files_named(cmd, params), outcome=outcome,
                         seconds=round(time.monotonic() - started, 2))

    def _dispatch(self, conn, rid, method, params, cmd, send):
        if method == "planetpdf.prove":
            # Before anything else, a program can make sure this is PlanetPDF and not something
            # that took its place: only what read api.json's key can answer (core/local_proof.py)
            nonce = params.get("nonce")
            if not self.pipe_key or not isinstance(nonce, str) or not 8 <= len(nonce) <= 200:
                raise ApiError(BAD_PARAMS, "planetpdf.prove takes a nonce: a random string of your own.")
            return {"proof": local_proof.proof(self.pipe_key, local_proof.SERVER, nonce)}
        if method == "planetpdf.hello":
            return self._hello(conn, params)
        if conn.client is None:
            raise ApiError(NOT_IDENTIFIED, "Call planetpdf.hello first.")
        current = next((c for c in self.clients.load() if c.id == conn.client.id), None)
        if current is None or current.blocked:
            raise ApiError(BLOCKED, "This program has been blocked in PlanetPDF's Preferences.")
        conn.client = current
        if not conn.limit.take():
            raise ApiError(TOO_MANY, f"Too many requests: at most {RATE:g} a second.")
        if method == "planetpdf.commands":
            return {"commands": commands.describe(), "permissions": commands.PERMISSIONS}
        if method == "planetpdf.cancel":
            event = conn.jobs.get(params.get("id"))
            if event is not None:
                event.set()
            return {"canceled": event is not None}
        if method in self.extra:
            permission, fn = self.extra[method]
            self._allowed(current, permission)
            return fn(conn, params)
        if cmd is None:
            raise ApiError(NO_METHOD, f"There's no method {method!r} (planetpdf.commands lists them).")
        self._allowed(current, cmd.permission)
        return self._run(conn, rid, cmd, params, send)

    @staticmethod
    def _allowed(client: Client, permission: str) -> None:
        if not client.may(permission):
            raise ApiError(NOT_ALLOWED, f"This plugin didn't ask for the {permission!r} permission.")

    def _hello(self, conn: Connection, params: dict) -> dict:
        token = params.get("token")
        if token:
            client = self.clients.by_token(str(token))
            if client is None:
                raise ApiError(NOT_IDENTIFIED, "That token isn't known (it may have been removed in Preferences).")
            if client.blocked:
                raise ApiError(BLOCKED, "This program has been blocked in PlanetPDF's Preferences.")
            if not client.plugin and self.ask_to_connect is not None and not same_program(client.program, conn.program):
                # (another program file is using this key: the user decides again)
                self._ask(conn, client.name, client.publisher, before=client.program or "(not known)")
                self.clients.change(client.id, program=conn.program)
            conn.client = self.clients.change(client.id, last_used=now_text())
            self.audit.write(program=client.label, program_id=client.id, method="planetpdf.hello", outcome="ok")
            return {"version": __version__, "protocol": PROTOCOL_VERSION, "program": client.id}
        name = str(params.get("name") or "").strip()
        if not name:
            raise ApiError(BAD_PARAMS, "Say who you are: planetpdf.hello with your program's name.")
        publisher = str(params.get("publisher") or "").strip()
        self._ask(conn, name, publisher)
        client, token = self.clients.register(name, publisher, program=conn.program)
        conn.client = self.clients.change(client.id, last_used=now_text())
        self.audit.write(program=client.label, program_id=client.id, method="planetpdf.hello",
                         outcome="new program")
        return {"version": __version__, "protocol": PROTOCOL_VERSION, "program": client.id, "token": token,
                "note": "Keep this token and send it with planetpdf.hello next time."}

    def _ask(self, conn: Connection, name: str, publisher: str, before: str = "") -> None:
        """May this program use PlanetPDF? Raises ``ApiError(DENIED)`` when not."""
        if self.ask_to_connect is None:
            return
        key = os.path.normcase(conn.program) if conn.program else ""
        with self._asking:
            allowed = key not in self._refused and bool(
                self.ask_to_connect(name[:100], publisher[:100], conn.program, before))
            if not allowed:
                self._refused.add(key)
        if not allowed:
            self.audit.write(program=name[:100], program_file=conn.program, method="planetpdf.hello",
                             outcome="not allowed by the user")
            raise ApiError(DENIED, "The user didn't allow this program to use PlanetPDF.")

    def _approve(self, conn: Connection, description: str) -> bool:
        client = conn.client
        if client.can_sign:
            return True
        if self.ask_to_sign is None:
            return False
        answer = self.ask_to_sign(client, description)
        if answer == "always":
            conn.client = self.clients.change(client.id, can_sign=True) or client
            return True
        return answer is True

    def _run(self, conn: Connection, rid, cmd: commands.Command, params: dict, send) -> dict:
        cancel = threading.Event()
        with conn.lock:
            if len(conn.jobs) >= JOBS:
                raise ApiError(TOO_MANY, f"At most {JOBS} jobs at once: wait for one to finish.")
            if rid is not None:
                conn.jobs[rid] = cancel
            else:
                conn.jobs[object()] = cancel

        def progress(done, total, stage=""):
            from planetpdf.core.progress import stage_text

            if rid is not None:
                send({"jsonrpc": "2.0", "method": "progress",
                      "params": {"id": rid, "done": done, "total": total, "stage": stage,
                                 "text": stage_text(stage or "Working on item", done, total)}})
            return not cancel.is_set()

        context = commands.Context(progress=progress, profile_root=self.profile_root,
                                   approve_signing=lambda description: self._approve(conn, description))
        try:
            result = commands.run(cmd.name, params, context)
        finally:
            with conn.lock:
                for key, event in list(conn.jobs.items()):
                    if event is cancel:
                        del conn.jobs[key]
        if cancel.is_set():
            raise ApiError(CANCELED, "Canceled.")
        return result


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
