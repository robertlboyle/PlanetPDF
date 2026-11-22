"""Document properties: editable metadata and read-only facts about the file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from planetpdf.core.document import PdfSession

EDITABLE = ("title", "author", "subject", "keywords")


@dataclass(frozen=True)
class Facts:
    file: str
    file_size: int
    pages: int
    page_size: str
    pdf_version: str
    producer: str
    creator: str
    created: str
    modified: str
    protected: bool
    signed: bool
    has_forms: bool
    fonts: list[str]


def _pdf_date(value: str) -> str:
    match = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value or "")
    if not match:
        return value or ""
    parts = [int(p) if p else 1 for p in match.groups()]
    parts[3:] = [int(p) if p else 0 for p in match.groups()[3:]]
    try:
        return datetime(*parts).strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return value


def _size_text(width: float, height: float) -> str:
    inches = f'{width / 72:.2f} × {height / 72:.2f} in'
    names = {(612, 792): "Letter", (792, 612): "Letter (landscape)", (595, 842): "A4", (842, 595): "A4 (landscape)",
             (612, 1008): "Legal"}
    name = names.get((round(width), round(height)))
    return f"{inches} ({name})" if name else inches


def get_metadata(session: PdfSession) -> dict[str, str]:
    meta = session.doc.metadata or {}
    return {k: meta.get(k, "") or "" for k in EDITABLE}


def set_metadata(session: PdfSession, values: dict[str, str]) -> None:
    meta = dict(session.doc.metadata or {})
    for key in EDITABLE:
        if key in values:
            meta[key] = values[key]
    meta["modDate"] = datetime.now().strftime("D:%Y%m%d%H%M%S")
    session.doc.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})
    session.mark_modified()


def facts(session: PdfSession) -> Facts:
    doc = session.doc
    meta = doc.metadata or {}
    first = doc[0].rect if doc.page_count else None
    fonts = set()
    for index in range(min(doc.page_count, 200)):
        for font in doc.get_page_fonts(index):
            name = (font[3] or font[4] or "").split("+")[-1]
            if name:
                fonts.add(name)
    try:
        size = session.path.stat().st_size
    except OSError:
        size = 0
    return Facts(
        file=str(session.path),
        file_size=size,
        pages=doc.page_count,
        page_size=_size_text(first.width, first.height) if first else "",
        pdf_version=meta.get("format", ""),
        producer=meta.get("producer", ""),
        creator=meta.get("creator", ""),
        created=_pdf_date(meta.get("creationDate", "")),
        modified=_pdf_date(meta.get("modDate", "")),
        protected=session.is_protected,
        signed=session.has_digital_signatures,
        has_forms=bool(doc.is_form_pdf),
        fonts=sorted(fonts),
    )
