from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QRectF, Qt
from PySide6.QtWidgets import QApplication

from planetpdf.core.document import PasswordRequired, PdfSession
from planetpdf.core.optimize import ReduceOptions
from planetpdf.core.page_marks import WatermarkSpec
from planetpdf.ui.commands import CropCommand, PageContentCommand, SetMetadataCommand, SetProtectionCommand
from planetpdf.ui.dialogs.document_tools import HeaderFooterDialog, ProtectDialog, PropertiesDialog
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "profile")
    win.resize(1100, 850)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def test_protect_via_dialog_then_save(window):
    dialog = ProtectDialog(window)
    dialog.open_pw.setText("pw1")
    dialog.open_pw2.setText("pw1")
    dialog._accept()
    assert dialog.result() == ProtectDialog.DialogCode.Accepted
    window.push_command(SetProtectionCommand(window, dialog.protection(), "Protect"))
    assert window.is_dirty()
    assert window.save()
    with pytest.raises(PasswordRequired):
        PdfSession.open(window.session.path)
    window.remove_protection()
    assert window.save()
    PdfSession.open(window.session.path).close()


def test_protect_dialog_validation(qapp):
    dialog = ProtectDialog()
    dialog.open_pw.setText("a")
    dialog.open_pw2.setText("b")
    dialog._accept()
    assert "don't match" in dialog.error.text()
    dialog.open_pw2.setText("a")
    dialog.allow["allow_print"].setChecked(False)
    dialog._accept()
    assert "permissions password" in dialog.error.text()


def test_page_numbers_undo_redo(window):
    dialog = HeaderFooterDialog(window.session.page_count, [], "page_numbers", window)
    pages, spec = dialog.pages(), dialog.spec()
    from planetpdf.core.page_marks import add_header_footer

    window.push_command(PageContentCommand(window, pages, lambda s: add_header_footer(s, pages, spec), "Add Page Numbers"))
    assert "Page 2 of 3" in window.session.doc[1].get_text()
    window.undo_stack.undo()
    assert "Page 2 of 3" not in window.session.doc[1].get_text()
    window.undo_stack.redo()
    assert "Page 3 of 3" in window.session.doc[2].get_text()
    assert window.save()
    assert "Page 1 of 3" in pymupdf.open(window.session.path)[0].get_text()


def test_watermark_command(window):
    from planetpdf.core.page_marks import add_watermark

    spec = WatermarkSpec(text="SAMPLE")
    window.push_command(PageContentCommand(window, [0], lambda s: add_watermark(s, [0], spec), "Add Watermark"))
    assert "SAMPLE" in window.session.doc[0].get_text()
    window.undo_stack.undo()
    assert "SAMPLE" not in window.session.doc[0].get_text()


def test_crop_command_changes_layout(window):
    width = window.view._items[0].boundingRect().width()
    window.push_command(CropCommand(window, [0], margins=(72, 0, 72, 0)))
    assert window.view._items[0].boundingRect().width() == pytest.approx(width - 144)
    window.undo_stack.undo()
    assert window.view._items[0].boundingRect().width() == pytest.approx(width)


def test_crop_by_drawing_a_box(window):
    window.crop_dialog = lambda: None  # not used; drive the drawn-box path directly
    window.markup.pick_box(lambda page, rect: window.push_command(CropCommand(window, [page], rect=rect)), "")
    window.view.shapeDrawn.emit("box", 0, QRectF(50, 50, 300, 300))
    assert window.session.doc[0].rect.width == pytest.approx(300, abs=1)


def test_split(window, tmp_path):
    from planetpdf.core import pages as P

    paths = window.split_to([("", grp) for grp in P.split_groups_every(3, 1)], tmp_path / "parts")
    assert len(paths) == 3 and all(pymupdf.open(p).page_count == 1 for p in paths)


def test_reduce_size_reports_sizes(window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "show_message", lambda title, text: shown.append(text))
    result = window.reduce_size_to(tmp_path / "small.pdf", ReduceOptions(image_dpi=96))
    assert result is not None and (tmp_path / "small.pdf").exists()
    assert "Before:" in shown[0] and "After:" in shown[0]
    assert window.reduce_size_to(window.session.path, ReduceOptions()) is None  # never over the open file


