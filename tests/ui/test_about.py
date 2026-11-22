"""Help ▸ About lists the third-party components and shows each one's license."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from planetpdf import APP_NAME, __version__
from planetpdf.ui.dialogs.about import AboutDialog, load_components


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_about_lists_components_and_jumps_to_a_license(qapp):
    dialog = AboutDialog()
    dialog.show()
    QApplication.processEvents()
    components = load_components()
    assert dialog.table.rowCount() == len(components) > 30
    assert dialog.windowTitle() == f"About {APP_NAME}"
    assert __version__ in dialog.findChildren(type(dialog.layout().itemAt(0).widget()))[0].text()
    names = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    qt = next(r for r, n in enumerate(names) if n.startswith("Qt"))
    dialog.table.selectRow(qt)
    QApplication.processEvents()
    assert dialog.section_at_top().startswith("Qt ")
    shown = dialog.text.toPlainText()
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in shown
    pymupdf = names.index("pymupdf")
    dialog.table.selectRow(pymupdf)
    QApplication.processEvents()
    assert dialog.section_at_top().startswith("pymupdf ")
    dialog.close()


def test_help_menu_opens_it(qapp, tmp_path, monkeypatch):
    from planetpdf.ui.main_window import MainWindow

    shown = []
    monkeypatch.setattr(AboutDialog, "exec", lambda self: shown.append(self.table.rowCount()) or 0)
    w = MainWindow(tmp_path / "profile")
    w.act_about.trigger()
    assert shown and shown[0] > 30
    w.close()
