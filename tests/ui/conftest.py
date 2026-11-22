"""UI test guards.

A modal dialog in an offscreen test waits forever for a click that never comes, so
every dialog entry point is patched to fail the test immediately instead. Tests that
expect a dialog monkeypatch it themselves (their patch is applied after this one).
"""

from __future__ import annotations

import pytest


class UnexpectedDialog(AssertionError):
    pass


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    from PySide6.QtWidgets import QColorDialog, QFileDialog, QInputDialog, QMessageBox

    def fail(name):
        def raiser(*args, **kwargs):
            raise UnexpectedDialog(f"unexpected modal dialog: {name} {args[1:3]!r}")

        return staticmethod(raiser)

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, fail(f"QMessageBox.{name}"))
    for name in ("getText", "getInt", "getItem", "getMultiLineText", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, fail(f"QInputDialog.{name}"))
    monkeypatch.setattr(QColorDialog, "getColor", fail("QColorDialog.getColor"))
    for name in ("getOpenFileName", "getSaveFileName", "getOpenFileNames"):
        monkeypatch.setattr(QFileDialog, name, fail(f"QFileDialog.{name}"))


@pytest.fixture(autouse=True)
def delete_closed_windows():
    """Closed main windows are only hidden; hundreds of them pile up over a run and make
    every gc.collect() (done before each save) slower and slower. Delete them."""
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from planetpdf.ui.main_window import MainWindow

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, MainWindow) and not widget.isVisible():
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
