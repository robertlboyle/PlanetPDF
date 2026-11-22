"""Edit PDF ▸ Link: add, edit, delete, remove all (undoable)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QDialog

from planetpdf.core import links as L
from planetpdf.ui.dialogs import link as LD
from planetpdf.ui.main_window import MainWindow


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
    w.open_file(path)
    w.messages = []
    w.show_message = lambda title, text: w.messages.append(text)
    yield w
    w.undo_stack.setClean()
    w.close()


def _links(win, page=0):
    return L.page_links(win.session, page)


def _answer(monkeypatch, fill):
    def run(dialog):
        return fill(dialog)

    monkeypatch.setattr(LD.LinkDialog, "exec", run)


def test_add_a_web_link_then_edit_it_to_a_page_then_delete_it(win, monkeypatch):
    def web(d):
        assert d.windowTitle() == "Create Link"
        d.address.setText("www.planet.net")
        return QDialog.DialogCode.Accepted

    _answer(monkeypatch, web)
    win._link_at_box(0, (72, 200, 300, 230))
    [link] = _links(win)
    assert link.kind is L.LinkKind.WEB and link.uri == "https://www.planet.net"
    assert win.undo_stack.undoText() == "Add Link"
    assert len(win.text_tools.links) == 1  # (the overlay knows it: hover hand, click follows)

    def to_page(d):
        assert d.windowTitle() == "Edit Link" and d.address.text() == "https://www.planet.net"
        d.page_number.setValue(3)
        return QDialog.DialogCode.Accepted

    _answer(monkeypatch, to_page)
    win._link_at_box(0, (150, 190, 160, 240))  # (a click inside it: edits that link)
    [link] = _links(win)
    assert link.kind is L.LinkKind.PAGE and link.target_page == 2
    assert link.rect == pytest.approx((72, 200, 300, 230))  # (same area)
    _answer(monkeypatch, lambda d: LD.DELETE)
    win._link_at_box(0, (150, 190, 160, 240))
    assert _links(win) == []
    win.undo_stack.undo()
    assert _links(win)[0].kind is L.LinkKind.PAGE
    win.undo_stack.undo()
    assert _links(win)[0].kind is L.LinkKind.WEB
    win.undo_stack.undo()
    assert _links(win) == []


def test_remove_links_from_pages(win, monkeypatch):
    L.insert_links(win.session, [(0, L.link_dict((10, 10, 50, 50), uri="https://a.example")),
                                 (1, L.link_dict((10, 10, 50, 50), target_page=0)),
                                 (2, L.link_dict((10, 10, 50, 50), uri="mailto:x@example.com"))])

    def pages_1_2(d):
        d.range_edit.line.setText("1-2")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(LD.RemoveLinksDialog, "exec", pages_1_2)
    win.remove_links_dialog()
    assert _links(win, 0) == [] and _links(win, 1) == [] and len(_links(win, 2)) == 1
    assert win.undo_stack.undoText() == "Remove 2 Links"
    win.undo_stack.undo()
    assert len(_links(win, 0)) == 1 and _links(win, 1)[0].target_page == 0


def test_addresses_are_completed():
    assert L.normalize_uri("planet.net") == "https://planet.net"
    assert L.normalize_uri("bob@planet.net") == "mailto:bob@planet.net"
    assert L.normalize_uri("http://x.example/a") == "http://x.example/a"


def test_link_tool_stays_on_in_edit_pdf(win, monkeypatch):
    """User 2026-10-17: an Edit PDF tool stays on until another tool is chosen or the tool closes."""
    _answer(monkeypatch, lambda d: (d.address.setText("www.planet.net"), QDialog.DialogCode.Accepted)[1])
    win.markup_ui.set_mode("edit")
    win.link_tool()
    assert win.markup.picking and win.view.draw_tool == "box"
    win.view.shapeDrawn.emit("box", 0, QRectF(72, 200, 228, 30))
    assert len(_links(win)) == 1 and win.markup.picking and win.view.draw_tool == "box"  # ready for another
    win.view.drawToolCanceled.emit()  # Esc keeps it
    assert win.markup.picking
    win.edit_ui.act_add_text.trigger()  # another tool stops it
    assert not win.markup.picking and win.edit.tool == "text"
