"""A file is open in one PlanetPDF only, and a second start never becomes a second copy
while the first is still starting (user 2026-10-22: two copies had one file open; closing
one asked nothing, while the other held the changes)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

import planetpdf
from planetpdf.ui import file_locks
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.single_instance import InstanceServer, instance_lock, send_to_running


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def locks(tmp_path):
    file_locks.configure(tmp_path / "open-files")
    yield tmp_path / "open-files"
    file_locks.release_all()
    file_locks.configure(None)


@pytest.fixture
def pdf(tmp_path, pdf_copy):
    target = tmp_path / "letter.pdf"
    shutil.copy(pdf_copy("text.pdf"), target)
    return target


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.show()
    QCoreApplication.processEvents()
    yield w
    for window in MainWindow.open_windows():
        for doc in window.documents:
            doc.undo_stack.setClean()
        window.close()


def _env():
    return dict(os.environ, PYTHONPATH=str(Path(planetpdf.__file__).parents[1]))


def _hold_in_other_process(folder: Path, path: Path) -> tuple[subprocess.Popen, int]:
    """Another PlanetPDF with the file open: it holds the file's lock until killed."""
    code = ("import sys, time; from pathlib import Path; from PySide6.QtCore import QCoreApplication; "
            "app = QCoreApplication([]); from planetpdf.ui import file_locks; "
            f"file_locks.configure(Path({str(folder)!r})); file_locks.sync([Path({str(path)!r})]); "
            "import os; print(os.getpid(), flush=True); time.sleep(60)")
    other = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=_env())
    return other, int(other.stdout.readline())  # (its own ID: the venv's python.exe starts the real one)


def _end(other: subprocess.Popen, pid: int, path: Path) -> None:
    """End the other PlanetPDF (its real process too) and wait until it's gone."""
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    other.kill()
    other.wait()
    end = time.time() + 10
    while file_locks.holder(path) == pid and time.time() < end:
        time.sleep(0.05)


def test_an_open_file_is_locked_and_freed_on_close(win, locks, pdf):
    win.open_paths([pdf])
    lock_file = locks / f"{file_locks.key(pdf)}.lock"
    assert lock_file.exists()
    assert file_locks.holder(pdf) is None  # (this process has it: its own tab is shown instead)
    win.close_document()
    QCoreApplication.processEvents()
    assert not lock_file.exists()


def test_a_file_open_in_another_planetpdf_is_not_opened_again(win, locks, pdf, monkeypatch):
    other, pid = _hold_in_other_process(locks, pdf)
    try:
        assert file_locks.holder(Path(str(pdf).upper() if sys.platform == "win32" else pdf)) == pid
        messages = []
        monkeypatch.setattr(win, "show_message", lambda title, text: messages.append(title))
        win.open_paths([pdf])  # (the other one doesn't listen: say so)
        assert not win.documents and messages == ["File Already Open"]
    finally:
        _end(other, pid, pdf)
    assert file_locks.holder(pdf) is None  # (its process is gone: free again)
    win.open_paths([pdf])
    assert len(win.documents) == 1


def test_the_other_planetpdf_is_asked_to_show_it(win, locks, pdf, monkeypatch):
    other, pid = _hold_in_other_process(locks, pdf)
    try:
        asked = []
        monkeypatch.setattr(file_locks, "show_in_holder", lambda path, pid: asked.append(pid) or True)
        monkeypatch.setattr(win, "show_message", lambda *a: pytest.fail("no message when it shows the file"))
        win.open_paths([pdf])
        assert asked == [pid] and not win.documents
    finally:
        _end(other, pid, pdf)


def test_files_handed_over_while_the_window_is_built_wait_for_it(qapp, pdf):
    server = InstanceServer(None, name=f"PlanetPDF-test-early-{os.getpid()}")
    try:
        code = ("from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
                "from planetpdf.ui.single_instance import send_to_running; "
                f"print(send_to_running([{str(pdf)!r}], name={server.name!r}))")
        second = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=_env())
        end = time.time() + 30
        while second.poll() is None and time.time() < end:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        QCoreApplication.processEvents()
        assert second.stdout.read().strip() == "True"
        received = []
        server.set_handler(received.append)
        assert received == [[pdf.resolve()]]
    finally:
        server.close()


def test_a_later_start_waits_for_a_copy_still_starting_but_not_for_none(qapp, tmp_path):
    name = f"PlanetPDF-test-wait-{os.getpid()}"
    lock = instance_lock(name)
    assert lock.tryLock(0)  # (nobody running: this start takes it at once, no waiting)
    lock.unlock()
    started = time.monotonic()
    assert not send_to_running([], name=name, wait=10, lock=lock)
    assert time.monotonic() - started < 3 and lock.isLocked()  # (free lock: this one is the copy now)
    lock.unlock()
