"""OCR with Tesseract (MuPDF's built-in engine; only language data files are needed).

OCR adds an *invisible* word-level text layer so scanned pages become searchable,
selectable and redactable. The page's images and graphics are not changed. The text
lives in an ON optional-content layer named ``OCR_LAYER`` so it can be recognized
(pages that already have it are skipped) and removed again (undo).

Recognition (slow) is separate from insertion (fast): ``recognize_pages`` works on
its own document copy, so it can run in a background thread; ``insert_ocr_text``
then writes the words into the real document on the main thread.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError, PdfSession

OCR_LAYER = "PlanetPDF OCR text"
DEFAULT_DPI = 300
_FONT = "helv"  # Latin text; the layer is invisible, only the character positions matter


class OcrUnavailable(PdfError):
    """Tesseract language data wasn't found."""


@dataclass(frozen=True)
class OcrWord:
    rect: tuple[float, float, float, float]  # unrotated page coordinates
    text: str


# --- finding Tesseract data -------------------------------------------------------------


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        dirs.append(Path(env))
    # Bundled with the packaged app (PyInstaller puts data next to the executable).
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    dirs.append(base / "tessdata")
    exe = shutil.which("tesseract")
    if exe:
        dirs.append(Path(exe).resolve().parent / "tessdata")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            dirs.append(Path(root) / "Tesseract-OCR" / "tessdata")
            dirs.append(Path(root) / "Programs" / "Tesseract-OCR" / "tessdata")
    for prefix in ("/opt/homebrew", "/usr/local", "/usr"):
        dirs.append(Path(prefix) / "share" / "tessdata")
        dirs.append(Path(prefix) / "share" / "tesseract-ocr" / "5" / "tessdata")
        dirs.append(Path(prefix) / "share" / "tesseract-ocr" / "4.00" / "tessdata")
    return dirs


def find_tessdata() -> Path | None:
    for directory in _candidate_dirs():
        if directory.is_dir() and any(directory.glob("*.traineddata")):
            return directory
    return None


def available_languages(tessdata: Path | None = None) -> list[str]:
    tessdata = tessdata or find_tessdata()
    if tessdata is None:
        return []
    return sorted(p.stem for p in tessdata.glob("*.traineddata") if p.stem != "osd")


def require_tessdata() -> Path:
    tessdata = find_tessdata()
    if tessdata is None:
        raise OcrUnavailable(
            "Text recognition (OCR) needs Tesseract language data, which wasn't found. "
            "Install Tesseract OCR, or set TESSDATA_PREFIX to its tessdata folder."
        )
    return tessdata


# --- which pages need OCR ------------------------------------------------------------------


def _ocr_layer_xref(doc: pymupdf.Document) -> int | None:
    for xref, info in doc.get_ocgs().items():
        if info.get("name") == OCR_LAYER:
            return xref
    return None


_PROPERTY_NAME = "PPDFOCR"  # resource name our OCR text uses for its layer


def page_has_ocr(doc: pymupdf.Document, index: int) -> bool:
    """True if PlanetPDF has already added OCR text to this page."""
    page = doc[index]
    kind, _ = doc.xref_get_key(page.xref, f"Resources/Properties/{_PROPERTY_NAME}")
    if kind == "null":
        return False
    return b"/" + _PROPERTY_NAME.encode() in page.read_contents()


def page_needs_ocr(doc: pymupdf.Document, index: int) -> bool:
    """A page with images but (almost) no text, or with words drawn as outlines (user
    2026-10-23: "searchable only"), and no OCR layer yet."""
    page = doc[index]
    if page_has_ocr(doc, index):
        return False
    if bool(page.get_images()) and len(page.get_text().strip()) < 20:
        return True
    return page_has_outlined_text(page)


OUTLINE_MIN_GLYPHS = 40  # (letter-sized filled shapes, or word paths counted by their curves)
OUTLINE_MIN_ROWS = 3  # (lined up in at least this many rows of text)


