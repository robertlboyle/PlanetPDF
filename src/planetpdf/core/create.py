"""New PDFs from other things: a file of another type, a picture or text (e.g. from the
clipboard), or a blank page. Used by File ▸ Create and File ▸ Insert Pages."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError

LETTER = pymupdf.paper_rect("letter")
MARGIN = 54.0  # 0.75 in

# What MuPDF can turn into PDF pages (Office documents would need a converter we don't have).
CONVERTIBLE = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".jxr", ".jpx", ".jp2", ".pnm", ".pbm", ".pgm", ".ppm",
    ".svg", ".xps", ".oxps", ".epub", ".fb2", ".cbz", ".mobi", ".txt",
}
FILE_FILTER = ("Files that can become PDF (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.svg *.xps *.oxps "
               "*.epub *.fb2 *.cbz *.mobi *.txt *.doc *.docx *.rtf *.xls *.xlsx *.ppt *.pptx)")


def pdf_from_file(path: Path) -> bytes:
    """Convert a picture (every frame of a multi-page TIFF), text file, e-book, XPS or
    SVG into a PDF (as bytes)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return path.read_bytes()
    if suffix not in CONVERTIBLE:
        raise PdfError(f"{path.name}: PlanetPDF can't convert {suffix or 'this kind of'} files to PDF.")
    if suffix == ".txt":
        return pdf_from_text(path.read_text(encoding="utf-8", errors="replace"))
    try:
        with pymupdf.open(path) as src:
            return src.tobytes() if src.is_pdf else src.convert_to_pdf()
    except (RuntimeError, ValueError) as exc:
        raise PdfError(f"{path.name} couldn't be converted: {exc}") from exc


def pdf_from_image(data: bytes) -> bytes:
    """One page the size of the picture (at 96 dpi, shrunk to fit Letter if bigger)."""
    try:
        pix = pymupdf.Pixmap(data)
    except (RuntimeError, ValueError) as exc:
        raise PdfError(f"That picture couldn't be read: {exc}") from exc
    width, height = pix.width * 72 / 96, pix.height * 72 / 96
    scale = min(1.0, LETTER.width / width, LETTER.height / height)
    out = pymupdf.open()
    page = out.new_page(width=width * scale, height=height * scale)
    page.insert_image(page.rect, stream=data)
    return out.tobytes(garbage=3, deflate=True)


TEXT_SIZE = 11.0
TEXT_LEADING = 1.3


def _wrap(line: str, width: float, fontname: str, font: pymupdf.Font | None) -> list[str]:
    def length(s: str) -> float:
        return font.text_length(s, TEXT_SIZE) if font else pymupdf.get_text_length(s, fontname=fontname, fontsize=TEXT_SIZE)

    out, current = [], ""
    for word in line.split(" "):
        trial = f"{current} {word}" if current else word
        if current and length(trial) > width:
            out.append(current)
            current = word
        else:
            current = trial
        while length(current) > width and len(current) > 1:  # one very long word
            cut = len(current)
            while cut > 1 and length(current[:cut]) > width:
                cut -= 1
            out.append(current[:cut])
            current = current[cut:]
    out.append(current)
    return out


def pdf_from_text(text: str) -> bytes:
    """Plain text on Letter pages, wrapped, as many pages as it needs. (Laid out by hand:
    MuPDF's HTML engine joins "fi" into one ligature character, which breaks searching.)"""
    latin = all(ord(c) < 256 for c in text)
    font = None
    fontname = "helv"
    if not latin:
        from planetpdf.core.resources import font_path

        font, fontname = pymupdf.Font(fontfile=str(font_path("sans"))), "PPDFNoto"
    width = LETTER.width - 2 * MARGIN
    lines = [w for line in (text.replace("\t", "    ").splitlines() or [""]) for w in _wrap(line, width, fontname, font)]
    per_page = int((LETTER.height - 2 * MARGIN) // (TEXT_SIZE * TEXT_LEADING))
    out = pymupdf.open()
    for start in range(0, max(len(lines), 1), per_page):
        page = out.new_page(width=LETTER.width, height=LETTER.height)
        if font is not None:
            from planetpdf.core.resources import font_path

            page.insert_font(fontname=fontname, fontfile=str(font_path("sans")))
        for n, line in enumerate(lines[start : start + per_page]):
            if line:
                y = MARGIN + TEXT_SIZE + n * TEXT_SIZE * TEXT_LEADING
                page.insert_text((MARGIN, y), line, fontsize=TEXT_SIZE, fontname=fontname)
    if font is not None:
        out.subset_fonts()
    return out.tobytes(garbage=3, deflate=True)


def blank_pdf(width: float = LETTER.width, height: float = LETTER.height) -> bytes:
    out = pymupdf.open()
    out.new_page(width=width, height=height)
    return out.tobytes()
