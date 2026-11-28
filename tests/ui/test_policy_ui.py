"""An administrator's policy in the window (security list 2026-11-26, item 5): Preferences shows
what it set, grayed, and the features it turned off say so."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core import policy as P
from planetpdf.ui.dialogs.preferences import PreferencesDialog
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def under_policy(tmp_path):
    P.reset(P.Policy(path=tmp_path / "policy.json",
                     settings={"check_updates_on_start": False, "update_every_days": 0, "check_revocation": True,
                               "api_enabled": False, "remember_private_numbers": False},
                     off=frozenset({"web_capture", "voice_download", "plugins"})))
    yield
    P.reset(None)


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.show()
    QCoreApplication.processEvents()
    yield w
    w.close()


def test_preferences_show_what_the_administrator_set_grayed(under_policy, win):
    assert win.settings.check_revocation is True and win.settings.update_every_days == 0
    dialog = PreferencesDialog(win)
    try:
        for control in (dialog.check_on_start, dialog.update_days, dialog.check_revocation, dialog.remember_private,
                        dialog.scripting.api_enabled):
            assert not control.isEnabled() and "administrator" in control.toolTip()
        assert dialog.check_revocation.isChecked() and not dialog.check_on_start.isChecked()
        assert dialog.update_days.value() == 0 and not dialog.scripting.api_enabled.isChecked()
        assert dialog.autosave.isEnabled() and dialog.tsa.isEnabled()  # (not in the policy: theirs to set)
        assert "set by your administrator" in dialog.policy_note.text() and not dialog.policy_note.isHidden()
        dialog.remember_text.setChecked(False)
        dialog.remember_text.setChecked(True)
        assert not dialog.remember_private.isEnabled()  # (ticking the box above doesn't hand it back)
        dialog.autosave.setValue(9)
        dialog._save()
    finally:
        dialog.deleteLater()
    assert win.settings.autosave_minutes == 9
    assert win.settings.check_revocation is True and win.settings.api_enabled is False  # (still the policy's)


def test_without_a_policy_nothing_is_grayed_or_said(win):
    P.reset(P.Policy())
    try:
        dialog = PreferencesDialog(win)
        try:
            assert dialog.check_on_start.isEnabled() and dialog.check_revocation.isEnabled()
            assert dialog.scripting.api_enabled.isEnabled() and dialog.policy_note.isHidden()
        finally:
            dialog.deleteLater()
    finally:
        P.reset(None)


def test_a_feature_turned_off_says_so_before_asking_anything(under_policy, win, monkeypatch):
    said = []
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))
    monkeypatch.setattr(win.extras, "ask_address", lambda: pytest.fail("no address is asked for"))
    assert win.extras.create_from_web() is False and win.extras.insert_from_web() is False
    assert len(said) == 2 and all(P.BY_ADMINISTRATOR in text for text in said)
    monkeypatch.setattr(win.extras, "ask_voices", lambda *a: pytest.fail("the voices aren't offered"))
    assert win.extras.suggest_natural_voices() is False
    assert win.extras.download_natural_voices() is False and P.BY_ADMINISTRATOR in said[-1]


def test_no_plugin_can_be_turned_on_where_plugins_are_off(under_policy, win):
    dialog = PreferencesDialog(win)
    try:
        assert P.BY_ADMINISTRATOR in dialog.scripting.plugins_off()
    finally:
        dialog.deleteLater()
    P.reset(P.Policy(path=win.profile.root / "policy.json", settings={"api_enabled": False}))
    win.settings._locked["api_enabled"] = False  # (as loaded under that policy)
    dialog = PreferencesDialog(win)
    try:
        assert "local API" in dialog.scripting.plugins_off()  # (plugins work through it)
    finally:
        dialog.deleteLater()


def test_check_for_updates_says_who_handles_them(under_policy, win, tmp_path, monkeypatch):
    from planetpdf.ui import updates

    P.reset(P.Policy(path=tmp_path / "policy.json", off=frozenset({"updates"})))
    manager = updates.UpdateManager(win, tmp_path / "updates", current="1.0.0", root=tmp_path / "install")
    manager.setParent(None)
    QApplication.instance().aboutToQuit.disconnect(manager.install_on_exit)
    assert manager.url == ""
    said = []
    monkeypatch.setattr(win, "show_message", lambda title, text: said.append(text))
    manager.check_now(win)
    assert said == ["Updates of PlanetPDF on this computer are handled by your administrator. "
                    "PlanetPDF 1.0.0 is installed."]
