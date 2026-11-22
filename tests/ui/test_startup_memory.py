"""Faster start, less memory (user 2026-10-07): tool bars built when first opened, except
the tools used last; the page cache capped by size (Preferences)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.toolbars import RECENT_TOOLS


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(profile):
    w = MainWindow(profile)
    w.resize(1100, 800)
    w.show()
    QCoreApplication.processEvents()
    return w


def _close(w):
    for doc in w.documents:
        doc.undo_stack.setClean()
    w.close()


def test_tool_bars_are_built_when_first_opened_and_the_last_used_at_start(qapp, tmp_path):
    w = _window(tmp_path / "profile")
    panes = w.tool_panes
    try:
        assert panes._built == set()  # a new profile: nothing used yet, nothing built
        redact = dict.__getitem__(panes.bars, "redact")
        assert not redact.actions()  # (empty, but already in the window's layout)
        panes.open("redact")
        assert "redact" in panes._built and redact.isVisible() and redact.actions()
        assert redact.apply_button is not None
        for key in ("comment", "organize", "stamp", "protect", "fill_sign", "export", "ocr"):
            panes.open(key)
        panes.close()
        recent = w.settings.recent_tools
        assert len(recent) == RECENT_TOOLS and recent[0] == "ocr" and "redact" not in recent
        assert w.symbol_actions["check"].isEnabled() is False  # (no document: built later, still in step)
    finally:
        _close(w)
    again = _window(tmp_path / "profile")
    try:
        assert again.tool_panes._built == set(recent)  # the tools used last are ready at start
        assert again.tool_panes.bars["redact"].actions()  # (asking for one builds it)
        assert "redact" in again.tool_panes._built
    finally:
        _close(again)


def test_page_cache_is_capped_by_size_but_keeps_the_pages_on_screen(qapp, tmp_path):
    doc = pymupdf.open()
    for n in range(12):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"page {n + 1}")
    doc.save(tmp_path / "pages.pdf")
    w = _window(tmp_path / "profile")
    try:
        w.open_file(tmp_path / "pages.pdf")
        view = w.view
        for _ in range(20):
            QCoreApplication.processEvents()
        on_screen = [i for i, item in enumerate(view._items)
                     if item.sceneBoundingRect().intersects(view._visible_scene_rect())]
        assert on_screen
        big = QPixmap(2000, 2600)  # ~20 MB each
        for index in range(12):
            view._cache[index] = (view._render_scale(), big)
        assert view.cache_bytes() > 200 * 1024 * 1024
        view.set_cache_limit(50)
        assert set(on_screen) <= set(view._cache)  # pages on screen always stay
        off_screen = [i for i in view._cache if i not in on_screen]
        assert view.cache_bytes() - len(on_screen) * view._pixmap_bytes(big) <= 50 * 1024 * 1024
        assert len(off_screen) <= 2
        assert view.cache_limit == 50 * 1024 * 1024
    finally:
        _close(w)


def test_page_cache_setting_in_preferences(qapp, tmp_path):
    from planetpdf.core.storage.settings import load_settings
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    w = _window(tmp_path / "profile")
    try:
        assert w.settings.page_cache_mb == 200 and w.view.cache_limit == 200 * 1024 * 1024
        dialog = PreferencesDialog(w)
        dialog.page_cache.setValue(500)
        dialog._save()
        assert w.view.cache_limit == 500 * 1024 * 1024
        assert load_settings(w.profile.settings_dir).page_cache_mb == 500
    finally:
        _close(w)
