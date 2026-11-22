"""Asking the user before a script, program or plugin signs (user 2026-10-18: "The script running
as me would need access to be approved to use any digital signatures. Other functions are fine").

A PlanetPDF window, on top, that the script can't answer through its input. Used by the command
line (``planetpdf.cli``) and the local API (``ui/api_server.py``).
"""

from __future__ import annotations


def _application():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        from planetpdf import APP_NAME, ORG_NAME

        app = QApplication([APP_NAME])
        app.setOrganizationName(ORG_NAME)
        app.setApplicationName(APP_NAME)
    return app


def connect_text(name: str, publisher: str, program: str, before: str = "") -> tuple[str, str]:
    """(question, details) for ``ask_to_connect``."""
    said = f"“{name}”" + (f" from “{publisher}”" if publisher else "")
    where = program or "(PlanetPDF couldn't tell which program this is)"
    if before:
        question = f"A different program is using the key of {said}."
        details = (f"{said} was allowed before, as:\n{before}\n\nThe program using its key now is:\n{where}\n\n"
                   "Allow it only if you updated or moved that program yourself.")
    else:
        question = "A program wants to use PlanetPDF."
        details = (f"It says it is {said}. The program is:\n{where}\n\nIf you allow it, it can open, read, "
                   "change, and save PDF files through PlanetPDF without asking again (signing always asks "
                   "first). Allow it only if you started or installed this program yourself. You can block "
                   "it later in Preferences ▸ Scripting & Plugins.")
    return question, details


def ask_to_connect(name: str, publisher: str, program: str, before: str = "") -> bool:
    """May this program use PlanetPDF through the local API? (security review 2026-11-21) The
    program file comes from the operating system, not from what the program says."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    _application()
    question, details = connect_text(name, publisher, program, before)
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Allow a Program to Use PlanetPDF?")
    box.setText(question)
    box.setInformativeText(details)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    allow = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
    deny = box.addButton("Don't Allow", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(deny)
    box.setEscapeButton(deny)
    box.raise_()
    box.activateWindow()
    box.exec()
    return box.clickedButton() is allow


def ask_to_sign(who: str, description: str, *, remember: bool = False) -> bool | str:
    """Ask whether ``who`` may sign as described. True / False; with ``remember`` the answer can
    also be "always" (the user's choice to let this program sign from now on, until revoked)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance()
    if app is None:
        from planetpdf import APP_NAME, ORG_NAME

        app = QApplication([APP_NAME])
        app.setOrganizationName(ORG_NAME)
        app.setApplicationName(APP_NAME)
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Allow Digital Signing?")
    box.setText(f"{who} wants to sign with your Digital ID.")
    box.setInformativeText(description + "\n\nA digital signature is your legal signature. Allow it only if you "
                                         "started this yourself.")
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    allow = box.addButton("Allow Once" if remember else "Allow", QMessageBox.ButtonRole.AcceptRole)
    always = box.addButton("Always Allow This Program", QMessageBox.ButtonRole.YesRole) if remember else None
    deny = box.addButton("Don't Allow", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(deny)
    box.setEscapeButton(deny)
    box.raise_()
    box.activateWindow()
    box.exec()
    clicked = box.clickedButton()
    if always is not None and clicked is always:
        return "always"
    return clicked is allow
