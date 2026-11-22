"""Font Awesome Free stands in for every Pro icon (user 2026-10-24: the code is shared publicly;
Pro icons only in builds made with a Pro license)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core.resources import resources_dir
from planetpdf.ui import fa
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer import cursors

FREE = resources_dir() / "icons" / "free"
PRO = resources_dir() / "icons" / "fa"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_free_set_is_bundled_with_its_license():
    assert (FREE / "LICENSE.txt").read_text(encoding="utf-8").find("Font Awesome Free License") >= 0
    assert "Font Awesome Free" in (FREE / "bookmark.svg").read_text(encoding="utf-8")


def test_every_icon_the_window_shows_has_a_free_one(qapp, tmp_path, pdf_copy, monkeypatch):
    asked = set()
    real = fa.icon_path
    monkeypatch.setattr(fa, "icon_path", lambda name: asked.add(name) or real(name))
    fa.icon.cache_clear()
    win = MainWindow(tmp_path / "profile")
    try:
        win.open_file(pdf_copy("text.pdf"))
        for key in list(win.tool_panes.bars.keys()):  # (every tool's bar, built only when first opened)
            win.tool_panes.ensure(key)
        QCoreApplication.processEvents()
        shown = {name for name in asked if (PRO / f"{name}.svg").is_file() or (FREE / f"{name}.svg").is_file()}
        assert len(shown) > 100
        missing = sorted(name for name in shown if not (FREE / f"{name}.svg").is_file())
        assert not missing, f"no free icon (add a stand-in in scripts/update_icons.py): {missing}"
    finally:
        win.undo_stack.setClean()
        win.close()
        fa.icon.cache_clear()


def test_every_cursor_has_a_free_icon():
    missing = sorted(icon for icon, _ in cursors.CURSORS.values() if not (FREE / f"{icon}.svg").is_file())
    assert not missing


def test_free_only_switch(monkeypatch):
    monkeypatch.setenv("PLANETPDF_ICONS", "free")
    assert fa.icons_dir() == FREE
    assert fa.icon_path("bookmark") == FREE / "bookmark.svg"
    assert fa.icon_path("scanner-image") == FREE / "scanner-image.svg"  # (a stand-in, under Pro's name)
    monkeypatch.delenv("PLANETPDF_ICONS")
    if PRO.is_dir():  # (a copy built with a Pro license prefers Pro's)
        assert fa.icon_path("bookmark") == PRO / "bookmark.svg"
