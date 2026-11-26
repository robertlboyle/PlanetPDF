from __future__ import annotations

import sys
from pathlib import Path

from planetpdf.ui import startup_log as S


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    from planetpdf.core.clean_environment import scrub

    scrub(argv)  # (the update's installing step: nothing of the user's environment steers it)
    from planetpdf.ui.printer_probe import PROBE_FLAG

    if PROBE_FLAG in argv:  # (a helper process checking a printer's driver: printer_probe)
        from planetpdf.ui.printer_probe import probe_main

        return probe_main(argv)
    from planetpdf.self_test import FLAG as SELF_TEST

    if SELF_TEST in argv:  # (is everything in the build? self_test.py)
        from planetpdf.self_test import main as self_test

        return self_test(argv)
    if sys.platform == "win32" and "--choose-pdf-app" in argv:  # (after installing: pick the PDF app)
        from planetpdf.ui.windows_shell import choose_pdf_app_main

        return choose_pdf_app_main()
    from planetpdf.core.update_helper import FLAGS as UPDATE_FLAGS

    if any(flag in argv for flag in UPDATE_FLAGS):  # (installing an update: core/update_helper)
        from planetpdf.core.update_helper import helper_main

        return helper_main(argv)
    from planetpdf import cli

    if cli.is_command(argv):  # (planetpdf <command> …: the command line, no window)
        return cli.main(argv)
    S.begin()
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QTimer, qVersion
    from PySide6.QtWidgets import QApplication

    S.log(f"PySide6 {pyside_version}, Qt {qVersion()}")
    from planetpdf import APP_NAME, ORG_NAME, __version__

    if sys.platform == "win32":
        from planetpdf.ui.windows_shell import set_app_user_model_id

        set_app_user_model_id()  # before any window: taskbar shows our icon, not Python's
    elif sys.platform == "darwin":
        from planetpdf.ui.macos_shell import set_app_name

        set_app_name(APP_NAME)  # before QApplication: the menu bar says PlanetPDF, not Python
    app = QApplication(argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    from planetpdf.ui.file_open_events import FileOpenEvents

    file_events = FileOpenEvents(app)  # macOS: Finder's Open / Open With / the Dock icon
    app.installEventFilter(file_events)
    S.log(f"QApplication created (PlanetPDF {__version__})")
    files = [Path(a) for a in argv[1:] if not a.startswith("-")]
    single = "--new-instance" not in argv
    from planetpdf.ui import paths, single_instance
    from planetpdf.ui.single_instance import InstanceServer

    # How this user's PlanetPDFs reach each other is said in the user's own folder, under names
    # made at random for each run: nobody else can stand in for one (security list 2026-11-26)
    single_instance.configure(paths.instance_dir())
    shared = False
    if single:
        # One PlanetPDF per user (user 2026-10-02): a later start hands its files to the
        # running one (tabs there) and stops here, before loading anything heavy. The running
        # one may still be starting (it holds the lock but its window isn't ready): keep trying
        # (user 2026-10-22: a second copy had the same file open).
        from planetpdf.ui.single_instance import WAIT_SECONDS, instance_lock, send_to_running

        instance = instance_lock()
        app.planetpdf_instance_lock = instance  # (held as long as this copy runs)
        if not instance.tryLock(0):
            if send_to_running(files, wait=WAIT_SECONDS, lock=instance):
                S.log("PlanetPDF is already running: the files were handed to it")
                S.finished()
                return 0
            if not instance.isLocked():
                S.log("Another PlanetPDF holds the start lock but didn't answer: starting anyway")
        shared = instance.isLocked()
    # Listening now (files wait for the window): for later starts when this is the user's running
    # copy (``shared``), and always for a copy asking this one to show a file it has open
    server = InstanceServer(None, shared=shared, parent=app)
    app.aboutToQuit.connect(server.close)
    S.log(("Listening for later starts" if shared else "Listening for other PlanetPDFs") if server.listening
          else "Couldn't listen for later starts")
    single_instance.forget_stale()
    from planetpdf.ui import file_locks

    profile = paths.profile()
    file_locks.configure(paths.open_files_dir())  # (a file open in one PlanetPDF only)
    app.aboutToQuit.connect(file_locks.release_all)
    from planetpdf.core import neural_speech

    neural_speech.USER_MODELS = paths.models_dir()  # (Read Out Loud's voices, downloaded on first use)
    S.attach(profile.root)  # from here on the log is also in <profile>/logs/startup.log
    S.log(f"Profile folder: {profile.root}")
    S.describe_screens(app)

    from planetpdf.ui.icons import app_icon

    app.setWindowIcon(app_icon())  # every window and dialog
    S.log("Loading the program (PyMuPDF, pyHanko, ...)")
    from planetpdf.ui.main_window import MainWindow

    if S.previous_start_failed:
        S.log("The last start didn't finish: skipping the saved toolbar and panel layout this time")
    S.log("Creating the main window")
    window = MainWindow(profile.root, paths.recovery_dir(), restore_layout=not S.previous_start_failed)
    from planetpdf.core import ai_ocr

    ai_ocr.IDLE_SECONDS = window.settings.ai_ocr_idle_minutes * 60  # (the AI text reader's time on the card)
    from planetpdf.ui.errors import install_excepthook

    install_excepthook(window)  # an error in a command is shown and logged, never silent
    S.describe_window(window, "Window before showing (saved position)")
    window.show()
    moved = window.ensure_on_screen()
    if moved:
        S.log(f"The saved window position was off every screen: moved onto {moved}")
    window.raise_()
    window.activateWindow()
    S.describe_window(window, "Window after showing")
    if files:  # several: a tab each
        S.log(f"Opening {", ".join(str(f) for f in files)}")
        window.open_paths(files)
        S.log(f"Files opened: {len(window.documents)} tab(s)")

    def handed_over(paths: list[Path]) -> None:  # a later start's files: tabs in the window in front
        target = next((w for w in MainWindow.open_windows() if w.isActiveWindow()), None) \
            or MainWindow.last_active_window() or window
        if paths:
            target.open_paths(paths)
            S.log(f"Handed over by a later start: {len(paths)} file(s); {len(target.documents)} tab(s) now")
        target.bring_forward()

    server.set_handler(handed_over)  # (files handed over while the window was built open now)

    from planetpdf.ui import api_server

    api = api_server.attach(window, profile.root)  # other programs and plugins (the local API)
    if api is not None:
        S.log(f"API listening at {api.address}" if api.listening else "The API couldn't listen (already served?)")
        app.aboutToQuit.connect(api.close)

    def from_finder(paths: list[Path]) -> None:  # (macOS sends files to the running copy)
        target = MainWindow.last_active_window() or window
        target.open_paths(paths)
        target.bring_forward()

    file_events.set_handler(from_finder)

    def started() -> None:
        S.describe_window(window, "Window once the event loop runs")
        S.finished()
        S.log("Checking for unsaved work from a crash")
        window.autosave.offer_recovery()  # after a crash: restore unsaved work?
        from planetpdf.ui import updates

        updates.manager(window).start()  # what the last update did; the check every 14 days
        from planetpdf.ui import default_app

        default_app.offer_at_first_start(window)  # Mac app: open PDF files with PlanetPDF?
        from planetpdf.ui import printouts

        printouts.open_waiting(window)  # printouts that came while PlanetPDF wasn't running
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            # "Open in PlanetPDF" in every print dialog's PDF menu (the Mac's PlanetPDF printer)
            try:
                from planetpdf.core import mac_pdf_service
                from planetpdf.ui import macos_shell

                mac_pdf_service.install(macos_shell.bundle_identifier() or "net.planet.planetpdf")
            except OSError as exc:
                S.log(f"The print dialog's Open in PlanetPDF couldn't be set up: {exc}")

    QTimer.singleShot(0, started)
    QTimer.singleShot(5000, lambda: S.describe_window(window, "Window after 5 seconds"))
    code = app.exec()
    S.log(f"Event loop ended with code {code}")
    return code
