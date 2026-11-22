"""Edit PDF's right-hand panel: FORMAT for the selected line, OBJECTS for the selected
image, Show bounding boxes. Each change is one undo step."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from planetpdf.ui.main_window import MainWindow
from planetpdf.ui.viewer.overlays import MarkupOverlay


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, path):
    win = MainWindow(tmp_path / "profile")
    win.resize(1200, 850)
    win.show()
    win.open_file(path)
    QCoreApplication.processEvents()
    win.markup_ui.set_mode("edit")
    return win


@pytest.fixture
def text_win(qapp, tmp_path, pdf_copy):
    win = _window(tmp_path, pdf_copy("text.pdf"))
    yield win
    win.view.set_bounding_boxes(False)
    win.undo_stack.setClean()
    win.close()


@pytest.fixture
def image_win(qapp, tmp_path, pdf_copy):
    win = _window(tmp_path, pdf_copy("image.pdf"))
    yield win
    win.undo_stack.setClean()
    win.close()


def _span(win, word):
    page = win.session.doc[0]
    return next(s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"] if word in s["text"])


def test_panel_shows_only_in_edit_mode(text_win):
    assert text_win._edit_dock.isVisible()
    text_win.markup_ui.set_mode("form")
    assert not text_win._edit_dock.isVisible()


def test_format_the_selected_line(text_win):
    win, panel = text_win, text_win.edit_panel.format_bar
    assert not panel.size_box.isEnabled()  # nothing selected yet
    line_id = next(k for k in win.view._markup_items if k.startswith("txt:0:1"))  # "Name: John Q. Public"
    win.view.markupClicked.emit(line_id)
    win.edit.finish_editing()
    assert panel.size_box.isEnabled()
    before = _span(win, "John")["size"]
    panel._size_chosen(str(round(before + 6, 1)))
    assert _span(win, "John")["size"] == pytest.approx(before + 6, abs=0.1)
    panel.buttons["bold"].click()
    assert _span(win, "John")["flags"] & 16  # bold
    win.pick_color = lambda initial, title: QColor("#cc0000")
    panel.color_button.click()
    assert _span(win, "John")["color"] == 0xCC0000
    for _ in range(3):
        win.undo_stack.undo()
    assert _span(win, "John")["size"] == pytest.approx(before, abs=0.1)


def test_objects_rotate_and_flip_the_selected_image(image_win):
    win, panel = image_win, image_win.edit_panel
    image_id = next(k for k in win.view._markup_items if k.startswith("img:"))
    win.view.markupClicked.emit(image_id)
    assert panel.object_buttons["rotate_cw"].isEnabled()
    [before] = win.edit._page_images(0)
    panel.object_buttons["rotate_cw"].click()
    [after] = win.edit._page_images(0)
    r0, r1 = pymupdf.Rect(before.rect), pymupdf.Rect(after.rect)
    assert (round(r1.width), round(r1.height)) == (round(r0.height), round(r0.width))
    assert abs((r0.x0 + r0.x1) - (r1.x0 + r1.x1)) < 1  # same center
    panel.object_buttons["flip_h"].click()
    win.undo_stack.undo()
    win.undo_stack.undo()
    assert win.edit._page_images(0)[0].rect == pytest.approx(before.rect, abs=0.5)


def test_show_bounding_boxes(text_win):
    text_win.edit_panel.bounds.setChecked(True)
    assert MarkupOverlay.show_bounds
    item = next(i for k, i in text_win.view._markup_items.items() if k.startswith("txt:"))
    assert item.pen().color().alpha() > 0
    text_win.edit_panel.bounds.setChecked(False)
    assert item.pen().color().alpha() == 0


# --- the panel works on whatever is selected (user 2026-10-01: "wire up these features") --------


def _vec_ids(win):
    return [k for k in win.view._markup_items if k.startswith("vec:0:")]


@pytest.fixture
def shapes_win(qapp, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "First line here", fontname="helv", fontsize=11)
    page.insert_text((72, 120), "Second line here", fontname="helv", fontsize=11)
    page.draw_line((100, 400), (300, 400), color=(0, 0, 0), width=2)
    page.draw_rect(pymupdf.Rect(350, 380, 450, 420), color=(1, 0, 0), width=1)
    path = tmp_path / "panel.pdf"
    doc.save(path)
    win = _window(tmp_path, path)
    yield win
    win.undo_stack.setClean()
    win.close()


def _texts(win):
    return [l for l in win.edit._page_lines(0)]


def test_format_several_lines_at_once(shapes_win):
    win, panel = shapes_win, shapes_win.edit_panel.format_bar
    ids = [k for k in win.view._markup_items if k.startswith("txt:0:")]
    win.view.set_group(ids)
    win.edit.selectionChanged.emit(win.view.selected_markup)
    assert panel.size_box.isEnabled() and panel.buttons["bold"].isEnabled()
    panel.buttons["bold"].click()
    assert all(l.bold for l in _texts(win))
    assert win.undo_stack.undoText() == "Format 2 Lines"
    assert len(win.view.selected_group) == 2  # still selected
    win.undo_stack.undo()
    assert not any(l.bold for l in _texts(win))


def test_underline_button_toggles_an_underline_in_the_text_color(shapes_win):
    win, panel = shapes_win, shapes_win.edit_panel.format_bar
    line_id = next(k for k in win.view._markup_items if k.startswith("txt:0:") and "First" in win.edit._line(k).text)
    win.view.markupClicked.emit(line_id)
    assert not panel.buttons["underline"].isChecked()
    panel.buttons["underline"].click()
    line = next(l for l in _texts(win) if "First" in l.text)
    [under] = win.edit.underlined(line)
    assert under.rect[0] == pytest.approx(line.rect[0]) and under.rect[1] > line.origin[1]
    assert panel.buttons["underline"].isChecked()
    panel.buttons["underline"].click()  # again: off
    assert win.edit.underlined(next(l for l in _texts(win) if "First" in l.text)) == []


def test_rotate_flip_recolor_and_delete_shapes(shapes_win):
    from planetpdf.core import drawings as DR

    win, panel = shapes_win, shapes_win.edit_panel
    line_id = next(k for k in _vec_ids(win) if win.edit._drawing(k).rect[2] < 310)
    win.view.markupClicked.emit(line_id)
    assert panel.object_buttons["rotate_cw"].isEnabled() and panel.object_buttons["delete"].isEnabled()
    assert not panel.object_buttons["replace"].isEnabled()  # (images only)
    panel.object_buttons["rotate_cw"].click()
    rects = [tuple(round(v) for v in d.rect) for d in DR.page_drawings(win.session, 0)]
    assert (200, 300, 200, 500) in rects  # the horizontal line now stands upright, same center
    win.pick_color = lambda initial, title: QColor("#00aa00")
    panel.color.click()  # (Shape Color…)
    [green] = [d for d in DR.page_drawings(win.session, 0) if d.path.get("color") and d.path["color"][1] > 0.5]
    panel.object_buttons["delete"].click()
    assert len(DR.page_drawings(win.session, 0)) == 1  # only the box is left
    for _ in range(3):
        win.undo_stack.undo()
    assert (100, 400, 300, 400) in [tuple(round(v) for v in d.rect) for d in DR.page_drawings(win.session, 0)]


def test_delete_works_for_text_too(shapes_win):
    win, panel = shapes_win, shapes_win.edit_panel
    line_id = next(k for k in win.view._markup_items if k.startswith("txt:0:") and "Second" in win.edit._line(k).text)
    win.view.markupClicked.emit(line_id)
    assert panel.object_buttons["delete"].isEnabled()
    panel.object_buttons["delete"].click()
    assert "Second" not in win.session.doc[0].get_text()


def test_color_icon_is_a_colored_A_without_a_bar():
    from planetpdf.ui.item_bar import color_swatch

    image = color_swatch(QColor("#ff0000"), 16).pixmap(32, 32).toImage()
    reds = [(x, y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 80 and image.pixelColor(x, y).alpha() > 200]
    assert len(reds) > 30  # the letter itself is red
    bottom = [image.pixelColor(x, image.height() - 1).alpha() for x in range(image.width())]
    assert max(bottom) < 60  # no colored bar along the bottom
