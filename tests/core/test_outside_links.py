"""Links out of a document (security review 2026-11-21): web pages and email only."""

from __future__ import annotations

import pytest

from planetpdf.core import links as L


@pytest.mark.parametrize("uri, kind, where", [
    ("https://www.example.com/a?b=c", "web", "www.example.com"),
    ("HTTP://Example.com", "web", "example.com"),
    ("mailto:someone@example.com?subject=Hi", "email", "someone@example.com"),
])
def test_web_and_email_links_are_offered(uri, kind, where):
    link = L.outside_link(uri)
    assert (link.kind, link.where, link.address) == (kind, where, uri.strip())


@pytest.mark.parametrize("uri", [
    "file://attacker-host/share/x.pdf",  # (Windows signs in to the server: the password hash leaks)
    "\\\\attacker\\share\\x",
    "file:///C:/Windows/System32/calc.exe",
    "ms-msdt:/id PCWDiagnostic",
    "search-ms:query=x&crumb=location:\\\\attacker\\share",
    "javascript:alert(1)",
    "https://bank.example.com@evil.example.net/",  # (the real site hidden behind a name)
    "https://exa mple.com",
    "https:///nohost",
    "http://example.com\\@evil.net",
    "mailto:",
    "",
    "www.example.com",  # (no scheme: not ours to guess in someone else's PDF)
])
def test_everything_else_is_refused(uri):
    with pytest.raises(L.LinkRefused):
        L.outside_link(uri)


def test_the_window_asks_with_the_site_and_never_opens_a_refused_link(monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from planetpdf.ui import outside_links as O

    opened, asked, said = [], [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)

    class Window:
        def ask(self, title, text):
            asked.append(text)
            return True

        def show_message(self, title, text):
            said.append(text)

    assert not O.open_outside_link(Window(), "file://attacker/share")
    assert opened == [] and asked == [] and "web (http, https) and email" in said[0]
    assert O.open_outside_link(Window(), "https://example.com/page")
    assert "Site: example.com" in asked[0] and opened == ["https://example.com/page"]
