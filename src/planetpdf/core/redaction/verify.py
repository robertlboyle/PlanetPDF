"""Verify a redacted output file, independently of how it was produced.

Checks (see CLAUDE.md):
* every redacted term: PyMuPDF text, pypdf text (independent parser, ignores layer
  visibility), whitespace/case-normalized forms of both;
* every term in raw file bytes, every decompressed stream and every object, as a
  literal (with PDF escapes), hex, and UTF-16BE string;
* every marked area: no extractable text inside it; image pixels under it are uniform.

A term still present only *outside* all marked areas is a warning (the user didn't
mark that occurrence); anywhere under a mark, or not locatable, is a failure.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pymupdf

from planetpdf.core.progress import Progress
from planetpdf.core.progress import report as _report

RectTuple = tuple[float, float, float, float]
MIN_TERM_LENGTH = 4
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


class Status(Enum):
    PASS = "pass"
    WARN = "warning"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    status: Status
    title: str
    detail: str = ""


@dataclass
class VerificationReport:
    output: Path
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.status is Status.FAIL for c in self.checks)

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]

    def summary(self) -> str:
        if not self.passed:
            return f"FAILED: {len(self.failures)} problem(s) found. Do not share this file."
        if self.warnings:
            return f"Passed with {len(self.warnings)} warning(s)."
        return "Passed: no redacted content could be found in the output."

    def as_text(self) -> str:
        lines = [f"Redaction verification: {self.output}", self.summary(), ""]
        for c in self.checks:
            lines.append(f"[{c.status.value.upper()}] {c.title}")
            if c.detail:
                lines.append(f"    {c.detail}")
        return "\n".join(lines)


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


def _mask(term: str) -> str:
    """Show enough of a term to identify it without repeating it in full."""
    if len(term) <= 4:
        return "*" * len(term)
    return f"{term[:2]}{'*' * (len(term) - 4)}{term[-2:]}"


def _byte_forms(term: str) -> list[bytes]:
    forms: list[bytes] = []
    try:
        latin = term.encode("latin-1")
    except UnicodeEncodeError:
        latin = None
    if latin is not None:
        escaped = latin.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        forms += [latin, escaped, latin.hex().encode()]
    utf16 = term.encode("utf-16-be")
    forms += [utf16, utf16.hex().encode()]
    return [f.lower() for f in dict.fromkeys(forms)]


def _decoded_blobs(data: bytes, doc: pymupdf.Document) -> list[tuple[str, bytes]]:
    blobs = [("raw file bytes", data)]
    for i, match in enumerate(_STREAM_RE.finditer(data)):
        try:
            blobs.append((f"stream #{i} (Flate)", zlib.decompressobj().decompress(match.group(1))))
        except zlib.error:
            pass
    for xref in range(1, doc.xref_length()):
        try:
            blobs.append((f"object {xref}", doc.xref_object(xref, compressed=False).encode("latin-1", "replace")))
            if doc.xref_is_stream(xref):
                stream = doc.xref_stream(xref)
                if stream:
                    blobs.append((f"object {xref} stream", stream))
        except (RuntimeError, ValueError):
            continue
    return blobs


def _pypdf_text(data: bytes) -> list[str]:
    # Imported here: pypdf (with fontTools and cryptography) took 0.6 s of every start,
    # and only redaction verification uses it (user 2026-10-07: "open it faster").
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf raises many types on odd files; treat as unreadable
        return [f"\x00pypdf-error:{exc}"]


def verify_redaction(
    output: Path,
    terms: list[str],
    areas: list[tuple[int, RectTuple]],
    overlay_text: str = "",
    progress: Progress | None = None,
) -> VerificationReport:
    """``overlay_text`` is the label printed on the boxes (e.g. "REDACTED"): words of it
    found inside an area are expected and not reported as leftovers. ``progress`` gets
    "Verifying: reading page x of y", "… searching for removed text x of y", "… checking
    the redacted areas on page x of y" (it can't cancel: a started check finishes)."""
    output = Path(output)
    report = VerificationReport(output)
    data = output.read_bytes()
    doc = pymupdf.open(output)
    try:
        _check_structure(report, data, doc)
        mu_pages = []
        for page in doc:
            _report(progress, page.number, doc.page_count, "Verifying: reading page")
            mu_pages.append(page.get_text())
        _report(progress, 0, 0, f"Verifying: reading the {doc.page_count} pages again with a second parser…")
        py_pages = _pypdf_text(data)
        if py_pages and py_pages[0].startswith("\x00pypdf-error:"):
            # (a check that couldn't run isn't a pass: security review 2026-11-21)
            report.checks.append(
                Check(Status.FAIL, "The second parser (pypdf) couldn't read the file, so it couldn't check it",
                      py_pages[0][14:])
            )
            py_pages = []
        _report(progress, 0, 0, f"Verifying: unpacking the file's {doc.xref_length() - 1:,} objects…")
        blobs = _decoded_blobs(data, doc)
        areas_by_page: dict[int, list[pymupdf.Rect]] = {}
        for page_no, rect in areas:
            areas_by_page.setdefault(page_no, []).append(pymupdf.Rect(rect))

        unique_terms = list(dict.fromkeys(t.strip() for t in terms if t and t.strip()))
        for n, term in enumerate(unique_terms):
            _report(progress, n, len(unique_terms), "Verifying: searching for removed text")
            _check_term(report, term, doc, mu_pages, py_pages, blobs, areas_by_page)
        overlay_words = set(overlay_text.split())
        pages_with_areas = sorted(areas_by_page.items())
        for n, (page_no, rects) in enumerate(pages_with_areas):
            _report(progress, n, len(pages_with_areas), "Verifying: checking the redacted areas, marked page")
            _check_areas(report, doc, page_no, rects, overlay_words)
        if not terms and not areas:
            report.checks.append(Check(Status.WARN, "Nothing to verify", "No redaction marks were given."))
    finally:
        doc.close()
    return report


