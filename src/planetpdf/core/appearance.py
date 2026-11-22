"""Annotations with our own appearance (stamps, seals, placed signatures).

Each such annotation is drawn from a small one-page *source* PDF (vector text with
subset fonts, or an image with transparency). The source is stored inside the
annotation (private key ``/PPDFSource``) so the item can be moved, resized or
flattened later, even after the file has been saved and reopened.

Rules learned the hard way (CLAUDE.md): ``add_stamp_annot`` changes the rect, so set it
again; adding/deleting pages invalidates ``Page`` objects; on a rotated page the
appearance must be rotated by +page.rotation to display upright.
"""

from __future__ import annotations

import pymupdf

SOURCE_KEY = "PPDFSource"


def finish_pdf(doc: pymupdf.Document) -> bytes:
    """Serialize a generated source PDF: subset fonts (whole fonts are megabytes)."""
    try:
        doc.subset_fonts()
    except Exception:  # fontTools missing or font can't be subset: keep the full font
        pass
    return doc.tobytes(garbage=4, deflate=True)


def source_size(pdf: bytes) -> tuple[float, float]:
    with pymupdf.open(stream=pdf, filetype="pdf") as src:
        rect = src[0].rect
    return rect.width, rect.height


def rotate_pdf(pdf: bytes, angle: float) -> bytes:
    """The source page turned by ``angle`` degrees (any angle; bounds grow to fit)."""
    if angle % 360 == 0:
        return pdf
    src = pymupdf.open(stream=pdf, filetype="pdf")
    width, height = src[0].rect.width, src[0].rect.height
    import math

    rad = math.radians(angle)
    new_w = abs(width * math.cos(rad)) + abs(height * math.sin(rad))
    new_h = abs(width * math.sin(rad)) + abs(height * math.cos(rad))
    out = pymupdf.open()
    page = out.new_page(width=new_w, height=new_h)
    page.show_pdf_page(page.rect, src, 0, rotate=angle)
    return finish_pdf(out)


def _embed_form(doc: pymupdf.Document, pdf: bytes) -> tuple[int, float, float]:
    """Turn the source PDF's page into a Form XObject inside ``doc``: returns (xref, w, h).

    A temporary page is added (so PyMuPDF copies fonts/images across) and deleted
    again; callers must re-fetch any ``Page`` objects afterwards.
    """
    src = pymupdf.open(stream=pdf, filetype="pdf")
    width, height = src[0].rect.width, src[0].rect.height
    tmp = doc.new_page(width=width, height=height)
    tmp.show_pdf_page(tmp.rect, src, 0)
    contents = b"\n".join(doc.xref_stream(x) or b"" for x in tmp.get_contents())
    kind, value = doc.xref_get_key(tmp.xref, "Resources")
    resources = value if kind == "dict" else doc.xref_object(int(value.split()[0]), compressed=True)
    doc.delete_page(tmp.number)
    form = doc.get_new_xref()
    doc.update_object(
        form, f"<< /Type /XObject /Subtype /Form /BBox [0 0 {width:.3f} {height:.3f}] /Resources {resources} >>"
    )
    doc.update_stream(form, contents)
    return form, width, height


def _store_source(doc: pymupdf.Document, pdf: bytes) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< /Type /PPDFSource >>")
    doc.update_stream(xref, pdf)
    return xref


def read_source(doc: pymupdf.Document, annot_xref: int) -> bytes | None:
    kind, value = doc.xref_get_key(annot_xref, SOURCE_KEY)
    if kind != "xref":
        return None
    return doc.xref_stream(int(value.split()[0]))


def add_appearance_annot(
    doc: pymupdf.Document,
    page_index: int,
    rect: tuple[float, float, float, float],
    upright_pdf: bytes,
    *,
    name: str,
    title: str = "",
    contents: str = "",
) -> int:
    """Add a /Stamp annotation showing ``upright_pdf`` in ``rect`` (unrotated page
    coordinates; the content shows upright to the reader). Returns the annot xref."""
    rotation = doc[page_index].rotation
    appearance, _w, _h = _embed_form(doc, rotate_pdf(upright_pdf, rotation))
    source = _store_source(doc, upright_pdf)
    page = doc[page_index]  # re-fetch: _embed_form added and deleted a page
    annot = page.add_stamp_annot(pymupdf.Rect(rect))
    annot.set_rect(pymupdf.Rect(rect))  # add_stamp_annot imposes its own aspect ratio
    annot.set_info(title=title, content=contents)
    xref = annot.xref
    doc.xref_set_key(xref, "AP", f"<< /N {appearance} 0 R >>")
    doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(name))
    doc.xref_set_key(xref, "Name", "/PlanetPDF")
    doc.xref_set_key(xref, SOURCE_KEY, f"{source} 0 R")
    return xref


def flatten_source(doc: pymupdf.Document, page_index: int, rect, upright_pdf: bytes, *, overlay: bool = True) -> None:
    """Draw ``upright_pdf`` into the page content at ``rect`` (unrotated coordinates),
    exactly where and how the annotation showed it. ``overlay=False`` puts it
    underneath the existing content (e.g. a watermark behind the text)."""
    page = doc[page_index]
    src = pymupdf.open(stream=upright_pdf, filetype="pdf")
    page.show_pdf_page(pymupdf.Rect(rect), src, 0, rotate=page.rotation, keep_proportion=False, overlay=overlay)
