"""A printout from the PlanetPDF printer arrives (user 2026-10-18): the Save dialog (starting in
the folder used last, "Open PDF after saving" ticked by default), or a default folder; Cancel."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core import printouts as P
from planetpdf.ui import printouts
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv(P.ENV_FOLDER, raising=False)
    monkeypatch.delenv(P.ENV_NAME, raising=False)
    inbox = tmp_path / "printouts"
    monkeypatch.setattr(printouts, "inbox", lambda: inbox)
    monkeypatch.setattr(printouts.paths, "documents_dir", lambda: tmp_path / "Documents")
    (tmp_path / "Documents").mkdir()
    w = MainWindow(tmp_path / "profile")
    monkeypatch.setattr(w, "save_settings_now", lambda: None)
    w.show()
    QCoreApplication.processEvents()
    w.inbox = inbox
    yield w
    w.undo_stack.setClean()
    w.close()


def _printout(win, name="Quote 1043", job=1) -> Path:
    folder = win.inbox / f"job-{job}-1"
    folder.mkdir(parents=True)
    path = folder / f"{name}.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 72), name)
    doc.save(path)
    return path


def test_asks_where_starting_in_the_last_folder(win, tmp_path, monkeypatch):
    asked = []
    chosen = tmp_path / "Quotes"
    chosen.mkdir()

    def answer(window, suggested):
        asked.append(suggested)
        return chosen / suggested.name.replace("Quote 1043", "Quote 1043 signed")

    monkeypatch.setattr(printouts, "ask_open", lambda w, d: (True, False))
    monkeypatch.setattr(printouts, "ask_where", answer)
    printout = _printout(win)
    win.open_paths([printout])
    assert asked == [tmp_path / "Documents" / "Quote 1043.pdf"]  # (Documents the first time)
    saved = chosen / "Quote 1043 signed.pdf"
    assert saved.is_file() and not printout.exists() and not printout.parent.exists()
    assert win.session is not None and Path(win.session.path) == saved  # (opened)
    assert win.settings.print_last_folder == str(chosen)
    win.open_paths([_printout(win, "Invoice 7", job=2)])
    assert asked[-1] == chosen / "Invoice 7.pdf"  # (the folder used last)


def test_asked_before_the_dialog_and_the_answer_can_be_remembered(win, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(printouts, "ask_open", lambda w, d: order.append("open?") or (False, True))
    monkeypatch.setattr(printouts, "ask_where", lambda w, s: order.append("where?") or tmp_path / "Plain.pdf")
    win.open_paths([_printout(win)])
    assert order == ["open?", "where?"]
    assert (tmp_path / "Plain.pdf").is_file() and win.session is None  # (saved, not opened)
    assert (win.settings.print_open_ask, win.settings.print_open_after) == (False, False)  # (remembered)
    monkeypatch.setattr(printouts, "ask_open", lambda w, d: pytest.fail("not asked again"))
    monkeypatch.setattr(printouts, "ask_where", lambda w, s: tmp_path / "Plain 2.pdf")
    win.open_paths([_printout(win, job=2)])
    assert (tmp_path / "Plain 2.pdf").is_file() and win.session is None


def test_cancel_discards_it(win, monkeypatch):
    monkeypatch.setattr(printouts, "ask_open", lambda w, d: (True, False))
    monkeypatch.setattr(printouts, "ask_where", lambda w, s: None)
    printout = _printout(win)
    win.open_paths([printout])
    assert not printout.exists() and win.session is None


def test_a_default_folder_and_template_skip_the_dialog(win, tmp_path, monkeypatch):
    monkeypatch.setattr(printouts, "ask_where", lambda *a: pytest.fail("no dialog with a default folder"))
    win.settings.print_open_ask = False
    win.settings.print_folder = str(tmp_path / "Prints")
    win.settings.print_name_template = "Copy of {document}"
    for job in (1, 2):
        win.open_paths([_printout(win, job=job)])
    names = sorted(p.name for p in (tmp_path / "Prints").iterdir())
    assert names == ["Copy of Quote 1043 (2).pdf", "Copy of Quote 1043.pdf"]
    assert len(win.documents) == 2


def test_waiting_printouts_open_at_start(win, tmp_path, monkeypatch):
    monkeypatch.setattr(printouts, "ask_open", lambda w, d: (True, False))
    monkeypatch.setattr(printouts, "ask_where", lambda w, s: tmp_path / s.name)
    _printout(win, "Early", job=1)
    printouts.open_waiting(win)
    assert (tmp_path / "Early.pdf").is_file() and len(win.documents) == 1


def test_preferences_page(win, monkeypatch):
    from planetpdf.ui.dialogs.preferences import PreferencesDialog

    dialog = PreferencesDialog(win)
    assert dialog.print_ask.isChecked() and dialog.print_open.currentData() == "ask"
    dialog.print_folder.setText(r"D:\Prints")
    dialog.print_to_folder.setChecked(True)
    dialog.print_name.setText("{date} {document}")
    dialog.print_open.setCurrentIndex(dialog.print_open.findData("keep"))
    dialog._save()
    s = win.settings
    assert (s.print_folder, s.print_name_template, s.print_open_ask, s.print_open_after) ==         (r"D:\Prints", "{date} {document}", False, False)
    dialog.close()


def test_the_question_has_a_remember_box(win):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    seen = {}

    def answer():
        box = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible())
        seen["remember"] = box.checkBox().text()
        box.checkBox().setChecked(True)
        next(b for b in box.buttons() if b.text() == "Don't Open It").click()

    QTimer.singleShot(200, answer)
    assert printouts.ask_open(win, "Quote 1043") == (False, True)
    assert seen["remember"].startswith("Remember my answer")
