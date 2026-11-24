"""Offscreen smoke tests: the main window builds, opens, navigates, zooms and saves.

Visual/layout checks are done by hand; these only catch wiring errors.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from planetpdf.core.storage.settings import load_settings  # noqa: E402
from planetpdf.ui.main_window import MainWindow  # noqa: E402
from planetpdf.ui.viewer.page_view import ZoomMode  # noqa: E402
from tests.fixtures import generate as g  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump(ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    QCoreApplication.processEvents()


@pytest.fixture
def window(qapp, tmp_path):
    win = MainWindow(tmp_path / "config")
    win.resize(1000, 800)
    win.show()
    _pump()
    yield win
    win.undo_stack.setClean()  # tests may leave edits; don't prompt to save them
    win.close()


def test_starts_empty(window):
    assert window.session is None
    assert not window.act_save.isEnabled()
    assert window.windowTitle() == "PlanetPDF"


def test_open_navigate_zoom(window, pdf_copy):
    path = pdf_copy("text.pdf")
    assert window.open_file(path)
    _pump(200)
    assert window.session.page_count == g.TEXT_PAGE_COUNT
    assert window.page_count_label.text().strip() == f"/ {g.TEXT_PAGE_COUNT}"  # Acrobat style: "1 / 3"
    assert window.thumbnails.count() == g.TEXT_PAGE_COUNT
    assert window.act_save.isEnabled()
    assert path.name in window.windowTitle()

    window.view.go_to_page(2)
    _pump()
    assert window.view.current_page == 2
    assert window.page_spin.value() == 3

    window.view.set_zoom(1.0)
    window.view.zoom_in()
    assert window.view.zoom == pytest.approx(1.25)  # (the next of Acrobat's zoom levels)
    assert window.view.zoom_mode is ZoomMode.CUSTOM
    window.view.fit_page()
    assert window.view.zoom_mode is ZoomMode.FIT_PAGE
    _pump(300)  # let lazy rendering run; it must not raise


def test_dirty_state_and_save(window, pdf_copy):
    path = pdf_copy("text.pdf")
    window.open_file(path)
    assert not window.is_dirty()
    window.insert_blank_page()
    assert window.is_dirty()
    assert window.isWindowModified()
    assert window.save()
    assert not window.is_dirty()
    assert not window.isWindowModified()
    # Undo past the save point is a change again; redo returns to the saved state.
    window.undo_stack.undo()
    assert window.is_dirty()
    window.undo_stack.redo()
    assert not window.is_dirty()


def _page_labels(window) -> list[str]:
    doc = window.session.doc
    return [(doc[i].get_text().strip().splitlines() or [""])[0] for i in range(doc.page_count)]


MIXED = [f"Page {i + 1}: {w}x{h}" for i, (w, h) in enumerate(g.MIXED_SIZES)]


def test_move_pages_with_undo(window, pdf_copy):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window.move_pages([0], 3)
    assert _page_labels(window) == [MIXED[1], MIXED[2], MIXED[0]]
    assert window.thumbnails.selected_pages() == [2]
    assert window.thumbnails.item(2).text() == "3"
    window.undo_stack.undo()
    assert _page_labels(window) == MIXED
    assert not window.is_dirty()
    window.undo_stack.redo()
    assert _page_labels(window) == [MIXED[1], MIXED[2], MIXED[0]]


def test_drop_in_place_is_not_a_change(window, pdf_copy):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window.move_pages([1], 1)
    window.move_pages([1], 2)
    assert window.undo_stack.count() == 0


def test_delete_selected_pages_with_undo(window, pdf_copy):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window.thumbnails.select_pages([0, 2])
    window.delete_selected_pages()
    assert _page_labels(window) == [MIXED[1]]
    assert window.thumbnails.count() == 1
    assert window.page_count_label.text().strip() == "/ 1"
    window.undo_stack.undo()
    assert _page_labels(window) == MIXED
    assert window.thumbnails.count() == 3


def test_cannot_delete_all_pages(window, pdf_copy, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window.thumbnails.select_pages([0, 1, 2])
    window.delete_selected_pages()
    assert shown
    assert window.session.page_count == 3
    assert window.undo_stack.count() == 0


def test_insert_blank_after_selection(window, pdf_copy):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window.thumbnails.select_pages([0])
    window.insert_blank_page()
    assert _page_labels(window) == [MIXED[0], "", MIXED[1], MIXED[2]]
    assert window.thumbnails.count() == 4
    window.undo_stack.undo()
    assert _page_labels(window) == MIXED


def test_files_dropped_on_thumbnails_insert_at_position(window, pdf_copy, fixture_dir):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    window._on_files_dropped_on_thumbnails([fixture_dir / "image.pdf", fixture_dir / "vector.pdf"], 1)
    labels = _page_labels(window)
    assert labels[0] == MIXED[0]
    assert labels[1] == "Caption under the image"  # image.pdf first, at the drop position
    assert labels[3:] == MIXED[1:]
    assert window.session.page_count == 5
    assert window.undo_stack.count() == 2  # one undo step per file


def test_drop_row_math(window, pdf_copy):
    window.open_file(pdf_copy("mixed_sizes.pdf"))
    _pump(100)
    thumbs = window.thumbnails
    first = thumbs.visualItemRect(thumbs.item(0))
    last = thumbs.visualItemRect(thumbs.item(2))
    assert thumbs.drop_row_at(first.center() - first.center().__class__(0, first.height() // 4)) == 0
    assert thumbs.drop_row_at(first.center() + first.center().__class__(0, first.height() // 4)) == 1
    assert thumbs.drop_row_at(last.bottomLeft() + last.bottomLeft().__class__(5, 40)) == 3


def test_settings_saved_on_close(qapp, tmp_path, pdf_copy):
    config = tmp_path / "config"
    win = MainWindow(config)
    win.show()
    win.open_file(pdf_copy("text.pdf"))
    win.close()
    settings = load_settings(config)
    assert settings.recent_files and settings.recent_files[0].endswith("text.pdf")
    assert settings.window_geometry
