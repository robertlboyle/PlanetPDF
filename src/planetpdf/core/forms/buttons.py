"""Push buttons and image fields (Prepare Form ▸ Button / Image, Acrobat; 2026-10-03).

A **button** shows a caption and does one thing when clicked: clear the form
(``reset``), print (``print``) or open a web page (``url:<address>``). These are standard
PDF actions (/ResetForm, /Named /Print, /URI), so other viewers do the same.

An **image field** is a push button with no caption whose picture the person filling the
form chooses (Acrobat's image field: its click runs ``event.target.buttonImportIcon()``).
PlanetPDF marks ours with ``/PPDFImage true`` and recognizes Acrobat's by that script.
The chosen picture becomes the button's icon (/MK /I) and its appearance, fitted inside
the box and upright on turned pages.
"""

from __future__ import annotations

import pymupdf

from planetpdf.core.document import PdfError, PdfSession

IMAGE_KEY = "PPDFImage"
IMPORT_ICON = "event.target.buttonImportIcon();"
ACTIONS = {"": "Nothing", "reset": "Clear the form", "print": "Print", "url": "Open a web page"}


def button_action(doc: pymupdf.Document, xref: int) -> str:
    """"" (none), "reset", "print", "url:<address>", "image", "barcode" (``barcodes``) or
    "other" (some other action)."""
    from planetpdf.core.forms.barcodes import BARCODE_KEY

    if doc.xref_get_key(xref, IMAGE_KEY)[1] == "true":
        return "image"
    if doc.xref_get_key(xref, BARCODE_KEY)[0] == "string":
        return "barcode"
    kind, value = doc.xref_get_key(xref, "A/S")
    if kind == "null":
        return ""
    if value == "/ResetForm":
        return "reset"
    if value == "/Named" and doc.xref_get_key(xref, "A/N")[1] == "/Print":
        return "print"
    if value == "/URI":
        return "url:" + _string(doc, xref, "A/URI")
    if value == "/JavaScript" and "buttonImportIcon" in _string(doc, xref, "A/JS"):
        return "image"
    return "other"


def _string(doc: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = doc.xref_get_key(xref, key)
    if kind == "xref":  # (a script in a stream)
        return (doc.xref_stream(int(value.split()[0])) or b"").decode("latin-1", "replace")
    return value if kind == "string" else ""


def set_button_action(doc: pymupdf.Document, xref: int, action: str) -> None:
    from planetpdf.core.forms.barcodes import BARCODE_KEY

    if action == "other":
        return  # (keep another program's action as it is)
    doc.xref_set_key(xref, IMAGE_KEY, "null")
    if action != "barcode":
        doc.xref_set_key(xref, BARCODE_KEY, "null")
    if action == "barcode":  # (a click does nothing: its picture is its value; barcodes.set_settings marks it)
        doc.xref_set_key(xref, "A", "null")
        doc.xref_set_key(xref, "MK/TP", "1")
    elif action == "reset":
        doc.xref_set_key(xref, "A", "<< /S /ResetForm >>")
    elif action == "print":
        doc.xref_set_key(xref, "A", "<< /S /Named /N /Print >>")
    elif action.startswith("url:"):
        address = action[4:].strip()
        if not address:
            raise PdfError("Type the web address the button opens.")
        if "://" not in address and not address.startswith("mailto:"):
            address = "https://" + address
        doc.xref_set_key(xref, "A", f"<< /S /URI /URI {pymupdf.get_pdf_str(address)} >>")
    elif action == "image":
        doc.xref_set_key(xref, "A", f"<< /S /JavaScript /JS {pymupdf.get_pdf_str(IMPORT_ICON)} >>")
        doc.xref_set_key(xref, IMAGE_KEY, "true")
        doc.xref_set_key(xref, "MK/TP", "1")  # (icon only)
    else:
        doc.xref_set_key(xref, "A", "null")


PICTURE_KEY = "PPDFPicture"  # the chosen picture's file bytes (drawn again after a move)


def has_image(doc: pymupdf.Document, xref: int) -> bool:
    return doc.xref_get_key(xref, "MK/I")[0] == "xref"


def stored_picture(doc: pymupdf.Document, xref: int) -> bytes | None:
    kind, value = doc.xref_get_key(xref, PICTURE_KEY)
    if kind != "xref":
        return None
    return doc.xref_stream(int(value.split()[0])) or None


def set_image(session: PdfSession, page_index: int, xref: int, picture: bytes | None) -> None:
    """The picture (PNG/JPEG bytes) shown by an image field, fitted into its box (None =
    empty again: a plain box saying "Click to choose a picture")."""
    from planetpdf.core.appearance import _embed_form, rotate_pdf

    doc = session.doc
    page = doc[page_index]
    rect = None
    for widget in page.widgets():
        if widget.xref == xref:
            rect = widget.rect
            break
    if rect is None:
        raise PdfError("That image field isn't on this page.")
    shown = (rect * page.rotation_matrix).normalize()
    src = pymupdf.open()
    sheet = src.new_page(width=shown.width, height=shown.height)
    sheet.draw_rect(sheet.rect, color=None, fill=(1, 1, 1))
    if picture:
        try:
            sheet.insert_image(sheet.rect + (1, 1, -1, -1), stream=picture, keep_proportion=True)
        except (RuntimeError, ValueError) as exc:
            raise PdfError(f"That picture couldn't be read: {exc}") from exc
    else:
        sheet.insert_textbox(sheet.rect + (2, 0, -2, 0), "\n" * max(0, int(shown.height / 24)) + "Click to choose a picture",
                             fontsize=min(9, max(5, shown.height / 4)), color=(0.45, 0.45, 0.45), align=1)
    sheet.draw_rect(sheet.rect, color=(0.35, 0.35, 0.35), width=1)
    data = src.tobytes(deflate=True)
    src.close()
    form, _w, _h = _embed_form(doc, rotate_pdf(data, page.rotation))
    doc.xref_set_key(xref, "AP", f"<< /N {form} 0 R >>")
    doc.xref_set_key(xref, "MK/I", f"{form} 0 R" if picture else "null")
    doc.xref_set_key(xref, "MK/TP", "1")
    if picture:
        if stored_picture(doc, xref) != picture:
            holder = doc.get_new_xref()
            doc.update_object(holder, "<< /Type /PPDFPicture >>")
            doc.update_stream(holder, picture)
            doc.xref_set_key(xref, PICTURE_KEY, f"{holder} 0 R")
    else:
        doc.xref_set_key(xref, PICTURE_KEY, "null")
    session.mark_modified()
