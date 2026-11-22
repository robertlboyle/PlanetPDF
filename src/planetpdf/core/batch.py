"""Work on many files at once (backlog Phase C, built 2026-10-02):

* Create ▸ Create Multiple PDF Files: each file becomes a PDF of its own (pictures,
  XPS / EPUB / text / SVG through MuPDF, Word / Excel / PowerPoint through Microsoft
  Office when it's installed).
* Scan & OCR ▸ Recognize Text ▸ In Multiple Files: an invisible text layer on every
  scanned page of each file (written as a new file, or over the original).
* Create ▸ PDF Portfolio: several files kept together, unchanged, inside one PDF (a cover
  page lists them; viewers that know portfolios show them as a list).

Real progress throughout: "Converting file 2 of 5", "File 2 of 5 (report.pdf): page 3 of 12".
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf

from planetpdf.core import office
from planetpdf.core.combine import _image_pages, is_image
from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

MUPDF_SUFFIXES = {".xps", ".oxps", ".epub", ".cbz", ".fb2", ".mobi", ".svg", ".txt"}


class BatchCanceled(PdfError):
    pass


@dataclass(frozen=True)
class BatchResult:
    source: Path
    output: Path | None  # None: it failed (``error`` says why)
    error: str = ""


def can_convert(path: Path) -> bool:
    suffix = Path(path).suffix.lower()
    return (suffix == ".pdf" or is_image(path) or suffix in MUPDF_SUFFIXES
            or (office.app_for(path) is not None and office.available(office.app_for(path))))


def add_contents_bookmarks(path: Path) -> int:
    """A converted Word file with a table of contents but no bookmarks (Word makes them only
    from heading styles) gets one per contents entry (user 2026-10-04). Returns how many."""
    from planetpdf.core import bookmark_detect

    with pymupdf.open(path) as doc:
        if doc.get_toc() or doc.needs_pass:
            return 0
        sections = bookmark_detect.from_toc(doc)
        if not sections:
            return 0
        doc.set_toc([[s.level, s.title, s.page + 1, {"kind": pymupdf.LINK_GOTO, "page": s.page,
                                                     "to": pymupdf.Point(0, s.y)}] for s in sections])
        doc.saveIncr()
    return len(sections)


def convert_to_pdf(source: Path, target: Path, *, image_page: str = "image") -> Path:
    """One file as a PDF."""
    source, target = Path(source), Path(target)
    suffix = source.suffix.lower()
    tmp = temp_path_beside(target, suffix=".pdf")
    try:
        if suffix == ".pdf":
            shutil.copyfile(source, tmp)
        elif is_image(source):
            doc = _image_pages(source, image_page)
            doc.save(str(tmp), garbage=3, deflate=True)
            doc.close()
        elif suffix in MUPDF_SUFFIXES:
            with pymupdf.open(source) as doc:
                pdf = pymupdf.open("pdf", doc.convert_to_pdf())
            pdf.save(str(tmp), garbage=3, deflate=True)
            pdf.close()
        elif office.app_for(source) is not None:
            office.to_pdf(source, tmp)
            if office.app_for(source) == "word":
                add_contents_bookmarks(tmp)
        else:
            raise PdfError(f"{source.name}: PlanetPDF can't turn {suffix or 'this kind of'} files into PDFs.")
        replace_with_retry(tmp, target)
    except (RuntimeError, ValueError, pymupdf.FileDataError) as exc:
        raise PdfError(f"{source.name} couldn't be converted: {exc}") from exc
    finally:
        remove_quietly(tmp)
    return target


def _unique(folder: Path, name: str) -> Path:
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


def create_multiple(files: Sequence[Path], folder: Path, progress: Progress | None = None,
                    *, image_page: str = "image") -> list[BatchResult]:
    """Each file as its own PDF in ``folder`` (never over an existing file). A file that
    fails is reported, the others still go."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for n, source in enumerate(files):
        if not report(progress, n, len(files), "Converting file"):
            raise BatchCanceled("Canceled.")
        source = Path(source)
        try:
            out = convert_to_pdf(source, _unique(folder, source.stem + ".pdf"), image_page=image_page)
            results.append(BatchResult(source, out))
        except PdfError as exc:
            results.append(BatchResult(source, None, str(exc)))
    report(progress, len(files), len(files), "Converting file")
    return results


