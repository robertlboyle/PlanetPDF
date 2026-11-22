"""The taskbar's icon for PlanetPDF's windows (user 2026-10-18: Windows 11 showed its generic icon
for the installed PlanetPDF): each window tells Windows its app ID, icon and relaunch command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' taskbar")


def test_relaunch_info_names_the_icon_and_how_to_start(monkeypatch, tmp_path):
    from planetpdf.ui import windows_shell as W

    info = W.relaunch_info()  # (from source: Python and the bundled icon)
    assert info["icon"].endswith("planetpdf.ico,0") and "-m planetpdf" in info["command"]
    program = tmp_path / "PlanetPDF" / "versions" / "0.1.3" / "PlanetPDF.exe"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    (tmp_path / "PlanetPDF" / "PlanetPDF.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program))
    launcher = tmp_path / "PlanetPDF" / "PlanetPDF.exe"
    assert W.relaunch_info() == {"icon": f"{launcher},0", "command": f'"{launcher}"'}  # (the launcher's)


def test_windows_takes_a_windows_identity():
    from PySide6.QtWidgets import QApplication, QWidget

    from planetpdf.ui import windows_shell as W

    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip("needs real windows (not offscreen)")
    widget = QWidget()
    assert W.window_identity(int(widget.winId()), **W.relaunch_info())
    assert Path(W.relaunch_info()["icon"].rsplit(",", 1)[0]).is_file()
