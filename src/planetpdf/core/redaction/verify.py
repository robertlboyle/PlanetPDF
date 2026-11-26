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
    thumbs = [page.number + 1 for page in doc if doc.xref_get_key(page.xref, "Thumb")[0] != "null"]
    if thumbs:  # (a small picture of the page as it was before; security review 2026-11-25)
        report.checks.append(Check(Status.FAIL, "Page thumbnails remain", f"Page(s) {thumbs[:10]} keep a thumbnail "
                                   "picture, which can show the page as it was before redacting."))
    if remaining:
        report.checks.append(
            Check(Status.FAIL, "Unapplied redaction marks remain", f"{remaining} mark(s) were not applied.")
        )


def _texts_outside_pages(doc: pymupdf.Document):
    """(place, text) for everything a file can carry besides its pages' text: document
    information, XMP, bookmarks, attachments (names, descriptions, and contents), comments, and
    form fields. A term found here was never "an unmarked occurrence on a page"."""
    for key, value in (doc.metadata or {}).items():
        if value:
            yield f"document information ({key})", value
    yield "XMP metadata", doc.get_xml_metadata() or ""
    for _level, title, *_rest in doc.get_toc(simple=True):
        yield "a bookmark", title
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        yield "an attachment's name", " ".join(str(info.get(k) or "") for k in ("name", "filename", "ufilename"))
        yield "an attachment's description", info.get("description") or ""
        try:
            yield f"attachment {info.get('filename') or name}", _as_text(doc.embfile_get(name))
        except (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase):
            yield f"attachment {name} (unreadable)", ""
    for page in doc:
        for annot in page.annots():
            info = annot.info
            yield f"a comment on page {page.number + 1}", " ".join(info.get(k) or "" for k in ("content", "title",
                                                                                                 "subject"))
            if annot.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                try:
                    yield f"a file attached on page {page.number + 1}", _as_text(annot.get_file())
                except (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase):
                    pass
        for widget in page.widgets():
            yield f"a form field on page {page.number + 1}", " ".join(
                str(v) for v in (widget.field_value, widget.field_label) if v not in (None, "", False, True))


def _as_text(data: bytes) -> str:
    if data[:2] in (b"\xfe\xff", b"\xff\xfe"):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8", "replace") + "\n" + data.decode("latin-1")


def _outside_pages(doc: pymupdf.Document, term: str, whole_word: bool) -> list[str]:
    """Where outside the pages' text ``term`` is (case ignored; a short term only as a whole word)."""
    pattern = re.compile((r"(?<![^\W_])" + re.escape(term) + r"(?![^\W_])") if whole_word else re.escape(term),
                         re.IGNORECASE)
    return list(dict.fromkeys(place for place, text in _texts_outside_pages(doc) if text and pattern.search(text)))


def _check_term(report, term, doc, mu_pages, py_pages, blobs, areas_by_page) -> None:
    label = f'Term "{_mask(term)}"'
    if len(term) < MIN_TERM_LENGTH:
        # (too short to look for in page text and the file's bytes without false alarms; outside
        # the pages, where a hit is never a word that only happens to match: security review
        # 2026-11-25, "XQZ" stayed in the subject)
        places = _outside_pages(doc, term, whole_word=True)
        if places:
            report.checks.append(Check(Status.FAIL, f"{label} (short) is still in the file",
                                       "In " + ", ".join(places[:4]) + ("…" if len(places) > 4 else "") + "."))
        else:
            report.checks.append(Check(Status.PASS, f"{label} (short)", "Not in the file's information, bookmarks, "
                                       "attachments, comments, or fields; on the pages, covered by the area checks."))
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
        # The unmarked occurrence explains the term in the pages' own data, not elsewhere in the
        # file (security review 2026-11-25: an attachment kept it, and this check never ran).
        places = _outside_pages(doc, term, whole_word=False)
        if places:
            report.checks.append(Check(Status.FAIL, f"{label} can still be extracted",
                                       "In " + ", ".join(places[:4]) + ("…" if len(places) > 4 else "") + "."))
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


