"""PDF Standards tool (user screenshot 2026-10-04): Save as PDF/A, PDF/X, PDF/E, Check
Compliance (not "Preflight"); only real problems fail; Fix and Save makes a checked copy."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QToolButton

from planetpdf.core import compliance as C
from planetpdf.core import ghostscript
from planetpdf.ui.dialogs.compliance import ComplianceDialog
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Hello")
    page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(10, 10, 50, 50), "file": "x.exe"})
    path = tmp_path / "plain.pdf"
    doc.save(path)
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(path)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _item(dialog, title):
    return next(dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
                if dialog.tree.topLevelItem(i).text(0) == title)


def test_bar_has_acrobats_commands(win):
    win.tool_panes.open("standards")
    bar = win.tool_panes.bars["standards"]
    assert bar.isVisible()
    texts = [bar.widgetForAction(a).text() for a in bar.actions() if isinstance(bar.widgetForAction(a), QToolButton)]
    assert texts == ["Save as PDF/A", "Save as PDF/X", "Save as PDF/E", "Check Compliance"]


def test_check_compliance_dialog(win, tmp_path, monkeypatch):
    dialog = ComplianceDialog(win, "pdfa")
    assert dialog.windowTitle() == "Check Compliance"
    result = dialog.run()
    assert not result.passed
    assert dialog.convert.text() == "Fix and Save as PDF/A…"
    fonts = _item(dialog, "All fonts are embedded")
    assert fonts.text(1) == "Fail"
    texts = [fonts.child(i).text(0) for i in range(fonts.childCount())]
    assert any(t.startswith("How to fix: Fix and Save embeds them") for t in texts)
    assert texts[-1] == "Pages: 1"
    dialog._go(fonts.child(fonts.childCount() - 1))
    assert _item(dialog, "Comments and fields print as they look").text(1) == "N/A"
    assert _item(dialog, "Color profile (output intent)").text(1) == "Added when saved"
    report = tmp_path / "report.txt"
    monkeypatch.setattr("planetpdf.ui.dialogs.compliance.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(report), ""))
    dialog._save_report()
    assert "[FAIL] All fonts are embedded" in report.read_text(encoding="utf-8")
    dialog.profile.setCurrentIndex(dialog.profile.findData("pdfe"))  # (checks again at once)
    assert dialog.result.profile == "pdfe"
    dialog.close()


@pytest.mark.skipif(ghostscript.find() is None, reason="needs Ghostscript")
def test_fix_and_save(win, tmp_path):
    dialog = ComplianceDialog(win, "pdfa")
    dialog.run()
    result = dialog.fix(tmp_path / "fixed.pdf")
    assert result.complete
    assert dialog.fixed_label.isVisibleTo(dialog) and "fixed.pdf" in dialog.fixed_label.text()
    assert "Removed 1 script(s) or forbidden action(s)" in dialog.fixed_label.text()
    assert dialog.open_fixed.isVisibleTo(dialog)
    assert C.LABELS[C.FAIL] not in [dialog.tree.topLevelItem(i).text(1) for i in range(dialog.tree.topLevelItemCount())]
    dialog.close()
