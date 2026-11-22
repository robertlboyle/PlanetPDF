from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core import ocr
from planetpdf.ui.dialogs.ocr import OcrDialog
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.tasks import TaskCanceled, run_in_background
from tests.fixtures import generate as g

needs_ocr = pytest.mark.skipif(ocr.find_tessdata() is None, reason="Tesseract language data not installed")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy):
    win = MainWindow(tmp_path / "config")
    win.show()
    win.open_file(pdf_copy("scanned.pdf"))
    QCoreApplication.processEvents()
    yield win
    win.undo_stack.setClean()
    win.close()


def test_background_runner_returns_and_raises(qapp):
    assert run_in_background(None, "t", "x", lambda progress: (progress(0, 1), 42)[1]) == 42
    with pytest.raises(ValueError):
        run_in_background(None, "t", "x", lambda progress: (_ for _ in ()).throw(ValueError("boom")))


def test_ocr_dialog_scope(qapp):
    dialog = OcrDialog(6, [1, 2, 3], [4], ["deu", "eng"], "eng", 300)
    assert dialog.rb_missing.isChecked() and dialog.pages() == [1, 2, 3]
    assert dialog.language_code() == "eng" and dialog.dpi_value() == 300
    dialog.rb_range.setChecked(True)
    dialog.range_edit.line.setText("1,6")
    assert dialog.pages() == [0, 5]
    none_missing = OcrDialog(3, [], [], ["eng"], "eng", 200)
    assert none_missing.rb_range.isChecked() and not none_missing.rb_missing.isEnabled()


@needs_ocr
def test_run_ocr_is_one_undo_step(window):
    session = window.session
    assert window.redaction_panel.ocr_button.isVisibleTo(window.redaction_panel)  # scanned pages hint
    words = window.run_ocr(list(range(session.page_count)), "eng", 200)
    assert words > 0
    assert all(g.SSN in session.doc[i].get_text() for i in range(session.page_count))
    assert window.undo_stack.count() == 1 and window.is_dirty()
    assert not window.redaction_panel.ocr_button.isVisibleTo(window.redaction_panel)
    # Running again skips pages that already have OCR text.
    assert window.run_ocr([0], "eng", 200) == 0
    window.undo_stack.undo()
    assert all(not session.doc[i].get_text().strip() for i in range(session.page_count))
    assert not window.is_dirty()
    window.undo_stack.redo()
    assert g.SSN in session.doc[3].get_text()


@needs_ocr
def test_ocr_then_find_ssn_in_redaction_panel(window):
    window.run_ocr(list(range(window.session.page_count)), "eng", 200)
    panel = window.redaction_panel
    panel.preset_checks["ssn"].setChecked(True)
    panel.find_presets()
    assert panel.results.topLevelItemCount() == len(g.SCAN_ROTATIONS)


def test_canceled_task_raises(qapp):
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QProgressDialog

    def work(progress):
        for i in range(200):
            if not progress(i, 200):
                return "stopped"
            time.sleep(0.01)
        return "done"

    def press_cancel():
        # The Cancel button emits canceled(); calling cancel() directly would not.
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QProgressDialog):
                widget.canceled.emit()

    QTimer.singleShot(100, press_cancel)  # the user clicks Cancel while it runs
    with pytest.raises(TaskCanceled):
        run_in_background(None, "t", "x", work)


def test_progress_text_names_the_page():
    from planetpdf.ui.tasks import page_progress_text

    every = page_progress_text("OCR in progress", [0, 1, 2])
    assert every(0, 3) == "OCR in progress\nWorking on page 1 of 3"
    assert every(2, 3) == "OCR in progress\nWorking on page 3 of 3"
    assert every(3, 3) == "OCR in progress\nFinishing (3 of 3 pages done)…"
    some = page_progress_text("OCR in progress", [3, 6, 9])
    assert some(1, 3) == "OCR in progress\nWorking on page 2 of 3 (page 7 of the document)"
    assert "%" not in every(1, 3)
    exporting = page_progress_text("Exporting to a.png", [0, 1], verb="Exporting page")
    assert exporting(1, 2) == "Exporting to a.png\nExporting page 2 of 2"


def test_a_stage_shows_its_own_count(qapp):
    """progress(done, total, stage): "<label>\\n<stage> <done + 1> of <total>"; total 0 =
    a step that can't be counted (the stage alone, busy bar)."""
    from PySide6.QtWidgets import QProgressDialog

    seen = []

    def label():
        dialog = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QProgressDialog) and w.isVisible())
        return dialog.labelText()

    def work(progress):
        progress(0, 4, "Reading page")
        seen.append(label())
        progress(3, 4, "Writing page")
        seen.append(label())
        progress(0, 0, "Analyzing the page layout…")
        seen.append(label())
        return True

    run_in_background(None, "Export", "Converting to Word: a.docx", work)
    assert seen == ["Converting to Word: a.docx\nReading page 1 of 4", "Converting to Word: a.docx\nWriting page 4 of 4",
                    "Converting to Word: a.docx\nAnalyzing the page layout…"]

    from planetpdf.ui.tasks import run_with_progress

    seen.clear()
    run_with_progress(None, "Split", "Splitting a.pdf", work)  # (same dialog, on this thread)
    assert seen[0] == "Splitting a.pdf\nReading page 1 of 4"


def test_background_work_past_the_point_of_no_return_ignores_cancel(qapp):
    """``progress.commit()`` in the background (user 2026-10-23: redaction moved there): once
    the new file is being written, Cancel is gone and a late one changes nothing."""
    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QProgressDialog

    from planetpdf.ui.tasks import TaskCanceled

    def dialog():
        return next(w for w in QApplication.topLevelWidgets() if isinstance(w, QProgressDialog) and w.isVisible())

    def work(progress):
        progress(0, 2, "Checking page")
        progress.commit()
        QMetaObject.invokeMethod(dialog(), "canceled", Qt.ConnectionType.QueuedConnection)  # (the Cancel button)
        return progress(1, 2, "Writing page")  # (still True: past the point of no return)

    assert run_in_background(None, "Apply Redactions", "Redacting", work) is True

    def early(progress):
        progress(0, 2, "Checking page")
        QMetaObject.invokeMethod(dialog(), "canceled", Qt.ConnectionType.QueuedConnection)
        return progress(1, 2, "Checking page")

    with pytest.raises(TaskCanceled):
        run_in_background(None, "Apply Redactions", "Redacting", early)


def test_dialog_shows_each_page_before_it_is_worked_on(qapp):
    """The label must already name page N when the work for page N starts (the
    window freezes during the page, so a late update would never be seen)."""
    from PySide6.QtWidgets import QProgressDialog

    from planetpdf.ui.tasks import page_progress_text

    seen = []

    def work(progress):
        for n in range(3):
            progress(n, 3)
            dialog = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QProgressDialog) and w.isVisible())
            seen.append((dialog.labelText(), dialog.bar().isTextVisible() if hasattr(dialog, "bar") else False))
        progress(3, 3)
        return True

    run_in_background(None, "OCR", "OCR in progress", work, describe=page_progress_text("OCR in progress"))
    assert [text for text, _ in seen] == [f"OCR in progress\nWorking on page {n} of 3" for n in (1, 2, 3)]


def test_finished_task_is_not_reported_canceled(qapp):
    # Regression: closing the progress dialog emits canceled(); that must not count.
    assert run_in_background(None, "t", "x", lambda progress: "ok") == "ok"