def _points_on(item, steps: int = 32):
    """Points along a path piece: a straight segment ("l") or a curve ("c")."""
    if item[0] == "l":
        p, q = item[1], item[2]
        return [pymupdf.Point(p.x + (q.x - p.x) * t / steps, p.y + (q.y - p.y) * t / steps) for t in range(steps + 1)]
    p0, c1, c2, p3 = item[1], item[2], item[3], item[4]
    out = []
    for n in range(steps + 1):
        t = n / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append(pymupdf.Point(a * p0.x + b * c1.x + c * c2.x + d * p3.x, a * p0.y + b * c1.y + c * c2.y + d * p3.y))
    return out


def _piece_kind(item, box: pymupdf.Rect) -> str | None:
    """What a path piece leaves under ``box``: None (nothing: it doesn't reach inside), "plain"
    (a level or upright line, or a rectangle's edge: table lines, frames, rules), or "shape"
    (a curve or a slanted line: a drawn signature, letters drawn as outlines, a chart)."""
    inner = box + (0.5, 0.5, -0.5, -0.5)
    kind = item[0]
    if kind == "re" or (kind == "qu" and item[1].is_rectangular):
        rect = item[1] if kind == "re" else item[1].rect
        return "plain" if rect.intersects(inner) and not rect.contains(box) else None
    if kind == "qu":
        q = item[1]
        edges = [("l", q.ul, q.ur), ("l", q.ur, q.lr), ("l", q.lr, q.ll), ("l", q.ll, q.ul)]
        kinds = [_piece_kind(e, box) for e in edges]
        return "shape" if "shape" in kinds else ("plain" if "plain" in kinds else None)
    if kind not in ("l", "c"):
        return None
    if not any(inner.contains(p) for p in _points_on(item)):
        return None
    if kind == "l":
        dx, dy = abs(item[2].x - item[1].x), abs(item[2].y - item[1].y)
        if dx < 0.5 or dy < 0.5:
            return "plain"
    return "shape"


def _check_shapes(report: VerificationReport, page: pymupdf.Page, rects) -> None:
    """Shapes that reach under a box keep their whole outline in the file (by default only shapes
    fully under a box are removed). A curve or a slanted line under a box fails: a signature drawn
    as lines, a zig-zag, letters drawn as outlines can be read from it (security review 2026-11-21,
    then 2026-11-25: straight segments crossing the box only warned, and a zig-zag around the box
    wasn't checked at all). Level and upright lines and rectangle edges (table lines, frames) warn."""
    try:
        drawings = page.get_drawings()
    except (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase) as exc:
        report.checks.append(Check(Status.FAIL, f"Page {page.number + 1}: shapes couldn't be read to check them",
                                   str(exc)))
        return
    shapes, plain = 0, 0
    boxes = [pymupdf.Rect(r) for r in rects]
    for d in drawings:
        area = pymupdf.Rect(d["rect"])
        padded = area + (-0.5, -0.5, 0.5, 0.5)  # (straight lines have an empty rect)
        found = None
        for box in boxes:
            if not padded.intersects(box):
                continue
            if _same_box(area, box) and d.get("fill") is not None:
                continue  # (the box's own fill)
            if box.contains(area) and not area.is_empty:
                continue  # (inside: removed, or our own label)
            kinds = {_piece_kind(item, box) for item in d.get("items", ())}
            if "shape" in kinds:
                found = "shape"
                break
            if "plain" in kinds:
                found = "plain"
        if found == "shape":
            shapes += 1
        elif found == "plain":
            plain += 1
    advice = "Apply the redactions again with “Also remove shapes that touch the box” turned on."
    if shapes:
        report.checks.append(Check(
            Status.FAIL, f"Page {page.number + 1}: {shapes} curved or slanted shape(s) reaching under a box are "
            "still in the file",
            "They keep their whole outline: a drawn signature, a drawing, or text drawn as outlines could still be "
            "read. " + advice))
    if plain:
        report.checks.append(Check(
            Status.WARN, f"Page {page.number + 1}: {plain} straight line(s) or frame(s) cross a box and keep their "
            "whole outline",
            "Usually table lines or frames. If a chart or a drawing crosses the box, " + advice[0].lower()
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
