"""Create Multiple PDF Files, PDF Portfolio + the Attachments panel, Enhance (Phase C)."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.ui import batch_ui as BU
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    for other in MainWindow.open_windows():
        other.undo_stack.setClean()
        other.close()


def test_create_multiple_from_the_dialog(win, tmp_path, monkeypatch):
    Image.new("RGB", (120, 80), "green").save(tmp_path / "a.png")
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")

    def fill(dialog):
        dialog.add_paths([tmp_path / "a.png", tmp_path / "b.txt", tmp_path / "a.png"])  # (a duplicate is skipped)
        assert len(dialog.files()) == 2
        dialog.folder.setText(str(tmp_path / "pdfs"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BU.FileListDialog, "exec", fill)
    win.act_create_multiple.trigger()
    assert sorted(p.name for p in (tmp_path / "pdfs").iterdir()) == ["a.pdf", "b.pdf"]
    assert win.messages[-1].startswith("2 of 2 files done")


def test_portfolio_opens_on_its_attachments(win, tmp_path):
    Image.new("RGB", (60, 60), "red").save(tmp_path / "logo.png")
    (tmp_path / "terms.txt").write_text("terms", encoding="utf-8")
    target = win.batch.portfolio([tmp_path / "logo.png", tmp_path / "terms.txt"], tmp_path / "bundle.pdf")
    assert win.session.path == target.resolve()  # (the portfolio opens; here in this empty window)
    shown = win
    panel = shown.attachments_panel
    assert not shown._attachments_dock.isHidden()  # (it opens on its list of files)
    assert panel.list.count() == 2 and "Portfolio" in panel.heading.text()
    saved = []
    shown.save_attachment = lambda name, data: saved.append((name, data))
    panel.list.setCurrentRow(0)
    panel.save_selected()
    assert saved[0] == ("logo.png", (tmp_path / "logo.png").read_bytes())
    panel.list.setCurrentRow(1)
    panel.remove_selected()
    assert panel.list.count() == 1
    shown.undo_stack.undo()
    assert panel.list.count() == 2


def test_enhance_is_one_undo_step(win, tmp_path):
    image = np.full((1100, 850, 3), 170, np.uint8)
    cv2.putText(image, "SCANNED TEXT", (100, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 20), 4)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=cv2.imencode(".png", image)[1].tobytes())
    doc.save(tmp_path / "scan.pdf")
    win.open_file(tmp_path / "scan.pdf")
    before = win.session.doc[0].get_pixmap(dpi=20).pixel(2, 2)
    assert win.batch.enhance("scan", [0], color=False)
    after = win.session.doc[0].get_pixmap(dpi=20).pixel(2, 2)
    assert min(after) > min(before) + 40  # (the gray paper went white)
    assert win.undo_stack.undoText() == "Enhance Scanned Document"
    win.undo_stack.undo()
    assert win.session.doc[0].get_pixmap(dpi=20).pixel(2, 2) == before
