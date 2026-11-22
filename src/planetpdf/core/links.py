"""Links on pages, and the bookmarks (outline)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pymupdf

from planetpdf.core.document import PdfSession


class LinkKind(Enum):
    PAGE = "page"  # jump within the document
    WEB = "web"  # URI (http, mailto, ...)
    NAMED = "named"  # NextPage, PrevPage, FirstPage, LastPage
    OTHER_FILE = "other_file"  # GoToR / Launch: not followed (security)


@dataclass(frozen=True)
class Link:
    page: int
    rect: tuple[float, float, float, float]  # unrotated page coordinates
    kind: LinkKind
    target_page: int = -1
    target_y: float = 0.0  # unrotated y of the destination point (0 = top)
    uri: str = ""
    name: str = ""

    @property
    def description(self) -> str:
        if self.kind is LinkKind.PAGE:
            return f"Go to page {self.target_page + 1}"
        if self.kind is LinkKind.WEB:
            return self.uri
        if self.kind is LinkKind.NAMED:
            return self.name
        return "Link to another file (not followed)"


def page_links(session: PdfSession, index: int) -> list[Link]:
    page = session.doc[index]
    out = []
    for link in page.get_links():
        rect = tuple(pymupdf.Rect(link["from"]))
        kind = link.get("kind")
        if kind == pymupdf.LINK_GOTO and link.get("page", -1) >= 0:
            to = link.get("to")
            out.append(Link(index, rect, LinkKind.PAGE, link["page"], float(to.y) if to is not None else 0.0))
        elif kind == pymupdf.LINK_URI and link.get("uri"):
            out.append(Link(index, rect, LinkKind.WEB, uri=link["uri"]))
        elif kind == pymupdf.LINK_NAMED:
            name = str(link.get("name") or link.get("nameddest") or "")
            out.append(Link(index, rect, LinkKind.NAMED, name=name))
        elif kind in (pymupdf.LINK_GOTOR, pymupdf.LINK_LAUNCH):
            out.append(Link(index, rect, LinkKind.OTHER_FILE, uri=str(link.get("file", ""))))
    return out


# --- following a link out of the document (security review 2026-11-21) ------------------------------

# A PDF from someone else can hold any address: file://server/share or \\server\share make Windows
# sign in to that server (leaking the user's password hash), and other schemes start programs
# (ms-msdt:, search-ms:…). Only web pages and email are offered.
SAFE_SCHEMES = ("http", "https", "mailto")


class LinkRefused(ValueError):
    """Why a link isn't opened (a sentence for the user)."""


@dataclass(frozen=True)
class OutsideLink:
    address: str  # (as checked: what is handed to the browser / mail program)
    kind: str  # "web" or "email"
    where: str  # the site (host) or the email address, shown to the user


def outside_link(uri: str) -> OutsideLink:
    """The link checked before anything opens it, else ``LinkRefused``."""
    from urllib.parse import unquote, urlsplit

    address = (uri or "").strip()
    if not address:
        raise LinkRefused("This link has no address.")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in address) or "\\" in address:
        raise LinkRefused("This link's address has characters a web address can't have, so PlanetPDF didn't open it.")
    try:
        parts = urlsplit(address)
    except ValueError as exc:
        raise LinkRefused(f"This link's address can't be read ({exc}).") from exc
    scheme = parts.scheme.lower()
    if scheme not in SAFE_SCHEMES:
        what = f"“{scheme}:”" if scheme else "no web address"
        raise LinkRefused(f"This link points to {what}, not a web page or an email address. For your safety, "
                          "PlanetPDF opens only web (http, https) and email links.")
    if scheme == "mailto":
        to = unquote(parts.path).split(",")[0].strip()
        if "@" not in to:
            raise LinkRefused("This email link has no address.")
        return OutsideLink(address, "email", to)
    if parts.username is not None or parts.password is not None:
        raise LinkRefused("This link hides its real site behind a name (“name@site”), a common trick, so PlanetPDF "
                          "didn't open it.")
    host = parts.hostname or ""
    if not host:
        raise LinkRefused("This link has no web site in it.")
    return OutsideLink(address, "web", host)


# --- editing links (Edit PDF ▸ Link, user request 2026-10-02) ----------------------------------


def link_dict(rect, *, uri: str = "", target_page: int = -1, target_y: float = 0.0) -> dict:
    """A PyMuPDF link for ``rect`` (unrotated): a web address, or a page of this document."""
    if uri:
        return {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(rect), "uri": uri}
    return {"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(rect), "page": target_page,
            "to": pymupdf.Point(0, target_y)}


def normalize_uri(text: str) -> str:
    """What the user typed as an address: www.x.com -> https://www.x.com; mail -> mailto:."""
    text = text.strip()
    if not text:
        return ""
    if "://" in text or text.lower().startswith(("mailto:", "tel:")):
        return text
    if "@" in text and " " not in text and "/" not in text:
        return "mailto:" + text
    return "https://" + text


def link_at(session: PdfSession, index: int, point) -> dict | None:
    """The link whose area holds ``point`` (unrotated), smallest first."""
    p = pymupdf.Point(point)
    hits = [link for link in session.doc[index].get_links() if pymupdf.Rect(link["from"]).contains(p)]
    return min(hits, key=lambda l: pymupdf.Rect(l["from"]).get_area()) if hits else None


