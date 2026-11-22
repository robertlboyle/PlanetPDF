"""Run slow core work (OCR, exports) on a background thread with a progress dialog.

**Every long job reports real numbers** (user rule 2026-10-02): "Exporting page 3 of
10", "Reading page 4 of 12", "Writing file 2 of 5", never only "Working…". The work
function receives ``progress(done, total, stage="")`` (see ``core.progress``), which
returns False once the user has pressed Cancel; with a ``stage`` the dialog shows
"<stage> <done + 1> of <total>" under the job's label.

The work function must not touch the open session's document: give it its own copy
(e.g. opened from ``session.doc.tobytes()``). Work that has to use the session runs
with ``run_with_progress`` instead (same dialog, on the GUI thread).

PyMuPDF holds the GIL while it processes a page, so the window freezes during each
page (accepted by the user). To make sure the dialog always shows *which* page is
being worked on before that freeze, ``progress`` waits briefly for the GUI thread to
repaint the dialog with the new text before returning to the work.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QProgressBar, QProgressDialog, QWidget

from planetpdf.core.progress import stage_text
from planetpdf.ui import accessibility

REPAINT_WAIT_S = 0.5  # max time the worker waits for the dialog to show the new page

Describe = Callable[[int, int], str]


def page_progress_text(activity: str, pages: list[int] | None = None, verb: str = "Working on page") -> Describe:
    """Label text like "OCR in progress\\nWorking on page 3 of 10" (``verb``: e.g.
    "Exporting page", "Recognizing text on page").

    ``done`` pages are finished, so the page being worked on is number done + 1. When
    ``pages`` (0-based document pages) isn't simply every page from the first, the
    real page number is added: "Working on page 2 of 3 (page 7 of the document)".
    """

    def describe(done: int, total: int) -> str:
        if total <= 0:
            return f"{activity}\nWorking…"
        if done >= total:
            return f"{activity}\nFinishing ({total} of {total} pages done)…"
        text = f"{activity}\n{verb} {done + 1} of {total}"
        if pages is not None and done < len(pages) and pages[done] != done:
            text += f" (page {pages[done] + 1} of the document)"
        return text

    return describe


class _Bridge(QObject):
    progressed = Signal(int, int, str)
    committed = Signal()
    finished = Signal()


class TaskCanceled(Exception):
    pass


def _progress_dialog(parent: QWidget | None, title: str, label: str, cancelable: bool,
                     cancel: threading.Event) -> QProgressDialog:
    dialog = QProgressDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)  # up at once: the window is about to freeze
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(380)
    bar = QProgressBar(dialog)
    bar.setTextVisible(False)  # no percentage: the label says which page
    dialog.setBar(bar)
    dialog.setRange(0, 0)  # busy until the first count arrives
    dialog.setLabelText(f"{label}\nStarting…")
    if cancelable:
        dialog.setCancelButtonText("Cancel")
    else:
        dialog.setCancelButton(None)
    dialog.canceled.connect(cancel.set)
    dialog.show()
    for _ in range(3):  # make sure it's actually painted before any work starts
        QApplication.processEvents()
    return dialog


def _show_progress(dialog: QProgressDialog, label: str, describe: Describe, done: int, total: int, stage: str) -> None:
    """The bar and the label: "<label>\\n<stage> 3 of 10" (or the job's own describe)."""
    if total > 0:
        dialog.setRange(0, total)
        dialog.setValue(min(done, total))
    else:
        dialog.setRange(0, 0)  # (a step that can't be counted: busy)
    dialog.setLabelText(f"{label}\n{stage_text(stage, done, total)}" if stage else describe(done, total))
    accessibility.say_progress(dialog, dialog.labelText())  # (screen readers: every few seconds)
    dialog.repaint()
    QApplication.processEvents()


def _close(dialog: QProgressDialog, cancel: threading.Event) -> bool:
    # Read the state *before* closing: QProgressDialog emits canceled() when closed.
    canceled = cancel.is_set()
    dialog.canceled.disconnect(cancel.set)
    dialog.close()
    dialog.deleteLater()
    return canceled


def run_with_progress(
    parent: QWidget | None,
    title: str,
    label: str,
    work: Callable[[Callable[..., bool]], Any],
    *,
    cancelable: bool = True,
    describe: Describe | None = None,
) -> Any:
    """Like ``run_in_background`` but on the GUI thread, for work that must use the open
    session (split, redaction): the same dialog with the same real counts; the window is
    blocked (modal) while it runs."""
    describe = describe or page_progress_text(label)
    cancel = threading.Event()
    dialog = _progress_dialog(parent, title, label, cancelable, cancel)

    def progress(done: int, total: int, stage: str = "") -> bool:
        _show_progress(dialog, label, describe, done, total, stage)
        return not cancel.is_set()

    def commit() -> None:
        """Past the point of no return (e.g. the new file is being written): no Cancel any
        more, and one pressed just now is ignored, so the finished result is kept."""
        dialog.setCancelButton(None)
        cancel.clear()

    progress.commit = commit
    try:
        result = work(progress)
    finally:
        canceled = _close(dialog, cancel)
    if canceled:
        raise TaskCanceled()
    return result


def run_in_background(
    parent: QWidget | None,
    title: str,
    label: str,
    work: Callable[[Callable[..., bool]], Any],
    *,
    cancelable: bool = True,
    describe: Describe | None = None,
) -> Any:
    """Run ``work(progress)``; return its result. Re-raises its exception here.
    Raises ``TaskCanceled`` if the user canceled. ``progress.commit()`` as in
    ``run_with_progress``: past the point of no return, Cancel goes."""
    describe = describe or page_progress_text(label)
    bridge = _Bridge()
    cancel = threading.Event()
    shown = threading.Event()
    committed = threading.Event()
    outcome: dict[str, Any] = {}
    dialog = _progress_dialog(parent, title, label, cancelable, cancel)

    def on_progress(done: int, total: int, stage: str) -> None:
        _show_progress(dialog, label, describe, done, total, stage)
        shown.set()

    def on_commit() -> None:
        dialog.setCancelButton(None)
        cancel.clear()

    bridge.progressed.connect(on_progress, Qt.ConnectionType.QueuedConnection)
    bridge.committed.connect(on_commit, Qt.ConnectionType.QueuedConnection)

    def progress(done: int, total: int, stage: str = "") -> bool:
        shown.clear()
        bridge.progressed.emit(done, total, stage)
        shown.wait(REPAINT_WAIT_S)  # let the dialog show this page before we freeze
        return committed.is_set() or not cancel.is_set()

    def commit() -> None:
        committed.set()
        bridge.committed.emit()

    progress.commit = commit

    def target() -> None:
        try:
            outcome["result"] = work(progress)
        except BaseException as exc:  # handed back to the GUI thread below
            outcome["error"] = exc
        finally:
            bridge.finished.emit()

    loop = QEventLoop()
    bridge.finished.connect(loop.quit, Qt.ConnectionType.QueuedConnection)
    thread = threading.Thread(target=target, name=f"planetpdf-{title}", daemon=True)
    thread.start()
    loop.exec()
    thread.join()
    canceled = _close(dialog, cancel) and not committed.is_set()

    if "error" in outcome:
        raise outcome["error"]
    if canceled:
        raise TaskCanceled()
    return outcome.get("result")
