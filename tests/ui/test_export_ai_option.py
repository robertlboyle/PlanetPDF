"""Export's "Read the pages with AI (Unlimited-OCR)" choice: grayed with the reason where it can't
run (no component, no NVIDIA card), offered for Word, Rich Text, Text and HTML only."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from planetpdf.core import ai_ocr
from planetpdf.core.export import ExportFormat, ExportOptions
from planetpdf.ui.dialogs.export import ExportDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(fmt, ready, monkeypatch, checked=True):
    reason = "Uses the NVIDIA RTX 4070 (12 GB)." if ready else "No NVIDIA graphics card was found."
    monkeypatch.setattr(ai_ocr, "status", lambda: ai_ocr.Status(ready, reason))
    return ExportDialog(3, [0], False, True, ExportOptions(ai_reading=checked), fmt)


def test_grayed_with_the_reason_without_a_card(qapp, monkeypatch):
    dialog = _dialog(ExportFormat.DOCX, False, monkeypatch)
    assert not dialog.ai.isEnabled() and "NVIDIA" in dialog.ai.toolTip()
    assert not dialog.options().ai_reading


def test_chosen_for_word_and_text_only(qapp, monkeypatch):
    dialog = _dialog(ExportFormat.DOCX, True, monkeypatch)
    assert dialog.ai.isEnabled() and dialog.options().ai_reading
    assert "flowing document" in dialog.note.text()
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(ExportFormat.PNG))
    assert not dialog.options().ai_reading  # (pictures: not for the AI)
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(ExportFormat.TXT))
    assert dialog.options().ai_reading