def _plain(link: dict) -> dict:
    return {k: v for k, v in link.items() if k not in ("xref", "id", "zoom")}


def _sync(session: PdfSession, pages) -> None:
    from planetpdf.core.drawings import _reload

    _reload(session.doc, set(pages))
    session.mark_modified()


def insert_links(session: PdfSession, items: list[tuple[int, dict]]) -> None:
    for index, link in items:
        session.doc[index].insert_link(_plain(link))
    _sync(session, {i for i, _l in items})


def delete_links_at(session: PdfSession, items: list[tuple[int, dict]]) -> None:
    """Delete the links matching these (page, link) by area and target."""
    for index, link in items:
        page = session.doc[index]
        area = pymupdf.Rect(link["from"])
        for current in page.get_links():
            same = pymupdf.Rect(current["from"])
            if abs(same.x0 - area.x0) + abs(same.y0 - area.y0) + abs(same.x1 - area.x1) + abs(same.y1 - area.y1) < 1 \
                    and current.get("kind") == link.get("kind"):
                page.delete_link(current)
                break
    _sync(session, {i for i, _l in items})


def links_on(session: PdfSession, pages) -> list[tuple[int, dict]]:
    """Every link on ``pages`` as (page, link) (to remove them, or put them back)."""
    return [(index, _plain(link)) for index in pages for link in session.doc[index].get_links()]


def resolve_named(name: str, current: int, page_count: int) -> int | None:
    return {
        "NextPage": min(current + 1, page_count - 1),
        "PrevPage": max(current - 1, 0),
        "FirstPage": 0,
        "LastPage": page_count - 1,
    }.get(name)


# --- bookmarks ------------------------------------------------------------------------------


DEST_KEYS = ("A", "Dest")  # where an outline item's destination lives
LOOK_KEYS = ("C", "F")  # its color and bold / italic


@dataclass(frozen=True)
class Bookmark:
    level: int  # 1 = top level
    title: str
    page: int  # 0-based; -1 if it points nowhere useful
    y: float = 0.0  # top-down, unrotated page coordinates (for jumping there)
    # the outline item's own destination and look as stored in the file ((key, PDF source)
    # pairs): written back unchanged, so an edit to the list never re-records the other
    # bookmarks (user bug 2026-10-04: named destinations moved, /FitH became a point). A
    # new destination (Set Destination) is a Bookmark without it.
    original: tuple = field(default=(), compare=False)


def _raw(doc: pymupdf.Document, xref: int, keys) -> tuple:
    out = []
    for key in keys:
        kind, value = doc.xref_get_key(xref, key)
        if kind == "string":  # (PyMuPDF returns a string's text: quote it again as PDF source)
            value = pymupdf.get_pdf_str(value)
        if kind != "null":
            out.append((key, value))
    return tuple(out)


def get_bookmarks(session: PdfSession) -> list[Bookmark]:
    doc = session.doc
    out = []
    for entry in doc.get_toc(simple=False):
        level, title, page = entry[0], entry[1], entry[2]
        dest = entry[3] if len(entry) > 3 else {}
        to = dest.get("to") if isinstance(dest, dict) else None
        y = float(to.y) if to is not None else 0.0
        if isinstance(dest, dict) and dest.get("nameddest") and to is not None and 1 <= page <= doc.page_count:
            # (PyMuPDF 1.28 gives a named destination's point in PDF bottom-up coordinates)
            y = (pymupdf.Point(to.x, to.y) * doc[page - 1].transformation_matrix).y
        xref = dest.get("xref", 0) if isinstance(dest, dict) else 0
        original = _raw(doc, xref, DEST_KEYS + LOOK_KEYS) if xref else ()
        out.append(Bookmark(level, title, page - 1, y, original))
    return out


def set_bookmarks(session: PdfSession, bookmarks: list[Bookmark]) -> None:
    toc = []
    for b in bookmarks:
        if b.page < 0:
            toc.append([b.level, b.title, -1])
            continue
        toc.append([b.level, b.title, b.page + 1, {"kind": pymupdf.LINK_GOTO, "to": pymupdf.Point(0, b.y), "page": b.page}])
    # PyMuPDF requires levels to increase by at most 1; clamp to keep any edit valid.
    fixed, previous = [], 0
    for entry in toc:
        entry[0] = max(1, min(entry[0], previous + 1))
        previous = entry[0]
        fixed.append(entry)
    doc = session.doc
    doc.set_toc(fixed)
    # every bookmark that was already there keeps its own destination and look exactly
    for b, entry in zip(bookmarks, doc.get_toc(simple=False)):
        xref = entry[3].get("xref", 0) if len(entry) > 3 and isinstance(entry[3], dict) else 0
        if not xref or not b.original:
            continue
        keys = dict(b.original)
        if any(k in keys for k in DEST_KEYS):
            for key in DEST_KEYS:
                doc.xref_set_key(xref, key, "null")
        for key, value in b.original:
            doc.xref_set_key(xref, key, value)
    session.mark_modified()
