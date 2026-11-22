"""Every command is wired the safe way (audit 2026-10-03: "check all menu items").

Qt's ``triggered`` / ``clicked`` signals pass ``checked`` (False) to their slot, so a
command connected straight to a method whose first argument is optional gets False
there: File ▸ Create ▸ PDF from File… opened ``Path(False)`` and failed every time;
Background ▸ Add… loaded "False" as settings. Such commands go through a lambda.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidgetAction

from planetpdf.ui.main_window import MainWindow

UI = Path(__file__).resolve().parents[2] / "src" / "planetpdf" / "ui"
CHECKED_NAMES = {"checked", "on", "_checked", "_c", "_"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    win = MainWindow(tmp_path / "profile")
    yield win
    win.close()


def _owners(win) -> dict:
    owners = {"self": win, "w": win, "window": win}
    for name in ("markup", "edit", "edit_ui", "markup_ui", "stamp_ui", "batch", "extras", "scan_tools",
                 "text_tools", "find_bar", "tool_panes", "view", "forms", "text_format"):
        if hasattr(win, name):
            owners[f"self.{name}"] = owners[f"w.{name}"] = owners[f"window.{name}"] = getattr(win, name)
    return owners


def test_no_command_passes_qts_checked_flag_into_an_optional_argument(window):
    owners = _owners(window)
    found = []
    pattern = re.compile(r"(?:_action\(\s*f?\"[^\"]*\",\s*|triggered\.connect\(\s*|clicked\.connect\(\s*)"
                         r"((?:self|w|window)(?:\.\w+)*)\.(\w+)\s*[,)]")
    for path in UI.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            owner = owners.get(match.group(1))
            method = getattr(owner, match.group(2), None) if owner is not None else None
            if method is None or not callable(method):
                continue
            try:
                params = list(inspect.signature(method).parameters.values())
            except (TypeError, ValueError):
                continue
            first = params[0] if params else None
            if (first is not None and first.default is not inspect.Parameter.empty and first.name not in CHECKED_NAMES
                    and first.kind in (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD)):
                line = text[:match.start()].count("\n") + 1
                found.append(f"{path.name}:{line} {match.group(1)}.{match.group(2)}({first.name}=…)")
    assert found == [], "connect these through a lambda: " + "; ".join(found)


def test_every_menu_command_has_something_to_do(window):
    """No menu item is left unconnected (or marked "not available yet")."""
    missing = []

    def walk(menu, path):
        for action in menu.actions():
            if action.isSeparator():
                continue
            name = f"{path} > {action.text().replace('&', '')}"
            if action.menu() is not None:
                action.menu().aboutToShow.emit()
                walk(action.menu(), name)
                continue
            if isinstance(action, QWidgetAction):
                continue
            connected = sum(action.receivers(s) for s in ("2triggered(bool)", "2triggered()", "2toggled(bool)"))
            if not connected or "not available" in (action.toolTip() or "").lower():
                missing.append(name)

    for action in window.menuBar().actions():
        if action.menu() is not None:
            action.menu().aboutToShow.emit()
            walk(action.menu(), action.text().replace("&", ""))
    assert missing == []
