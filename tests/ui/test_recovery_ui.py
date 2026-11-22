from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core.page_marks import WatermarkSpec, add_watermark
from planetpdf.ui.commands import PageContentCommand
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.recovery import RecoveryDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, path=None):
    win = MainWindow(tmp_path / "profile", tmp_path / "recovery")
    win.show()
    if path is not None:
        win.open_file(path)
    QCoreApplication.processEvents()
    return win


def _edit(win):
    win.push_command(PageContentCommand(win, [0], lambda s: add_watermark(s, [0], WatermarkSpec(text="DRAFT")), "Watermark"))


def test_autosave_only_while_dirty_and_cleared_on_save(qapp, tmp_path, pdf_copy):
    win = _window(tmp_path, pdf_copy("text.pdf"))
    saver = win.autosave
    assert saver.timer.isActive() and saver.timer.interval() == 5 * 60_000
    assert not saver.tick()  # nothing to save
    _edit(win)
    assert saver.tick()
    [entry] = saver.store.entries()
    assert "DRAFT" in pymupdf.open(entry.pdf)[0].get_text() and entry.original == win.session.path
    assert saver.leftovers() == []  # this window's own live copy is never offered
    assert win.save()
    assert saver.store.entries() == []
    _edit(win)
    saver.tick()
    win.undo_stack.undo()  # back to the saved state
    saver.tick()
    assert saver.store.entries() == []
    win.undo_stack.setClean()
    win.close()


def test_leftover_after_crash_is_offered_and_restored(qapp, tmp_path, pdf_copy, monkeypatch):
    first = _window(tmp_path, pdf_copy("text.pdf"))
    _edit(first)
    first.autosave.tick()
    original = first.session.path
    # a second, live window must not offer the first one's copy
    second = _window(tmp_path)
    assert second.autosave.leftovers() == []
    # "crash": the first window's lock goes away without the copy being discarded
    first.document.autosave_lock.unlock()
    first.document.autosave_entry = None
    [entry] = second.autosave.leftovers()
    assert entry.original == original
    dialog = RecoveryDialog(second, second.autosave, [entry])
    dialog._restore()
    assert dialog.restored == original.with_name("text (recovered).pdf")
    assert second.session.path == dialog.restored and "DRAFT" in second.session.doc[0].get_text()
    assert "DRAFT" not in pymupdf.open(original)[0].get_text()  # original untouched
    assert second.autosave.store.entries() == []
    for win in (first, second):
        win.undo_stack.setClean()
        win.close()


def test_autosave_off_in_preferences(qapp, tmp_path):
    win = _window(tmp_path)
    win.settings.autosave_minutes = 0
    win.autosave.apply_settings()
    assert not win.autosave.timer.isActive()
    win.close()
