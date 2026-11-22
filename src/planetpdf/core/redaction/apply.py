"""Apply redaction marks and write a new, verified file.

Works on an in-memory, decrypted copy of the current state (including unsaved
changes), so the open document is untouched unless the user chose to overwrite it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import pymupdf

from planetpdf.core.cert_encryption import CertificateProtection
from planetpdf.core.document import PdfError, PdfSession, Protection, protect_pdf_file
from planetpdf.core.progress import Progress, report
from planetpdf.core.redaction.marks import Mark, list_marks, mark_text
from planetpdf.core.redaction.sanitize import LABELS, SanitizeOptions, SanitizeResult, sanitize
from planetpdf.core.redaction.verify import Check, Status, VerificationReport, verify_redaction
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

Color = tuple[float, float, float]


class RedactionCanceled(PdfError):
    pass


@dataclass
class RedactionOptions:
    fill: Color = (0.0, 0.0, 0.0)
    overlay_text: str = ""  # e.g. "REDACTED"; empty = no text
    remove_touching_graphics: bool = False  # default: only shapes fully inside the box


@dataclass
class RedactionResult:
    output: Path
    report: VerificationReport
    pages_redacted: int
    marks_applied: int
    overlapping_removed: dict[str, int] = field(default_factory=dict)
    sanitized: SanitizeResult | None = None


def overlay_text_color(fill: Color) -> Color:
    """Black or white, whichever reads better on the fill."""
    luminance = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
    return (0.0, 0.0, 0.0) if luminance > 0.5 else (1.0, 1.0, 1.0)


def _overlay_fontsize(rect: pymupdf.Rect, text: str) -> float:
    if not text:
        return 11
    by_height = rect.height * 0.7
    by_width = rect.width / max(1, len(text)) * 1.7  # Helvetica caps are ~0.6 em wide
    return max(4.0, min(12.0, by_height, by_width))


def apply_marks(doc: pymupdf.Document, options: RedactionOptions,
                progress: Progress | None = None) -> tuple[int, int, dict[str, int]]:
    """Apply every /Redact annotation in ``doc``. Returns (pages, marks, removed-overlaps).
    ``progress``: "Applying redactions: page x of y" (counting the pages with marks)."""
    removed = {"annotations": 0, "links": 0, "form fields": 0}
    graphics = (
        pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
        if options.remove_touching_graphics
        else pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED
    )
    text_color = overlay_text_color(options.fill)
    pages = marks = 0
    marked = [page.number for page in doc if page.first_annot is not None
              and any(True for _ in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]))]
    for n, number in enumerate(marked):
        if not report(progress, n, len(marked), "Applying redactions: marked page"):
            raise RedactionCanceled()
        page = doc[number]
        redacts = list(page.annots(types=[pymupdf.PDF_ANNOT_REDACT]))
        rects = [pymupdf.Rect(a.rect) for a in redacts]
        for annot in redacts:
            page.delete_annot(annot)

        # apply_redactions() leaves other annotations, links and form fields alone, and
        # they can carry the very data being removed (a comment, a mailto: link, a field
        # value). Delete anything that overlaps a redaction.
        for annot in [a for a in page.annots() if any(a.rect.intersects(r) for r in rects)]:
            page.delete_annot(annot)
            removed["annotations"] += 1
        for widget in [w for w in page.widgets() if any(w.rect.intersects(r) for r in rects)]:
            page.delete_widget(widget)
            removed["form fields"] += 1
        for link in [l for l in page.get_links() if any(pymupdf.Rect(l["from"]).intersects(r) for r in rects)]:
            page.delete_link(link)
            removed["links"] += 1

        for rect in rects:
            text = options.overlay_text.strip()
            page.add_redact_annot(
                rect,
                text=text or None,
                fontname="helv",
                fontsize=_overlay_fontsize(rect, text),
                align=pymupdf.TEXT_ALIGN_CENTER,
                fill=options.fill,
                text_color=text_color,
                cross_out=False,
            )
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
            graphics=graphics,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
        pages += 1
        marks += len(rects)
    return pages, marks, removed


def verification_inputs(session: PdfSession, marks: list[Mark],
                        progress: Progress | None = None) -> tuple[list[str], list[tuple[int, tuple]]]:
    """Terms (text under each mark, captured before applying) and areas to check."""
    terms: list[str] = []
    areas: list[tuple[int, tuple]] = []
    for n, mark in enumerate(marks):
        if not report(progress, n, len(marks), "Reading the text under redaction mark"):
            raise RedactionCanceled()
        text = mark_text(session, mark)
        if text:
            terms.append(text)
            # Also check each line separately: a wrapped match is extracted per line.
            if len(mark.rects) > 1:
                page = session.doc[mark.page]
                terms.extend(page.get_textbox(pymupdf.Rect(r)).strip() for r in mark.rects)
        areas.extend((mark.page, r) for r in mark.rects)
    return [t for t in terms if t], areas


def redact_to_file(
    session: PdfSession,
    target: Path,
    options: RedactionOptions,
    sanitize_options: SanitizeOptions | None = None,
    *,
    extra_terms: list[str] | None = None,
    progress: Progress | None = None,
    protection: Protection | None = None,
) -> RedactionResult:
    """Apply all marks (+ optional sanitizing) and write ``target``; then verify it.

    If ``target`` is the session's own file, the session is reopened on the result.
    ``protection``: password-protect the result (after verifying: checks of encrypted
    bytes would prove nothing).
    ``progress(done, total, stage)`` (``core.progress``) counts every step: the text
    under mark x of y, marked page x of y, then the verification's own counts. Cancel
    works until the new file is being written (``RedactionCanceled``; nothing written).

    The work is ``redact_copy`` (on a copy of the document: the UI runs it in a background
    thread) then ``finish_redaction`` (the file put in place).
    """
    report(progress, 0, 0, f"Making a working copy of the {session.page_count} pages…")
    data = session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
    finished = redact_copy(data, target, options, sanitize_options, extra_terms=extra_terms, progress=progress,
                           protection=protection)
    return finish_redaction(session, finished)


class _Pages:
    """Enough of a session for ``list_marks`` / ``mark_text``: a document of its own."""

    def __init__(self, doc: pymupdf.Document) -> None:
        self.doc = doc


@dataclass
class FinishedRedaction:
    """``redact_copy``'s result: the checked (and protected) file waiting beside its target."""

    file: Path
    result: RedactionResult
    protection: Protection | None
    plain: bytes | None  # (its unencrypted bytes, when certificate-encrypted)

    def discard(self) -> None:
        remove_quietly(self.file)


