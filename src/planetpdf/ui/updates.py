"""Updates in the window (user 2026-10-04): Help ▸ Check for Updates…, a quiet check
while PlanetPDF runs once 14 days have passed since the last one, and installing when PlanetPDF closes (core/update.py,
core/update_helper.py).

Found automatically: downloaded quietly in the background, then "PlanetPDF X is ready:
Restart Now / When I Close PlanetPDF / Skip This Version". Checked from the menu: the same
with counted dialogs. Installing runs after the last window closes (one Windows
administrator prompt); "Restart Now" closes every window first (each asks about unsaved
work) and starts the new version afterwards. What the install did is shown on the next
start."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from planetpdf import UPDATE_URL, __version__
from planetpdf.core import update as U
from planetpdf.core import update_helper as H
from planetpdf.ui.tasks import TaskCanceled, run_in_background

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

FIRST_CHECK_MS = 20_000  # after starting (don't slow the start)
TICK_MS = 60 * 60 * 1000  # look again every hour whether it's time
# When it checks by itself is a preference (user 2026-10-07): at start (settings.
# check_updates_on_start) and every settings.update_every_days days while it runs (0 = never);
# Help ▸ Check for Updates… any time.
DAY = 24 * 60 * 60


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f}"


def _installed_at() -> float:
    """When the running program's files were made (its build), for telling an older install's
    result from this one's; 0 when not a built copy."""
    if not getattr(sys, "frozen", False):
        return 0.0
    try:
        return Path(sys.executable).stat().st_mtime
    except OSError:
        return 0.0


def download_text(done: int, total: int, stage: str = "") -> str:
    return f"Downloading the update: {_mb(done)} of {_mb(total)} MB"


