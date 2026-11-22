"""Startup safety (user report 2026-10-01: "the UI never appears"): the startup log, a
saved layout from an older build is ignored, a failed start skips the saved layout, and a
window saved off every screen comes back into view."""

from __future__ import annotations

import base64

import pytest
from PySide6.QtCore import QCoreApplication, QPoint
from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar

from planetpdf.core.storage.settings import load_settings, save_settings
from planetpdf.ui import startup_log as S
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _profile_with(tmp_path, **values):
    from planetpdf.core.storage.profile import Profile

    prof = Profile(tmp_path / "profile").ensure()
    settings = load_settings(prof.settings_dir)
    for key, value in values.items():
        setattr(settings, key, value)
    save_settings(settings, prof.settings_dir)
    return prof.root


def _old_build_state() -> str:
    """A layout saved by an older build: unversioned, tool bars side by side."""
    old = QMainWindow()
    for name in ("mainToolbar", "tool_comment", "toolStrip"):
        bar = QToolBar(name, old)
        bar.setObjectName(name)
        old.addToolBar(bar)
    return base64.b64encode(bytes(old.saveState())).decode("ascii")


def test_old_layout_is_ignored(qapp, tmp_path):
    root = _profile_with(tmp_path, window_state=_old_build_state())
    w = MainWindow(root)
    w.show()
    QCoreApplication.processEvents()
    main = w.findChild(QToolBar, "mainToolbar")
    assert all(not bar.isVisible() for bar in w.tool_panes.bars.values())  # no tool left open
    assert main.isVisible() and w.tool_panes.strip.isVisible()
    w.close()


def test_layout_round_trip_and_skip(qapp, tmp_path):
    root = _profile_with(tmp_path)
    w = MainWindow(root)
    w.show()
    w._history_dock.show()  # (not a tool panel: those close when no tool is open)
    QCoreApplication.processEvents()
    w.close()  # saves the (versioned) layout
    again = MainWindow(root)
    assert not again._history_dock.isHidden()  # restored
    again.close()
    safe = MainWindow(root, restore_layout=False)
    assert safe._history_dock.isHidden()  # the last start failed: defaults
    safe.close()


def test_window_off_every_screen_comes_back(qapp, tmp_path):
    w = MainWindow(_profile_with(tmp_path))
    w.show()
    w.move(QPoint(-20000, -20000))
    assert w.ensure_on_screen() is not None
    area = QApplication.primaryScreen().availableGeometry()
    assert area.intersects(w.frameGeometry())
    assert w.ensure_on_screen() is None  # already fine: left alone
    w.close()


def test_startup_log_notices_a_failed_start(tmp_path):
    log = tmp_path / "logs" / S.LOG_NAME
    log.parent.mkdir(parents=True)
    log.write_text("[0.00s] PlanetPDF starting\n[1.13s] Window before showing\n", encoding="utf-8")
    try:
        assert S.attach(tmp_path) == log
        assert S.previous_start_failed
        S.log("hello")
        S.finished()
        assert "hello" in log.read_text(encoding="utf-8")
        S.attach(tmp_path)  # this time the last start finished
        assert not S.previous_start_failed
    finally:
        S.finished()
        import faulthandler

        faulthandler.disable()
        if S._file is not None:
            S._file.close()
            S._file = None
