"""Opening a link that leads out of the document: a link on a page, a form button's address.

Checked first (``core.links.outside_link``: web pages and e-mail only, security review
2026-11-21), then the user is asked, with the site or the e-mail address shown plainly.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from planetpdf.core import links as L


def question(link: L.OutsideLink) -> tuple[str, str]:
    """(title, text) of the question for this link."""
    if link.kind == "email":
        return "Start an Email?", (f"Start an email to {link.where} in your email program?\n\n"
                                   f"Address: {link.address}")
    return "Open This Web Page?", (f"Open this page in your web browser?\n\nSite: {link.where}\n"
                                   f"Address: {link.address}\n\nOpen it only if you trust where the document "
                                   "came from.")


def open_outside_link(window, uri: str) -> bool:
    """Ask, then open. True when it was handed to the browser / mail program."""
    try:
        link = L.outside_link(uri)
    except L.LinkRefused as exc:
        window.show_message("Link Not Opened", str(exc))
        return False
    title, text = question(link)
    if not window.ask(title, text):
        return False
    url = QUrl(link.address, QUrl.ParsingMode.StrictMode)
    if not url.isValid() or url.scheme().lower() not in L.SAFE_SCHEMES:  # (as Qt reads it, too)
        window.show_message("Link Not Opened", "This link's address can't be read, so PlanetPDF didn't open it.")
        return False
    return QDesktopServices.openUrl(url)
