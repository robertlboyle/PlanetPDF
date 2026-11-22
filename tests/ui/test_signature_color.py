"""The chosen ink color is what you see and what gets placed (user bug report)."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QPointF
from PySide6.QtWidgets import QApplication

from planetpdf.core.signing.visual import INK_BLACK, INK_BLUE, ink_color, recolor_ink, signature_source_pdf, trim_and_clear_background
from planetpdf.ui.dialogs.signature_capture import SignatureCaptureDialog
from planetpdf.ui.dialogs.signature_manager import SignatureManagerDialog
from planetpdf.ui.main_window import MainWindow

RED = (0.8, 0.1, 0.1)
LIGHT = (0.95, 0.85, 0.2)  # a light yellow ink


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _scan_png() -> bytes:
    """A 'photo' of a signature: dark gray pen on off-white paper."""
    image = Image.new("RGB", (300, 100), (245, 243, 238))
    for x in range(30, 270):
        for y in range(45, 55):
            image.putpixel((x, y), (40, 40, 45))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _close(a, b, tol=0.03):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_recolor_keeps_transparency():
    png = recolor_ink(trim_and_clear_background(_scan_png()), RED)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    assert image.getpixel((0, 0))[3] == 0  # paper stays transparent
    assert _close(ink_color(png), RED)


def test_import_is_shown_and_saved_in_the_chosen_color(qapp):
    dialog = SignatureCaptureDialog()
    dialog.tabs.setCurrentIndex(2)
    dialog.use_image(_scan_png())
    dialog.set_ink(INK_BLUE)
    assert _close(ink_color(dialog.build_png()), INK_BLUE)
    shown = dialog.import_preview.pixmap().toImage()
    colors = {shown.pixelColor(x, shown.height() // 2).name() for x in range(shown.width())}
    assert any(c != "#000000" and c.startswith("#0") for c in colors)  # the preview is blue now
    dialog.set_ink(RED)
    assert _close(ink_color(dialog.build_png()), RED)
    assert "Custom" in dialog.ink.currentText()


def test_draw_and_type_in_any_color_even_light_ones(qapp):
    dialog = SignatureCaptureDialog(default_name="Robert Boyle")
    dialog.set_ink(LIGHT)
    png = dialog.build_png()  # Type tab (a default name was given)
    assert _close(ink_color(png), LIGHT)
    assert max(Image.open(io.BytesIO(png)).getchannel("A").tobytes()) == 255  # full-strength ink, not faded
    dialog.tabs.setCurrentIndex(0)
    pad = dialog.pad
    pad.mousePressEvent(type("E", (), {"position": lambda self: QPointF(40, 80)})())
    for x in range(40, 300, 10):
        pad.mouseMoveEvent(type("E", (), {"position": lambda self, x=x: QPointF(x, 80 + (x % 20))})())
    pad.mouseReleaseEvent(type("E", (), {"position": lambda self: QPointF(300, 80)})())
    assert _close(ink_color(dialog.build_png()), LIGHT)
    dialog.set_ink(INK_BLACK)
    assert dialog.ink.currentText() == "Black" and _close(ink_color(dialog.build_png()), INK_BLACK)


def test_placed_signature_keeps_its_color(qapp, tmp_path):
    png = recolor_ink(_scan_png(), RED)
    source = signature_source_pdf(png)
    page = pymupdf.open(stream=source, filetype="pdf")[0]
    pix = page.get_pixmap(dpi=72)
    r, g, b = pix.pixel(pix.width // 2, pix.height // 2)[:3]
    assert _close((r / 255, g / 255, b / 255), RED, tol=0.06)


def test_change_color_of_a_saved_signature(qapp, tmp_path):
    win = MainWindow(tmp_path / "profile")
    item = win.signature_library.add(recolor_ink(_scan_png(), INK_BLACK), "signature", "Mine")
    dialog = SignatureManagerDialog(win)
    dialog.list.setCurrentRow(0)
    assert dialog.color_button.isEnabled()
    dialog.recolor_selected(INK_BLUE)
    assert _close(ink_color(win.signature_library.png(item)), INK_BLUE)
    assert dialog.list.currentRow() == 0
    QCoreApplication.processEvents()
    win.close()