def ocr_files(files: Sequence[Path], folder: Path | None, *, language: str = "eng", dpi: int = 300,
              progress: Progress | None = None) -> list[BatchResult]:
    """OCR the scanned pages of each PDF (pages that already have text are skipped).
    ``folder``: write "<name>.pdf" there; None: replace each original (after it worked)."""
    from planetpdf.core.ocr import insert_ocr_text_doc, page_needs_ocr, recognize_pages, require_tessdata

    tessdata = require_tessdata()
    results = []
    for n, source in enumerate(files):
        source = Path(source)
        stage = f"File {n + 1} of {len(files)} ({source.name}): recognizing text on page"
        if not report(progress, 0, 0, f"File {n + 1} of {len(files)} ({source.name}): opening…"):
            raise BatchCanceled("Canceled.")
        try:
            doc = pymupdf.open(source)
            if doc.needs_pass:
                doc.close()
                raise PdfError(f"{source.name} is password protected.")
            needing = [i for i in range(doc.page_count) if page_needs_ocr(doc, i)]
            if needing:
                canceled = []

                def step(done, total, _stage=stage):
                    keep = report(progress, done, total, _stage)
                    if not keep:
                        canceled.append(True)
                    return keep

                words = recognize_pages(doc.tobytes(), needing, language=language, dpi=dpi, tessdata=tessdata,
                                        progress=step)
                if canceled:
                    doc.close()
                    raise BatchCanceled("Canceled.")
                for index, page_words in words.items():
                    insert_ocr_text_doc(doc, index, page_words)
            target = (Path(folder) / source.name) if folder is not None else source
            if folder is not None:
                Path(folder).mkdir(parents=True, exist_ok=True)
                target = _unique(Path(folder), source.name)
            tmp = temp_path_beside(target, suffix=".pdf")
            try:
                doc.save(str(tmp), garbage=3, deflate=True)
                doc.close()
                replace_with_retry(tmp, target)
            finally:
                remove_quietly(tmp)
            results.append(BatchResult(source, target, "" if needing else "no scanned pages: copied as it was"))
        except BatchCanceled:
            raise
        except (PdfError, RuntimeError, ValueError, pymupdf.FileDataError) as exc:
            results.append(BatchResult(source, None, str(exc)))
    return results


# --- PDF Portfolio -------------------------------------------------------------------------------


def make_portfolio(files: Sequence[Path], target: Path, progress: Progress | None = None,
                   *, title: str = "") -> Path:
    """A PDF holding the files unchanged, as attachments, with a cover page listing them
    and a /Collection so viewers that know portfolios show the list (Acrobat's Details view)."""
    files = [Path(f) for f in files]
    if not files:
        raise PdfError("Add the files to put in the portfolio.")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    heading = title or Path(target).stem
    page.insert_text((72, 90), heading, fontname="hebo", fontsize=20)
    page.insert_text((72, 118), f"PDF Portfolio: {len(files)} file{'s' if len(files) != 1 else ''}, "
                                f"{datetime.now():%B %d, %Y}", fontname="helv", fontsize=10, color=(0.4, 0.4, 0.4))
    y = 160
    names: set[str] = set()
    for n, path in enumerate(files):
        if not report(progress, n, len(files), "Adding file"):
            doc.close()
            raise BatchCanceled("Canceled.")
        name = path.name
        k = 2
        while name in names:  # (two files with the same name)
            name = f"{path.stem} ({k}){path.suffix}"
            k += 1
        names.add(name)
        data = path.read_bytes()
        doc.embfile_add(name, data, filename=name, ufilename=name, desc=f"{len(data) / 1024:.0f} KB")
        if y < 740:
            page.insert_text((72, y), f"{n + 1}. {name}", fontname="helv", fontsize=11)
            page.insert_text((400, y), f"{len(data) / 1024:,.0f} KB", fontname="helv", fontsize=10, color=(0.4, 0.4, 0.4))
            y += 18
    if y >= 740:
        page.insert_text((72, 760), f"… and more: open the Attachments to see all {len(files)}.", fontname="helv", fontsize=10)
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "Collection", "<< /Type /Collection /View /D >>")
    doc.xref_set_key(catalog, "PageMode", "/UseAttachments")
    report(progress, 0, 0, f"Saving the portfolio ({len(files)} files)…")
    tmp = temp_path_beside(Path(target), suffix=".pdf")
    try:
        doc.save(str(tmp), garbage=3, deflate=True)
        doc.close()
        replace_with_retry(tmp, Path(target))
    finally:
        remove_quietly(tmp)
    return Path(target)


def attachments(doc: pymupdf.Document) -> list[tuple[str, int]]:
    """(name, size in bytes) of the files embedded in the document (portfolio or not)."""
    out = []
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        out.append((name, int(info.get("size") or info.get("length") or 0)))
    return out


def is_portfolio(doc: pymupdf.Document) -> bool:
    return doc.xref_get_key(doc.pdf_catalog(), "Collection")[0] != "null"