def page_has_outlined_text(page: pymupdf.Page) -> bool:
    """Words drawn as filled shapes instead of text (a PDF made with "convert text to
    outlines", logos, some design programs): no letters to search or copy until recognized.
    Many small filled shapes, letter-sized or word paths of many curves, lined up in rows
    like text, and not on top of real text."""
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    rows: dict[int, int] = {}
    glyphs = 0
    for path in page.get_cdrawings():
        if path.get("fill") is None:
            continue
        r = pymupdf.Rect(path["rect"])
        if not 2 <= r.height <= 40:
            continue
        curves = len(path.get("items", ()))
        weight = 1 if r.width <= max(r.height * 2, 8) else curves // 8  # (a letter, or a whole word's path)
        if weight <= 0 or any(r.intersects(w) for w in words):
            continue
        glyphs += weight
        row = round(r.y1 / 2)  # (letters of a line share their baseline, within 2 points)
        rows[row] = rows.get(row, 0) + weight
    return glyphs >= OUTLINE_MIN_GLYPHS and sum(1 for n in rows.values() if n >= 6) >= OUTLINE_MIN_ROWS


# --- recognition (thread-safe: own document) --------------------------------------------------


def recognize_pages(
    pdf_bytes: bytes,
    pages: Iterable[int],
    *,
    language: str = "eng",
    dpi: int = DEFAULT_DPI,
    tessdata: Path | None = None,
    progress: Callable[[int, int], bool] | None = None,
) -> dict[int, list[OcrWord]]:
    """OCR ``pages`` of the PDF in ``pdf_bytes``. ``progress(done, total)`` returns
    False to cancel (the pages finished so far are returned)."""
    tessdata = tessdata or require_tessdata()
    pages = list(pages)
    results: dict[int, list[OcrWord]] = {}
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for n, index in enumerate(pages):
            if progress is not None and not progress(n, len(pages)):
                break
            results[index] = _recognize_page(doc[index], language, dpi, tessdata)
        if progress is not None:
            progress(len(results), len(pages))
    finally:
        doc.close()
    return results


def _recognize_page(page: pymupdf.Page, language: str, dpi: int, tessdata: Path) -> list[OcrWord]:
    """OCR one page as displayed; word boxes returned in unrotated page coordinates.

    MuPDF's ``get_textpage_ocr`` finds nothing on /Rotate 270 pages (1.28.2), so render
    the page upright, OCR that image on an unrotated scratch page, and map back.
    """
    pix = page.get_pixmap(dpi=dpi, annots=False)
    scratch = pymupdf.open()
    try:
        upright = scratch.new_page(width=page.rect.width, height=page.rect.height)
        upright.insert_image(upright.rect, pixmap=pix)
        try:
            textpage = upright.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=str(tessdata))
        except RuntimeError as exc:
            raise PdfError(f"Text recognition failed on page {page.number + 1}: {exc}") from exc
        words = []
        for w in upright.get_text("words", textpage=textpage):
            if not w[4].strip():
                continue
            rect = (pymupdf.Rect(w[:4]) * page.derotation_matrix).normalize()
            words.append(OcrWord(tuple(rect), w[4]))
        return words
    finally:
        scratch.close()


# --- insertion / removal (main thread) ------------------------------------------------------------


def _ensure_layer(doc: pymupdf.Document) -> int:
    xref = _ocr_layer_xref(doc)
    if xref is None:
        xref = doc.add_ocg(OCR_LAYER, on=True)
    return xref


def insert_ocr_text(session: PdfSession, page_index: int, words: list[OcrWord]) -> int:
    """Write ``words`` as invisible, correctly positioned text. Returns words written."""
    written = insert_ocr_text_doc(session.doc, page_index, words)
    if written:
        session.mark_modified()
    return written


def ocr_page_text(page: pymupdf.Page, *, language: str = "eng", dpi: int = DEFAULT_DPI, tessdata: Path | None = None) -> str:
    """Plain text of a page by OCR (reading order), without changing the page."""
    tessdata = tessdata or require_tessdata()
    pix = page.get_pixmap(dpi=dpi, annots=False)
    scratch = pymupdf.open()
    try:
        upright = scratch.new_page(width=page.rect.width, height=page.rect.height)
        upright.insert_image(upright.rect, pixmap=pix)
        textpage = upright.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=str(tessdata))
        return upright.get_text("text", textpage=textpage, sort=True)
    finally:
        scratch.close()


