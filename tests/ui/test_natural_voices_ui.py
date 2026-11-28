"""Read Out Loud suggests the natural voices (Kokoro) and downloads them on first use; a Mac reads
with its own voices meanwhile (2026-10-14; user: "prefer and suggest kokoro")."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from planetpdf.core import neural_speech as NS
from planetpdf.core import voice_download as V
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.phase_d_ui import ExtrasUi


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(NS, "USER_MODELS", tmp_path / "models")
    monkeypatch.setattr(NS, "models_dir", lambda: None)  # (no voices installed anywhere)
    monkeypatch.setattr(ExtrasUi, "_suggested", False)
    w = MainWindow(tmp_path / "profile")
    yield w
    w.close()


def test_suggests_once_per_run_and_downloads(win, monkeypatch):
    asked, fetched = [], []
    monkeypatch.setattr(win.extras, "ask_voices", lambda installer, just_me: asked.append((installer, just_me)) or "download")

    def fake_download(url, target, progress=None, **kw):
        fetched.append((url, target))
        (target / "kokoro").mkdir(parents=True)
        return target

    monkeypatch.setattr(V, "download", fake_download)
    monkeypatch.setattr("planetpdf.ui.tasks.run_in_background",
                        lambda parent, title, label, work, **kw: work(lambda *a: True))
    monkeypatch.setenv("PLANETPDF_UPDATE_URL", "https://software.example/PDF/")  # (tests never reach the real one)
    assert win.extras.suggest_natural_voices()
    assert fetched == [("https://software.example/PDF/", NS.USER_MODELS)]
    assert not win.extras.suggest_natural_voices() and asked == [(False, True)]  # (once per run; from source: no installer)


def test_not_now_keeps_the_fallback_and_doesnt_ask_again(win, monkeypatch):
    monkeypatch.setattr(win.extras, "ask_voices", lambda installer, just_me: None)
    monkeypatch.setattr(V, "download", lambda *a, **k: pytest.fail("downloaded"))
    assert not win.extras.suggest_natural_voices()
    assert ExtrasUi._suggested


def test_a_failed_download_says_what_reads_meanwhile(win, monkeypatch):
    from planetpdf.core import update as U

    shown = []
    monkeypatch.setattr(win, "show_message", lambda title, text: shown.append(text))
    assert not win.extras.download_natural_voices()  # (no server address: tests blank it)
    assert "No update server" in shown.pop()
    monkeypatch.setenv("PLANETPDF_UPDATE_URL", "https://software.example/PDF/")
    monkeypatch.setattr("planetpdf.ui.tasks.run_in_background",
                        lambda parent, title, label, work, **kw: (_ for _ in ()).throw(U.UpdateError("No network.")))
    assert not win.extras.download_natural_voices()
    assert "No network." in shown[0] and ("macOS voices" in shown[0] or "Windows voices" in shown[0])


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS' own voices")
def test_preferences_offer_macos_voices_and_the_download(win):
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    dialog = PreferencesDialog(win)
    keys = [dialog.read_engine.itemData(i) for i in range(dialog.read_engine.count())]
    assert "mac" in keys and dialog.voice_boxes["mac"].count() > 3
    assert dialog.download_voices is not None and "354 MB" in dialog.download_voices.text()


def test_installed_on_windows_offers_the_voices_installer(win, monkeypatch):
    """2026-11-27: the voices have their own installer, for everyone on the computer; over Remote
    Desktop (a terminal server) no copy just for one user is offered."""
    from planetpdf.ui import phase_d_ui, windows_shell

    monkeypatch.setattr(phase_d_ui, "voices_installer_offered", lambda: True)
    monkeypatch.setattr(windows_shell, "is_remote_session", lambda: True, raising=False)
    asked, opened = [], []
    monkeypatch.setattr(win.extras, "ask_voices", lambda installer, just_me: asked.append((installer, just_me)) or "install")
    monkeypatch.setattr(win.extras, "open_voices_page", lambda: opened.append(1))
    monkeypatch.setattr(V, "download", lambda *a, **k: pytest.fail("downloaded just for one user"))
    assert not win.extras.suggest_natural_voices()
    assert asked == [(True, False)] and opened == [1]


def test_per_user_download_only_away_from_remote_desktop(monkeypatch, tmp_path):
    from planetpdf.ui import phase_d_ui, windows_shell

    monkeypatch.setattr(NS, "USER_MODELS", tmp_path / "models")
    monkeypatch.setattr(phase_d_ui, "voices_installer_offered", lambda: True)
    monkeypatch.setattr(windows_shell, "is_remote_session", lambda: False, raising=False)
    assert phase_d_ui.per_user_voices_offered()
    monkeypatch.setattr(windows_shell, "is_remote_session", lambda: True, raising=False)
    assert not phase_d_ui.per_user_voices_offered()
    monkeypatch.setattr(phase_d_ui, "voices_installer_offered", lambda: False)  # (Mac, Linux, from source)
    assert phase_d_ui.per_user_voices_offered()
    monkeypatch.setattr(NS, "USER_MODELS", None)
    assert not phase_d_ui.per_user_voices_offered()


def test_preferences_offer_the_voices_installer_when_installed(win, monkeypatch):
    from planetpdf.ui import phase_d_ui, windows_shell
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    monkeypatch.setattr(NS, "kokoro_available", lambda root=None: False)
    monkeypatch.setattr(phase_d_ui, "voices_installer_offered", lambda: True)
    monkeypatch.setattr(windows_shell, "is_remote_session", lambda: True, raising=False)
    opened = []
    monkeypatch.setattr(win.extras, "open_voices_page", lambda: opened.append(1))
    dialog = PreferencesDialog(win)
    assert dialog.install_voices is not None and dialog.download_voices is None  # (Remote Desktop)
    dialog.install_voices.click()
    assert opened == [1]
