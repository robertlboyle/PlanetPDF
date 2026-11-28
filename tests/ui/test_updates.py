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
    refusal = kw.pop("refusal", "")
    m = updates.UpdateManager(win, tmp_path / "updates", current="1.0.0", **kw)
    m._refusal = refusal  # (the test's folder stands in for one only administrators can change)
    m.setParent(None)
    QApplication.instance().aboutToQuit.disconnect(m.install_on_exit)
    return m


def _answer(monkeypatch, asked, *choices):
    """Answers each question with the first of ``choices`` it offers (None: closes it, as its X)."""

    def answer(box):
        asked.append(box.text())
        names = [b.text() for b in box.buttons()]
        pick = next((c for c in choices if c in names), None)
        if pick is None:
            box.escapeButton().click() if box.escapeButton() is not None else box.reject()
        else:
            box.buttons()[names.index(pick)].click()

    monkeypatch.setattr(QMessageBox, "exec", answer)


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
    monkeypatch.setattr(U, "user_may_install", lambda root: True)
    asked = []
    _answer(monkeypatch, asked, "Download", "When I Close PlanetPDF")
    started = []
    monkeypatch.setattr(H, "start_detached", started.append)
    m = _manager(win, tmp_path)
    m.check_now(win)
    assert asked == ["PlanetPDF 9.0.0 is available (you have 1.0.0). Download it now?",
                     "PlanetPDF 9.0.0 is ready to install (you have 1.0.0)."]
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


def test_a_quiet_check_downloads_only_when_asked(win, tmp_path, monkeypatch):
    """The ethos: downloads the user asks for (security review 2026-11-25)."""
    monkeypatch.setattr(U, "user_may_install", lambda root: True)
    monkeypatch.setattr(U, "downloaded", lambda folder, current: None)
    monkeypatch.setattr(U, "check", lambda url, current: SIGNED)
    downloads = []
    monkeypatch.setattr(U, "download", lambda *a, **k: downloads.append(a) or tmp_path / "x.zip")
    asked = []
    _answer(monkeypatch, asked, "Not Now")
    m = _manager(win, tmp_path)
    m._check_and_download()
    QCoreApplication.processEvents()
    assert asked == ["PlanetPDF 9.0.0 is available (you have 1.0.0). Download it now?"]
    assert downloads == [] and m.pending is None


def test_closing_the_install_question_installs_nothing(win, tmp_path, monkeypatch):
    """Its X counted as "When I Close PlanetPDF", and quitting raised an administrator prompt."""
    asked = []
    _answer(monkeypatch, asked)  # (closed, no button)
    started = []
    monkeypatch.setattr(H, "start_detached", started.append)
    m = _manager(win, tmp_path)
    m.ask(SIGNED, tmp_path / "x.zip")
    assert asked and m.pending is None
    m.install_on_exit()
    assert started == []


def test_a_user_who_cant_install_is_told_once_and_nothing_downloads(win, tmp_path, monkeypatch):
    monkeypatch.setattr(U, "user_may_install", lambda root: False)  # (a standard user, a terminal server)
    monkeypatch.setattr(U, "downloaded", lambda folder, current: None)
    monkeypatch.setattr(U, "check", lambda url, current: SIGNED)
    downloads = []
    monkeypatch.setattr(U, "download", lambda *a, **k: downloads.append(a))
    asked = []
    _answer(monkeypatch, asked, "OK")
    m = _manager(win, tmp_path)
    m._check_and_download()
    m._check_and_download()
    QCoreApplication.processEvents()
    assert len(asked) == 1 and "9.0.0 is available" in asked[0]
    assert downloads == []


# --- the install folder's own programs (security list 2026-11-26, item 6) ---------------------------


