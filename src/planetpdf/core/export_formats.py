"""More File ▸ Export To formats (Acrobat's; backlog Phase C, built 2026-10-02): HTML,
XML 1.0, accessible text, PostScript / EPS, JPEG 2000, Excel, XML Spreadsheet 2003,
PowerPoint, Word 97-2003.

Like ``core.export``, every function takes its own ``pymupdf.Document`` (a private copy:
background threads), writes through a temp file, and reports real progress
(``progress(done, total, stage)``: "Exporting page 3 of 10", or the stage's own words).
"""

from __future__ import annotations

import base64
import html
import io
import re
import zlib
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.export import ExportOptions, Progress, _AtomicTarget, _check_pages, _render, _tick


def _title(doc: pymupdf.Document, target: Path) -> str:
    return (doc.metadata or {}).get("title") or Path(target).stem


# --- HTML ------------------------------------------------------------------------------------------


def export_html(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None) -> Path:
    """One web page: each PDF page's text as headings / paragraphs (MuPDF's XHTML, which
    keeps bold, italics and pictures, the pictures embedded), pages separated by a rule."""
    pages = _check_pages(doc, pages)
    parts = []
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        body = doc[index].get_text("xhtml")
        body = re.sub(r"^\s*<div[^>]*>|</div>\s*$", "", body.strip())
        parts.append(f'<section class="page" id="page-{index + 1}">\n{body}\n</section>')
    title = html.escape(_title(doc, target))
    document = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{title}</title>\n<style>body{{font-family:sans-serif;max-width:52em;margin:2em auto;padding:0 1em}}"
        "img{max-width:100%;height:auto}section.page{margin-bottom:2em}hr{margin:2em 0}</style>\n</head>\n<body>\n"
        + "\n<hr>\n".join(parts) + "\n</body>\n</html>\n"
    )
    with _AtomicTarget(target) as tmp:
        tmp.write_text(document, encoding="utf-8", newline="\n")
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- XML 1.0 ---------------------------------------------------------------------------------------


def export_xml(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None) -> Path:
    """The text with its structure: pages, blocks, lines and spans (font, size, color,
    position), and where the pictures are. Positions in points from the top-left, as shown."""
    pages = _check_pages(doc, pages)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<document title="{xml_escape(_title(doc, target), {chr(34): "&quot;"})}" pages="{len(pages)}">']
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        page = doc[index]
        matrix = page.rotation_matrix
        out.append(f'  <page number="{index + 1}" width="{page.rect.width:.2f}" height="{page.rect.height:.2f}">')
        for block in page.get_text("dict", sort=True)["blocks"]:
            box = pymupdf.Rect(block["bbox"]) * matrix
            where = f'x="{box.x0:.1f}" y="{box.y0:.1f}" width="{box.width:.1f}" height="{box.height:.1f}"'
            if block.get("type") == 1:
                out.append(f"    <image {where}/>")
                continue
            out.append(f"    <block {where}>")
            for line in block.get("lines", []):
                out.append("      <line>")
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    attrs = (f'font="{xml_escape(span["font"], {chr(34): "&quot;"})}" size="{span["size"]:.1f}" '
                             f'color="#{span["color"]:06x}"' + (' bold="true"' if span["flags"] & 16 else "")
                             + (' italic="true"' if span["flags"] & 2 else ""))
                    out.append(f"        <span {attrs}>{xml_escape(span['text'])}</span>")
                out.append("      </line>")
            out.append("    </block>")
        out.append("  </page>")
    out.append("</document>")
    with _AtomicTarget(target) as tmp:
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- Text (Accessible) -----------------------------------------------------------------------------


def _paragraphs(page: pymupdf.Page) -> list[str]:
    """Reading order, one paragraph per text block (its lines joined, hyphenated words
    rejoined), "[Picture]" where a picture is."""
    out = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block.get("type") == 1:
            out.append("[Picture]")
            continue
        text = ""
        for line in block.get("lines", []):
            piece = "".join(s["text"] for s in line["spans"]).strip()
            if not piece:
                continue
            if text.endswith("-") and piece[:1].islower():
                text = text[:-1] + piece
            else:
                text = f"{text} {piece}" if text else piece
        if text:
            out.append(text)
    return out


