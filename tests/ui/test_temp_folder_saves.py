"""Never save a document into a folder that gets emptied by itself (user 2026-11-24: "I opened a
file from a temporary location and when the temp location was closed, it deleted the file I had
been working on … Force a save outside the temp folder")."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from planetpdf.core import temp_folders as T
from planetpdf.ui.main_window import MainWindow


@pytest.fixture
def strict(monkeypatch):
    """The rule on (tests/conftest.py switches it off for pytest's own temporary folders)."""
    monkeypatch.delenv(T.ALLOW_ENV, raising=False)


def test_temporary_folders_are_recognized(strict, tmp_path):
    temp = Path(tempfile.gettempdir())
    assert T.temporary_reason(temp / "a.pdf") == "a temporary folder"
    zipped = temp / "b418a22c-137e_Updated Invoices.zip.128" / "PJ Invoices" / "INV.pdf"
    assert "zip" in T.temporary_reason(zipped)
    outlook = Path.home() / "AppData/Local/Microsoft/Windows/INetCache/Content.Outlook/AB12CD34/x.pdf"
    assert "Outlook" in T.temporary_reason(outlook)
    assert T.temporary_reason(Path.home() / "AppData/Local/Microsoft/Olk/Attachments/x.pdf")
    assert T.temporary_reason(Path.home() / "Documents" / "Invoices" / "INV.pdf") is None
    assert T.temporary_reason(Path.home() / "Documents" / "Temporary notes.pdf") is None
    assert T.temporary_reason(temp / ".." / "Templates.pdf") is None  # (beside Temp, not in it)


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
def test_short_names_are_spelled_out(strict):
    import ctypes

    long_temp = tempfile.gettempdir()
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(long_temp, buffer, 1024) and buffer.value != long_temp:
        assert T.is_temporary(Path(buffer.value) / "x.pdf")  # e.g. C:\Users\ROBERT~1\AppData\Local\Temp


def test_lasting_folder_skips_temporary_ones(strict, tmp_path):
    assert T.lasting_folder(tempfile.gettempdir()) != Path(tempfile.gettempdir())
    assert not T.is_temporary(T.lasting_folder(None))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.open_file(pdf_copy("text.pdf"))  # (in pytest's temporary folder)
    yield w
    w.undo_stack.setClean()
    w.close()


def _dirty(win):
    win.session.doc.set_metadata({"title": "changed"})
    win.session.mark_modified()
    win.undo_stack.resetClean()  # (anything to save)


def test_save_asks_where_to_keep_a_file_from_a_temporary_folder(win, strict, monkeypatch, tmp_path):
    original = win.session.path
    keep = tmp_path / "kept"  # (a lasting folder, as far as this test goes)
    keep.mkdir()
    monkeypatch.setattr(T, "temporary_reason",
                        lambda p: "a temporary folder" if Path(p).parent == original.parent else None)
    asked, offered = [], []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Save))
    monkeypatch.setattr(T, "lasting_folder", lambda *a: keep)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda parent, title, start, f: offered.append(start) or (str(keep / "INV.pdf"), f)))
    before = original.read_bytes()
    _dirty(win)
    assert win.save()
    assert "temporary folder" in asked[0] and offered == [str(keep / original.name)]  # (not the temporary one)
    assert win.session.path == keep / "INV.pdf" and win.session.path.is_file()
    assert original.read_bytes() == before  # the copy in the temporary folder isn't written


def test_save_as_into_a_temporary_folder_is_refused_and_asks_again(win, strict, monkeypatch, tmp_path):
    keep = tmp_path / "kept"
    keep.mkdir()
    temp_target = Path(tempfile.gettempdir()) / "planetpdf-test-never-written.pdf"
    choices = [str(temp_target), str(keep / "Fine.pdf")]
    warned = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a: (choices.pop(0), "")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[1])))
    monkeypatch.setattr(T, "temporary_reason",
                        lambda p: "a temporary folder" if Path(p).parent == temp_target.parent else None)
    assert win.save_as_dialog()
    assert warned == ["Temporary Folder"] and not choices
    assert not temp_target.exists() and (keep / "Fine.pdf").is_file()


def test_canceling_the_question_saves_nothing(win, strict, monkeypatch):
    monkeypatch.setattr(T, "temporary_reason", lambda p: "a temporary folder")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a: pytest.fail("no dialog")))
    _dirty(win)
    assert not win.save() and win.is_dirty()