def _visible_words(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Boxes of the page's own visible text (not an invisible OCR layer, not comments)."""
    from planetpdf.core.content_edit import page_text

    out = []
    for block in page_text(page, "dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip() and span.get("alpha", 255) != 0:
                    out.append(pymupdf.Rect(span["bbox"]))
    return out


def insert_ocr_text_doc(doc: pymupdf.Document, page_index: int, words: list[OcrWord]) -> int:
    """Document-level version of ``insert_ocr_text`` (used on export copies). A word on the
    page's own visible text is left out: it's searchable already, and a page partly in
    outlines (user 2026-10-23) would otherwise find it twice."""
    if not words:
        return 0
    visible = _visible_words(doc[page_index])
    words = [w for w in words if not any((pymupdf.Rect(w.rect) & r).get_area() > 0.5 * pymupdf.Rect(w.rect).get_area()
                                         for r in visible)]
    if not words:
        return 0
    layer = _ensure_layer(doc)
    page = doc[page_index]
    shape = page.new_shape()
    written = 0
    for word in words:
        shown = pymupdf.Rect(word.rect) * page.rotation_matrix  # as displayed (upright)
        if shown.is_empty:
            continue
        unit_width = pymupdf.get_text_length(word.text, fontname=_FONT, fontsize=1)
        if unit_width <= 0:
            continue
        # Fit the width exactly (search and redaction boxes follow character widths).
        size = max(1.0, min(shown.height * 2.0, shown.width / unit_width))
        start = pymupdf.Point(shown.x0, shown.y1 - 0.2 * shown.height) * page.derotation_matrix
        try:
            shape.insert_text(start, word.text, fontsize=size, fontname=_FONT, rotate=page.rotation, render_mode=3)
        except (ValueError, RuntimeError):
            continue  # characters the font can't encode
        written += 1
    shape.commit(overlay=True)
    # Put the new content stream in the OCR layer: wrap it in /OC ... BDC/EMC.
    _wrap_last_stream_in_layer(doc, page, layer)
    return written


def _wrap_last_stream_in_layer(doc: pymupdf.Document, page: pymupdf.Page, layer: int) -> None:
    contents = page.get_contents()
    last = contents[-1]
    name = _PROPERTY_NAME
    _set_resource_entry(doc, page.xref, "Properties", name, f"{layer} 0 R")
    data = doc.xref_stream(last) or b""
    doc.update_stream(last, b"/OC /" + name.encode() + b" BDC\n" + data + b"\nEMC\n")


def _ref(value: str) -> int:
    return int(value.split()[0])


def _set_resource_entry(doc: pymupdf.Document, page_xref: int, category: str, name: str, value: str) -> None:
    """Set /Resources/<category>/<name> = value, following indirect objects.

    ``xref_set_key`` can't write through a path containing indirect references, which
    is how most PDFs store /Resources, so resolve each level explicitly.
    """
    owner, key = page_xref, "Resources"
    kind, val = doc.xref_get_key(owner, key)
    if kind == "xref":
        owner, prefix = _ref(val), ""
    elif kind == "null":
        doc.xref_set_key(owner, key, "<<>>")
        prefix = "Resources/"
    else:
        prefix = "Resources/"
    kind, val = doc.xref_get_key(owner, f"{prefix}{category}")
    if kind == "xref":
        doc.xref_set_key(_ref(val), name, value)
    elif kind == "null":
        doc.xref_set_key(owner, f"{prefix}{category}", f"<</{name} {value}>>")
    else:
        doc.xref_set_key(owner, f"{prefix}{category}/{name}", value)


def remove_ocr_text(session: PdfSession, pages: Iterable[int]) -> int:
    """Delete the OCR layer's text from ``pages`` (undo of ``insert_ocr_text``)."""
    from planetpdf.core.redaction.ocg import remove_layer_content

    layer = _ocr_layer_xref(session.doc)
    if layer is None:
        return 0
    removed = remove_layer_content(session.doc, {layer}, pages)
    if removed:
        session.mark_modified()
    return removed