def export_accessible_text(doc, pages: Sequence[int], target: Path, options: ExportOptions,
                           progress: Progress = None) -> Path:
    """Text for screen readers and other tools: paragraphs in reading order (not the page's
    line breaks), a "Page N" heading per page, pictures marked."""
    pages = _check_pages(doc, pages)
    parts = []
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        parts.append(f"Page {index + 1}\n\n" + "\n\n".join(_paragraphs(doc[index])))
    with _AtomicTarget(target) as tmp:
        tmp.write_text("\n\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- PostScript / EPS --------------------------------------------------------------------------------


def _ps_image(pix: pymupdf.Pixmap, width: float, height: float) -> bytes:
    data = base64.a85encode(zlib.compress(pix.samples, 6), adobe=True)
    lines = b"\n".join(data[i:i + 76] for i in range(0, len(data), 76))
    return (
        f"gsave\n{width:.3f} {height:.3f} scale\n/DeviceRGB setcolorspace\n"
        f"<< /ImageType 1 /Width {pix.width} /Height {pix.height} /BitsPerComponent 8 "
        f"/Decode [0 1 0 1 0 1] /ImageMatrix [{pix.width} 0 0 -{pix.height} 0 {pix.height}] "
        "/DataSource currentfile /ASCII85Decode filter /FlateDecode filter >> image\n"
    ).encode("ascii") + lines + b"\ngrestore\n"


def write_raster_ps(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None,
                    *, eps: bool = False) -> Path:
    """PostScript (Level 3) with every page as a picture: works with no other software
    (Ghostscript, when installed, writes real vector PostScript instead: ``core.ghostscript``)."""
    pages = _check_pages(doc, pages)
    if eps and len(pages) != 1:
        raise PdfError("Encapsulated PostScript holds one page: choose a single page.")
    first = doc[pages[0]].rect
    header = ["%!PS-Adobe-3.0" + (" EPSF-3.0" if eps else ""), "%%Creator: PlanetPDF",
              f"%%Title: {_title(doc, target)}", "%%LanguageLevel: 3"]
    if eps:
        header.append(f"%%BoundingBox: 0 0 {int(round(first.width))} {int(round(first.height))}")
    else:
        header.append(f"%%Pages: {len(pages)}")
    header.append("%%EndComments")
    body = [("\n".join(header) + "\n").encode("latin-1", "replace")]
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        page = doc[index]
        pix = _render(doc, index, options.dpi)
        w, h = page.rect.width, page.rect.height
        if not eps:
            body.append(f"%%Page: {n + 1} {n + 1}\n<< /PageSize [{w:.2f} {h:.2f}] >> setpagedevice\n".encode("ascii"))
        body.append(_ps_image(pix, w, h))
        if not eps:
            body.append(b"showpage\n")
    body.append(b"%%EOF\n")
    with _AtomicTarget(target) as tmp:
        tmp.write_bytes(b"".join(body))
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- JPEG 2000 ---------------------------------------------------------------------------------------


def export_jpeg2000(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None) -> list[Path]:
    """One .jp2 file per page (Pillow's OpenJPEG; quality from the JPG quality setting)."""
    from PIL import Image

    from planetpdf.core.export import ExportFormat, image_file_names

    pages = _check_pages(doc, pages)
    names = image_file_names(target, pages, ExportFormat.JPX)
    for n, (index, name) in enumerate(zip(pages, names)):
        _tick(progress, n, len(pages), "Exporting page")
        pix = _render(doc, index, options.dpi)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        rate = max(1.0, (100 - options.jpeg_quality) / 2.5)  # compression ratio from the 1-100 quality
        with _AtomicTarget(name) as tmp:
            image.save(tmp, format="JPEG2000", quality_mode="rates", quality_layers=[rate], irreversible=True)
    _tick(progress, len(pages), len(pages), "Exporting page")
    return names


# --- spreadsheets (Excel, XML Spreadsheet 2003) ------------------------------------------------------


def page_rows(page: pymupdf.Page) -> list[list[str]]:
    """A page as spreadsheet rows: each table found on it cell for cell, the other text one
    line per row (in reading order), a blank row between parts."""
    rows: list[list[str]] = []
    try:
        tables = list(page.find_tables())
    except Exception:  # (MuPDF's table finder gives up on some pages)
        tables = []
    boxes = [pymupdf.Rect(t.bbox) for t in tables]
    items: list[tuple[float, list[list[str]], bool]] = []  # (top, rows, is a table)
    for table, box in zip(tables, boxes):
        cells = [[(c or "").replace("\n", " ").strip() for c in row] for row in table.extract()]
        items.append((box.y0, cells, True))
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block.get("type") == 1:
            continue
        bbox = pymupdf.Rect(block["bbox"])
        if any(bbox.intersects(b) for b in boxes):
            continue
        for line in block.get("lines", []):
            # columns: where the line's spans are far apart (tab-stop style layouts)
            cells, last_x1 = [], None
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                if cells and last_x1 is not None and span["bbox"][0] - last_x1 < span["size"] * 1.5:
                    cells[-1] += " " + text
                else:
                    cells.append(text)
                last_x1 = span["bbox"][2]
            if cells:
                items.append((line["bbox"][1], [cells], False))
    previous_table = False
    for _y, block_rows, is_table in sorted(items, key=lambda i: i[0]):
        if rows and (is_table or previous_table):
            rows.append([])  # (a blank row around each table)
        rows.extend([[_number(c) for c in r] for r in block_rows])
        previous_table = is_table
    return rows


def _number(text):
    """Numbers as numbers ("1,234.50", "$99", "12%"), so spreadsheet sums work."""
    if not isinstance(text, str):
        return text
    plain = text.strip().replace(",", "")
    percent = plain.endswith("%")
    plain = plain.rstrip("%").lstrip("$€£")
    negative = plain.startswith("(") and plain.endswith(")")
    plain = plain.strip("()")
    if re.fullmatch(r"-?\d+(\.\d+)?", plain):
        value = float(plain) if "." in plain else int(plain)
        value = -value if negative else value
        return value / 100 if percent else value
    return text


FORMULA_STARTS = ("=", "+", "-", "@")  # (what a spreadsheet may take for the start of a formula)


def export_xlsx(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None) -> Path:
    """An Excel workbook: one sheet per page; tables cell for cell, other text line by line."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    pages = _check_pages(doc, pages)
    book = Workbook()
    book.remove(book.active)
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        sheet = book.create_sheet(f"Page {index + 1}")
        widths: dict[int, int] = {}
        for row in page_rows(doc[index]):
            sheet.append(row)
            for col, value in enumerate(row, start=1):
                widths[col] = max(widths.get(col, 8), min(60, len(str(value)) + 2))
                if isinstance(value, str) and value[:1] in FORMULA_STARTS:
                    # The page's words, never a formula (security review 2026-11-25: "=SUM(2,3)" in a
                    # PDF became a live formula); quoted, so editing the cell keeps it text too.
                    cell = sheet.cell(row=sheet.max_row, column=col)
                    cell.data_type = "s"
                    cell.quotePrefix = True
        for col, width in widths.items():
            sheet.column_dimensions[get_column_letter(col)].width = width
    with _AtomicTarget(target) as tmp:
        book.save(tmp)
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


def export_xml_spreadsheet(doc, pages: Sequence[int], target: Path, options: ExportOptions,
                           progress: Progress = None) -> Path:
    """XML Spreadsheet 2003 (SpreadsheetML: opens in Excel), the same rows as the workbook."""
    pages = _check_pages(doc, pages)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<?mso-application progid="Excel.Sheet"?>',
           '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
           'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">']
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        out.append(f' <Worksheet ss:Name="Page {index + 1}"><Table>')
        for row in page_rows(doc[index]):
            cells = "".join(
                f'<Cell><Data ss:Type="{"Number" if isinstance(v, (int, float)) else "String"}">{xml_escape(str(v))}</Data></Cell>'
                for v in row)
            out.append(f"  <Row>{cells}</Row>")
        out.append(" </Table></Worksheet>")
    out.append("</Workbook>")
    with _AtomicTarget(target) as tmp:
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- PowerPoint ----------------------------------------------------------------------------------------


def export_pptx(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None) -> Path:
    """A slide per page, the size of the page: the page's pictures and shapes as the
    slide's background (its text taken out), and the text as editable text boxes where
    it was, in its size, color, bold and italics."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    pages = _check_pages(doc, pages)
    first = doc[pages[0]].rect
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(int(first.width * 12700)), Emu(int(first.height * 12700))
    blank = deck.slide_layouts[6]
    # the background: a copy of the pages with all their text removed
    plain = pymupdf.open()
    for index in pages:
        plain.insert_pdf(doc, from_page=index, to_page=index)
    for n, index in enumerate(pages):
        _tick(progress, n, len(pages), "Exporting page")
        source, page = doc[index], plain[n]
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 0:
                page.add_redact_annot(pymupdf.Rect(block["bbox"]))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        slide = deck.slides.add_slide(blank)
        pix = page.get_pixmap(dpi=options.dpi, alpha=False, annots=True)
        sx, sy = deck.slide_width / source.rect.width, deck.slide_height / source.rect.height
        slide.shapes.add_picture(io.BytesIO(pix.tobytes("png")), 0, 0, deck.slide_width, deck.slide_height)
        matrix = source.rotation_matrix
        for block in source.get_text("dict", sort=True)["blocks"]:
            if block.get("type") != 0:
                continue
            box = pymupdf.Rect(block["bbox"]) * matrix
            if box.is_empty:
                continue
            shape = slide.shapes.add_textbox(Emu(int(box.x0 * sx)), Emu(int(box.y0 * sy)),
                                             Emu(int(max(box.width, 4) * sx)), Emu(int(max(box.height, 4) * sy)))
            frame = shape.text_frame
            frame.word_wrap = True
            for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
                setattr(frame, side, 0)
            for i, line in enumerate(block.get("lines", [])):
                paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
                for span in line["spans"]:
                    if not span["text"]:
                        continue
                    run = paragraph.add_run()
                    run.text = span["text"]
                    run.font.size = Pt(max(1.0, span["size"]))
                    run.font.bold = bool(span["flags"] & 16)
                    run.font.italic = bool(span["flags"] & 2)
                    c = span["color"]
                    run.font.color.rgb = RGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255)
    plain.close()
    with _AtomicTarget(target) as tmp:
        deck.save(tmp)
    _tick(progress, len(pages), len(pages), "Exporting page")
    return Path(target)


# --- Word 97-2003 (through Microsoft Word) ------------------------------------------------------------


def export_doc(doc, pages: Sequence[int], target: Path, options: ExportOptions, progress: Progress = None,
               notes: list[str] | None = None) -> Path:
    """Word 97-2003 (.doc): the Word export (.docx) first, then Microsoft Word saves it in
    the old format (Word must be installed: ``core.office``, Windows or Mac)."""
    import shutil
    import tempfile

    from planetpdf.core import office
    from planetpdf.core.export import export_docx
    from planetpdf.core.storage.files import replace_with_retry

    if not office.available("word"):
        raise PdfError("Word 97-2003 needs Microsoft Word on this computer.")
    # (an ordinary folder and names: Word refuses PlanetPDF's hidden ".~" temp names)
    work = Path(tempfile.mkdtemp(prefix="planetpdf-word-"))
    try:
        docx, old = work / "document.docx", work / "document.doc"
        export_docx(doc, pages, docx, options, progress, notes)
        _tick(progress, 0, 0, "Saving as Word 97-2003 with Microsoft Word…")
        office.word_save_as(docx, old, office.WORD_97_2003)
        replace_with_retry(old, Path(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Path(target)