def test_an_install_with_an_older_print_service_is_said_once_and_can_be_brought_up_to_date(
        win, tmp_path, monkeypatch):
    stale = [r"printer\planetpdf-print-service.exe", "PlanetPDF.exe"]
    monkeypatch.setattr(U, "stale_root_files", lambda root, source=None: list(stale))
    monkeypatch.setattr(U, "user_may_install", lambda root: True)
    m = _manager(win, tmp_path)
    asked, ran, said = [], [], []
    _answer(monkeypatch, asked, "Update Now")
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))

    def as_administrator(command):
        ran.append(command)
        stale.clear()  # (the administrator step replaced them)
        return 0

    monkeypatch.setattr(m, "run_as_administrator", as_administrator)
    assert m.check_install_parts() is True
    assert "the PlanetPDF printer's service and the program that starts PlanetPDF" in asked[0]
    assert ran == [H.refresh_command(m.root)] and H.REFRESH in ran[0]
    assert said == ["PlanetPDF's install is up to date now."]
    assert m.check_install_parts() is False and len(asked) == 1  # (nothing older any more)
    stale.append("PlanetPDF.exe")
    assert m.check_install_parts() is False  # (and once per version at most)


def test_closing_that_question_changes_nothing_and_someone_who_cannot_install_is_told_whom_to_ask(
        win, tmp_path, monkeypatch):
    monkeypatch.setattr(U, "stale_root_files", lambda root, source=None: [r"printer\planetpdf-print-service.exe"])
    monkeypatch.setattr(U, "user_may_install", lambda root: True)
    m = _manager(win, tmp_path)
    asked = []
    _answer(monkeypatch, asked)  # (closed with its X)
    monkeypatch.setattr(m, "run_as_administrator", lambda command: pytest.fail("nothing runs"))
    assert m.check_install_parts() is True and "is still from an older version" in asked[0]
    win.settings.install_parts_noticed = ""
    monkeypatch.setattr(U, "user_may_install", lambda root: False)  # (a standard user, as on a terminal server)
    told = []
    monkeypatch.setattr(m, "show_box", lambda box: told.append((box.informativeText(), len(box.buttons()))))
    assert m.check_install_parts() is True
    assert "An administrator can bring it up to date" in told[0][0] and told[0][1] == 1  # (only OK)
    monkeypatch.setattr(U, "stale_root_files", lambda root, source=None: [])
    win.settings.install_parts_noticed = ""
    assert m.check_install_parts() is False  # (an install that is whole: nothing is said)


def test_no_administrator_step_from_a_folder_others_can_change(win, tmp_path, monkeypatch):
    """Security review 2026-11-27, finding 3: a folder others could change can't be put right from
    the inside. PlanetPDF offers neither the download to install nor "Update Now" there; it says to
    run the installer, with the download page one click away."""
    m = _manager(win, tmp_path, refusal="PlanetPDF's folder can be changed by other users of this computer (X).")
    monkeypatch.setattr(updates.U, "user_may_install", lambda root: True)
    shown = []
    monkeypatch.setattr(m, "show_box", lambda box: shown.append((box.text(), box.informativeText(),
                                                                  [b.text() for b in box.buttons()])))
    signed = type("S", (), {"release": type("R", (), {"version": "2.0", "size": 1, "notes": ""})()})()
    assert m.offer_download(signed, asked=True) is None
    text, why, buttons = shown[-1]
    assert "2.0 is available" in text and "other users" in why
    assert "Open Download Page" in buttons and "Download" not in buttons
    elevated = []
    monkeypatch.setattr(m, "run_as_administrator", lambda command: elevated.append(command) or 0)
    assert m.refresh_install() is False
    assert elevated == []


def test_the_download_question_gives_the_size_in_megabytes(win, tmp_path, monkeypatch):
    """It said "The download is 288.2." (review 2026-11-27)."""
    m = _manager(win, tmp_path)
    monkeypatch.setattr(updates.U, "user_may_install", lambda root: True)
    shown = []
    monkeypatch.setattr(m, "show_box", lambda box: shown.append(box.informativeText()))
    signed = type("S", (), {"release": type("R", (), {"version": "2.0", "size": 288_248_000, "notes": ""})()})()
    m.offer_download(signed, asked=True)
    assert "The download is 288.2 MB." in shown[-1]
