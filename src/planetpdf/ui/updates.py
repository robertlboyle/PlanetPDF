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

    @property
    def user_can_install(self) -> bool:
        """An installed copy this user can update (not a standard user on a per-machine install,
        as on a terminal server: ``update.user_may_install``)."""
        return self.can_install and U.user_may_install(self.root)

    # --- automatic ------------------------------------------------------------------------

    def start(self) -> None:
        """The quiet checks, and what the last install did."""
        result = H.read_result(self.folder, running=str(self.current), installed_at=_installed_at())
        if result:
            self.show_result(result)
        self.check_install_parts()
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

    # --- the install folder's own programs --------------------------------------------------

    def check_install_parts(self) -> bool:
        """Security list 2026-11-26, item 6: an update is installed by the version it replaces, and
        versions up to 1.0.6 replaced only the program, so an install updated from one of them
        still has its old launcher and PlanetPDF printer service. Said once per version: someone
        who can approve Windows' administrator prompt is offered to bring them up to date now;
        anyone else is told whom to ask. True when something was said."""
        if not self.can_install:
            return False
        stale = U.stale_root_files(self.root)
        version = str(self.current)
        if not stale or self.settings.install_parts_noticed == version:
            return False
        self.settings.install_parts_noticed = version
        self.window.save_settings_now()
        printer = any(name.startswith("printer") for name in stale)
        launcher = any(not name.startswith("printer") for name in stale)
        what = " and ".join(part for part, there in (("the PlanetPDF printer's service", printer),
                                                     ("the program that starts PlanetPDF", launcher)) if there)
        verb, them = ("are", "them") if printer and launcher else ("is", "it")
        box = QMessageBox(QMessageBox.Icon.Information, "PlanetPDF Update",
                          f"PlanetPDF {version} is installed, but {what} on this computer {verb} still from an "
                          "older version.", parent=self.window)
        if not self.user_can_install:
            box.setInformativeText(f"An administrator can bring {them} up to date by running PlanetPDF's "
                                   f"installer for version {version} on this computer. Ask whoever looks after "
                                   "it. PlanetPDF itself works as usual.")
            box.addButton(QMessageBox.StandardButton.Ok)
            self.show_box(box)
            return True
        box.setIcon(QMessageBox.Icon.Question)
        box.setInformativeText(f"Bring {them} up to date now? Windows asks for an administrator's OK. "
                               "(Later: run PlanetPDF's installer for this version.)")
        now = box.addButton("Update Now", QMessageBox.ButtonRole.AcceptRole)
        later = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(now)
        box.setEscapeButton(later)  # (closing the question changes nothing)
        self.show_box(box)
        if box.clickedButton() is now:
            self.refresh_install()
        return True

    def refresh_install(self) -> bool:
        """Runs the administrator step that replaces the install folder's own programs with this
        version's (one Windows prompt), and says how it went. True when they're up to date."""
        window = self.window
        try:
            self.run_as_administrator(H.refresh_command(self.root))
        except Exception as exc:  # noqa: BLE001 (the prompt was refused, or Windows couldn't start it)
            canceled = type(exc).__name__ == "ElevationCanceled"
            window.show_message("PlanetPDF Update", "Nothing was changed: Windows' administrator prompt was "
                                "canceled." if canceled else f"Windows couldn't start it as administrator ({exc}).")
            return False
        left = U.stale_root_files(self.root)
        window.show_message("PlanetPDF Update", "PlanetPDF's install is up to date now." if not left else
                            "It couldn't be finished. Run PlanetPDF's installer for this version instead "
                            f"(still older: {', '.join(left)}).")
        return not left

    def run_as_administrator(self, command: list[str]) -> int:
        """Windows' administrator prompt, then ``command`` (tests replace this)."""
        from planetpdf.core import windows_elevate as E

        return E.run_elevated(command)

    def due(self, now: float | None = None) -> bool:
        """The periodic check is due: every ``update_every_days`` days (0 = never)."""
        days = self.settings.update_every_days
        now = time.time() if now is None else now
        return days > 0 and now - self.settings.update_last_check >= days * DAY

    def check_quietly(self, at_start: bool = False) -> None:
        """Check in the background; a newer version is offered, and downloaded only when the user
        says so (security review 2026-11-25). At start (when that preference is on), else when the
        periodic check is due."""
        if self._busy or self.pending or not self.can_install or not (at_start or self.due()):
            return
        self.settings.update_last_check = time.time()
        self._busy = True
        threading.Thread(target=self._check_and_download, daemon=True).start()

    def _check_and_download(self) -> None:
        """Only checks (downloads are asked for: ``offer_download``). An update downloaded before
        is offered again without downloading."""
        try:
            ready = U.downloaded(self.folder, self.current)
            found = ready if ready is not None else (U.check(self.url, self.current), None)
        except (U.UpdateError, OSError):
            found = (None, None)  # (quiet: offline, server down… tried again tomorrow)
        finally:
            self._busy = False
        signed, path = found
        if signed is not None and signed.release.version != self.settings.update_skipped:
            self._found.emit(signed, path)

    def _offer(self, signed: U.SignedRelease, path: Path | None) -> None:
        if self.pending is None:
            from planetpdf.ui.main_window import MainWindow

            self.window = MainWindow.last_active_window() or self.window  # (the first may be closed)
            if path is None:
                path = self.offer_download(signed)
            if path is not None:
                self.ask(signed, path)

    def offer_download(self, signed: U.SignedRelease, *, asked: bool = False) -> Path | None:
        """A newer version is out: download it only when the user says so; where they can't
        install it (a standard user on a per-machine install), only say so, once per version.
        Returns the downloaded file, or None."""
        release = signed.release
        if not self.user_can_install:
            if asked or self.settings.update_noticed != release.version:
                self.settings.update_noticed = release.version
                box = QMessageBox(QMessageBox.Icon.Information, "PlanetPDF Update",
                                  f"PlanetPDF {release.version} is available (you have {self.current}).",
                                  parent=self.window)
                box.setInformativeText("On this computer, updates are installed by an administrator, so PlanetPDF "
                                       "doesn't download it. Ask whoever looks after this computer.")
                box.addButton(QMessageBox.StandardButton.Ok)
                self.show_box(box)
            return None
        box = QMessageBox(QMessageBox.Icon.Information, "PlanetPDF Update",
                          f"PlanetPDF {release.version} is available (you have {self.current}). Download it now?",
                          parent=self.window)
        info = f"The download is {_mb(release.size)}."
        if release.notes:
            info = f"What's new:\n{release.notes}\n\n{info}"
        box.setInformativeText(info)
        download = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        not_now = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        skip = None if asked else box.addButton("Skip This Version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(download)
        box.setEscapeButton(not_now)  # (closing the question is "not now", never a yes)
        self.show_box(box)
        clicked = box.clickedButton()
        if skip is not None and clicked is skip:
            self.settings.update_skipped = release.version
            return None
        if clicked is not download:
            return None
        try:
            return run_in_background(self.window, "PlanetPDF Update", "Downloading the update",
                                     lambda progress: U.download(self.url, signed, self.folder, progress),
                                     describe=download_text)
        except TaskCanceled:
            return None
        except U.UpdateError as exc:
            QMessageBox.warning(self.window, "PlanetPDF Update", str(exc))
            return None

    # --- Help ▸ Check for Updates ---------------------------------------------------------

    def check_now(self, window: MainWindow) -> None:
        self.window = window
        if not self.url:
            off = U.checks_off(self.root)
            window.show_message("Check for Updates", f"{off} PlanetPDF {self.current} is installed." if off else
                                "No update server is set up for this copy of PlanetPDF yet.")
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
            path = self.offer_download(signed, asked=True)  # (asked first: downloads the user asks for)
            if path is None:
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
        later = box.addButton("When I Close PlanetPDF", QMessageBox.ButtonRole.ActionRole)
        not_now = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        skip = None if asked else box.addButton("Skip This Version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(later)
        # Closing the question (its X, Esc) is "not now": nothing is installed (security review
        # 2026-11-25: it counted as "When I Close", and quitting raised an administrator prompt)
        box.setEscapeButton(not_now)
        self.show_box(box)
        clicked = box.clickedButton()
        if skip is not None and clicked is skip:
            self.settings.update_skipped = release.version
            return
        if clicked is not now and clicked is not later:
            return  # (offered again at the next check: it stays downloaded)
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