def test_compress_pdf_is_a_tool_that_shrinks_this_document_in_one_undo_step(qapp, tmp_path, monkeypatch):
    """User 2026-10-03: "optimize the size of a file … That should be a tool. Some files
    especially scanned files are huge"."""
    from planetpdf.ui.dialogs.document_tools import ReduceSizeDialog
    from tests.core.test_optimize import scanned_pdf

    path = scanned_pdf(tmp_path / "scans.pdf", ("text", "color"))
    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(path)
    try:
        win.tool_panes.strip_actions["compress"].trigger()
        QCoreApplication.processEvents()
        assert win.tool_panes.bars["compress"].isVisibleTo(win)
        dialog = ReduceSizeDialog(path.stat().st_size, win, pages=2, scanned=2)
        assert dialog.in_place.isChecked() and dialog.black_white.isChecked() and dialog.options().scans_gray
        dialog.deleteLater()
        shown = []
        monkeypatch.setattr(win, "show_message", lambda title, text: shown.append(text))
        assert win.compress_document(ReduceOptions(), path.stat().st_size)
        assert "smaller" in shown[0] and "1 scanned page in black and white" in shown[0]
        assert win.undo_stack.undoText() == "Compress PDF" and not win.undo_stack.isClean()
        filters = [img[8] for page in win.session.doc for img in page.get_images(full=True)]
        assert filters[0] == "CCITTFaxDecode"
        win.undo_stack.undo()
        assert [img[8] for page in win.session.doc for img in page.get_images(full=True)][0] == "DCTDecode"
        win.undo_stack.redo()
        before = path.stat().st_size
        assert win.save() and path.stat().st_size < before * 0.75  # (the color page stays as it was)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_optimize_scans_is_a_tool_of_its_own(qapp, tmp_path, monkeypatch):
    """User 2026-10-03: scan clean-up as its own tool (black and white, straighten, page
    direction, blank pages, bleed-through), and Recognize Text offering to clean first."""
    from tests.core.test_scan_cleanup import _blank_back, _crooked, _pdf

    path = _pdf(tmp_path / "rough.pdf", [_crooked(2.0), _blank_back()])
    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(path)
    try:
        win.tool_panes.strip_actions["scans"].trigger()
        QCoreApplication.processEvents()
        bar = win.tool_panes.bars["scans"]
        assert bar.isVisibleTo(win)
        win.scan_tools.black_white()
        assert win.undo_stack.undoText() == "Black & White"
        assert win.session.doc[0].get_images(full=True)[0][8] == "CCITTFaxDecode"
        win.undo_stack.undo()
        assert win.session.doc[0].get_images(full=True)[0][8] == "DCTDecode"
        asked = []
        monkeypatch.setattr(win.scan_tools, "choose_blank_pages", lambda blank: asked.append(blank) or blank)
        assert win.scan_tools.remove_blank_pages() == 1
        assert asked == [[1]] and win.session.page_count == 1
        # Recognize Text on a crooked scan: offers to straighten and clean first
        monkeypatch.setattr(win.scan_tools, "ask_cleanup_first", lambda tilt, specks: "clean")
        assert win.scan_tools.suggest_cleanup([0]) == "clean"
        assert win.undo_stack.undoText() == "Straighten & Clean"
        from planetpdf.core import scan_cleanup as S

        assert S.looks_rough(win.session.doc, [0]) is None  # (straight and clean now)
    finally:
        win.undo_stack.setClean()
        win.close()


def test_optimize_scans_dialog_is_simple_and_automatic_until_advanced(qapp, tmp_path):
    """User 2026-10-03: "Default would be all automatic"; manual versions with a preview of
    each step in the advanced mode."""
    from planetpdf.core import scan_cleanup as S
    from planetpdf.ui.scan_ui import BlankPagesDialog, OptimizeScansDialog
    from tests.core.test_scan_cleanup import _blank_back, _crooked, _pdf

    win = MainWindow(tmp_path / "profile")
    win.show()
    win.open_file(_pdf(tmp_path / "rough.pdf", [_crooked(2.0), _blank_back()]))
    try:
        dialog = OptimizeScansDialog(win, [0, 1], 0, ocr_available=False)
        assert not dialog.is_advanced and dialog.simple.isVisibleTo(dialog) and not dialog.advanced.isVisibleTo(dialog)
        options = dialog.options()
        assert all(options.mode(s) == "auto" for s in ("straighten", "borders", "clean", "bleed_through"))
        assert options.direction == "off"  # (no OCR here: it can't tell which way up)
        dialog.checks["clean"].setChecked(False)
        assert dialog.options().clean == "off" and dialog.panels["clean"].mode() == "off"
        dialog.set_advanced(True)
        straighten = dialog.panels["straighten"]
        straighten.set_mode("manual")
        straighten.value.setValue(-1.0)
        dialog._advanced_changed()
        assert dialog.options().straighten == "manual" and dialog.options().angle == -1.0
        dialog.update_preview()
        dialog.step_list.setCurrentRow(S.STEPS.index("straighten"))
        assert dialog.before_title.text() == "Before: straighten" and "1.0°" in dialog.detail.text()
        dialog.set_advanced(False)  # (back to simple: manual steps go back to automatic)
        assert dialog.options().straighten == "auto"
        dialog.deleteLater()
        blank = BlankPagesDialog(win, [1])
        assert blank.chosen() == [1] and blank.delete_button.text() == "Delete 1 Page"
        blank.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        assert blank.chosen() == [] and not blank.delete_button.isEnabled()
        blank.deleteLater()
    finally:
        win.undo_stack.setClean()
        win.close()


def test_properties_dialog_and_undo(window):
    from planetpdf.core import properties as PR

    old = PR.get_metadata(window.session)
    dialog = PropertiesDialog(old, PR.facts(window.session), window)
    dialog.edits["title"].setText("New Title")
    window.push_command(SetMetadataCommand(window, old, dialog.values()))
    assert PR.get_metadata(window.session)["title"] == "New Title"
    window.undo_stack.undo()
    assert PR.get_metadata(window.session)["title"] == old["title"]
