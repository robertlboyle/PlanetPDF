"""Redaction UI wiring (offscreen): area tool, search → marks, review, undo, apply."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton

from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.sanitize import SanitizeOptions
from planetpdf.ui.main_window import MainWindow
from tests.fixtures import generate as g


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, pdf_copy, monkeypatch):
    win = MainWindow(tmp_path / "config")
    win.resize(1100, 800)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    shown = []
    monkeypatch.setattr(win, "_show_report", lambda dialog: shown.append(dialog))
    win.reports = shown
    yield win
    win.undo_stack.setClean()
    win.close()


def _marks(win):
    return M.list_marks(win.session)


def test_area_tool_drag_creates_mark(window):
    view = window.view
    window.act_redact_area.setChecked(True)
    assert view.area_tool and window.redaction_panel.area_button.isChecked()

    page_item = view._items[0]
    top_left = view.mapFromScene(page_item.mapToScene(72, 60))
    bottom_right = view.mapFromScene(page_item.mapToScene(250, 130))

    def send(kind, pos, buttons):
        event = QMouseEvent(kind, pos, view.viewport().mapToGlobal(pos), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)

    send(QMouseEvent.Type.MouseButtonPress, top_left, Qt.MouseButton.LeftButton)
    send(QMouseEvent.Type.MouseMove, bottom_right, Qt.MouseButton.LeftButton)
    send(QMouseEvent.Type.MouseButtonRelease, bottom_right, Qt.MouseButton.NoButton)

    marks = _marks(window)
    assert len(marks) == 1 and marks[0].label == "Area" and marks[0].page == 0
    assert g.SSN in M.mark_text(window.session, marks[0])
    assert window.redaction_panel.marks_tree.topLevelItemCount() == 1
    assert window.is_dirty()
    window.undo_stack.undo()
    assert _marks(window) == []
    assert not window.is_dirty()


def test_area_on_rotated_page_maps_back_to_unrotated(window):
    window.rotate_pages([0], 90)
    page = window.session.doc[0]
    target = page.search_for(g.SSN)[0]  # unrotated coordinates
    shown = M.to_display(page, tuple(target))  # where it appears on screen
    window._on_area_drawn(0, QRectF(shown.x0 - 1, shown.y0 - 1, shown.width + 2, shown.height + 2))
    mark = _marks(window)[0]
    assert M.mark_text(window.session, mark) == g.SSN


def test_search_presets_then_mark(window):
    panel = window.redaction_panel
    panel.preset_checks["ssn"].setChecked(True)
    panel.preset_checks["email"].setChecked(True)
    panel.find_presets()
    assert panel.results.topLevelItemCount() == 2 * g.TEXT_PAGE_COUNT
    panel.results.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)  # skip one
    panel.mark_checked_results()
    assert len(_marks(window)) == 2 * g.TEXT_PAGE_COUNT - 1
    assert window.undo_stack.count() == 1  # one undo step for the batch
    # Searching again shows the marked ones as already marked (unchecked).
    panel.find_presets()
    checked = sum(
        panel.results.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
        for i in range(panel.results.topLevelItemCount())
    )
    assert checked == 1


def test_text_search_with_bad_regex_reports_error(window):
    panel = window.redaction_panel
    panel.mode_combo.setCurrentIndex(1)
    panel.search_edit.setText("(broken")
    panel.find_text()
    assert "Invalid regular expression" in panel.results_label.text()


def test_review_select_and_remove_with_undo(window):
    panel = window.redaction_panel
    panel.search_edit.setText(g.EMAIL)
    panel.find_text()
    panel.mark_checked_results()
    assert panel.marks_tree.topLevelItemCount() == g.TEXT_PAGE_COUNT
    panel.marks_tree.topLevelItem(1).setSelected(True)
    selected = panel.selected_mark_ids()
    assert window.view.selected_marks == set(selected)
    panel.remove_selected()
    assert len(_marks(window)) == g.TEXT_PAGE_COUNT - 1
    window.undo_stack.undo()
    assert len(_marks(window)) == g.TEXT_PAGE_COUNT
    window.undo_stack.redo()
    assert len(_marks(window)) == g.TEXT_PAGE_COUNT - 1


def test_marks_follow_page_moves(window):
    window.add_marks([M.make_spec(2, [(72, 60, 200, 80)], "Area")])
    window.move_pages([2], 0)
    assert _marks(window)[0].page == 0
    assert window.redaction_panel.marks_tree.topLevelItem(0).text(0) == "1"
    assert list(window.view._overlays)  # overlay rebuilt on the moved page


def test_apply_writes_verified_copy_and_keeps_original(window, tmp_path):
    panel = window.redaction_panel
    panel.preset_checks["ssn"].setChecked(True)
    panel.find_presets()
    panel.mark_checked_results()
    original = window.session.path.read_bytes()
    target = tmp_path / "text_redacted.pdf"
    assert window.redact_to(target, SanitizeOptions())
    report = window.reports[0]
    assert window.last_redaction.report.passed
    assert window.session.path.name == "text.pdf"  # still the original
    assert window.session.path.read_bytes() == original
    with pymupdf.open(target) as doc:
        assert all(g.SSN not in page.get_text() for page in doc)
    assert report is not None


def test_apply_over_original_reloads(window, monkeypatch):
    window.add_marks([M.make_spec(0, [tuple(window.session.doc[0].search_for(g.SSN)[0])], "Area")])
    path = window.session.path
    # (the number is on other pages too, unmarked: a warning, so the report asks first; Replace)
    monkeypatch.setattr(window, "_show_report", lambda dialog: setattr(dialog, "keep_requested", True))
    assert window.redact_to(path, SanitizeOptions())
    assert window.undo_stack.count() == 0
    assert not window.is_dirty()
    assert _marks(window) == []
    assert g.SSN not in window.session.doc[0].get_text()


def _failing_checks(monkeypatch):
    from planetpdf.core.redaction import apply as A
    from planetpdf.core.redaction.verify import Check, Status, VerificationReport

    def failed(path, *args, **kwargs):
        return VerificationReport(path, [Check(Status.FAIL, "Text remains under the box")])

    monkeypatch.setattr(A, "verify_redaction", failed)


def test_a_failed_redaction_saves_nothing(window, tmp_path, monkeypatch):
    """Security review 2026-11-21 ("redaction fails open"): failed checks = no file, nothing replaced."""
    _failing_checks(monkeypatch)
    window.add_marks([M.make_spec(0, [tuple(window.session.doc[0].search_for(g.SSN)[0])], "Area")])
    target = tmp_path / "out.pdf"
    assert not window.redact_to(target, SanitizeOptions())
    assert not target.exists() and not list(tmp_path.glob("*.tmp*"))
    held = window.reports[0]
    assert held.keep_requested is False
    # over the original: the original stays, and there's no Save Anyway
    path, original = window.session.path, window.session.path.read_bytes()
    assert not window.redact_to(path, SanitizeOptions())
    assert path.read_bytes() == original and _marks(window)
    assert not any(b.text().startswith("Save Anyway") for b in window.reports[1].findChildren(QPushButton))


def test_save_anyway_keeps_a_failed_redaction(window, tmp_path, monkeypatch):
    _failing_checks(monkeypatch)
    window.add_marks([M.make_spec(0, [tuple(window.session.doc[0].search_for(g.SSN)[0])], "Area")])
    monkeypatch.setattr(window, "_show_report", lambda dialog: setattr(dialog, "keep_requested", True))
    target = tmp_path / "out.pdf"
    assert not window.redact_to(target, SanitizeOptions())
    assert target.exists()


def test_redaction_options_are_remembered(window, tmp_path):
    panel = window.redaction_panel
    panel.overlay_check.setChecked(True)
    panel.overlay_edit.setText("WITHHELD")
    panel.touching_check.setChecked(True)
    panel.save_options()
    assert window.settings.redact_overlay_text == "WITHHELD"
    opts = panel.options()
    assert opts.overlay_text == "WITHHELD" and opts.remove_touching_graphics


def test_long_searches_count_pages_across_patterns_and_can_be_canceled(window, monkeypatch):
    """Find Text & Redact on a long document: "Pattern 2 of 2: searching page 1 of 3" (user
    2026-10-23: no progress for searches); Cancel leaves the results alone and says so."""
    from planetpdf.core.progress import stage_text
    from planetpdf.ui import tasks
    from planetpdf.ui.panels import redaction_panel

    monkeypatch.setattr(redaction_panel, "PROGRESS_PAGES", 1)
    counted = []

    def run(parent, title, label, work, **kw):
        counted.append(label)
        return work(lambda done, total, stage="": counted.append(stage_text(stage, done, total)) or True)

    monkeypatch.setattr(tasks, "run_with_progress", run)
    panel = window.redaction_panel
    panel.preset_checks["ssn"].setChecked(True)
    panel.preset_checks["email"].setChecked(True)
    panel.find_presets()
    pages = g.TEXT_PAGE_COUNT
    assert counted[0] == "Searching for 2 patterns"
    assert counted[1:] == [f"Pattern {p} of 2: searching page {n + 1} of {pages}" for p in (1, 2) for n in range(pages)]
    assert panel.results.topLevelItemCount() == 2 * pages

    def canceled(*a, **kw):
        raise tasks.TaskCanceled()

    monkeypatch.setattr(tasks, "run_with_progress", canceled)
    panel.search_edit.setText(g.EMAIL)
    panel.find_text()
    assert panel.results_label.text() == "Search canceled." and panel.results.topLevelItemCount() == 2 * pages


def _warning_checks(monkeypatch):
    from planetpdf.core.redaction import apply as A
    from planetpdf.core.redaction.verify import Check, Status, VerificationReport

    def warned(path, *args, **kwargs):
        return VerificationReport(path, [Check(Status.PASS, "No text under the boxes"),
                                         Check(Status.WARN, 'Term "ZE*****RD" still appears where it wasn\'t marked')])

    monkeypatch.setattr(A, "verify_redaction", warned)


def test_warnings_are_shown_before_the_original_is_replaced(window, monkeypatch):
    """Security review 2026-11-27, finding 9: with a warning, the original was replaced first and the
    report came after. Now the report comes first, and Don't Replace (its default) keeps it."""
    _warning_checks(monkeypatch)
    window.add_marks([M.make_spec(0, [tuple(window.session.doc[0].search_for(g.SSN)[0])], "Area")])
    path, original = window.session.path, window.session.path.read_bytes()
    assert not window.redact_to(path, SanitizeOptions())  # (the report's default: Don't Replace)
    assert path.read_bytes() == original and _marks(window)
    asked = window.reports[0]
    assert any(b.text() == "Replace the Original" for b in asked.findChildren(QPushButton))
    monkeypatch.setattr(window, "_show_report", lambda dialog: (window.reports.append(dialog),
                                                                setattr(dialog, "keep_requested", True)))
    assert window.redact_to(path, SanitizeOptions())  # (Replace the Original: the report isn't shown twice)
    assert path.read_bytes() != original and _marks(window) == []
    assert len(window.reports) == 2
