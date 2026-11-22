"""View ▸ Rotate View and the rest of View ▸ Zoom (user screenshots 2026-10-02): Dynamic
Zoom, Fit Height, Fit Visible, Pan & Zoom, Loupe Tool, Reflow. None of them changes the file."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planetpdf.core.render import content_bounds, render_area
from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.page_view import ZoomMode


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pdf(tmp_path):
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=612, height=792)
        # text only in the middle third: wide margins for Fit Visible
        page.insert_text((200, 300), f"Page {n + 1} hello reflow-", fontname="helv", fontsize=14)
        page.insert_text((200, 320), "ing text here", fontname="helv", fontsize=14)
        page.draw_rect(pymupdf.Rect(200, 400, 400, 500), color=(0, 0, 0), fill=(0, 0, 0))
    path = tmp_path / "three.pdf"
    doc.save(path)
    return path


@pytest.fixture
def win(qapp, tmp_path, pdf):
    w = MainWindow(tmp_path / "profile")
    w.resize(1200, 850)
    w.show()
    w.open_file(pdf)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _menu(win, *titles):
    menu = next(a.menu() for a in win.menuBar().actions() if a.menu() and a.menu().title() == "&View")
    for title in titles:
        menu = next(a.menu() for a in menu.actions() if a.menu() and a.menu().title() == title)
    return [a for a in menu.actions() if a.text()]


def test_menus_have_no_grayed_items_left(win):
    for titles in (("Rotate &View",), ("&Zoom",)):
        items = _menu(win, *titles)
        assert all(a.isEnabled() for a in items), [a.text() for a in items if not a.isEnabled()]
    names = [a.text().replace("&&", "\0").replace("&", "").replace("\0", "&") for a in _menu(win, "&Zoom")]
    assert names == ["Zoom To…", "Marquee Zoom", "Dynamic Zoom", "Actual Size", "Zoom to Page Level", "Fit Width",
                     "Fit Height", "Fit Visible", "Pan & Zoom", "Loupe Tool", "Reflow"]
    assert win.act_fit_visible.shortcut() == QKeySequence("Ctrl+3")
    assert win.act_reflow.shortcut() == QKeySequence("Ctrl+4")
    assert win.act_rotate_view_cw.shortcut() == QKeySequence("Ctrl+Shift++")
    assert win.act_rotate_view_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")


def test_rotate_view_turns_the_pages_but_not_the_file(win, pdf):
    view = win.view
    page = view.page_item(0)
    upright = page.mapRectToScene(page.boundingRect())
    win.act_rotate_view_cw.trigger()
    page = view.page_item(0)
    turned = page.mapRectToScene(page.boundingRect())
    assert view.view_rotation == 90 and page.rotation() == 90
    assert turned.width() == pytest.approx(upright.height()) and turned.height() == pytest.approx(upright.width())
    # the top-left corner of the page is now shown at the top right
    corner = view.mapFromScene(page.mapToScene(QPointF(0, 0)))
    middle = view.mapFromScene(turned.center())
    assert corner.x() > middle.x() and corner.y() < middle.y()
    assert win.undo_stack.count() == 0 and win.session.doc[0].rotation == 0  # (nothing changed in the file)
    win.act_rotate_view_ccw.trigger()
    win.act_rotate_view_ccw.trigger()
    assert view.view_rotation == 270
    win.open_file(pdf)  # reopened: upright again
    assert win.view.view_rotation == 0


def test_rotate_view_keys_dont_clash_with_zoom(win):
    win.view.setFocus()
    zoom = win.view.zoom
    QTest.keyClick(win.view, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    QCoreApplication.processEvents()
    assert win.view.view_rotation == 90
    QTest.keyClick(win.view, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    QCoreApplication.processEvents()
    assert win.view.view_rotation == 0
    QTest.keyClick(win.view, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)  # Ctrl+= still zooms in
    assert win.view.zoom > zoom and win.view.view_rotation == 0


def test_clicks_on_a_turned_page_land_where_they_show(win):
    view = win.view
    win.act_rotate_view_cw.trigger()
    page = view.page_item(0)
    # a point on the page (as the file has it) and where it is on screen now
    spot = QPointF(100, 50)
    on_screen = view.mapFromScene(page.mapToScene(spot))
    found = view._page_at(view.mapToScene(on_screen))
    assert found is page
    back = page.mapFromScene(view.mapToScene(on_screen))
    assert abs(back.x() - spot.x()) < 2 and abs(back.y() - spot.y()) < 2


def test_fit_height_and_fit_visible(win):
    view = win.view
    win.act_fit_height.trigger()
    assert view.zoom_mode is ZoomMode.FIT_HEIGHT
    page = view.page_item(0)
    shown = view.mapFromScene(page.mapRectToScene(page.boundingRect())).boundingRect()
    assert shown.height() <= view.viewport().height() and shown.height() > view.viewport().height() * 0.9
    win.act_fit_width.trigger()
    by_width = view.zoom
    win.act_fit_visible.trigger()  # the printed part (x 200..400) fills the width: far bigger than Fit Width
    assert view.zoom_mode is ZoomMode.FIT_VISIBLE and view.zoom > by_width * 2
    box = win._content_bounds(0)
    middle = view.mapFromScene(page.mapToScene(box.center()))
    assert abs(middle.x() - view.viewport().width() / 2) < 20


def test_content_bounds_and_render_area_on_a_rotated_page(tmp_path):
    from planetpdf.core.document import PdfSession

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(pymupdf.Rect(100, 100, 200, 150), color=(0, 0, 0), fill=(0, 0, 0))
    page.set_rotation(90)
    doc.save(tmp_path / "r.pdf")
    session = PdfSession.open(tmp_path / "r.pdf")
    try:
        x0, y0, x1, y1 = content_bounds(session, 0)
        shown = pymupdf.Rect(100, 100, 200, 150) * session.doc[0].rotation_matrix  # as displayed
        assert abs(x0 - shown.x0) < 3 and abs(y0 - shown.y0) < 3 and abs(x1 - shown.x1) < 3 and abs(y1 - shown.y1) < 3
        inside = render_area(session, 0, (shown.x0 + 5, shown.y0 + 5, shown.x1 - 5, shown.y1 - 5), 2.0)
        assert inside.samples[:3] == b"\x00\x00\x00"  # (displayed coordinates: inside the black box)
        assert content_bounds(session, 0) is not None
    finally:
        session.close()


def test_dynamic_zoom_drag_up_zooms_in(win):
    view = win.view
    win.act_dynamic_zoom.trigger()
    assert view.view_tool == "dynamic_zoom" and not win.act_select_tool.isChecked()
    start = view.zoom
    center = view.viewport().rect().center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(view.viewport(), center - QPoint(0, 80))
    assert view.zoom > start * 1.5
    QTest.mouseMove(view.viewport(), center + QPoint(0, 80))
    assert view.zoom < start
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=center + QPoint(0, 80))
    assert win.undo_stack.count() == 0
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.view_tool is None and not win.act_dynamic_zoom.isChecked() and win.act_select_tool.isChecked()


def test_loupe_shows_the_spot_clicked(win):
    view = win.view
    win.act_loupe.trigger()
    loupe = win.loupe_window()
    assert view.view_tool == "loupe" and loupe.isVisible()
    page = view.page_item(0)
    spot = view.mapFromScene(page.mapToScene(QPointF(300, 450)))  # the black box
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=spot)
    assert loupe._page == 0 and abs(loupe._point.x() - 300) < 2 and abs(loupe._point.y() - 450) < 2
    image = loupe.image.pixmap().toImage()
    assert image.pixelColor(image.width() // 2, image.height() // 2).lightness() < 30  # magnified black
    assert loupe._marker is not None and loupe._marker.rect().contains(QPointF(300, 450))
    loupe.slider.setValue(loupe.slider.value() + 2)  # more magnification: a smaller box on the page
    assert loupe._marker.rect().width() < loupe.area().width() * 1.01
    loupe.close()
    assert view.view_tool is None and not win.act_loupe.isChecked()


def test_pan_and_zoom_moves_the_view(win):
    view = win.view
    view.set_zoom(3.0)
    QCoreApplication.processEvents()
    win.act_pan_zoom.trigger()
    pz = win.pan_zoom_window()
    assert pz.isVisible()
    canvas = pz.canvas
    source, target = canvas._layout()
    before = view.mapToScene(view.viewport().rect().center())
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=target.bottomRight().toPoint() - QPoint(5, 5))
    after = view.mapToScene(view.viewport().rect().center())
    assert after.y() > before.y() + 100 and after.x() > before.x()
    pz.close()


def test_reflow_shows_the_page_text_and_turns_pages(win):
    win.act_reflow.trigger()
    reflow = win.reflow_view()
    assert win.central.currentWidget() is reflow
    text = reflow.toPlainText()
    assert "Page 1 hello reflowing text here" in text  # (the broken word joined up)
    QTest.keyClick(reflow, Qt.Key.Key_Right)
    assert win.view.current_page == 1 and "Page 2 hello" in reflow.toPlainText()
    win.act_reflow.trigger()  # Ctrl+4 again: the page view
    assert win.central.currentWidget() is win.view
    win.act_reflow.trigger()
    win.tool_panes.open("organize")  # another tool: Reflow gives way
    assert not win.act_reflow.isChecked() and win.central.currentWidget() is win.organizer