def _check_structure(report: VerificationReport, data: bytes, doc: pymupdf.Document) -> None:
    revisions = data.count(b"%%EOF")
    if revisions == 1:
        report.checks.append(Check(Status.PASS, "Single revision", "No earlier versions are kept in the file."))
    else:
        report.checks.append(
            Check(Status.FAIL, "Earlier revisions present", f"The file has {revisions} revisions.")
        )
    if doc.is_encrypted or doc.needs_pass:
        report.checks.append(Check(Status.WARN, "Output is encrypted", "Verification may be incomplete."))
    remaining = sum(1 for page in doc for _ in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]))
    if remaining:
        report.checks.append(
            Check(Status.FAIL, "Unapplied redaction marks remain", f"{remaining} mark(s) were not applied.")
        )


def _check_term(report, term, doc, mu_pages, py_pages, blobs, areas_by_page) -> None:
    label = f'Term "{_mask(term)}"'
    if len(term) < MIN_TERM_LENGTH:
        report.checks.append(
            Check(Status.PASS, f"{label} (short)", "Too short to search reliably; covered by the area checks.")
        )
        return
    norm = _norm(term)

    # Where does the term still appear in the (visible) text?
    under_mark: list[int] = []
    outside: list[int] = []
    for page_no, page in enumerate(doc):
        for rect in page.search_for(term):
            if any(rect.intersects(a) for a in areas_by_page.get(page_no, [])):
                under_mark.append(page_no + 1)
            else:
                outside.append(page_no + 1)
    if under_mark:
        report.checks.append(
            Check(Status.FAIL, f"{label} is still readable under a redaction", f"Pages {sorted(set(under_mark))}")
        )
        return

    in_mu = [i + 1 for i, t in enumerate(mu_pages) if norm in _norm(t)]
    in_py = [i + 1 for i, t in enumerate(py_pages) if norm in _norm(t)]
    found_bytes = [name for name, blob in blobs if any(f in blob.lower() for f in _byte_forms(term))]

    if outside:
        report.checks.append(
            Check(
                Status.WARN,
                f"{label} still appears where it wasn't marked",
                f"Unmarked occurrence(s) on page(s) {sorted(set(outside))}. Mark them if they should go.",
            )
        )
        extra_py = sorted(set(in_py) - set(outside))
        if extra_py:
            report.checks.append(
                Check(Status.FAIL, f"{label} found by the second parser", f"Page(s) {extra_py} (pypdf).")
            )
        return

    problems = []
    if in_mu:
        problems.append(f"PyMuPDF text on page(s) {in_mu}")
    if in_py:
        problems.append(f"pypdf text on page(s) {in_py}")
    if found_bytes:
        problems.append(f"file data ({', '.join(found_bytes[:3])}{'…' if len(found_bytes) > 3 else ''})")
    if problems:
        report.checks.append(Check(Status.FAIL, f"{label} can still be extracted", "; ".join(problems)))
    else:
        report.checks.append(
            Check(Status.PASS, f"{label} removed", "Not found by PyMuPDF, pypdf, or in the file data.")
        )


