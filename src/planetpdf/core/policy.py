"""Settings an administrator makes for everyone on a computer (security list 2026-11-26, item 5).

Until now each user decided everything for themselves: update checks, the local API, plugins,
turning web pages and Office files into PDFs, the AI text reader, downloading voices, remembered
text, checking signers' certificates online. On a computer many people share, IT needs to decide
some of that once. A file named ``policy.json`` does it:

    {
      "settings": {"check_updates_on_start": false, "update_every_days": 0, "check_revocation": true},
      "off": ["plugins", "web_capture"]
    }

- ``settings``: a value for each setting named (``SETTINGS`` lists the ones a policy may set). The
  user's own choice is kept in their settings file but not used while the policy is there, and
  Preferences shows the control grayed, "Set by your administrator".
- ``off``: whole features nobody on the computer gets (``FEATURES``).

**Where:** beside PlanetPDF.exe in the install folder on Windows; ``/Library/Application
Support/PlanetPDF/policy.json`` on a Mac; ``/etc/planetpdf/policy.json`` on Linux.

**Only an administrator's file counts.** A policy can also loosen things (turn the local API on),
so one that anybody else could have written or could change is ignored, and PlanetPDF says why
(``Policy.problems``; ``planetpdf policy`` on the command line shows them): on Windows the file
and its folder must be closed to everyone but administrators (``core/windows_acl.py``), elsewhere
owned by root and not writable by others.

A policy is a convenience for managing a shared computer, not a wall: someone who can run their
own copy of PlanetPDF isn't held by it. Pure Python, no Qt.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

POLICY_FILE = "policy.json"
MAC_FOLDER = Path("/Library/Application Support/PlanetPDF")
LINUX_FOLDER = Path("/etc/planetpdf")
MAX_BYTES = 256 * 1024

# The settings a policy may set (core/storage/settings.py), and what each one is, for the
# ``planetpdf policy`` listing and the manual.
SETTINGS = {
    "check_updates_on_start": "Check for updates when PlanetPDF starts (true / false)",
    "update_every_days": "Check for updates every this many days while it runs (0 = never)",
    "api_enabled": "The local API for other programs and plugins (true / false)",
    "check_revocation": "Ask certificate authorities online whether a signer's certificate was revoked (true / false)",
    "trust_windows_roots": "Also trust the certificates Windows (or macOS) trusts (true / false)",
    "timestamp_url": "The timestamp server for digital signatures (an address, or \"\")",
    "remember_text": "Remember added text and suggest it while typing (true / false)",
    "remember_private_numbers": "Also remember text with Social Security, card, or account numbers (true / false)",
    "autosave_minutes": "Minutes between recovery copies of unsaved work (0 = off)",
    "company_name": "The company name on stamps",
    "print_folder": "The folder printouts from the PlanetPDF printer are saved in without asking (\"\" = ask)",
    "print_name_template": "The name printouts get, for example {document} {date}",
}

# Whole features a policy can turn off, and how PlanetPDF names each when it says it is off.
FEATURES = {
    "updates": "Checking for updates and installing them from inside PlanetPDF",
    "plugins": "Plugins",
    "web_capture": "Making a PDF from a web page",
    "office_conversion": "Turning Word, Excel, and PowerPoint files into PDFs, and saving as Word 97-2003",
    "ai_reader": "AI text reading for exports",
    "voice_download": "Downloading the natural reading voices",
}


@dataclass(frozen=True)
class Policy:
    path: Path | None = None  # the file in force (None: there is none, or it was ignored)
    settings: dict = field(default_factory=dict)  # setting name -> the value everyone gets
    off: frozenset = frozenset()  # features turned off
    problems: tuple[str, ...] = ()  # what was ignored, and why
    found: Path | None = None  # a policy file that is there, in force or not

    def locks(self, name: str) -> bool:
        return name in self.settings

    def allows(self, feature: str) -> bool:
        return feature not in self.off

    @property
    def in_force(self) -> bool:
        return self.path is not None


def policy_path() -> Path | None:
    """Where this copy of PlanetPDF looks for its policy (None: a copy that isn't installed, such
    as the source code, has none)."""
    if sys.platform == "darwin":
        return MAC_FOLDER / POLICY_FILE
    if sys.platform != "win32":
        return LINUX_FOLDER / POLICY_FILE
    from planetpdf.core import update

    root = update.install_root()
    return root / POLICY_FILE if root is not None and root.is_dir() else None


def untrusted(path: Path) -> str:
    """Why this policy file can't be taken for an administrator's ("" = it can)."""
    if sys.platform == "win32":
        from planetpdf.core import windows_acl

        for part, what in ((path, "It"), (path.parent, "Its folder")):
            try:
                others = windows_acl.open_to(part)
            except (OSError, ValueError) as exc:
                return f"Its permissions couldn't be read ({exc})."
            if others:
                who = ", ".join(windows_acl.in_words(o) for o in others)
                return f"{what} can be changed by {who}, not only by administrators."
        return ""
    for part, what in ((path, "It"), (path.parent, "Its folder")):
        try:
            info = part.stat()
        except OSError as exc:
            return f"Its permissions couldn't be read ({exc})."
        if info.st_uid != 0:
            return f"{what} doesn't belong to root."
        if info.st_mode & 0o022:
            return f"{what} can be changed by users other than root."
    return ""


def load(path: Path | None = None, *, trust=untrusted) -> Policy:
    """The policy in ``path`` (default: this copy's own). A file that isn't an administrator's, or
    can't be read, is not in force; entries it doesn't know or with the wrong kind of value are left
    out. Each of those is said in ``problems``."""
    path = policy_path() if path is None else Path(path)
    if path is None or not path.is_file():
        return Policy()
    why = trust(path)
    if why:
        return Policy(found=path, problems=(f"{path} is ignored. {why}",))
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("it is too large")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("it isn't a JSON object")
    except (OSError, ValueError) as exc:
        return Policy(found=path, problems=(f"{path} is ignored: it can't be read ({exc}).",))
    problems, settings, off = [], {}, set()
    from planetpdf.core.storage.settings import Settings

    defaults = Settings()
    wanted = data.get("settings", {})
    if not isinstance(wanted, dict):
        problems.append('"settings" must be an object of names and values.')
        wanted = {}
    for name, value in wanted.items():
        if name not in SETTINGS:
            problems.append(f'"{name}" isn\'t a setting a policy can set.')
        elif type(value) is not type(getattr(defaults, name)):
            kind = type(getattr(defaults, name)).__name__
            problems.append(f'"{name}" needs a value of the kind {kind}, not {json.dumps(value)}.')
        else:
            settings[name] = value
    listed = data.get("off", [])
    if not isinstance(listed, list):
        problems.append('"off" must be a list of feature names.')
        listed = []
    for name in listed:
        if name in FEATURES:
            off.add(name)
        else:
            problems.append(f'"{name}" isn\'t a feature a policy can turn off.')
    for name in data:
        if name not in ("settings", "off") and not str(name).startswith(("_", "#", "comment")):
            problems.append(f'"{name}" isn\'t a part of a policy (it has "settings" and "off").')
    return Policy(path=path, settings=settings, off=frozenset(off), problems=tuple(problems), found=path)


_current: Policy | None = None


def current() -> Policy:
    """This computer's policy, read once for the run."""
    global _current
    if _current is None:
        try:
            _current = load()
        except Exception as exc:  # noqa: BLE001 (a policy that can't be judged never stops PlanetPDF)
            _current = Policy(problems=(f"The policy couldn't be read ({exc}).",))
    return _current


def reset(policy: Policy | None = None) -> None:
    """Forget what was read (tests; or put ``policy`` in force)."""
    global _current
    _current = policy


BY_ADMINISTRATOR = "turned off on this computer by your administrator"


def reason(feature: str) -> str:
    """"" when the feature may be used; else the sentence to show."""
    if current().allows(feature):
        return ""
    return f"{FEATURES[feature]} is {BY_ADMINISTRATOR}."


def describe(policy: Policy | None = None) -> dict:
    """The policy as plain data (``planetpdf policy``)."""
    policy = current() if policy is None else policy
    where = policy_path()
    return {
        "file": str(policy.path or policy.found or where or ""),
        "looked_for": str(where or ""),
        "in_force": policy.in_force,
        "settings": dict(policy.settings),
        "off": sorted(policy.off),
        "problems": list(policy.problems),
        "can_set": dict(SETTINGS),
        "can_turn_off": dict(FEATURES),
    }
