"""Text from a document is shown as written, never as rich text (security review 2026-11-25,
finding 3: a link whose address held ``<img src="file://SERVER/share/x.png">`` made Qt open that
picture, and Windows sign in to the server, as soon as the pointer rested on the link; a comment
with such text did the same when the Comments panel opened)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolTip

from planetpdf.ui import plain
from planetpdf.ui.main_window import MainWindow

# (a picture nobody has: if Qt ever loaded it, nothing would happen; the checks look at how the
# text is held, which is what decides whether Qt loads it)
BAD = '<img src="file:///C:/planetpdf-no-such-folder/x.png">'
BAD_NAME = '<img src=planetpdf-no-such-file>'  # (field names can't hold dots)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def crafted(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Click here", fontsize=14)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 72, 300, 100), "uri": "http://a/" + BAD})
    note = page.add_text_annot((100, 300), BAD)
    note.set_info(content=BAD, title=BAD)
    note.update()
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = BAD_NAME
    widget.field_label = BAD
    widget.rect = pymupdf.Rect(72, 400, 300, 420)
    page.add_widget(widget)
    doc.embfile_add(BAD + ".txt", b"attached", filename=BAD + ".txt")
    doc.set_toc([[1, BAD, 1]])
    doc.set_metadata({"title": BAD, "author": BAD, "subject": BAD})
    path = tmp_path / "crafted.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def win(qapp, tmp_path, crafted):
    w = MainWindow(tmp_path / "profile")
    w.resize(1300, 900)
    w.show()
    w.open_file(crafted)
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def _labels_hold_no_rich_document_text() -> list[str]:
    problems = []
    for widget in QApplication.allWidgets():
        if isinstance(widget, QLabel) and "planetpdf-no-such" in widget.text():
            widget.ensurePolished()  # (what Qt does before it lays a label out)
            if widget.textFormat() != Qt.TextFormat.PlainText and GuiQt.mightBeRichText(widget.text()):
                problems.append(f"{type(widget.parentWidget()).__name__}: {widget.text()[:80]}")
    return problems


def _tooltip_at(widget, pos: QPoint) -> str:
    QToolTip.hideText()
    QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, pos, widget.mapToGlobal(pos)))
    return QToolTip.text()


def test_helpers():
    assert plain.risky(BAD) and plain.risky("x <IMG SRC=y>") and plain.risky("<p style='background-image:url(x)'>")
    assert not plain.risky("<b>Comments</b> 3") and not plain.risky("Add a background to pages")
    assert plain.tip("a <b> c") == "<p style='white-space:pre-wrap'>a &lt;b&gt; c</p>"
    assert plain.tip("Plain words") == "Plain words"


def test_a_label_or_message_with_document_text_is_shown_plain(qapp):
    plain.install(qapp)
    label = QLabel("Name: " + BAD)
    label.show()
    assert label.textFormat() == Qt.TextFormat.PlainText
    own = QLabel("<b>Comments</b> <span style='color:gray'>3</span>")
    own.show()
    assert own.textFormat() == Qt.TextFormat.AutoText  # (PlanetPDF's own formatting stays)
    box = QMessageBox(QMessageBox.Icon.Warning, "Title", f"Open “{BAD}”?")
    box.ensurePolished()
    shown = [w for w in box.findChildren(QLabel) if BAD in w.text()]
    assert shown and all(w.textFormat() == Qt.TextFormat.PlainText for w in shown)
    label.close()
    own.close()


def test_hovering_a_link_shows_its_address_as_written(win, qapp):
    view = win.view
    link = view._link_items[0]
    pos = view.mapFromScene(link.mapToScene(link.rect().center()))
    shown = _tooltip_at(view.viewport(), pos)
    assert "&lt;img" in shown or ("<img" not in shown and shown)
    assert "<img src" not in shown


def test_panels_show_the_documents_words_plain(win, qapp):
    win.markup_ui.act_mode_markup.trigger()  # (the Comments panel)
    QCoreApplication.processEvents()
    win.show_tool_panel(win._forms_dock)
    QCoreApplication.processEvents()
    tree = win.forms_panel.tree
    if tree.topLevelItemCount():
        tree.setCurrentItem(tree.topLevelItem(0))  # (the field's name over the fill-in box)
    win._attachments_dock.show()
    win.act_bookmarks_panel.trigger()
    QCoreApplication.processEvents()
    assert _labels_hold_no_rich_document_text() == []


def test_an_item_lists_tooltip_from_the_document_is_shown_escaped(win, qapp):
    from PySide6.QtWidgets import QListWidget, QListWidgetItem

    plain.install(qapp)
    view = QListWidget()
    item = QListWidgetItem("x")
    item.setToolTip("Old: " + BAD)  # (as a place that forgot to escape it would)
    view.addItem(item)
    view.show()
    QCoreApplication.processEvents()
    shown = _tooltip_at(view.viewport(), view.visualItemRect(item).center())
    assert "<img src" not in shown
    view.close()
