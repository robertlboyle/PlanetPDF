"""PDF from Scanner with a network scanner (AirScan / eSCL; Mac and Linux, 2026-10-14)."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core import escl_scanner as E
from planetpdf.ui.dialogs.scan import ScanDialog
from planetpdf.ui.main_window import MainWindow
from tests.core.test_escl_scanner import FakeScanner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake():
    scanner = FakeScanner(["red", "green"])
    scanner.busy_once = False
    yield scanner
    scanner.close()


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_scanning_is_offered_on_every_system(win):
    assert win.act_scan_create.isVisible() and win.act_scan_insert.isVisible()


def test_dialog_offers_what_the_scanner_can_do_and_remembers_it(win, fake):
    win.settings.scan_source, win.settings.scan_dpi = "feeder", 600
    dialog = ScanDialog(win, [fake.scanner])
    assert dialog.scanner.currentText() == "Test Scanner"
    assert [dialog.source.itemData(i) for i in range(dialog.source.count())] == ["glass", "feeder"]
    assert dialog.source.currentData() == "feeder"
    assert dialog.dpi.currentData() == 300  # (the feeder only does 300)
    dialog.source.setCurrentIndex(0)  # the glass: 300 and 600
    assert [dialog.dpi.itemData(i) for i in range(dialog.dpi.count())] == [300, 600]
    dialog.dpi.setCurrentIndex(1)
    choice = dialog.choice()
    assert (choice.source, choice.color, choice.dpi) == ("glass", "color", 600)
    assert (win.settings.scan_device, win.settings.scan_source, win.settings.scan_dpi) == (fake.scanner.url, "glass", 600)
    dialog.deleteLater()


def test_no_scanner_found_says_what_to_check(win):
    dialog = ScanDialog(win, [])
    assert "No scanners were found" in dialog.status.text() and not dialog.scan_button.isEnabled()
    if sys.platform == "darwin":
        assert "Local Network" in dialog.status.text()
    dialog.deleteLater()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows scans with WIA")
def test_insert_from_the_glass_page_by_page(win, fake, monkeypatch):
    from planetpdf.ui.dialogs.scan import ScanChoice

    caps = E.capabilities(fake.scanner)
    monkeypatch.setattr(win.extras, "choose_scanner", lambda: ScanChoice(fake.scanner, caps, "glass", "color", 300))
    answers = iter([True, False])  # "Scan another page?" yes, then no
    win.ask = lambda title, text: next(answers)
    before = win.session.page_count
    assert win.extras.insert_from_scanner()
    assert win.session.page_count == before + 2 and fake.jobs == 2
    assert win.undo_stack.undoText().startswith("Insert Pages from Scanner")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows scans with WIA")
def test_an_empty_feeder_is_explained(win, fake, monkeypatch):
    from planetpdf.ui.dialogs.scan import ScanChoice

    fake.pages = []
    caps = E.capabilities(fake.scanner)
    monkeypatch.setattr(win.extras, "choose_scanner", lambda: ScanChoice(fake.scanner, caps, "feeder", "color", 300))
    shown = []
    monkeypatch.setattr(win, "show_message", lambda title, text: shown.append(text))
    before = win.session.page_count
    assert not win.extras.insert_from_scanner()
    assert win.session.page_count == before and "sent no pages" in shown[0]
