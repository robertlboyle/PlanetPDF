"""The default app for PDF files (user 2026-10-10) and files macOS asks PlanetPDF to open."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from planetpdf.ui import default_app, macos_shell
from planetpdf.ui.file_open_events import FileOpenEvents
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    yield w
    w.close()


def test_only_the_installed_program_offers_it():
    assert default_app.available("darwin", True) and default_app.available("win32", True)
    assert not default_app.available("darwin", False)  # from source it would make Python the PDF app
    assert not default_app.available("linux", True)


def _as_mac_app(monkeypatch, current="com.apple.Preview"):
    state = {"default": current, "set": []}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(macos_shell, "bundle_identifier", lambda: "net.planet.planetpdf")
    monkeypatch.setattr(macos_shell, "default_app", lambda content_type=macos_shell.PDF_TYPE: state["default"])

    def set_default(bundle_id, content_type=macos_shell.PDF_TYPE):
        state["set"].append(bundle_id)
        state["default"] = bundle_id
        return True

    monkeypatch.setattr(macos_shell, "set_default_app", set_default)
    return state


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_the_mac_app_asks_once_at_its_first_start(win, monkeypatch, answer):
    state = _as_mac_app(monkeypatch)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or answer)
    default_app.offer_at_first_start(win)
    assert len(asked) == 1 and "default app for opening PDF files" in asked[0]
    assert state["set"] == (["net.planet.planetpdf"] if answer == QMessageBox.StandardButton.Yes else [])
    assert win.settings.asked_default_pdf_app
    default_app.offer_at_first_start(win)  # never again
    assert len(asked) == 1


def test_no_question_when_it_already_opens_pdfs_or_runs_from_source(win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked"))
    _as_mac_app(monkeypatch, current="net.planet.planetpdf")
    default_app.offer_at_first_start(win)
    win.settings.asked_default_pdf_app = False
    monkeypatch.setattr(sys, "frozen", False)
    default_app.offer_at_first_start(win)
    assert not win.settings.asked_default_pdf_app


def test_preferences_button_makes_it_the_pdf_app(win, monkeypatch):
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    state = _as_mac_app(monkeypatch)
    dialog = PreferencesDialog(win)
    assert dialog.make_default.isEnabled()
    dialog.make_default.click()
    assert state["set"] == ["net.planet.planetpdf"]
    dialog.reject()
    assert not PreferencesDialog(win).make_default.isEnabled()  # it does now


def test_windows_opens_its_app_chooser(win, monkeypatch):
    """Windows' "How do you want to open .pdf files?" (user 2026-10-18: the Default apps page had
    nothing to click); the Default apps page only when the chooser can't open. Never the real one
    here: it would appear on the screen."""
    from PySide6.QtGui import QDesktopServices

    from planetpdf.ui import windows_shell

    chooser, opened = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows_shell, "pdf_app", lambda: "Acrobat.Document.DC")
    monkeypatch.setattr(windows_shell, "choose_pdf_app",lambda hwnd=0: chooser.append(hwnd) or True)
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString()) or True))
    assert default_app.make_default(win)
    assert len(chooser) == 1 and opened == []
    monkeypatch.setattr(windows_shell, "choose_pdf_app", lambda hwnd=0: False)
    assert default_app.make_default(win)
    assert opened == ["ms-settings:defaultapps?registeredAppMachine=PlanetPDF"]


def test_windows_stays_silent_when_planetpdf_already_opens_pdf_files(win, monkeypatch):
    """User 2026-10-23: the chooser showed PlanetPDF as the default with only "Just once"; then
    2026-11-22: no message either ("annoying to see every upgrade. Just stay silent")."""
    from planetpdf.ui import windows_shell

    chooser, told = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows_shell, "choose_pdf_app", lambda hwnd=0: chooser.append(hwnd) or True)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda parent, title, text, *a: told.append(text)))
    for prog_id in ("PlanetPDF.Document", r"Applications\planetpdf.exe"):
        monkeypatch.setattr(windows_shell, "pdf_app", lambda prog_id=prog_id: prog_id)
        assert default_app.is_default() is True
        assert default_app.make_default(win)
    assert chooser == [] and told == []
    for prog_id in ("Acrobat.Document.DC", None):
        monkeypatch.setattr(windows_shell, "pdf_app", lambda prog_id=prog_id: prog_id)
        assert default_app.is_default() is False


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_windows_choice_is_read_from_the_registry():
    from planetpdf.ui import windows_shell

    prog_id = windows_shell.pdf_app()  # whatever this computer's user chose, or None
    assert prog_id is None or (isinstance(prog_id, str) and prog_id)


def test_the_chooser_opens_a_page_saying_what_happened(tmp_path):
    """The chooser opens its sample in the app picked (OAIF_EXEC: user 2026-10-23, the forced
    registration flags only got "go to Settings"), so the sample is a readable page."""
    import pymupdf

    from planetpdf.ui import windows_shell

    path = tmp_path / "PDF files.pdf"
    windows_shell.write_sample(path)
    with pymupdf.open(path) as doc:
        pages = doc.page_count
        text = " ".join(doc[0].get_text().split())
    assert pages == 1
    assert "PDF files now open in the app you chose." in text and "Default apps" in text and "?" not in text


def test_files_from_finder_wait_for_the_window_then_open(qapp, tmp_path):
    events = FileOpenEvents()
    qapp.installEventFilter(events)
    try:
        early, late = tmp_path / "a.pdf", tmp_path / "b.pdf"
        QCoreApplication.sendEvent(qapp, QFileOpenEvent(str(early)))  # before the window exists
        got = []
        events.set_handler(got.extend)
        assert got == [early]
        QCoreApplication.sendEvent(qapp, QFileOpenEvent(str(late)))
        assert got == [early, late]
        assert events.eventFilter(qapp, QEvent(QEvent.Type.FileOpen))  # (taken, nothing to open)
        assert got == [early, late]
    finally:
        qapp.removeEventFilter(events)


@pytest.mark.skipif(sys.platform != "darwin", reason="Launch Services is macOS")
def test_launch_services_reads_the_pdf_app():
    assert macos_shell.default_app()  # e.g. com.apple.Preview
    assert isinstance(macos_shell.bundle_identifier(), str)
