"""PlanetPDF's command line: ``planetpdf <command> [options]`` (user 2026-10-18: every function
scriptable from a command line, with script-friendly output and real exit codes).

The commands are ``core.commands`` (the same list the local API serves); this module only turns
them into command-line options, prints results and progress, and asks for the approval signing
needs (a PlanetPDF window: a script can't answer it through its input).

Output: a readable summary, or the command's result as JSON with ``--json``. Progress goes to
stderr ("Reading page 3 of 10"), never into the result; ``--quiet`` turns it off.

Exit codes: 0 done · 1 it failed (the message says why) · 2 the command was used wrongly ·
3 signing wasn't approved · 4 done, but a check failed (redaction verification, compliance) ·
130 stopped (Ctrl+C).

Passwords: ``--password -`` asks for it (not echoed); ``--password env:NAME`` reads the
environment variable NAME. A password typed straight on the command line can be seen by other
programs on this computer.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

OK, FAILED, USAGE, NOT_APPROVED, CHECK_FAILED, STOPPED = 0, 1, 2, 3, 4, 130


def is_command(argv: list[str]) -> bool:
    """Whether ``argv`` (sys.argv) asks for a command rather than opening files."""
    if len(argv) < 2:
        return False
    first = argv[1]
    if first in ("help", "commands", "serve", "--help", "-h", "--version"):
        return True
    from planetpdf.core.commands import COMMANDS

    return first in COMMANDS and not os.path.exists(first)


def _console() -> None:
    """A windowed program on Windows has no console: use the one it was started from."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    else:  # (nothing to print to; the exit code still says how it went)
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")


# --- options ------------------------------------------------------------------------------------


def _option(p) -> str:
    return "--" + p.name.replace("_", "-")


def _add(parser: argparse.ArgumentParser, p) -> None:
    help_text = p.help + (f" (default: {p.default})" if p.default not in (None, "", False) else "")
    if p.kind == "flag":
        parser.add_argument(_option(p), action="store_true", dest=p.name, help=help_text)
        return
    many = p.kind in ("files", "texts")
    if p.positional:
        parser.add_argument(p.name, nargs="+" if many else None, help=help_text,
                            metavar=p.name.upper())
        return
    kwargs = {"dest": p.name, "help": help_text, "metavar": p.kind.upper() if p.kind != "choice" else None}
    if p.choices:
        kwargs["choices"] = list(p.choices)
        kwargs.pop("metavar")
    if many:
        kwargs["nargs"] = "+"
        kwargs["action"] = "extend"
    if p.required:
        kwargs["required"] = True
    parser.add_argument(_option(p), **kwargs)


def build_parser() -> argparse.ArgumentParser:
    from planetpdf import __version__
    from planetpdf.core.commands import COMMANDS

    parser = argparse.ArgumentParser(
        prog="planetpdf", description="PlanetPDF from the command line. Run without a command to open the program.",
        epilog="planetpdf help COMMAND shows a command's options; planetpdf commands --json lists them all.")
    parser.add_argument("--version", action="version", version=f"PlanetPDF {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Print the result as JSON")
    common.add_argument("--quiet", action="store_true", help="Don't show progress")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for group in dict.fromkeys(c.group for c in COMMANDS.values()):
        for cmd in (c for c in COMMANDS.values() if c.group == group):
            p = sub.add_parser(cmd.name, help=f"[{group}] {cmd.summary}", description=cmd.summary, parents=[common])
            for param in cmd.params:
                _add(p, param)
    p = sub.add_parser("commands", help="List the commands", parents=[common])
    sub.add_parser("serve", help="Serve PlanetPDF's local API without a window, for other programs (Ctrl+C stops it)")
    p = sub.add_parser("help", help="Show a command's options")
    p.add_argument("topic", nargs="?")
    return parser


# --- output -------------------------------------------------------------------------------------


