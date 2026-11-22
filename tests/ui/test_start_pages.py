"""Start pages in the middle of the window: Create PDF, Export PDF and Prepare Form (no
document open), as in Acrobat."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QToolButton

from planetpdf.core.export import ExportFormat
from planetpdf.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def empty(qapp, tmp_path):
    w = MainWindow(tmp_path / "profile")
    w.show()
    QCoreApplication.processEvents()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_create_pdf_page_blank_opens_a_document(empty, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    w = empty
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "new.pdf"), ""))
    w.tool_panes.open("create")
    page = w.start_pages["create"]
    assert w.central.currentWidget() is page
    labels = [page.list.item(i).text() for i in range(page.list.count())]
    assert labels == ["Single File", "Multiple Files", "Scanner", "Web Page", "Clipboard", "Blank Page"]  # (Acrobat)
    page.list.setCurrentRow(labels.index("Scanner"))
    assert page.go.isEnabled()
    page.list.setCurrentRow(labels.index("Blank Page"))
    page.run()
    assert w.session is not None and w.session.page_count == 1
    assert w.central.currentWidget() is w.view and w.tool_panes.current is None


def test_prepare_form_starts_with_field_detection(empty, tmp_path):
    """Acrobat (user screenshot 2026-10-03): a document without fields opens on Start, which
    turns its blanks into fields ("If you send it a standard PDF page without form fields,
    it creates a form to fill")."""
    import pymupdf

    from planetpdf.core.forms import fields as F

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Name: ______________________________", fontsize=11)
    page.insert_text((72, 140), "Date: ____________________", fontsize=11)
    doc.save(tmp_path / "form.pdf")
    w = empty
    w.tool_panes.open("prepare_form")
    start = w.start_pages["prepare_form"]
    assert w.central.currentWidget() is start
    w.open_file(tmp_path / "form.pdf")
    w.tool_panes.close()
    w.tool_panes.open("prepare_form")
    assert w.central.currentWidget() is start and start.document_box.isVisibleTo(start)  # (no fields yet)
    assert "auto detection is ON" in start.detect_line.text()
    start.list.setCurrentRow(0)
    start.run()
    assert {f.name for f in F.list_fields(w.session)} == {"Name", "Date"}
    assert w.central.currentWidget() is w.view and w.markup_ui.mode == "design"
    assert w._form_design_dock.isVisible() and w.form_panel.fields.topLevelItemCount() == 1
    w.undo_stack.undo()
    assert F.list_fields(w.session) == []
    w.tool_panes.close()
    w.tool_panes.open("prepare_form")
    assert w.central.currentWidget() is w.view  # (started once: the fields are edited here now)


def test_detection_can_be_switched_off(empty):
    empty.center_for(None)
    start = empty.start_pages["prepare_form"]
    start.detect_line.linkActivated.emit("change")
    assert not empty.settings.form_auto_detect and "OFF" in start.detect_line.text()


def test_export_page_runs_the_chosen_format(empty, pdf_copy):
    w = empty
    w.open_file(pdf_copy("text.pdf"))
    asked = []
    w.export_dialog = lambda fmt=None: asked.append(fmt)
    w.tool_panes.open("export")
    page = w.start_pages["export"]
    assert w.central.currentWidget() is page
    assert page.thumb.pixmap() is not None and not page.thumb.pixmap().isNull()
    page.list.setCurrentRow(0)
    page.run()
    page.choose("Image", "PNG")
    page.run()
    assert asked == [ExportFormat.DOCX, ExportFormat.PNG]
    # user 2026-10-02: Excel, PowerPoint and HTML still said "not available"
    for category, option, fmt in (("Spreadsheet", "Microsoft Excel Workbook", ExportFormat.XLSX),
                                  ("Microsoft PowerPoint", "PowerPoint Presentation", ExportFormat.PPTX),
                                  ("HTML Web Page", "HTML Web Page", ExportFormat.HTML),
                                  ("Microsoft Word", "Word 97-2003 Document", ExportFormat.DOC),
                                  ("More Formats", "PostScript", ExportFormat.PS)):
        page.choose(category, option)
        assert page.go.isEnabled()
        page.run()
        assert asked[-1] == fmt
    # the Export PDF tool bar offers them too
    bar_texts = {a.text() for a in w.tool_panes.bars["export"].actions()}
    bar_texts |= {b.text() for b in w.tool_panes.bars["export"].findChildren(QToolButton)}
    assert {"Spreadsheet", "PowerPoint", "HTML", "More Formats"} <= bar_texts


def test_no_start_page_choice_is_grayed_out(empty):
    empty.center_for(None)  # (builds the start pages)
    for page in empty.start_pages.values():
        missing = [c.label for c in page.choices if c.action is None]
        assert not missing, missing
        for row in range(page.list.count()):
            assert page.list.item(row).toolTip() == ""
