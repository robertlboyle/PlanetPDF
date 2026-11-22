"""Create PDF from File with one Word, Excel or PowerPoint file goes through Office (user report
2026-10-18: it was refused there, while Create Multiple PDF Files took it). Office itself is
replaced here (the real-Office tests are opt-in)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from planetpdf.core import office
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_a_word_file_goes_through_office(qapp, tmp_path, monkeypatch):
    source = tmp_path / "Letter.docx"
    source.write_bytes(b"not really Word")
    converted = []

    def fake_office(src, target):
        converted.append(src)
        doc = pymupdf.open()
        doc.new_page(width=612, height=792).insert_text((72, 72), "From Word")
        doc.save(target)

    monkeypatch.setattr(office, "to_pdf", fake_office)
    out = tmp_path / "Letter.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    w = MainWindow(tmp_path / "profile")
    try:
        assert w.create_pdf_from_file(source)
        assert converted == [source]
        with pymupdf.open(out) as doc:
            assert "From Word" in doc[0].get_text()
    finally:
        w.undo_stack.setClean()
        w.close()