MAX_LEFTOVER_CHARS = 80


def describe_area(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """Where a box is, as the user sees the page: inches from the top-left corner."""
    shown = (pymupdf.Rect(rect) * page.rotation_matrix).normalize()

    def inch(v: float) -> str:
        return f"{v / 72:.1f}"

    return (
        f"box {inch(shown.x0)}–{inch(shown.x1)} in from the left, "
        f"{inch(shown.y0)}–{inch(shown.y1)} in from the top"
    )


def _check_areas(
    report: VerificationReport, doc: pymupdf.Document, page_no: int, rects, overlay_words: set[str] = frozenset()
) -> None:
    if page_no >= doc.page_count:
        report.checks.append(Check(Status.FAIL, f"Page {page_no + 1} missing from output"))
        return
    page = doc[page_no]
    failures = 0
    for number, rect in enumerate(rects, start=1):
        inner = pymupdf.Rect(rect.x0 + 1, rect.y0 + 1, rect.x1 - 1, rect.y1 - 1)
        if inner.is_empty:
            continue
        # Only characters lying mostly inside the area count (neighbors that merely
        # touch the edge aren't leftovers), and the overlay label we printed ourselves
        # is expected.
        words = [
            w[4]
            for w in page.get_text("words", clip=inner)
            if _mostly_inside(w[:4], inner) and w[4] not in overlay_words
        ]
        if words:
            failures += 1
            text = " ".join(words)
            if len(text) > MAX_LEFTOVER_CHARS:
                text = text[: MAX_LEFTOVER_CHARS - 1] + "…"
            report.checks.append(
                Check(
                    Status.FAIL,
                    f"Page {page_no + 1}, redaction {number} of {len(rects)}: text remains under the box",
                    f'Found "{text}" ({describe_area(page, rect)}).',
                )
            )
    if not failures:
        report.checks.append(Check(Status.PASS, f"Page {page_no + 1}: no text under {len(rects)} redaction(s)"))

    _check_shapes(report, page, rects)

    image_problems = _image_problems(doc, page, rects)
    if image_problems:
        report.checks.append(
            Check(Status.FAIL, f"Page {page_no + 1}: image content remains under a redaction", "; ".join(image_problems))
        )
    elif any(True for _ in _images_under(page, rects)):
        report.checks.append(Check(Status.PASS, f"Page {page_no + 1}: image areas under redactions are blank"))


CURVED_SHARE = 0.25  # a curved shape with this much of it under a box is likely letters or ink


def _check_shapes(report: VerificationReport, page: pymupdf.Page, rects) -> None:
    """Shapes that cross a box keep their whole outline in the file (by default only shapes
    fully under a box are removed; security review 2026-11-21: "text drawn as outlines, drawn
    signatures, charts"). Curved shapes largely under a box (letters drawn as outlines, a drawn
    signature) fail; others (table lines, frames) are a warning."""
    try:
        drawings = page.get_drawings()
    except (RuntimeError, ValueError) as exc:
        report.checks.append(Check(Status.FAIL, f"Page {page.number + 1}: shapes couldn't be read to check them",
                                   str(exc)))
        return
    curved, crossing = 0, 0
    boxes = [pymupdf.Rect(r) for r in rects]
    for d in drawings:
        area = pymupdf.Rect(d["rect"])
        if area.is_empty and area.width == 0 and area.height == 0:
            continue
        padded = area + (-0.5, -0.5, 0.5, 0.5)  # (straight lines have an empty rect)
        for box in boxes:
            if not padded.intersects(box):
                continue
            if _same_box(area, box) and d.get("fill") is not None:
                break  # (the box's own fill)
            if box.contains(area):
                break  # (inside: removed, or our own label)
            has_curves = any(item[0] == "c" for item in d.get("items", ()))
            if not has_curves and area.contains(box):
                break  # (a background or frame around the box: nothing of it is under the box but a plain area)
            if has_curves and \
                    (area & box).get_area() >= CURVED_SHARE * max(area.get_area(), 1e-6):
                curved += 1
            else:
                crossing += 1
            break
    advice = "Apply the redactions again with “Also remove shapes that touch the box” turned on."
    if curved:
        report.checks.append(Check(
            Status.FAIL, f"Page {page.number + 1}: {curved} curved shape(s) partly under a box are still in the file",
            "They keep their whole outline: text drawn as outlines or a drawn signature could still be read. "
            + advice))
    if crossing:
        report.checks.append(Check(
            Status.WARN, f"Page {page.number + 1}: {crossing} shape(s) cross a box and keep their whole outline",
            "Usually table lines or frames. If a chart, a drawing or outlined text crosses the box, " + advice[0].lower()
            + advice[1:]))


def _same_box(a: pymupdf.Rect, b: pymupdf.Rect, tolerance: float = 1.0) -> bool:
    return all(abs(p - q) <= tolerance for p, q in zip(tuple(a), tuple(b)))


def _mostly_inside(bbox, area: pymupdf.Rect) -> bool:
    box = pymupdf.Rect(bbox)
    if box.is_empty:
        return False
    overlap = box & area
    return overlap.get_area() >= 0.5 * box.get_area()


def _images_under(page: pymupdf.Page, rects):
    for info in page.get_image_info(xrefs=True):
        bbox = pymupdf.Rect(info["bbox"])
        for rect in rects:
            if bbox.intersects(rect):
                yield info, rect


def _image_problems(doc: pymupdf.Document, page: pymupdf.Page, rects) -> list[str]:
    problems = []
    for info, rect in _images_under(page, rects):
        xref = info.get("xref") or 0
        if not xref:
            continue  # inline image: already covered by the rendered-content removal
        try:
            pix = pymupdf.Pixmap(doc, xref)
        except (RuntimeError, ValueError) as exc:
            # (a picture that can't be read can't be shown blank: security review 2026-11-21)
            problems.append(f"image {xref} couldn't be read to check it ({exc})")
            continue
        # Map the redaction rect into image pixel space via the image's placement matrix.
        inv = ~pymupdf.Matrix(info["transform"])
        unit = (pymupdf.Rect(rect) & pymupdf.Rect(info["bbox"])) * inv
        unit.normalize()
        # PyMuPDF's image transform works in top-left page coordinates, so unit-square
        # y maps straight to pixel rows (row 0 = top). Off-center tests guard this.
        x0 = max(0, int(unit.x0 * pix.width) + 2)
        x1 = min(pix.width, int(unit.x1 * pix.width) - 2)
        y0 = max(0, int(unit.y0 * pix.height) + 2)
        y1 = min(pix.height, int(unit.y1 * pix.height) - 2)
        if x1 <= x0 or y1 <= y0:
            continue
        step_x = max(1, (x1 - x0) // 40)
        step_y = max(1, (y1 - y0) // 40)
        first = pix.pixel(x0, y0)
        for y in range(y0, y1, step_y):
            if any(_differs(pix.pixel(x, y), first) for x in range(x0, x1, step_x)):
                problems.append(f"image {xref}")
                break
    return problems


def _differs(a, b, tolerance: int = 3) -> bool:
    return any(abs(p - q) > tolerance for p, q in zip(a, b))
