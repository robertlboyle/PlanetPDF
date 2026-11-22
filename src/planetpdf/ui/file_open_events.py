"""Files macOS asks PlanetPDF to open (Mac app, 2026-10-10).

On a Mac, a PDF double-clicked in Finder, chosen with Open With or dropped on the Dock icon
doesn't arrive on the command line: macOS sends it to the running program, which Qt hands
over as a ``QFileOpenEvent`` to the application. They can come before the main window exists
(the file that started PlanetPDF), so they wait until ``set_handler`` is called.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFileOpenEvent


class FileOpenEvents(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._waiting: list[Path] = []
        self._handler: Callable[[list[Path]], None] | None = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() if isinstance(event, QFileOpenEvent) else ""
            if path:
                self._received([Path(path)])
            return True
        return False

    def set_handler(self, handler: Callable[[list[Path]], None]) -> None:
        """From now on files go straight to ``handler``; any that came earlier go now."""
        self._handler = handler
        waiting, self._waiting = self._waiting, []
        if waiting:
            handler(waiting)

    def _received(self, paths: list[Path]) -> None:
        if self._handler is None:
            self._waiting.extend(paths)
        else:
            self._handler(paths)
