"""Acrobat's Stamp tool: the catalog, Stamp ▾ with pictures, favorites, the palette and
Paste Clipboard Image as Stamp Tool."""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from planetpdf.core import stamps as S
from planetpdf.core.markup import Kind
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, pdf_copy):
    w = MainWindow(tmp_path / "profile")
    w.show()
    w.open_file(pdf_copy("text.pdf"))
    QCoreApplication.processEvents()
    yield w
    w.markup.set_tool(None)
    w.undo_stack.setClean()
    w.close()


def test_catalog_matches_acrobat():
    names = {c: [s.name for s in S.BUILTIN_STAMPS if s.category == c] for c in S.CATEGORIES}
    assert names["Sign Here"] == ["Witness", "Initial Here", "Sign Here", "Accepted", "Rejected"]
    assert "Company Confidential" in names["Dynamic"]
    assert len(names["Standard Business"]) == 12
    for spec in S.BUILTIN_STAMPS:  # every one renders
        lines = S.resolve_lines(spec, user="R. Boyle", now=datetime(2026, 10, 1, 12, 16))
        assert S.render_text_stamp(spec, lines).startswith(b"%PDF")


def test_dynamic_stamp_text():
    spec = S.builtin("reviewed")
    now = datetime(2026, 10, 1, 12, 16)
    assert S.resolve_lines(spec, user="R. Boyle", now=now) == ["REVIEWED", "By R. Boyle at 12:16 pm, Oct 01, 2026"]
    assert S.resolve_lines(spec, user="", now=now) == ["REVIEWED", "12:16 pm, Oct 01, 2026"]  # no dangling "By"


def test_stamp_menu_last_used_favorites_and_names(win):
    menu = win.markup_ui.stamp_menu
    win.markup.place_stamp(S.builtin("void").stamp_id)
    win.markup.set_tool(None)
    assert win.settings.last_stamp == "builtin-void"
    win.stamp_ui.fill_menu(menu)
    first = menu.actions()[0]
    assert first.data() == "builtin-void"  # the one used last, at the top
    titles = [a.menu().title() for a in menu.actions() if a.menu()]
    assert titles[:3] == ["Dynamic", "Sign Here", "Standard Business"] and "Custom Stamps" in titles
    win.stamp_ui.add_favorite()
    assert win.settings.stamp_favorites == ["builtin-void"]
    win.stamp_ui.fill_menu(menu)
    texts = [a.text() for a in menu.actions()]
    assert "Paste Clipboard Image as Stamp Tool" in texts and "Stamps Palette" in texts
    remove = next(a for a in menu.actions() if a.text() == "Remove Current Stamp from Favorites")
    assert remove.isEnabled()
    remove.trigger()
    assert win.settings.stamp_favorites == []
    win.stamp_ui.act_show_names.setChecked(True)
    win.stamp_ui.fill_menu(menu)
    assert menu.actions()[0].text() == "Void"


def test_palette_places_a_stamp(win):
    win.stamp_ui.show_palette()
    palette = win.stamp_ui.palette
    palette.category.setCurrentText("Sign Here")
    assert palette.list.count() == 5
    palette._place(palette.list.item(2))
    assert win.markup.tool is Kind.STAMP and win.settings.last_stamp == "builtin-sign-here"
    palette.close()