class Progress:
    """Counted progress on stderr: one updating line on a terminal, else a line per change
    (at most every half second)."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.tty = sys.stderr is not None and sys.stderr.isatty()
        self.last = ""
        self.at = 0.0

    def __call__(self, done: int, total: int, stage: str = "") -> bool:
        if self.quiet:
            return True
        from planetpdf.core.progress import stage_text

        text = stage_text(stage or "Working on item", done, total)
        now = time.monotonic()
        if text != self.last and (self.tty or now - self.at > 0.5):
            self.last, self.at = text, now
            if self.tty:
                sys.stderr.write("\r" + text[:100].ljust(100))
            else:
                sys.stderr.write(text + "\n")
            sys.stderr.flush()
        return True

    def finish(self) -> None:
        if self.tty and self.last and not self.quiet:
            sys.stderr.write("\r" + " " * 100 + "\r")
            sys.stderr.flush()


def _show(value, indent: int = 0) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                print(f"{pad}{key}:")
                _show(item, indent + 1)
            else:
                print(f"{pad}{key}: {'' if item is None else item}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                first = True
                for key, v in item.items():
                    print(f"{pad}{'- ' if first else '  '}{key}: {'' if v is None else v}")
                    first = False
            else:
                print(f"{pad}- {item}")
    else:
        print(f"{pad}{value}")


def _secret(value: str | None, what: str) -> str | None:
    if value == "-":
        return getpass.getpass(f"{what}: ")
    if value and value.startswith("env:"):
        name = value[4:]
        if name not in os.environ:
            raise SystemExit(f"planetpdf: the environment variable {name} isn't set.")
        return os.environ[name]
    return value


def _check_failed(name: str, result: dict) -> bool:
    return (name == "redact" and result.get("verified") is False) or \
           (name == "check-compliance" and not result.get("passed", True))


# --- running ------------------------------------------------------------------------------------


def _profile_root():
    try:
        from PySide6.QtCore import QCoreApplication

        from planetpdf import APP_NAME, ORG_NAME
        from planetpdf.ui import paths

        QCoreApplication.setOrganizationName(ORG_NAME)
        QCoreApplication.setApplicationName(APP_NAME)
        return paths.profile().root
    except Exception:  # (no profile: imported trusted certificates just aren't used)
        return None


def _approve(description: str) -> bool:
    from planetpdf.ui.approval import ask_to_sign

    return ask_to_sign("A command-line script", description)


def main(argv: list[str]) -> int:
    _console()
    for stream in (sys.stdout, sys.stderr):  # (piped output is UTF-8, whatever the code page)
        if stream is not None and not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from planetpdf.core import commands
    from planetpdf.core.document import PdfError

    parser = build_parser()
    try:
        ns = parser.parse_args(argv[1:])
    except SystemExit as e:  # (argparse: --help, --version, or a usage error it already printed)
        return OK if e.code in (0, None) else USAGE
    if ns.command in (None, "help"):
        topic = getattr(ns, "topic", None)
        if topic:
            if topic not in commands.COMMANDS:
                print(f"planetpdf: there's no command {topic!r}.", file=sys.stderr)
                return USAGE
            parser.parse_args([topic, "--help"])
        parser.print_help()
        return OK
    if ns.command == "commands":
        if ns.json:
            print(json.dumps(commands.describe(), indent=2))
        else:
            for cmd in commands.COMMANDS.values():
                print(f"{cmd.name:20} {cmd.summary}")
        return OK
    if ns.command == "serve":
        from planetpdf.ui import api_server

        root = _profile_root()
        if root is None:
            print("planetpdf: PlanetPDF's profile folder couldn't be found.", file=sys.stderr)
            return FAILED
        return api_server.serve(root, log=lambda text: print(text, file=sys.stderr, flush=True))
    cmd = commands.COMMANDS[ns.command]
    raw = {}
    for p in cmd.params:
        value = getattr(ns, p.name, None)
        if p.kind == "secret":
            value = _secret(value, p.help)
        if value not in (None, False, []):
            raw[p.name] = value
    progress = Progress(ns.quiet)
    context = commands.Context(progress=progress, approve_signing=_approve,
                               profile_root=_profile_root() if cmd.name == "signatures" else None)
    try:
        result = commands.run(cmd.name, raw, context)
    except KeyboardInterrupt:
        progress.finish()
        print("planetpdf: stopped.", file=sys.stderr)
        return STOPPED
    except commands.NotApproved as e:
        progress.finish()
        print(f"planetpdf: {e}", file=sys.stderr)
        return NOT_APPROVED
    except commands.CommandError as e:
        progress.finish()
        print(f"planetpdf {cmd.name}: {e}", file=sys.stderr)
        return USAGE
    except (PdfError, OSError, ValueError) as e:
        progress.finish()
        print(f"planetpdf {cmd.name}: {e}", file=sys.stderr)
        return FAILED
    progress.finish()
    if ns.json:
        print(json.dumps(result, indent=2, default=str))
    elif cmd.name == "text" and "text" in result:
        sys.stdout.write(result["text"])
    else:
        _show(result)
    return CHECK_FAILED if _check_failed(cmd.name, result) else OK
