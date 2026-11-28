"""Combine PDFs and images into one new PDF (also: a new PDF from scans/images)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from planetpdf.core.document import PasswordRequired, PdfError
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jxr", ".jpx"}
PAPER = {"letter": pymupdf.paper_rect("letter"), "a4": pymupdf.paper_rect("a4")}


@dataclass
class CombineOptions:
    image_page: str = "image"  # "image" = page the size of the image; or "letter" / "a4" (fit, centered)
    passwords: dict[str, str] | None = None  # file name -> password for protected PDFs


def is_image(path: Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def _image_pages(path: Path, paper: str) -> pymupdf.Document:
    """An image file (multi-page TIFFs included) as PDF pages."""
    try:
        img = pymupdf.open(path)
    except (RuntimeError, ValueError, pymupdf.FileDataError) as exc:
        raise PdfError(f"{path.name} couldn't be read as an image: {exc}") from exc
    pdf = pymupdf.open("pdf", img.convert_to_pdf())
    img.close()
    if paper == "image":
        return pdf
    target = PAPER[paper]
    out = pymupdf.open()
    for page in pdf:
        new = out.new_page(width=target.width, height=target.height)
        margin = 18
        new.show_pdf_page(new.rect + (margin, margin, -margin, -margin), pdf, page.number, keep_proportion=True)
    pdf.close()
    return out


def combine_files(
    files: Sequence[Path],
    target: Path,
    options: CombineOptions | None = None,
    progress: Progress | None = None,
    protection=None,
) -> int:
    """Write ``files`` (in order) into ``target`` (encrypted as it is written with ``protection``).
    Returns the number of pages."""
    options = options or CombineOptions()
    files = [Path(f) for f in files]
    if not files:
        raise PdfError("Add at least one file.")
    out = pymupdf.open()
    try:
        for n, path in enumerate(files):
            if not report(progress, n, len(files)):
                raise PdfError("Canceled.")
            if is_image(path):
                src = _image_pages(path, options.image_page)
            else:
                try:
                    src = pymupdf.open(path)
                except (RuntimeError, ValueError, pymupdf.FileDataError) as exc:
                    raise PdfError(f"{path.name} couldn't be opened: {exc}") from exc
                if not src.is_pdf:
                    pdf = pymupdf.open("pdf", src.convert_to_pdf())  # XPS, EPUB, ...
                    src.close()
                    src = pdf
                if src.needs_pass:
                    password = (options.passwords or {}).get(path.name)
                    if password is None or not src.authenticate(password):
                        src.close()
                        raise PasswordRequired(f"{path.name} is password protected.")
            first = out.page_count
            out.insert_pdf(src)
            src.close()
            from planetpdf.core.markup import new_id, renew_ids

            renew_ids(out, range(first, out.page_count), new_id())  # (the same file twice: no shared ids)
        if out.page_count == 0:
            raise PdfError("The files contain no pages.")
        report(progress, 0, 0, f"Saving the combined PDF ({out.page_count} pages from {len(files)} files)…")
        tmp = temp_path_beside(Path(target))
        try:
            from planetpdf.core.font_embedding import embed_quietly

            embed_quietly(out)  # (fonts it only names, from this computer)
            if protection is not None:
                from planetpdf.core.document import save_pdf

                save_pdf(out, Path(target), protection)
            else:
                out.save(str(tmp), garbage=3, deflate=True)
                replace_with_retry(tmp, Path(target))
        finally:
            remove_quietly(tmp)
        if progress is not None:
            progress(len(files), len(files))
        return out.page_count
    finally:
        out.close()
