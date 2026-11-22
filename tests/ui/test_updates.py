"""Help ▸ Check for Updates (user 2026-10-04): found → downloaded → asked → installed when
PlanetPDF closes (the helper is started, not run, here)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from planetpdf.core import update as U
from planetpdf.core import update_helper as H
from planetpdf.ui import updates
from planetpdf.ui.main_window import MainWindow

RELEASE = U.Release("9.0.0", "PlanetPDF-9.0.0.zip", 10, "0" * 64, "Faster")
SIGNED = U.SignedRelease(RELEASE, b"{}", b"sig")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.show()
    QCoreApplication.processEvents()
    yield w
    w.close()


def _manager(win, tmp_path, **kw):
    kw.setdefault("root", tmp_path / "install")
    kw.setdefault("url", "https://updates.example.com/")
    m = updates.UpdateManager(win, tmp_path / "updates", current="1.0.0", **kw)
    m.setParent(None)
    QApplication.instance().aboutToQuit.disconnect(m.install_on_exit)
    return m


def test_menu_item(win):
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    assert win.act_check_updates in help_menu.actions()


def test_no_server_set(win, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))
    _manager(win, tmp_path, url="").check_now(win)
    assert "No update server" in said[0]


def test_up_to_date(win, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))
    monkeypatch.setattr(U, "check", lambda url, current: None)
    _manager(win, tmp_path).check_now(win)
    assert "latest version of PlanetPDF (1.0.0)" in said[0]


def test_found_downloaded_and_installed_on_exit(win, tmp_path, monkeypatch):
    zip_path = tmp_path / "updates" / RELEASE.file
    monkeypatch.setattr(U, "check", lambda url, current: SIGNED)
    monkeypatch.setattr(U, "downloaded", lambda folder, current: None)
    monkeypatch.setattr(U, "download", lambda url, signed, folder, progress=None: zip_path)
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: asked.append(box.text()) or
                        box.buttons()[[b.text() for b in box.buttons()].index("When I Close PlanetPDF")].click())
    started = []
    monkeypatch.setattr(H, "start_detached", started.append)
    m = _manager(win, tmp_path)
    m.check_now(win)
    assert asked == ["PlanetPDF 9.0.0 is ready to install (you have 1.0.0)."]
    assert m.pending == (SIGNED, zip_path) and not m.restart
    m.install_on_exit()
    command = started[0]
    assert H.FINISH in command and str(zip_path) in command and "--restart" not in command
    assert m.pending is None


def test_not_installed_copy_only_tells(win, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))
    monkeypatch.setattr(U, "check", lambda url, current: SIGNED)
    m = _manager(win, tmp_path)
    m.root = None
    m.check_now(win)
    assert "9.0.0 is available" in said[0] and "can't update itself" in said[0]


def test_download_text():
    assert updates.download_text(12_300_000, 48_000_000) == "Downloading the update: 12.3 of 48.0 MB"


def test_when_it_checks_by_itself(win, tmp_path, monkeypatch):
    """Preferences (user 2026-10-07): "Check for updates on startup" and "every N days"
    (0 = never); Help ▸ Check for Updates any time."""
    checks = []
    monkeypatch.setattr(updates.threading, "Thread", lambda target, daemon: type(
        "T", (), {"start": lambda self: checks.append(target)})())
    s = win.settings
    m = _manager(win, tmp_path)
    s.update_every_days, s.update_last_check = 14, updates.time.time() - 13 * updates.DAY
    assert not m.due()
    m.check_quietly()
    assert checks == []
    m.check_quietly(at_start=True)  # (the startup check doesn't wait for the 14 days)
    assert len(checks) == 1
    m._busy = False
    s.update_last_check = updates.time.time() - 14 * updates.DAY
    assert m.due()
    s.update_every_days = 0  # never by itself
    assert not m.due()
    # Startup check off: start() doesn't schedule it.
    scheduled = []
    monkeypatch.setattr(updates.QTimer, "singleShot", lambda ms, fn: scheduled.append(ms))
    s.check_updates_on_start = False
    m.start()
    assert scheduled == []
    s.check_updates_on_start = True
    m.start()
    assert scheduled == [updates.FIRST_CHECK_MS]
    m._timer.stop()


def test_last_install_result_shown(win, tmp_path, monkeypatch):
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / H.RESULT_FILE).write_text('{"ok": true, "message": "PlanetPDF was updated to version 9.0.0."}')
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    m = _manager(win, tmp_path, root=None)
    m.start()
    assert shown == ["PlanetPDF was updated to version 9.0.0."]
    assert not Path(folder / H.RESULT_FILE).exists()


def test_a_failed_elevation_offers_the_download_page(win, tmp_path, monkeypatch):
    """User 2026-10-26: Windows' administrator prompt failed ("File system error"); PlanetPDF
    says so plainly and opens the download page for the full installer."""
    from PySide6.QtGui import QDesktopServices

    from planetpdf import DOWNLOAD_PAGE

    m = _manager(win, tmp_path)
    opened, shown = [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    def click_download(box):
        shown.append([b.text() for b in box.buttons()])
        box.buttons()[0].click()  # (the first: Open Download Page)

    monkeypatch.setattr(m, "show_box", click_download)
    m.show_result({"ok": False, "message": "Windows couldn't start the update's installer.", "manual": True})
    assert "Open Download Page" in shown[0] and opened == [DOWNLOAD_PAGE]
    opened.clear()
    m.show_result({"ok": False, "message": "canceled", "manual": False})  # (no download button then)
    assert "Open Download Page" not in shown[1] and opened == []