def redact_copy(
    data: bytes,
    target: Path,
    options: RedactionOptions,
    sanitize_options: SanitizeOptions | None = None,
    *,
    extra_terms: list[str] | None = None,
    progress: Progress | None = None,
    protection: Protection | None = None,
) -> FinishedRedaction:
    """The redaction, on a copy: ``data`` = the document's bytes, unencrypted (no open
    session is used, so it can run in a background thread: user 2026-10-23). Writes and
    checks a new file beside ``target``; ``finish_redaction`` puts it in place."""
    target = Path(target).resolve()
    before = pymupdf.open(stream=data, filetype="pdf")
    try:
        marks = list_marks(_Pages(before))
        if not marks:
            raise PdfError("There are no redaction marks to apply.")
        terms, areas = verification_inputs(_Pages(before), marks, progress)
    finally:
        before.close()
    terms += [t for t in (extra_terms or []) if t]

    work = pymupdf.open(stream=data, filetype="pdf")
    tmp = None
    try:
        pages, applied, removed = apply_marks(work, options, progress)
        sanitized = None
        if sanitize_options is not None and sanitize_options.any():
            kinds = [LABELS[f.name][0].lower() for f in fields(sanitize_options) if getattr(sanitize_options, f.name)]
            report(progress, 0, 0, f"Removing hidden information ({len(kinds)} kinds: {', '.join(kinds)})…")
            sanitized = sanitize(work, sanitize_options)

        report(progress, 0, 0, f"Writing the new file ({work.page_count} pages)…")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_path_beside(target)
        # Fonts it only names, from this computer (cut down to the letters left on the
        # pages after redacting, so nothing redacted comes back through a font).
        from planetpdf.core.font_embedding import embed_quietly

        embed_quietly(work)
        # Full rewrite, maximum garbage collection, no incremental update: nothing
        # from earlier revisions or unreferenced objects survives.
        work.save(
            str(tmp),
            garbage=4,
            clean=True,
            deflate=True,
            incremental=False,
            encryption=pymupdf.PDF_ENCRYPT_NONE,
        )
        work.close()
        # verified as written, before any encryption (and before it replaces anything)
        verification = verify_redaction(tmp, terms, areas, overlay_text=options.overlay_text.strip(),
                                        progress=progress)
        verification.output = target
        plain = tmp.read_bytes() if isinstance(protection, CertificateProtection) else None
        if protection is not None:
            report(progress, 0, 0, "Protecting the new file…")
            protect_pdf_file(tmp, protection)
    except BaseException:
        if tmp is not None:
            remove_quietly(tmp)
        raise
    finally:
        if not work.is_closed:
            work.close()
    result = RedactionResult(target, verification, pages, applied, removed, sanitized)
    _protection_checks(result, protection)
    return FinishedRedaction(tmp, result, protection, plain)


def finish_redaction(session: PdfSession, finished: FinishedRedaction) -> RedactionResult:
    """Put ``redact_copy``'s file in place: the target, or the session's own file (then the
    session reopens on it)."""
    result, protection = finished.result, finished.protection
    try:
        if result.output == session.path:
            if finished.plain is not None:
                session.adopt_file(finished.file, cert=protection, plain=finished.plain)
            else:
                session.adopt_file(finished.file, protection.open_password if protection else None)
        else:
            replace_with_retry(finished.file, result.output)
    finally:
        finished.discard()
    return result


def _protection_checks(result: RedactionResult, protection: Protection | None) -> None:
    verification = result.report
    if isinstance(protection, CertificateProtection):
        verification.checks.append(Check(
            Status.PASS, "Encrypted with certificates",
            "The file was checked before it was encrypted (AES-256). It opens only for: "
            + ", ".join(protection.names) + "."))
    elif protection is not None:
        verification.checks.append(Check(
            Status.PASS, "Protected with a password",
            "The file was checked before it was encrypted (AES-256)."
            + ("" if protection.open_password else " Anyone can open it; the permissions password limits changes.")))
