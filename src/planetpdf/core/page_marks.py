"""Headers, footers, page numbers, Bates numbering and watermarks, drawn into the
page content (so every viewer and printer shows them).

Each piece of text is drawn from a small source PDF in a standard PDF font
(Helvetica / Times: nothing embedded, so files stay small) and placed with
``appearance.flatten_source`` (upright on rotated pages). Undo: ``core.snapshots``.

**Update / Remove (Acrobat; user request 2026-10-02):** every piece PlanetPDF places
is a Form XObject of its own, tagged ``/PPDFMark /<kind>`` (``header``, ``bates``,
``watermark``, ``background``) with the settings used as JSON in ``/PPDFSpec``.
``remove_marks`` deletes the ``Do`` and the resource entry of the tagged ones;
``saved_spec`` reads the settings back so Update can show the dialog filled in. Marks
added before this (or by other software) are plain page content and can't be found.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pymupdf

from planetpdf.core.appearance import rotate_pdf
from planetpdf.core.contents import replace_contents
from planetpdf.core.document import PdfError, PdfSession

KINDS = ("header", "bates", "watermark", "background")

POSITIONS = ("top_left", "top_center", "top_right", "bottom_left", "bottom_center", "bottom_right")
FONTS = {"sans": ("helv", "hebo"), "serif": ("tiro", "tibo")}  # (regular, bold) standard fonts


@dataclass
class HeaderFooterSpec:
    texts: dict[str, str] = field(default_factory=dict)  # position -> template
    font: str = "sans"
    bold: bool = False
    size: float = 10.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    margin: float = 36.0  # points from the page edge (as displayed)
    first_number: int = 1  # {page} of the first page in the range
    bates_prefix: str = ""
    bates_suffix: str = ""
    bates_start: int = 1
    bates_digits: int = 6
    date_format: str = "%b %d, %Y"


TOKENS = {
    "{page}": "page number",
    "{pages}": "total pages",
    "{bates}": "Bates number",
    "{date}": "today's date",
    "{file}": "file name",
}


def expand(template: str, spec: HeaderFooterSpec, *, n: int, total: int, file_name: str, now: datetime) -> str:
    bates = f"{spec.bates_prefix}{spec.bates_start + n:0{max(1, spec.bates_digits)}d}{spec.bates_suffix}"
    return (
        template.replace("{page}", str(spec.first_number + n))
        .replace("{pages}", str(total + spec.first_number - 1))
        .replace("{bates}", bates)
        .replace("{date}", now.strftime(spec.date_format))
        .replace("{file}", file_name)
    )


def _text_source(text: str, fontname: str, size: float, color, opacity: float = 1.0) -> bytes:
    width = pymupdf.get_text_length(text, fontname=fontname, fontsize=size) + 2
    height = size * 1.3
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((1, size * 1.02), text, fontname=fontname, fontsize=size, color=color, fill_opacity=opacity)
    return doc.tobytes(garbage=3, deflate=True)


def _spec_json(spec) -> str:
    data = dataclasses.asdict(spec)
    for key, value in data.items():
        if isinstance(value, bytes):
            data[key] = {"b64": base64.b64encode(value).decode("ascii")}
    return json.dumps({"type": type(spec).__name__, "spec": data}, separators=(",", ":"))


def _spec_from_json(text: str):
    data = json.loads(text)
    cls = {c.__name__: c for c in (HeaderFooterSpec, WatermarkSpec, BackgroundSpec)}[data["type"]]
    values = data["spec"]
    for key, value in list(values.items()):
        if isinstance(value, dict) and "b64" in value:
            values[key] = base64.b64decode(value["b64"])
        elif isinstance(value, list):
            values[key] = tuple(value)
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in names})


def _place(session: PdfSession, index: int, rect, source: bytes, *, overlay: bool, kind: str, spec) -> None:
    """``appearance.flatten_source``, and tag the Form XObject it adds (see the module doc)."""
    doc = session.doc
    page = doc[index]
    before = {name for _x, name, invoker, _b in page.get_xobjects() if invoker == 0}
    src = pymupdf.open(stream=source, filetype="pdf")
    page.show_pdf_page(pymupdf.Rect(rect), src, 0, rotate=page.rotation, keep_proportion=False, overlay=overlay)
    page = doc[index]
    for xref, name, invoker, _bbox in page.get_xobjects():
        if invoker == 0 and name not in before:
            doc.xref_set_key(xref, "PPDFMark", f"/{kind}")
            text = spec if isinstance(spec, str) else _spec_json(spec)  # (a str: stored as it is)
            doc.xref_set_key(xref, "PPDFSpec", pymupdf.get_pdf_str(text))


def place_mark(session: PdfSession, index: int, rect, source: bytes, kind: str, spec: str) -> None:
    """Draw ``source`` on top of page ``index`` at ``rect`` (unrotated), tagged ``kind``
    with ``spec`` (any text: what ``remove_marks(where=…)`` matches on)."""
    _place(session, index, rect, source, overlay=True, kind=kind, spec=spec)


def _drawn_names(page: pymupdf.Page) -> set[bytes]:
    from planetpdf.core.redaction.ocg import tokenize

    tokens = list(tokenize(page.read_contents()))
    return {tokens[i - 1].value for i, tok in enumerate(tokens) if tok.kind == "op" and tok.value == b"Do" and i}


def _tagged(session: PdfSession, index: int, kind: str | None = None) -> list[tuple[int, str]]:
    """(xref, resource name) of PlanetPDF's marks the page draws (of ``kind``, or any).
    Only what the content stream invokes counts: a removed mark's resource entry stays
    (identical pages can share one resource dictionary after a full save, so dropping
    the entry for one page would break the others)."""
    doc = session.doc
    page = doc[index]
    found = []
    drawn = None
    for xref, name, invoker, _bbox in page.get_xobjects():
        if invoker != 0:
            continue
        tag_kind, tag = doc.xref_get_key(xref, "PPDFMark")
        if tag_kind == "name" and (kind is None or tag == f"/{kind}"):
            drawn = _drawn_names(page) if drawn is None else drawn
            if b"/" + name.encode("latin-1") in drawn:
                found.append((xref, name))
    return found


def pages_with(session: PdfSession, kind: str) -> list[int]:
    return [i for i in range(session.page_count) if _tagged(session, i, kind)]


def saved_spec(session: PdfSession, kind: str):
    """(spec, pages) of the ``kind`` marks PlanetPDF added (for Update), or None."""
    doc = session.doc
    pages = pages_with(session, kind)
    if not pages:
        return None
    xref, _name = _tagged(session, pages[0], kind)[0]
    tag_kind, value = doc.xref_get_key(xref, "PPDFSpec")
    if tag_kind != "string":
        return None
    try:
        return _spec_from_json(value), pages
    except (ValueError, KeyError, TypeError):
        return None


def remove_marks(session: PdfSession, kind: str, pages: Sequence[int] | None = None, *, where=None) -> int:
    """Take PlanetPDF's ``kind`` marks off ``pages`` (default: every page); ``where(spec
    text) -> bool``: only those. Returns how many were removed. Undo: ``core.snapshots``
    (contents and resources are restored)."""
    from planetpdf.core.redaction.ocg import tokenize

    doc = session.doc
    removed = 0
    for index in pages if pages is not None else range(session.page_count):
        marks = _tagged(session, index, kind)
        if where is not None:
            marks = [(x, n) for x, n in marks if where(doc.xref_get_key(x, "PPDFSpec")[1])]
        if not marks:
            continue
        page = doc[index]
        names = {b"/" + name.encode("latin-1") for _x, name in marks}
        data = page.read_contents()
        tokens = list(tokenize(data))
        out, pos = bytearray(), 0
        for i, tok in enumerate(tokens):
            if tok.kind == "op" and tok.value == b"Do" and i and tokens[i - 1].value in names:
                out += data[pos:tokens[i - 1].start] + b" "
                pos = tok.end
                removed += 1
        out += data[pos:]
        replace_contents(doc, index, bytes(out))  # (never in place: streams can be shared)
    if removed:
        session.mark_modified()
    return removed


def add_header_footer(session: PdfSession, pages: Sequence[int], spec: HeaderFooterSpec) -> int:
    """Draw the header/footer texts on ``pages``. Returns the number of texts placed.
    Tagged ``bates`` when a text has a {bates} number, else ``header``."""
    texts = {p: t for p, t in spec.texts.items() if t.strip() and p in POSITIONS}
    if not texts:
        raise PdfError("Enter some header or footer text.")
    kind = "bates" if any("{bates}" in t for t in texts.values()) else "header"
    fontname = FONTS.get(spec.font, FONTS["sans"])[1 if spec.bold else 0]
    now = datetime.now()
    file_name = session.path.name
    placed = 0
    for n, index in enumerate(pages):
        page = session.doc[index]
        shown = page.rect  # as displayed (rotation applied)
        for position, template in texts.items():
            text = expand(template, spec, n=n, total=len(pages), file_name=file_name, now=now)
            source = _text_source(text, fontname, spec.size, spec.color)
            src = pymupdf.open(stream=source, filetype="pdf")
            w, h = src[0].rect.width, src[0].rect.height
            vertical, horizontal = position.split("_")
            y = spec.margin if vertical == "top" else shown.height - spec.margin - h
            x = {"left": spec.margin, "center": (shown.width - w) / 2, "right": shown.width - spec.margin - w}[horizontal]
            box = pymupdf.Rect(x, y, x + w, y + h) * page.derotation_matrix
            _place(session, index, tuple(box.normalize()), source, overlay=True, kind=kind, spec=spec)
            page = session.doc[index]
            placed += 1
    session.mark_modified()
    return placed


@dataclass
class WatermarkSpec:
    text: str = "CONFIDENTIAL"
    image: bytes | None = None  # PNG/JPG instead of text
    font: str = "sans"
    color: tuple[float, float, float] = (0.8, 0.1, 0.1)
    opacity: float = 0.25
    rotation: float = 45.0  # degrees, counterclockwise positive
    scale: float = 0.6  # width as a share of the page width
    behind: bool = True  # under the page content (a watermark is an underlay; stamps go on top)


def _watermark_source(spec: WatermarkSpec) -> bytes:
    if spec.image:
        from planetpdf.core.stamps import with_opacity

        png = with_opacity(spec.image, spec.opacity)
        pix = pymupdf.Pixmap(png)
        doc = pymupdf.open()
        page = doc.new_page(width=pix.width, height=pix.height)
        page.insert_image(page.rect, stream=png)
        source = doc.tobytes(garbage=3, deflate=True)
    else:
        if not spec.text.strip():
            raise PdfError("Enter the watermark text.")
        source = _text_source(spec.text, FONTS.get(spec.font, FONTS["sans"])[1], 72, spec.color, spec.opacity)
    return rotate_pdf(source, spec.rotation)


def add_watermark(session: PdfSession, pages: Sequence[int], spec: WatermarkSpec) -> None:
    source = _watermark_source(spec)
    src = pymupdf.open(stream=source, filetype="pdf")
    sw, sh = src[0].rect.width, src[0].rect.height
    for index in pages:
        page = session.doc[index]
        shown = page.rect
        width = shown.width * spec.scale
        height = width * sh / sw
        if height > shown.height * 0.9:
            height = shown.height * 0.9
            width = height * sw / sh
        x, y = (shown.width - width) / 2, (shown.height - height) / 2
        box = (pymupdf.Rect(x, y, x + width, y + height) * page.derotation_matrix).normalize()
        _place(session, index, tuple(box), source, overlay=not spec.behind, kind="watermark", spec=spec)
    session.mark_modified()


@dataclass
class BackgroundSpec:
    """Edit PDF ▸ More ▸ Background (Acrobat): a color or a picture under the page."""

    color: tuple[float, float, float] | None = (1.0, 1.0, 0.85)  # None = use ``image``
    image: bytes | None = None  # PNG/JPG
    opacity: float = 1.0
    scale: float = 1.0  # image: share of the page (1 = fill it, keeping its proportions)


def _background_source(spec: BackgroundSpec, width: float, height: float) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    if spec.image:
        from planetpdf.core.stamps import with_opacity

        png = with_opacity(spec.image, spec.opacity)
        pix = pymupdf.Pixmap(png)
        k = min(width / pix.width, height / pix.height) * max(0.05, min(1.0, spec.scale))
        w, h = pix.width * k, pix.height * k
        page.insert_image(pymupdf.Rect((width - w) / 2, (height - h) / 2, (width + w) / 2, (height + h) / 2), stream=png)
    elif spec.color is not None:
        page.draw_rect(page.rect, color=None, fill=spec.color, fill_opacity=spec.opacity)
    else:
        raise PdfError("Choose a color or a picture for the background.")
    return doc.tobytes(garbage=3, deflate=True)


def add_background(session: PdfSession, pages: Sequence[int], spec: BackgroundSpec) -> None:
    """Under everything on ``pages``, the whole page (as displayed)."""
    for index in pages:
        page = session.doc[index]
        shown = page.rect
        source = _background_source(spec, shown.width, shown.height)
        box = (pymupdf.Rect(0, 0, shown.width, shown.height) * page.derotation_matrix).normalize()
        _place(session, index, tuple(box), source, overlay=False, kind="background", spec=spec)
    session.mark_modified()


def image_bytes(path) -> bytes:
    from PIL import Image

    with Image.open(path) as image:
        out = io.BytesIO()
        image.convert("RGBA").save(out, format="PNG")
        return out.getvalue()