class UpdateManager(QObject):
    """One per running PlanetPDF (every window shares it)."""

    _found = Signal(object, object)  # (SignedRelease, zip path) from the worker thread

    def __init__(self, window: MainWindow, folder: Path, *, root: Path | None = None, url: str | None = None,
                 current: str | None = None) -> None:
        super().__init__(QApplication.instance())
        self.window = window
        self.folder = folder
        self.root = root if root is not None else U.install_root()
        self.url = url if url is not None else U.configured_url(self.root, UPDATE_URL)
        self.current = current or (U.current_version(self.root) if self.root else None) or __version__
        self.pending: tuple[U.SignedRelease, Path] | None = None  # installs when PlanetPDF closes
        self.restart = False
        self._busy = False
        self._found.connect(self._offer)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_quietly)
        app = QApplication.instance()
        app.aboutToQuit.connect(self.install_on_exit)

    @property
    def settings(self):
        return self.window.settings

    @property
    def can_install(self) -> bool:
        return self.root is not None

    # --- automatic ------------------------------------------------------------------------

    def start(self) -> None:
        """The quiet checks, and what the last install did."""
        result = H.read_result(self.folder, running=str(self.current), installed_at=_installed_at())
        if result:
            self.show_result(result)
        if self.can_install and self.url:
            if self.settings.check_updates_on_start:
                QTimer.singleShot(FIRST_CHECK_MS, lambda: self.check_quietly(at_start=True))
            self._timer.start(TICK_MS)

    def show_result(self, result: dict) -> None:
        """What the last install did. When Windows couldn't start the installer as administrator
        (user 2026-10-26: Windows 11's prompt failing), the download page is one click away."""
        message = str(result.get("message", ""))
        if result.get("ok"):
            QMessageBox.information(self.window, "PlanetPDF Update", message)
            return
        box = QMessageBox(QMessageBox.Icon.Warning, "PlanetPDF Update", message, parent=self.window)
        page = None
        if result.get("manual"):
            page = box.addButton("Open Download Page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close if page else QMessageBox.StandardButton.Ok)
        self.show_box(box)
        if page is not None and box.clickedButton() is page:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            from planetpdf import DOWNLOAD_PAGE

            QDesktopServices.openUrl(QUrl(DOWNLOAD_PAGE))

    def show_box(self, box: QMessageBox) -> None:
        """Shows ``box`` (tests replace this)."""
        box.exec()

    def due(self, now: float | None = None) -> bool:
        """The periodic check is due: every ``update_every_days`` days (0 = never)."""
        days = self.settings.update_every_days
        now = time.time() if now is None else now
        return days > 0 and now - self.settings.update_last_check >= days * DAY

    def check_quietly(self, at_start: bool = False) -> None:
        """Check and download in the background; ask only when it's ready. At start (when
        that preference is on), else when the periodic check is due."""
        if self._busy or self.pending or not self.can_install or not (at_start or self.due()):
            return
        self.settings.update_last_check = time.time()
        self._busy = True
        threading.Thread(target=self._check_and_download, daemon=True).start()

    def _check_and_download(self) -> None:
        try:
            ready = U.downloaded(self.folder, self.current)
            if ready is None:
                signed = U.check(self.url, self.current)
                if signed is not None and signed.release.version != self.settings.update_skipped:
                    ready = signed, U.download(self.url, signed, self.folder)
        except (U.UpdateError, OSError):
            ready = None  # (quiet: offline, server down… tried again tomorrow)
        finally:
            self._busy = False
        if ready is not None and ready[0].release.version != self.settings.update_skipped:
            self._found.emit(*ready)

    def _offer(self, signed: U.SignedRelease, path: Path) -> None:
        if self.pending is None:
            from planetpdf.ui.main_window import MainWindow

            self.window = MainWindow.last_active_window() or self.window  # (the first may be closed)
            self.ask(signed, path)

    # --- Help ▸ Check for Updates ---------------------------------------------------------

    def check_now(self, window: MainWindow) -> None:
        self.window = window
        if not self.url:
            window.show_message("Check for Updates", "No update server is set up for this copy of PlanetPDF yet.")
            return
        if self.pending is not None:
            self.ask(*self.pending)
            return
        try:
            signed = run_in_background(window, "Check for Updates", "Checking for updates",
                                       lambda progress: U.check(self.url, self.current), cancelable=True,
                                       describe=lambda _d, _t: f"Asking {self.url} for the latest version…")
        except TaskCanceled:
            return
        except U.UpdateError as exc:
            QMessageBox.warning(window, "Check for Updates", str(exc))
            return
        self.settings.update_last_check = time.time()
        if signed is None:
            window.show_message("Check for Updates", f"You have the latest version of PlanetPDF ({self.current}).")
            return
        if not self.can_install:
            window.show_message("Check for Updates",
                                f"PlanetPDF {signed.release.version} is available (you have {self.current}).\n\n"
                                "This copy isn't an installed one (it runs from the source code or a plain "
                                "build folder), so it can't update itself.")
            return
        ready = U.downloaded(self.folder, self.current)
        if ready is None or ready[0].release.version != signed.release.version:
            try:
                path = run_in_background(window, "Check for Updates", "Downloading the update",
                                         lambda progress: U.download(self.url, signed, self.folder, progress),
                                         describe=download_text)
            except TaskCanceled:
                return
            except U.UpdateError as exc:
                QMessageBox.warning(window, "Check for Updates", str(exc))
                return
            ready = signed, path
        self.ask(*ready, asked=True)

    # --- installing -----------------------------------------------------------------------

    def ask(self, signed: U.SignedRelease, path: Path, *, asked: bool = False) -> None:
        release = signed.release
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("PlanetPDF Update")
        box.setText(f"PlanetPDF {release.version} is ready to install (you have {self.current}).")
        info = install_note(self.root)
        if release.notes:
            info = f"What's new:\n{release.notes}\n\n{info}"
        box.setInformativeText(info)
        now = box.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
        later = box.addButton("When I Close PlanetPDF", QMessageBox.ButtonRole.RejectRole)
        skip = None if asked else box.addButton("Skip This Version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(later)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.settings.update_skipped = release.version
            return
        self.pending = (signed, path)
        if clicked is now:
            self.restart = True
            if not self.window.quit_all():  # (a window canceled closing: install when it does)
                self.restart = False
        else:
            self.window.statusBar().showMessage(
                f"PlanetPDF {release.version} will be installed when you close PlanetPDF.", 8000)

    def install_on_exit(self) -> None:
        if self.pending is None or self.root is None:
            return
        _signed, path = self.pending
        self.pending = None
        if U.is_appimage(self.root):  # (here and now: a helper would run from the AppImage's mount)
            H.install_appimage(self.root, path, self.folder, self.restart)
            return
        H.start_detached(H.finish_command(self.root, path, self.folder, os.getpid(), self.restart))


def install_note(root: Path | None, platform: str | None = None) -> str:
    """What installing involves on this system."""
    platform = platform or sys.platform
    if platform == "darwin" or platform.startswith("linux"):
        admin = root is not None and U.needs_admin(root)
        return ("Installing asks for an administrator's password, and PlanetPDF must close." if admin
                else "PlanetPDF must close to install it.")
    return "Installing needs a Windows administrator's OK, and PlanetPDF must close."


_manager: UpdateManager | None = None


def manager(window: MainWindow) -> UpdateManager:
    """The running PlanetPDF's update manager (made with the first window that asks)."""
    global _manager
    if _manager is None:
        from planetpdf.ui import paths

        _manager = UpdateManager(window, paths.updates_dir())
    return _manager
