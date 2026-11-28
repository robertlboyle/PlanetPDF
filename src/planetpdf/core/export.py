"""Export pages to images, image-only PDF, plain text, RTF and Word.

Every function takes a ``pymupdf.Document`` rather than a session, so the UI can run
it on a private copy in a background thread. ``pages`` are 0-based, in output order.
``progress(done, total, stage="")`` (``core.progress``) may return False to cancel
(raises ``ExportCanceled``). Files are written to a temp file and moved into place,
never half-written.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.ocr import DEFAULT_DPI, ocr_page_text, page_needs_ocr, recognize_pages
from planetpdf.core.progress import Progress as ProgressCallback, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

Progress = ProgressCallback | None  # progress(done, total, stage="") -> keep going?


class ExportCanceled(PdfError):
    pass


class ExportFormat(Enum):
    PDF = "pdf"  # selected pages as a normal PDF (core.pages.export_pages)
    PNG = "png"
    JPG = "jpg"
    TIFF = "tiff"  # one file per page
    TIFF_MULTI = "tiff_multi"  # one multi-page file
    IMAGE_PDF = "image_pdf"  # every page rendered to a picture
    DOCX = "docx"
    RTF = "rtf"
    TXT = "txt"
    # added in backlog Phase C (core.export_formats)
    DOC = "doc"  # Word 97-2003 (through Microsoft Word)
    XLSX = "xlsx"
    XML_SPREADSHEET = "xml_spreadsheet"  # XML Spreadsheet 2003
    PPTX = "pptx"
    HTML = "html"
    XML = "xml"  # XML 1.0
    TXT_ACCESSIBLE = "txt_accessible"
    PS = "ps"
    EPS = "eps"
    JPX = "jpx"  # JPEG 2000

    @property
    def extension(self) -> str:
        return {
            ExportFormat.PDF: ".pdf",
            ExportFormat.PNG: ".png",
            ExportFormat.JPG: ".jpg",
            ExportFormat.TIFF: ".tif",
            ExportFormat.TIFF_MULTI: ".tif",
            ExportFormat.IMAGE_PDF: ".pdf",
            ExportFormat.DOCX: ".docx",
            ExportFormat.RTF: ".rtf",
            ExportFormat.TXT: ".txt",
            ExportFormat.DOC: ".doc",
            ExportFormat.XLSX: ".xlsx",
            ExportFormat.XML_SPREADSHEET: ".xml",
            ExportFormat.PPTX: ".pptx",
            ExportFormat.HTML: ".html",
            ExportFormat.XML: ".xml",
            ExportFormat.TXT_ACCESSIBLE: ".txt",
            ExportFormat.PS: ".ps",
            ExportFormat.EPS: ".eps",
            ExportFormat.JPX: ".jp2",
        }[self]

    @property
    def is_image(self) -> bool:
        return self in (ExportFormat.PNG, ExportFormat.JPG, ExportFormat.TIFF, ExportFormat.TIFF_MULTI, ExportFormat.IMAGE_PDF,
                        ExportFormat.JPX)

    @property
    def is_document(self) -> bool:
        return self in (ExportFormat.DOCX, ExportFormat.RTF, ExportFormat.TXT, ExportFormat.DOC)


@dataclass
class ExportOptions:
    dpi: int = 150
    jpeg_quality: int = 85  # JPG files, and image-only PDF when compress_images is on
    compress_images: bool = True  # image-only PDF: JPEG (smaller) vs lossless
    ocr_missing_text: bool = False  # text/RTF/Word: OCR pages that have no text
    ocr_language: str = "eng"
    # Word / RTF / text / HTML: read the pages with the AI text reader (Unlimited-OCR, optional,
    # an NVIDIA card: core/ai_ocr.py) and write its flowing text instead (user 2026-10-21)
    ai_reading: bool = False


AI_FORMATS = ("docx", "doc", "rtf", "txt", "txt_accessible", "html")


def _tick(progress: Progress, done: int, total: int, stage: str = "") -> None:
    if not report(progress, done, total, stage):
        raise ExportCanceled("Export canceled.")


def _check_pages(doc: pymupdf.Document, pages: Sequence[int]) -> list[int]:
    pages = list(pages)
    if not pages:
        raise PdfError("No pages selected.")
    if any(not 0 <= p < doc.page_count for p in pages):
        raise PdfError("Page number out of range.")
    return pages


class _AtomicTarget:
    """Write to a temp file beside ``target``; move into place only on success."""

    def __init__(self, target: Path) -> None:
        self.target = Path(target)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = temp_path_beside(self.target, suffix=self.target.suffix or ".tmp")

    def __enter__(self) -> Path:
        return self.tmp

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                replace_with_retry(self.tmp, self.target)
        finally:
            remove_quietly(self.tmp)


def image_file_names(target: Path, pages: Sequence[int], fmt: ExportFormat) -> list[Path]:
    """One file per page: ``name_p001.png`` etc. (just ``target`` for a single page)."""
    target = Path(target)
    if fmt in (ExportFormat.TIFF_MULTI, ExportFormat.IMAGE_PDF) or len(pages) == 1:
        return [target.with_suffix(fmt.extension) if not target.suffix else target]
    width = max(3, len(str(max(pages) + 1)))
    stem = target.stem
    return [target.with_name(f"{stem}_p{p + 1:0{width}d}{fmt.extension}") for p in pages]


def _render(doc: pymupdf.Document, index: int, dpi: int) -> pymupdf.Pixmap:
    pix = doc[index].get_pixmap(dpi=dpi, alpha=False, annots=True)
    pix.set_dpi(dpi, dpi)
    return pix


# --- images -------------------------------------------------------------------------------------


def output_files(target: Path, pages: Sequence[int], fmt: ExportFormat) -> list[Path]:
    """The files an export writes: a picture per page for PNG, JPG, and TIFF (one per page), else
    the one file named."""
    if fmt in (ExportFormat.PNG, ExportFormat.JPG, ExportFormat.TIFF):
        return image_file_names(Path(target), list(pages), fmt)
    return [Path(target)]


def export_images(
    doc: pymupdf.Document,
    pages: Sequence[int],
    target: Path,
    fmt: ExportFormat,
    options: ExportOptions,
    progress: Progress = None,
) -> list[Path]:
    """PNG / JPG / TIFF (per page or multi-page). Returns the files written."""
    pages = _check_pages(doc, pages)
    if fmt is ExportFormat.IMAGE_PDF:
        export_image_pdf(doc, pages, target, options, progress)
        return [Path(target)]
    if fmt is ExportFormat.TIFF_MULTI:
        return [_export_multipage_tiff(doc, pages, Path(target), options, progress)]
    names = image_file_names(target, pages, fmt)
    for n, (index, name) in enumerate(zip(pages, names)):
        _tick(progress, n, len(pages))
        pix = _render(doc, index, options.dpi)
        with _AtomicTarget(name) as tmp:
            if fmt is ExportFormat.PNG:
                pix.save(str(tmp), output="png")
            elif fmt is ExportFormat.JPG:
                pix.save(str(tmp), output="jpg", jpg_quality=options.jpeg_quality)
            else:
                _pil_image(pix).save(tmp, format="TIFF", compression="tiff_lzw", dpi=(options.dpi, options.dpi))
    _tick(progress, len(pages), len(pages))
    return names


def _pil_image(pix: pymupdf.Pixmap):
    from PIL import Image

    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _export_multipage_tiff(doc, pages, target: Path, options: ExportOptions, progress: Progress) -> Path:
    total = len(pages)

    def frames() -> Iterator:
        # Rendered one at a time as Pillow writes them, so memory stays at ~one page.
        for n, index in enumerate(pages[1:], start=1):
            _tick(progress, n, total)
            yield _pil_image(_render(doc, index, options.dpi))

    _tick(progress, 0, total)
    first = _pil_image(_render(doc, pages[0], options.dpi))
    with _AtomicTarget(target) as tmp:
        first.save(
            tmp,
            format="TIFF",
            save_all=True,
            append_images=frames(),
            compression="tiff_lzw",
            dpi=(options.dpi, options.dpi),
        )
    _tick(progress, total, total)
    return target


def export_image_pdf(
    doc: pymupdf.Document, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None,
    protection=None,
) -> Path:
    """A PDF whose pages are pictures of the originals: no text, no layers, no forms (encrypted as it
    is written with ``protection``)."""
    pages = _check_pages(doc, pages)
    out = pymupdf.open()
    try:
        for n, index in enumerate(pages):
            _tick(progress, n, len(pages))
            source = doc[index]
            pix = _render(doc, index, options.dpi)
            page = out.new_page(width=source.rect.width, height=source.rect.height)
            if options.compress_images:
                page.insert_image(page.rect, stream=pix.tobytes("jpg", jpg_quality=options.jpeg_quality))
            else:
                page.insert_image(page.rect, pixmap=pix)
        _tick(progress, 0, 0, f"Saving the PDF ({len(pages)} pages)…")
        from planetpdf.core.document import save_pdf

        Path(target).parent.mkdir(parents=True, exist_ok=True)
        save_pdf(out, Path(target), protection, garbage=4, deflate=True)
    finally:
        out.close()
    _tick(progress, len(pages), len(pages))
    return Path(target)


# --- text ----------------------------------------------------------------------------------------


def _page_text(doc: pymupdf.Document, index: int, options: ExportOptions) -> str:
    if options.ocr_missing_text and page_needs_ocr(doc, index):
        return ocr_page_text(doc[index], language=options.ocr_language)
    return doc[index].get_text("text", sort=True)


def export_text(
    doc: pymupdf.Document, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None
) -> Path:
    """UTF-8 plain text in reading order; pages separated by a form feed."""
    pages = _check_pages(doc, pages)
    parts = []
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages))
        parts.append(_page_text(doc, index, options).rstrip() + "\n")
    with _AtomicTarget(target) as tmp:
        tmp.write_text("\f".join(parts), encoding="utf-8", newline="\n")
    _tick(progress, len(pages), len(pages))
    return Path(target)


# --- RTF -----------------------------------------------------------------------------------------

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\t":
            out.append("\\tab ")
        elif 32 <= code < 128:
            out.append(ch)
        elif code < 32:
            continue
        else:
            # RTF \u takes a signed 16-bit value; characters above U+FFFF use surrogates.
            for unit in ch.encode("utf-16-be").hex(" ", 2).split():
                value = int(unit, 16)
                out.append(f"\\u{value - 65536 if value > 32767 else value}?")
    return "".join(out)


class _RtfBuilder:
    def __init__(self) -> None:
        self.fonts: dict[str, int] = {}
        self.colors: dict[tuple[int, int, int], int] = {}
        self.body: list[str] = []

    def font(self, name: str, flags: int) -> int:
        name = _SUBSET_PREFIX.sub("", name or "Helvetica").split(",")[0].split("-")[0] or "Helvetica"
        if name not in self.fonts:
            family = "fmodern" if flags & 8 else ("froman" if flags & 4 else "fswiss")
            self.fonts[name] = len(self.fonts)
            self._families = getattr(self, "_families", {})
            self._families[name] = family
        return self.fonts[name]

    def color(self, srgb: int) -> int:
        rgb = ((srgb >> 16) & 255, (srgb >> 8) & 255, srgb & 255)
        if rgb == (0, 0, 0):
            return 0  # auto
        if rgb not in self.colors:
            self.colors[rgb] = len(self.colors) + 1
        return self.colors[rgb]

    def document(self) -> str:
        families = getattr(self, "_families", {})
        fonts = "".join(
            f"{{\\f{i}\\{families.get(name, 'fswiss')} {_rtf_escape(name)};}}" for name, i in self.fonts.items()
        ) or "{\\f0\\fswiss Helvetica;}"
        colors = ";".join(f"\\red{r}\\green{g}\\blue{b}" for (r, g, b) in self.colors)
        return (
            "{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1"
            f"{{\\fonttbl{fonts}}}"
            f"{{\\colortbl ;{colors + ';' if colors else ''}}}"
            "\\viewkind4\n" + "".join(self.body) + "}"
        )


def _rtf_image(block: dict, max_width_pt: float) -> str:
    data, ext = block.get("image"), (block.get("ext") or "").lower()
    if not data:
        return ""
    if ext in ("jpg", "jpeg"):
        kind = "jpegblip"
    else:
        if ext != "png":
            try:
                data = pymupdf.Pixmap(data).tobytes("png")
            except (RuntimeError, ValueError):
                return ""
        kind = "pngblip"
    bbox = pymupdf.Rect(block["bbox"])
    width, height = bbox.width, bbox.height
    if width > max_width_pt:
        height *= max_width_pt / width
        width = max_width_pt
    return (
        f"{{\\pard\\sa120{{\\pict\\{kind}\\picwgoal{round(width * 20)}\\pichgoal{round(height * 20)}\n"
        + data.hex()
        + "}\\par}\n"
    )


def export_rtf(
    doc: pymupdf.Document, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None
) -> Path:
    """Paragraph per text block (reflowed), with font, size, bold, italic and color;
    images embedded; a page break between pages."""
    pages = _check_pages(doc, pages)
    rtf = _RtfBuilder()
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages))
        if n:
            rtf.body.append("\\page\n")
        page = doc[index]
        if options.ocr_missing_text and page_needs_ocr(doc, index):
            for para in ocr_page_text(page, language=options.ocr_language).split("\n\n"):
                if para.strip():
                    rtf.body.append(f"{{\\pard\\sa120\\f0\\fs22 {_rtf_escape(' '.join(para.split()))}\\par}}\n")
            continue
        usable_width = page.rect.width - 72
        for block in page.get_text("dict", sort=True)["blocks"]:
            if block.get("type") == 1:
                rtf.body.append(_rtf_image(block, usable_width))
                continue
            runs = []
            for line_no, line in enumerate(block.get("lines", [])):
                for span_no, span in enumerate(line["spans"]):
                    text = span["text"]
                    if line_no and span_no == 0 and runs and not text.startswith(" "):
                        text = " " + text  # reflow: lines of a block become one paragraph
                    if not text:
                        continue
                    f = rtf.font(span.get("font", ""), span.get("flags", 0))
                    c = rtf.color(span.get("color", 0))
                    size = max(2, round(span.get("size", 11) * 2))
                    style = ("\\b" if span.get("flags", 0) & 16 else "") + ("\\i" if span.get("flags", 0) & 2 else "")
                    runs.append(f"{{\\f{f}\\fs{size}\\cf{c}{style} {_rtf_escape(text)}}}")
            if runs:
                rtf.body.append("{\\pard\\sa120 " + "".join(runs) + "\\par}\n")
    with _AtomicTarget(target) as tmp:
        tmp.write_text(rtf.document(), encoding="ascii", newline="\n")
    _tick(progress, len(pages), len(pages))
    return Path(target)


# --- Word ----------------------------------------------------------------------------------------


def _import_pdf2docx():
    """pdf2docx's Converter, without the side effects its import has on the whole app
    (user report 2026-10-02, console noise): it calls ``logging.basicConfig(level=INFO)``
    (a console handler on the root logger, and every library's INFO messages shown from
    then on), and its ``import fitz`` prints PyMuPDF's "The `fitz` API is deprecated"
    notice. Both are undone / muted here."""
    import io

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    previous = pymupdf._g_out_message  # (where PyMuPDF prints its messages: stdout)
    pymupdf.set_messages(stream=io.StringIO())
    try:
        from pdf2docx import Converter
    finally:
        pymupdf.set_messages(stream=previous)
        for handler in root.handlers[:]:
            if handler not in handlers:
                root.removeHandler(handler)
        root.setLevel(level)
    return Converter


def export_docx(
    doc: pymupdf.Document, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None,
    notes: list[str] | None = None,
) -> Path:
    """Word document via pdf2docx (layout, tables and images kept as far as possible).

    Converts a temporary PDF of just ``pages`` (in order); with ``ocr_missing_text``
    scanned pages get an OCR text layer first so Word receives real text. ``notes``
    receives pdf2docx's errors (a page or part of one it couldn't convert and left
    out); its routine layout warnings are dropped.
    """
    pages = _check_pages(doc, pages)
    Converter = _import_pdf2docx()
    work = pymupdf.open()
    target = Path(target)
    tmp_pdf = temp_path_beside(target, suffix=".pdf")
    try:
        for n, index in enumerate(pages):
            _tick(progress, n, len(pages), "Copying page")
            work.insert_pdf(doc, from_page=index, to_page=index)
        if options.ocr_missing_text:
            needing = [i for i in range(work.page_count) if page_needs_ocr(work, i)]
            if needing:
                words = recognize_pages(
                    work.tobytes(), needing, language=options.ocr_language, dpi=DEFAULT_DPI,
                    progress=lambda d, t: _tick(progress, d, t, "Recognizing text (OCR) on page") or True)
                for i, page_words in words.items():
                    _replace_with_text_page(work, i, page_words)
        work.save(str(tmp_pdf), garbage=3, deflate=True)
        with _AtomicTarget(target) as tmp_docx, _Pdf2docxProgress(progress, len(pages)) as watch:
            converter = Converter(str(tmp_pdf))
            try:
                converter.convert(str(tmp_docx), multi_processing=False)
            finally:
                converter.close()
        if notes is not None:
            # pdf2docx's page numbers are of the temporary PDF (the chosen pages, in order)
            notes.extend(re.sub(r"\bpage (\d+)\b", lambda m: f"page {pages[int(m.group(1)) - 1] + 1}"
                                if 0 < int(m.group(1)) <= len(pages) else m.group(0), problem)
                         for problem in watch.problems)
        _tick(progress, len(pages), len(pages), "Writing page")
    finally:
        work.close()
        remove_quietly(tmp_pdf)
    return target


class _Pdf2docxProgress(logging.Filter):
    """pdf2docx says how far it is only in its log ("[3/4] Parsing pages...", then
    "(i/n) Page p" per page, "[4/4] Creating pages..." and the pages again), through the
    root logger. While converting, this filter on the root logger turns those records
    from *this* thread into progress (Analyzing the layout; Reading page i of n; Writing
    page i of n) and drops them. Its warnings ("Ignore Line ... due to overlap": routine
    layout decisions) are dropped too, and its errors (a page it couldn't convert) are
    kept in ``problems`` for the user instead of going to the console. Records from other
    threads are logged as usual. Cancel: raising from the filter stops pdf2docx between
    pages (its page loop doesn't catch it)."""

    STAGES = {"[2/4]": "Analyzing the page layout…", "[3/4]": "Reading page", "[4/4]": "Writing page"}

    def __init__(self, progress: Progress, total: int) -> None:
        super().__init__()
        self.progress, self.total = progress, total
        self.stage = ""
        self.problems: list[str] = []
        self.thread = threading.get_ident()
        self.root = logging.getLogger()
        self.level = self.root.level

    def __enter__(self):
        self.root.addFilter(self)
        if self.root.getEffectiveLevel() > logging.INFO:
            self.root.setLevel(logging.INFO)  # (pdf2docx logs at INFO; nothing else sees them)
        return self

    def __exit__(self, *exc) -> None:
        self.root.removeFilter(self)
        self.root.setLevel(self.level)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.thread != self.thread:
            return record.levelno >= self.level
        if record.levelno >= logging.ERROR:
            try:
                self.problems.append(record.getMessage())
            except Exception:  # (a malformed log call: keep the raw text)
                self.problems.append(str(record.msg))
            return False
        if record.levelno > logging.INFO:
            return False
        message = str(record.msg)
        for key, stage in self.STAGES.items():
            if key in message:
                self.stage = stage
                if stage.endswith("…"):
                    _tick(self.progress, 0, 0, stage)
        if message.startswith("(%d/%d) Page") and record.args and self.stage:
            i, n = record.args[0], record.args[1]
            _tick(self.progress, i - 1, n, self.stage)
        return False


def _replace_with_text_page(work: pymupdf.Document, index: int, words) -> None:
    """For Word export: swap a scanned page for a text-only page with the recognized
    words drawn visibly where they were (pdf2docx ignores invisible OCR text and would
    otherwise just embed the scan as a picture)."""
    old = work[index]
    size, rotation_matrix = old.rect, old.rotation_matrix
    page = work.new_page(pno=index, width=size.width, height=size.height)
    shape = page.new_shape()
    for word in words:
        shown = pymupdf.Rect(word.rect) * rotation_matrix  # upright position
        unit = pymupdf.get_text_length(word.text, fontname="helv", fontsize=1)
        if shown.is_empty or unit <= 0:
            continue
        fontsize = max(4.0, min(shown.height * 1.2, shown.width / unit))
        try:
            shape.insert_text((shown.x0, shown.y1 - 0.2 * shown.height), word.text, fontsize=fontsize, fontname="helv")
        except (ValueError, RuntimeError):
            continue
    shape.commit()
    work.delete_page(index + 1)


def export(
    doc: pymupdf.Document,
    pages: Sequence[int],
    target: Path,
    fmt: ExportFormat,
    options: ExportOptions,
    progress: Progress = None,
    notes: list[str] | None = None,
) -> list[Path]:
    """Dispatch to the right exporter. Returns the files written. ``notes`` collects
    what the user should know about (Word: pages that couldn't be fully converted)."""
    from planetpdf.core import export_formats as F

    if options.ai_reading and fmt.value in AI_FORMATS:
        return [_export_ai(doc, _check_pages(doc, pages), Path(target), fmt, progress)]
    if fmt is ExportFormat.JPX:
        return F.export_jpeg2000(doc, pages, target, options, progress)
    if fmt in (ExportFormat.PS, ExportFormat.EPS):
        from planetpdf.core import ghostscript

        if ghostscript.find() is not None:  # vector PostScript
            return [ghostscript.to_postscript(doc, pages, target, eps=fmt is ExportFormat.EPS, progress=progress)]
        if notes is not None:
            notes.append("Ghostscript isn't installed, so the pages were written as pictures "
                         "(install Ghostscript for PostScript with real text and shapes).")
        return [F.write_raster_ps(doc, pages, target, options, progress, eps=fmt is ExportFormat.EPS)]
    simple = {ExportFormat.HTML: F.export_html, ExportFormat.XML: F.export_xml,
              ExportFormat.TXT_ACCESSIBLE: F.export_accessible_text, ExportFormat.XLSX: F.export_xlsx,
              ExportFormat.XML_SPREADSHEET: F.export_xml_spreadsheet, ExportFormat.PPTX: F.export_pptx}
    if fmt in simple:
        return [simple[fmt](doc, pages, target, options, progress)]
    if fmt is ExportFormat.DOC:
        return [F.export_doc(doc, pages, target, options, progress, notes)]
    if fmt.is_image:
        return export_images(doc, pages, target, fmt, options, progress)
    if fmt is ExportFormat.TXT:
        return [export_text(doc, pages, target, options, progress)]
    if fmt is ExportFormat.RTF:
        return [export_rtf(doc, pages, target, options, progress)]
    if fmt is ExportFormat.DOCX:
        return [export_docx(doc, pages, target, options, progress, notes)]
    raise PdfError(f"{fmt.value} export is handled by core.pages.export_pages")


def _export_ai(doc, pages: list[int], target: Path, fmt: ExportFormat, progress: Progress = None) -> Path:
    """The pages read by the AI text reader, written as one flowing document (``core/ai_ocr.py``)."""
    from planetpdf.core import ai_ocr

    try:
        blocks = ai_ocr.read_document(doc, pages, progress)
    except ai_ocr.AiOcrCanceled:
        raise ExportCanceled("Export canceled.") from None
    _tick(progress, 0, 0, f"Writing {target.name}…")
    with _AtomicTarget(target) as tmp:
        if fmt is ExportFormat.DOCX:
            ai_ocr.to_docx(blocks, tmp)
        elif fmt is ExportFormat.DOC:
            import shutil
            import tempfile

            from planetpdf.core import office

            if not office.available("word"):
                raise PdfError("Word 97-2003 needs Microsoft Word on this computer.")
            work = Path(tempfile.mkdtemp(prefix="planetpdf-word-"))
            try:
                ai_ocr.to_docx(blocks, work / "document.docx")
                office.word_save_as(work / "document.docx", work / "document.doc", office.WORD_97_2003)
                shutil.copyfile(work / "document.doc", tmp)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        elif fmt is ExportFormat.RTF:
            Path(tmp).write_text(ai_ocr.to_rtf(blocks), encoding="ascii", errors="replace")
        elif fmt is ExportFormat.HTML:
            Path(tmp).write_text(ai_ocr.to_html(blocks, target.stem), encoding="utf-8")
        else:
            Path(tmp).write_text(ai_ocr.to_text(blocks), encoding="utf-8")
    return target