def test_paste_clipboard_image_as_stamp(win):
    image = QImage(60, 30, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3366cc"))
    QApplication.clipboard().setImage(image)
    assert win.stamp_ui.paste_clipboard_stamp()
    assert win.markup.tool is Kind.SEAL


def test_stamp_bar_matches_acrobat(win):
    win.tool_panes.open("stamp")
    bar = win.tool_panes.bars["stamp"]
    from PySide6.QtWidgets import QToolButton

    texts = [b.text() for b in bar.findChildren(QToolButton) if b.isVisible()]
    for wanted in ("Stamp", "Custom Stamps", "Bates Numbering"):
        assert wanted in texts


def test_no_builtin_stamp_names_a_company():
    """Documents get no company's name unless the user sets one (user 2026-10-10)."""
    for spec in S.BUILTIN_STAMPS:
        text = " ".join(S.resolve_lines(spec, user="", company="")).lower()
        assert "planet" not in text and "planet" not in spec.name.lower()


def test_company_confidential_uses_the_company_name_from_preferences():
    spec = S.builtin("company-confidential")
    now = datetime(2026, 10, 10, 9, 30)
    assert S.resolve_lines(spec, company="", now=now) == ["CONFIDENTIAL"]
    assert S.resolve_lines(spec, company="Acme Corp", now=now) == ["Acme Corp", "CONFIDENTIAL"]
    assert S.resolve_text("{company} only", company="Acme Corp") == "Acme Corp only"
    assert "{company}" in S.TOKENS


def test_placed_company_stamp_and_the_old_stamp_id(win):
    from planetpdf.core.storage.settings import Settings

    assert Settings().company_name == ""
    win.settings.company_name = "Acme Corp"
    win.settings.stamp_favorites = ["builtin-planet-confidential"]
    assert [s.stamp_id for s in win.stamp_ui.stamps_in("Favorites")] == ["builtin-company-confidential"]
    assert win.stamp_library.get("builtin-planet-confidential").stamp_id == "builtin-company-confidential"
    win.markup.place_stamp("builtin-planet-confidential")
    assert win.settings.last_stamp == "builtin-company-confidential"
    win.markup.set_tool(None)


def test_stamps_can_be_moved_and_stretched_while_the_stamp_tool_is_open(win):
    """User 2026-10-10: with the Stamp tool still open, a stamp just placed has corner handles
    and can be dragged; outside the tool (Fill & Sign) stamps stay out of the way."""
    from PySide6.QtCore import QRectF

    win.tool_panes.open("stamp")
    win.markup.place_stamp(S.builtin("approved").stamp_id)
    win.view.shapeDrawn.emit("place", 0, QRectF(300, 400, 2, 2))  # a click
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    assert win.view.selected_markup == item.item_id  # selected with its handles right away
    overlay = win.view._markup_items[item.item_id]
    assert overlay.movable and overlay.keep_aspect  # corners stretch it, keeping its shape

    old = item.spec.rect
    width, height = old[2] - old[0], old[3] - old[1]
    win.view.markupMoved.emit(item.item_id, QRectF(100, 150, width * 2, height * 2))
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    new = item.spec.rect
    assert abs(new[0] - 100) < 1 and abs(new[1] - 150) < 1
    assert abs((new[2] - new[0]) - 2 * width) < 1 and abs((new[3] - new[1]) - 2 * height) < 1

    win.tool_panes.close()
    assert item.item_id not in win.view._markup_items  # Fill & Sign: not selectable
    win.tool_panes.open("stamp")
    assert item.item_id in win.view._markup_items


def _drag_corner(win, item_id, dx, dy, modifiers):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    view = win.view
    overlay = view._markup_items[item_id]
    corner = overlay.rect().bottomRight()
    left = Qt.MouseButton.LeftButton
    steps = [(QMouseEvent.Type.MouseButtonPress, corner, left, left),
             (QMouseEvent.Type.MouseMove, corner + QPointF(dx / 2, dy / 2), Qt.MouseButton.NoButton, left),
             (QMouseEvent.Type.MouseMove, corner + QPointF(dx, dy), Qt.MouseButton.NoButton, left),
             (QMouseEvent.Type.MouseButtonRelease, corner + QPointF(dx, dy), left, Qt.MouseButton.NoButton)]
    for kind, point, button, buttons in steps:
        pos = QPointF(view.mapFromScene(overlay.parentItem().mapToScene(point)))
        event = QMouseEvent(kind, pos, QPointF(view.viewport().mapToGlobal(pos.toPoint())), button, buttons, modifiers)
        QApplication.sendEvent(view.viewport(), event)
    QCoreApplication.processEvents()
    [item] = [i for i in win.markup.items if i.item_id == item_id]
    r = item.spec.rect
    return r[2] - r[0], r[3] - r[1]


def test_a_corner_resizes_a_stamp_and_shift_changes_its_proportions(win):
    """User 2026-10-10: by default a corner only changes the size; with Shift held it
    changes the proportions too."""
    from PySide6.QtCore import QRectF, Qt

    win.view.set_zoom(1.0)
    win.tool_panes.open("stamp")
    win.markup.place_stamp(S.builtin("approved").stamp_id)
    win.view.shapeDrawn.emit("place", 0, QRectF(250, 300, 2, 2))
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    r = item.spec.rect
    width, height = r[2] - r[0], r[3] - r[1]

    w1, h1 = _drag_corner(win, item.item_id, 60, 0, Qt.KeyboardModifier.NoModifier)
    assert w1 > width + 30 and abs(w1 / h1 - width / height) < 0.02  # bigger, same shape

    w2, h2 = _drag_corner(win, item.item_id, 0, 40, Qt.KeyboardModifier.ShiftModifier)
    assert abs(w2 - w1) < 1 and h2 > h1 + 20  # taller only: stretched

    # the stamp's picture fills its stretched box (no letterboxing)
    import pymupdf

    page = win.session.doc[0]
    [item] = [i for i in win.markup.items if i.kind is Kind.STAMP]
    box = pymupdf.Rect(item.spec.rect)
    pix = page.get_pixmap(clip=box, dpi=72)
    rows = [y for y in range(pix.height) if any(sum(pix.pixel(x, y)) < 600 for x in range(pix.width))]
    assert rows and rows[0] < pix.height * 0.2 and rows[-1] > pix.height * 0.8


def test_shift_never_stretches_signatures_or_placed_text():
    assert Kind.STAMP.stretchable and Kind.SEAL.stretchable
    assert not any(k.stretchable for k in (Kind.SIGNATURE, Kind.INITIALS, Kind.TEXT, Kind.IMAGE, Kind.SYMBOL))
