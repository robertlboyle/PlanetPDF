"""Plugins (2026-10-18): programs that add commands to PlanetPDF's menus and do their work through
the local API (``core/api.py``), never inside PlanetPDF.

A plugin is a folder with a ``plugin.json``:

    {
      "id": "com.example.invoice-stamper",      (letters, digits, . - _)
      "name": "Invoice Stamper",
      "version": "1.0",
      "publisher": "Example Accounting",
      "description": "Stamps PAID on the open invoice and files it.",
      "api": 1,
      "run": ["stamper.exe"],                    (or ["python", "stamper.py"]; the first word is looked
                                                  for in the plugin's folder, then on the PATH)
      "permissions": ["read", "write"],          (what it may do through the API; "sign" too if needed)
      "menu_items": [{"menu": "Tools", "title": "Stamp as Paid", "command": "stamp",
                      "needs_document": true}]
    }

Where: the user's ``<profile>/plugins`` folder, and folders listed in ``PLANETPDF_PLUGINS`` (IT's
shared plugins). **Nothing runs until the user turns a plugin on** in Preferences ▸ Scripting &
Plugins, which shows what it asks for; PlanetPDF remembers the plugin's **fingerprint** (a SHA-256
of every file in its folder), and a plugin whose files change afterwards is off again until the
user turns it on again (it's identified, not yet signed).

When one of its menu items is chosen, PlanetPDF starts its ``run`` in its own process, in its own
folder, with ``PLANETPDF_API_ADDRESS``, ``PLANETPDF_API_TOKEN`` (a fresh token for this plugin, which
the API holds to the plugin's ``permissions``), ``PLANETPDF_COMMAND`` (the item's command),
``PLANETPDF_DOCUMENT`` / ``PLANETPDF_PAGE`` (the open document, when there is one) and
``PLANETPDF_PLUGIN_NAME``. Its output goes to ``<profile>/logs/plugins/<id>.log``. It runs with the
user's own rights like any program they start; the permissions limit what it can do *through
PlanetPDF*. Signing still asks the user (unless they chose "Always Allow" for it).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from planetpdf.core.commands import PERMISSIONS
from planetpdf.core.document import PdfError

MANIFEST = "plugin.json"
API_VERSION = 1
ENV_FOLDERS = "PLANETPDF_PLUGINS"
MENUS = ("File", "Edit", "View", "Tools", "Window", "Help")
SKIP = {"__pycache__", ".git"}


class PluginError(PdfError):
    pass


@dataclass(frozen=True)
class MenuItem:
    menu: str
    title: str
    command: str
    needs_document: bool = False


@dataclass
class Plugin:
    folder: Path
    id: str
    name: str
    version: str = ""
    publisher: str = ""
    description: str = ""
    run: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    menu_items: list[MenuItem] = field(default_factory=list)
    problem: str = ""  # why it can't be used (a bad manifest): shown in Preferences

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip()

    def fingerprint(self) -> str:
        return fingerprint(self.folder)


def fingerprint(folder: Path) -> str:
    """SHA-256 over every file's relative path and contents (so any change shows)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file() and not SKIP & set(p.parts)):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def read_manifest(folder: Path) -> Plugin:
    folder = Path(folder)
    plugin = Plugin(folder, id=folder.name, name=folder.name)
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("it isn't a JSON object")
    except (OSError, ValueError) as e:
        plugin.problem = f"Its {MANIFEST} can't be read: {e}"
        return plugin
    problems = []
    pid = str(data.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", pid):
        problems.append('"id" must be letters, digits, dots, dashes, or underscores')
    else:
        plugin.id = pid
    plugin.name = str(data.get("name") or pid or folder.name)[:100]
    plugin.version = str(data.get("version") or "")[:30]
    plugin.publisher = str(data.get("publisher") or "")[:100]
    plugin.description = str(data.get("description") or "")[:500]
    if data.get("api", API_VERSION) != API_VERSION:
        problems.append(f"it's for API version {data.get('api')}; this PlanetPDF has {API_VERSION}")
    run = data.get("run")
    if not (isinstance(run, list) and run and all(isinstance(r, str) and r for r in run)):
        problems.append('"run" must be a list: the program and its arguments')
    else:
        plugin.run = run
    perms = data.get("permissions") or []
    unknown = [p for p in perms if p not in PERMISSIONS]
    if unknown:
        problems.append(f"unknown permissions: {', '.join(map(str, unknown))}")
    plugin.permissions = [p for p in perms if p in PERMISSIONS]
    for item in data.get("menu_items") or []:
        if not isinstance(item, dict) or not item.get("title") or not item.get("command"):
            problems.append('each of "menu_items" needs a "title" and a "command"')
            continue
        menu = str(item.get("menu") or "Tools").title()
        if menu not in MENUS:
            problems.append(f"menu {menu!r} isn't one of {', '.join(MENUS)}")
            continue
        plugin.menu_items.append(MenuItem(menu, str(item["title"])[:80], str(item["command"])[:80],
                                          bool(item.get("needs_document"))))
    plugin.problem = "; ".join(problems)
    return plugin


def folders(profile_root: Path) -> list[Path]:
    places = [Path(profile_root) / "plugins"]
    places += [Path(p) for p in os.environ.get(ENV_FOLDERS, "").split(os.pathsep) if p.strip()]
    return places


def discover(profile_root: Path) -> list[Plugin]:
    """Every plugin found (the first one with an id wins), bad ones with their ``problem``."""
    found: dict[str, Plugin] = {}
    for place in folders(profile_root):
        if not place.is_dir():
            continue
        for folder in sorted(p for p in place.iterdir() if p.is_dir() and (p / MANIFEST).is_file()):
            plugin = read_manifest(folder)
            found.setdefault(plugin.id, plugin)
    return list(found.values())


def is_enabled(plugin: Plugin, enabled: dict[str, str]) -> bool:
    """On, and unchanged since the user turned it on."""
    return not plugin.problem and enabled.get(plugin.id) == plugin.fingerprint()


def command_line(plugin: Plugin) -> list[str]:
    program = plugin.run[0]
    local = plugin.folder / program
    if local.is_file():
        return [str(local), *plugin.run[1:]]
    if program.lower() in ("python", "python3", "py") and getattr(sys, "frozen", False) is False:
        return [sys.executable, *plugin.run[1:]]
    found = shutil.which(program)
    if found is None:
        raise PluginError(f"{plugin.label} needs {program}, which wasn't found.")
    return [found, *plugin.run[1:]]


def start(plugin: Plugin, item: MenuItem, *, address: str, token: str, log_folder: Path,
          document: Path | None = None, page: int | None = None) -> subprocess.Popen:
    """Start the plugin for one of its menu items (never through a shell)."""
    env = dict(os.environ)
    env.update({"PLANETPDF_API_ADDRESS": address, "PLANETPDF_API_TOKEN": token, "PLANETPDF_COMMAND": item.command,
                "PLANETPDF_PLUGIN_NAME": plugin.name, "PLANETPDF_PLUGIN_ID": plugin.id})
    for key in ("PLANETPDF_DOCUMENT", "PLANETPDF_PAGE"):
        env.pop(key, None)
    if document is not None:
        env["PLANETPDF_DOCUMENT"] = str(document)
        env["PLANETPDF_PAGE"] = str(page or 1)
    log_folder.mkdir(parents=True, exist_ok=True)
    log = (log_folder / f"{plugin.id}.log").open("ab")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.Popen(command_line(plugin), cwd=plugin.folder, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    finally:
        log.close()
