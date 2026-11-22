"""Auto-save (every few minutes while there are unsaved changes) and, on the next
start after a crash, the offer to restore what was auto-saved.

Each window's recovery copy is locked with a QLockFile, so a second running PlanetPDF
never offers another live window's copy. Qt treats a lock whose process has died as
stale, which is exactly the "left behind by a crash" case.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QLockFile, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from planetpdf.core.storage.recovery import RecoveryEntry, RecoveryStore

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _lock(path: Path) -> QLockFile:
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)  # never stale by age; only when its process is gone
    return lock


class AutoSaver(QObject):
    """One per window; every document (tab) in it has its own recovery copy, kept on the
    ``Document`` so it moves along when the document moves to another window."""

    def __init__(self, window: MainWindow, root: Path) -> None:
        super().__init__(window)
        self._window = window
        self.store = RecoveryStore(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.apply_settings()

    def apply_settings(self) -> None:
        minutes = max(0, int(self._window.settings.autosave_minutes or 0))
        if minutes:
            self.timer.start(minutes * 60_000)
        else:
            self.timer.stop()
            self.discard()

    def tick(self) -> bool:
        """Write a recovery copy of every document with unsaved changes. Returns True if
        any was written."""
        window = self._window
        if QApplication.activeModalWidget() is not None:
            return False  # mid-dialog or background task: try again next time
        written = 0
        for doc in list(window.documents):
            if not doc.is_dirty():
                self.discard(doc)  # e.g. undone back to the saved state
                continue
            try:
                data = doc.session.recovery_bytes()
                if doc.autosave_entry is None:
                    self.store.root.mkdir(parents=True, exist_ok=True)
                    entry_id = self.store.new_id()
                    lock = _lock(self.store.lock_path(entry_id))
                    lock.tryLock(0)
                    doc.autosave_entry, doc.autosave_lock = entry_id, lock
                self.store.write(doc.autosave_entry, doc.session.path, data)
                written += 1
            except (OSError, RuntimeError, ValueError) as exc:
                window.statusBar().showMessage(f"Auto-save of {doc.name} failed: {exc}", 8000)
                return False
        if written:
            window.statusBar().showMessage("Unsaved changes auto-saved for recovery.", 3000)
        return bool(written)

    def discard(self, doc=None) -> None:
        """The document (default: the current one) was saved or closed normally: its
        recovery copy isn't needed."""
        doc = doc if doc is not None else self._window.document
        if doc is None or doc.autosave_entry is None:
            return
        self.store.discard(doc.autosave_entry)
        if doc.autosave_lock is not None:
            doc.autosave_lock.unlock()
        doc.autosave_entry, doc.autosave_lock = None, None

    def leftovers(self) -> list[RecoveryEntry]:
        """Copies left behind by a crash (not ones an open document still owns)."""
        from planetpdf.ui.main_window import MainWindow

        live = {d.autosave_entry for _w, d in MainWindow.all_documents()} | {
            d.autosave_entry for d in self._window.documents}
        out = []
        for entry in self.store.entries():
            if entry.entry_id in live:
                continue
            lock = _lock(self.store.lock_path(entry.entry_id))
            if lock.tryLock(0):
                lock.unlock()
                out.append(entry)
        return out

    def offer_recovery(self) -> None:
        entries = self.leftovers()
        from planetpdf.ui import startup_log

        startup_log.log(f"Unsaved work from a crash: {len(entries)} document(s)")
        if entries:
            startup_log.log("Showing the Recover Unsaved Work dialog")
            RecoveryDialog(self._window, self, entries).exec()
            startup_log.log("Recover Unsaved Work dialog closed")

    def restore(self, entry: RecoveryEntry) -> Path | None:
        from planetpdf.ui.paths import documents_dir

        documents = documents_dir()
        try:
            target = self.store.restore_to(entry, documents)
        except OSError as exc:
            QMessageBox.critical(self._window, "Restore", f"The recovered document couldn't be written:\n\n{exc}")
            return None
        self._window.open_file(target)
        return target


class RecoveryDialog(QDialog):
    def __init__(self, window: MainWindow, saver: AutoSaver, entries: list[RecoveryEntry]) -> None:
        super().__init__(window)
        self._saver = saver
        self.setWindowTitle("Recover Unsaved Work")
        self.setMinimumWidth(520)
        self.restored: Path | None = None
        text = QLabel(
            "PlanetPDF closed unexpectedly. It auto-saved unsaved changes to these documents.\n\n"
            "Restore opens the recovered copy as “<name> (recovered).pdf” next to the original "
            "(the original file isn't changed); use Tools ▸ Compare Files to see what differs."
        )
        text.setWordWrap(True)
        self.list = QListWidget()
        for entry in entries:
            item = QListWidgetItem(f"{entry.name}   —   auto-saved {entry.saved_at:%Y-%m-%d %H:%M}\n{entry.original.parent}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self._restore())
        restore = QPushButton("Restore")
        restore.setDefault(True)
        restore.clicked.connect(self._restore)
        discard = QPushButton("Discard")
        discard.clicked.connect(self._discard)
        later = QPushButton("Decide Later")
        later.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(discard)
        buttons.addStretch(1)
        buttons.addWidget(later)
        buttons.addWidget(restore)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def _current(self) -> RecoveryEntry | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _restore(self) -> None:
        entry = self._current()
        if entry is None:
            return
        self.restored = self._saver.restore(entry)  # (a tab of its own: restore the others too)
        if self.restored is not None:
            self.list.takeItem(self.list.currentRow())
        if self.list.count() == 0:
            self.accept()

    def _discard(self) -> None:
        entry = self._current()
        if entry is None:
            return
        answer = QMessageBox.question(self, "Discard", f"Throw away the unsaved changes to {entry.name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._saver.store.discard(entry.entry_id)
        self.list.takeItem(self.list.currentRow())
        if self.list.count() == 0:
            self.accept()
