"""Digital (cryptographic) signatures with pyHanko: sign (PAdES) and validate.

Signing always appends an incremental update to the *saved* file, so it's the last
write (CLAUDE.md): callers save pending edits first. Any later change shows up as a
modification after signing (and full rewrites invalidate the signature entirely).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.signing.providers.base import SigningProvider, TrustStore
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside


@dataclass
class SignOptions:
    field_name: str = ""  # empty: a new unique name
    reason: str = ""
    location: str = ""
    contact: str = ""
    timestamp_url: str = ""  # RFC 3161 server; empty = no timestamp (PAdES B-B)
    signature_image: bytes | None = None  # optional visual signature shown in the box
    timestamper: object | None = None  # tests: a pyHanko TimeStamper to use instead of the URL
    # Save as Other ▸ Certified PDF (Acrobat; 2026-10-02): the author's certification
    # signature, the first in the file, saying which later changes are allowed:
    # "none", "forms" (fill in forms and sign) or "comments" (that, plus comments)
    certify: str = ""


CERTIFY_LEVELS = {
    "none": "No changes allowed",
    "forms": "Filling in forms and signing",
    "comments": "Filling in forms, signing, and commenting",
}


def _pdf_box(doc: pymupdf.Document, page_index: int, rect) -> tuple[float, float, float, float]:
    """PyMuPDF unrotated top-left rect -> PDF user-space box (x1, y1, x2, y2) for pyHanko."""
    page = doc[page_index]
    # page.transformation_matrix maps PDF space -> PyMuPDF (unrotated) space.
    r = pymupdf.Rect(rect) * ~page.transformation_matrix
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


def _has_signature(path: Path) -> bool:
    with pymupdf.open(path) as doc:
        for page in doc:
            for widget in page.widgets():
                if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and widget.field_value:
                    return True
    return False


def _unique_field_name(path: Path) -> str:
    with pymupdf.open(path) as doc:
        existing = {w.field_name for page in doc for w in page.widgets()}
    n = 1
    while f"Signature{n}" in existing:
        n += 1
    return f"Signature{n}"


def sign_pdf(
    source: Path,
    target: Path,
    provider: SigningProvider,
    page_index: int,
    rect,
    options: SignOptions,
) -> Path:
    """Sign ``source`` into ``target`` (may be the same file) with a visible box at
    ``rect`` (unrotated page coordinates) on ``page_index``; ``rect`` None = an invisible
    signature (e.g. a certification without a box)."""
    from pyhanko import stamp
    from pyhanko.pdf_utils import images
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.layout import SimpleBoxLayoutRule, AxisAlignment, Margins
    from pyhanko.sign import fields, signers, timestamps

    source, target = Path(source), Path(target)
    with pymupdf.open(source) as doc:
        if doc.needs_pass or doc.is_encrypted:
            raise PdfError("Password-protected PDFs can't be signed yet. Save an unprotected copy first.")
        if not 0 <= page_index < doc.page_count:
            raise PdfError("Page out of range.")
        box = _pdf_box(doc, page_index, rect) if rect is not None else None
    name = options.field_name or _unique_field_name(source)
    if options.certify and options.certify not in CERTIFY_LEVELS:
        raise PdfError(f"Unknown certification level {options.certify!r}.")
    if options.certify and _has_signature(source):
        raise PdfError("This document is already signed. A certification must be the first signature.")

    timestamper = options.timestamper
    if timestamper is None and options.timestamp_url:
        timestamper = timestamps.HTTPTimeStamper(options.timestamp_url)

    lines = ["Certified by %(signer)s" if options.certify else "Digitally signed by %(signer)s", "Date: %(ts)s"]
    if options.reason:
        lines.append(f"Reason: {options.reason}")
    if options.location:
        lines.append(f"Location: {options.location}")
    background = None
    if options.signature_image:
        from PIL import Image

        background = images.PdfImage(Image.open(io.BytesIO(options.signature_image)))
    style = stamp.TextStampStyle(
        stamp_text="\n".join(lines),
        background=background,
        background_opacity=1.0,
        background_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MIN, y_align=AxisAlignment.ALIGN_MID, margins=Margins.uniform(2)
        ),
        border_width=1,
        timestamp_format="%Y-%m-%d %H:%M %Z",
    )
    certify = {}
    if options.certify:
        mdp = {"none": fields.MDPPerm.NO_CHANGES, "forms": fields.MDPPerm.FILL_FORMS,
               "comments": fields.MDPPerm.ANNOTATE}[options.certify]
        certify = {"certify": True, "docmdp_permissions": mdp}
    meta =signers.PdfSignatureMetadata(
        field_name=name,
        md_algorithm="sha256",
        subfilter=fields.SigSeedSubFilter.PADES,
        reason=options.reason or None,
        location=options.location or None,
        contact_info=options.contact or None,
        **certify,
    )
    pdf_signer = signers.PdfSigner(meta, signer=provider.signer(), stamp_style=style, timestamper=timestamper)

    tmp = temp_path_beside(target)
    try:
        with open(source, "rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            # (box None: an invisible signature field)
            fields.append_signature_field(writer, sig_field_spec=fields.SigFieldSpec(name, box=box, on_page=page_index))
            with open(tmp, "wb") as out:
                pdf_signer.sign_pdf(writer, output=out)
        replace_with_retry(tmp, target)
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"Signing failed: {exc}") from exc
    finally:
        remove_quietly(tmp)
    return target


# --- validation --------------------------------------------------------------------------------


class Verdict(Enum):
    VALID = "valid"  # intact, trusted signer, no disallowed changes
    VALID_UNTRUSTED = "valid_untrusted"  # intact, but the signer isn't in the trusted roots
    MODIFIED = "modified"  # intact, but the document was changed afterwards
    INVALID = "invalid"  # the signed content was altered / the signature doesn't verify


@dataclass
class SignatureStatus:
    field_name: str
    signer: str
    signing_time: datetime | None
    timestamped: bool
    intact: bool
    trusted: bool
    covers_whole_file: bool
    changes_after: str  # "", "form filling", "annotations", "other changes"
    verdict: Verdict
    summary: str
    certified: str = ""  # a certification signature: its CERTIFY_LEVELS key ("" = an ordinary signature)
    revocation_checked: bool = False  # asked online whether the signer's certificate was revoked
    revoked: bool = False


def validate_signatures(path: Path, trust: TrustStore | None = None, *,
                        check_revocation: bool = False) -> list[SignatureStatus]:
    """Check every signature in the file. Offline unless ``check_revocation`` (Preferences ▸
    Signatures, security review 2026-11-21): then the certificate authorities' revocation lists /
    OCSP servers named in the certificates are asked whether the signer's certificate was revoked
    (where they can't be reached, that is said, not counted as revoked)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    roots = trust.roots() if trust is not None else []
    intermediates = trust.intermediates() if trust is not None else []
    results = []
    with open(path, "rb") as f:
        try:
            reader = PdfFileReader(f, strict=False)
            signatures = list(reader.embedded_signatures)
        except Exception as exc:
            raise PdfError(f"The signatures couldn't be read: {exc}") from exc
        for sig in signatures:
            context = ValidationContext(trust_roots=roots, other_certs=intermediates,
                                        allow_fetching=check_revocation, revocation_mode="soft-fail")
            try:
                status = validate_pdf_signature(sig, context)
            except Exception as exc:
                results.append(
                    SignatureStatus(sig.field_name, "Unknown", None, False, False, False, False, "", Verdict.INVALID,
                                    f"The signature couldn't be checked: {exc}")
                )
                continue
            results.append(_summarize(sig.field_name, status, getattr(sig, "docmdp_level", None),
                                      check_revocation))
    return results


def _summarize(field_name: str, status, mdp=None, revocation_checked: bool = False) -> SignatureStatus:
    from pyhanko.sign.diff_analysis import ModificationLevel
    from pyhanko.sign.validation.status import SignatureCoverageLevel

    cert = status.signing_cert
    try:
        signer = cert.subject.native.get("common_name") or cert.subject.human_friendly
    except Exception:
        signer = "Unknown signer"
    intact = bool(status.intact and status.valid)
    trusted = bool(getattr(status, "trusted", False))
    coverage = getattr(status, "coverage", None)
    whole = coverage == SignatureCoverageLevel.ENTIRE_FILE
    level = getattr(status, "modification_level", None)
    changes = ""
    if not whole and level is not None:
        changes = {
            ModificationLevel.NONE: "",
            ModificationLevel.LTA_UPDATES: "",
            ModificationLevel.FORM_FILLING: "form filling",
            ModificationLevel.ANNOTATIONS: "annotations",
            ModificationLevel.OTHER: "other changes",
        }.get(level, "other changes")
    docmdp_ok = getattr(status, "docmdp_ok", True)
    ts = getattr(status, "timestamp_validity", None)
    when = getattr(status, "signer_reported_dt", None)
    notes = []
    if ts is not None and not (getattr(ts, "intact", False) and getattr(ts, "valid", False)):
        # (a timestamp that doesn't check out proves nothing: security review 2026-11-21)
        notes.append("Its timestamp doesn't check out, so the signing time is only what the signer's computer said.")
        ts = None
    elif ts is not None and not getattr(ts, "trusted", False):
        notes.append("Its timestamp is from a timestamp server you don't trust.")
    if ts is not None and getattr(ts, "timestamp", None):
        when = ts.timestamp
    revoked = bool(getattr(status, "revoked", False))
    details = getattr(status, "revocation_details", None)

    # Rules: damaged -> INVALID; disallowed later changes -> MODIFIED; otherwise VALID
    # or VALID_UNTRUSTED depending on the trust store. Allowed later changes (form
    # filling, comments) are mentioned but don't change the verdict.
    allowed_note = f" Later changes ({changes}) are allowed by the signer." if changes in ("form filling", "annotations") else ""
    if not intact:
        verdict, summary = Verdict.INVALID, "INVALID: the signed content has been altered, or the signature is damaged."
    elif revoked:
        on = getattr(details, "revocation_date", None)
        verdict, summary = (Verdict.INVALID, "INVALID: the signer's certificate was revoked"
                            + (f" on {on:%B %d, %Y}." if on else ".") + " Don't rely on this signature.")
    elif changes == "other changes" or docmdp_ok is False:
        verdict, summary = Verdict.MODIFIED, "Signature intact, but the document was changed after signing in ways the signer didn't allow."
    elif trusted:
        verdict, summary = Verdict.VALID, "Valid: signed by a trusted certificate." + (allowed_note or " Not changed since signing.")
    else:
        verdict, summary = (
            Verdict.VALID_UNTRUSTED,
            "Signature intact" + (allowed_note or " and not changed since signing") + ", but the signer isn't in your trusted certificates.",
        )
    if verdict in (Verdict.VALID, Verdict.VALID_UNTRUSTED):
        notes.append("Revocation was checked online." if revocation_checked else
                     "Whether the certificate was revoked wasn't checked (Preferences ▸ Signatures).")
    if notes:
        summary += " " + " ".join(notes)
    certified = ""
    if mdp is not None:  # (a certification signature: the changes it allows)
        certified = {1: "none", 2: "forms", 3: "comments"}.get(int(getattr(mdp, "value", mdp)), "")
        if certified:
            allowed = {"none": "no changes allowed", "forms": "form filling and signing allowed",
                       "comments": "form filling, signing, and comments allowed"}[certified]
            summary = f"Certified ({allowed}). " + summary
    return SignatureStatus(field_name, signer, when, ts is not None, intact, trusted, whole, changes, verdict, summary,
                           certified, revocation_checked, revoked)
