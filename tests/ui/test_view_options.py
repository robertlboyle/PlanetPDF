"""View ▸ Show Gaps Between Pages, Rulers & Grids (rulers, grid, snap, guides), Cursor
Coordinates, Button Labels, Page Controls, Tools Pane; Edit ▸ Search More Tools."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.view_options import CommandSearch


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    doc = pymupdf.open()
    for n in range(3):
        doc.new_page(width=612, height=792).insert_text((72, 100), f"Page {n + 1}", fontname="helv")
    path = tmp_path / "three.pdf"
    doc.save(path)
    w = MainWindow(tmp_path / "profile")
    w.resize(1200, 850)
    w.show()
    w.open_file(path)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _page_box(win, i):
    page = win.view.page_item(i)
    return page.mapRectToScene(page.boundingRect())


def test_gaps_between_pages(win):
    gap = _page_box(win, 1).top() - _page_box(win, 0).bottom()
    assert gap > 5
    win.act_gaps.trigger()  # off: the pages touch
    assert abs(_page_box(win, 1).top() - _page_box(win, 0).bottom()) < 0.01
    assert not win.settings.show_page_gaps


def test_snap_to_grid_and_guides(win):
    view = win.view
    win.act_snap.trigger()
    assert view.snapped(QPointF(20, 37)) == QPointF(18, 36)  # (quarter inch = 18 pt)
    win.act_rulers.trigger()
    QCoreApplication.processEvents()
    top, left = view._rulers
    assert top.isVisible() and left.isVisible() and view.viewport().geometry().top() >= top.height()
    # drag from the top ruler onto page 1: a horizontal guide there (snapped)
    target = view.mapFromScene(_page_box(win, 0).topLeft() + QPointF(100, 145))
    QTest.mousePress(top, Qt.MouseButton.LeftButton, pos=QPoint(50, 10))
    view_global = view.viewport().mapToGlobal(target)
    QTest.mouseMove(top, top.mapFromGlobal(view_global))
    QTest.mouseRelease(top, Qt.MouseButton.LeftButton, pos=top.mapFromGlobal(view_global))
    assert view.guides == [("h", 144.0)]
    win.act_clear_guides.trigger()
    assert view.guides == []


def test_grid_draws_over_the_page(win):
    win.act_grid.trigger()
    QCoreApplication.processEvents()
    image = win.view.viewport().grab().toImage()
    box = win.view.mapFromScene(_page_box(win, 0)).boundingRect()
    x = box.left() + int(72 * win.view.transform().m11())  # the 1-inch line
    hits = sum(1 for y in range(box.top() + 50, box.top() + 250)
               if image.pixelColor(x, y).blue() > image.pixelColor(x, y).red() + 10)
    assert hits > 100


def test_cursor_coordinates(win):
    win.act_cursor.trigger()
    corner = win.view.mapFromScene(_page_box(win, 0).topLeft() + QPointF(72, 144))
    QTest.mouseMove(win.view.viewport(), corner)
    assert "X: 1.00 in" in win.view_options.coordinates.text() and "Y: 2.00 in" in win.view_options.coordinates.text()


def test_button_labels_and_tools_pane(win):
    buttons = win.view_options._buttons()
    assert buttons
    win.view_options.label_actions["none"].trigger()
    assert all(b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly for b in buttons)
    win.view_options.label_actions["all"].trigger()
    assert all(b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon for b in buttons)
    win.view_options.label_actions["default"].trigger()
    assert win.settings.button_labels == "default"
    assert win.act_tools_pane.shortcut() == QKeySequence("Shift+F4")
    win.act_tools_pane.trigger()
    assert not win.tool_panes.strip.isVisible()


def test_page_controls(win):
    win.act_page_controls.trigger()
    controls = win.view_options.page_controls
    assert controls.isVisible() and controls.label.text().startswith("1 / 3")
    controls.findChildren(QToolButton)[1].click()  # next page
    assert controls.label.text().startswith("2 / 3")


def test_search_more_tools_finds_and_runs_a_command(win, monkeypatch):
    dialog = CommandSearch(win)
    dialog.edit.setText("rotate view clock")
    paths = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert "View ▸ Rotate View ▸ Clockwise" in paths
    assert all("not available" not in p for p in paths)
    dialog.list.setCurrentRow(paths.index("View ▸ Rotate View ▸ Clockwise"))
    dialog._run_current()
    assert dialog.chosen is win.act_rotate_view_cw
    monkeypatch.setattr(CommandSearch, "exec", lambda self: (setattr(self, "chosen", win.act_rotate_view_cw), 1)[1])
    win.act_search_tools.trigger()
    assert win.view.view_rotation == 90
