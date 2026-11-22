"""Document sanitizing offered alongside redaction. All options default to on."""

from __future__ import annotations

from dataclasses import dataclass, fields

import pymupdf

from planetpdf.core.redaction.ocg import remove_hidden_layers

# Markup annotations ("comments"). Widgets (form fields) and links are handled separately.
_COMMENT_TYPES = {
    pymupdf.PDF_ANNOT_TEXT,
    pymupdf.PDF_ANNOT_FREE_TEXT,
    pymupdf.PDF_ANNOT_LINE,
    pymupdf.PDF_ANNOT_SQUARE,
    pymupdf.PDF_ANNOT_CIRCLE,
    pymupdf.PDF_ANNOT_POLYGON,
    pymupdf.PDF_ANNOT_POLY_LINE,
    pymupdf.PDF_ANNOT_HIGHLIGHT,
    pymupdf.PDF_ANNOT_UNDERLINE,
    pymupdf.PDF_ANNOT_SQUIGGLY,
    pymupdf.PDF_ANNOT_STRIKE_OUT,
    pymupdf.PDF_ANNOT_STAMP,
    pymupdf.PDF_ANNOT_CARET,
    pymupdf.PDF_ANNOT_INK,
    pymupdf.PDF_ANNOT_POPUP,
    pymupdf.PDF_ANNOT_FILE_ATTACHMENT,
    pymupdf.PDF_ANNOT_SOUND,
}


@dataclass
class SanitizeOptions:
    metadata: bool = True
    xmp_metadata: bool = True
    bookmarks: bool = True
    embedded_files: bool = True
    javascript: bool = True
    hidden_layers: bool = True
    comments: bool = True
    form_values: bool = True
    structure: bool = True  # (tags and alternate text; security review 2026-11-21)

    def any(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


LABELS = {
    "metadata": ("Document information", "Title, author, subject, keywords, creator, and dates."),
    "xmp_metadata": ("XMP metadata", "The XML metadata packet (often a copy of the above, plus history)."),
    "bookmarks": ("Bookmarks", "The outline shown in the bookmarks panel."),
    "embedded_files": ("Attachments", "Embedded files and file-attachment annotations."),
    "javascript": ("JavaScript", "Document, page, and form-field scripts, including field formatting."),
    "hidden_layers": ("Hidden layers", "Content in layers that are turned off. It can't be searched, so it's easy to miss."),
    "comments": ("Comments and markup", "Notes, highlights, drawings, and stamps that haven't been flattened."),
    "form_values": ("Form field values", "Clears every field's value and default value, including XFA form data."),
    "structure": ("Tags and alternate text", "The accessibility tags, with alternate text for pictures and "
                  "replacement text: they can describe what was redacted. Screen readers then read the "
                  "pages without tags."),
}


@dataclass
class SanitizeResult:
    removed: dict[str, int]


def sanitize(doc: pymupdf.Document, options: SanitizeOptions) -> SanitizeResult:
    removed: dict[str, int] = {}
    catalog = doc.pdf_catalog()

    if options.hidden_layers:
        removed["hidden_layers"] = remove_hidden_layers(doc)

    if options.comments:
        count = 0
        for page in doc:
            for annot in [a for a in page.annots() if a.type[0] in _COMMENT_TYPES]:
                page.delete_annot(annot)
                count += 1
        removed["comments"] = count

    if options.form_values:
        removed["form_values"] = _clear_form_values(doc)

    if options.embedded_files:
        count = 0
        for name in list(doc.embfile_names()):
            doc.embfile_del(name)
            count += 1
        for page in doc:
            for annot in [a for a in page.annots() if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT]:
                page.delete_annot(annot)
                count += 1
        removed["embedded_files"] = count

    if options.javascript:
        removed["javascript"] = _remove_javascript(doc)

    if options.bookmarks:
        removed["bookmarks"] = len(doc.get_toc(simple=True))
        doc.set_toc([])
        doc.xref_set_key(catalog, "Outlines", "null")

    if options.metadata:
        removed["metadata"] = sum(1 for v in (doc.metadata or {}).values() if v)
        doc.set_metadata({})
        info_kind, info_value = doc.xref_get_key(-1, "Info")
        if info_kind == "xref":
            doc.xref_set_key(-1, "Info", "null")

    if options.xmp_metadata:
        removed["xmp_metadata"] = 1 if doc.get_xml_metadata() else 0
        doc.del_xml_metadata()
        doc.xref_set_key(catalog, "Metadata", "null")

    if options.structure:
        removed["structure"] = _remove_structure(doc)

    # Any sanitizing also drops page thumbnails and piece-info caches, which can hold
    # old renderings of the page.
    for page in doc:
        for key in ("Thumb", "PieceInfo"):
            if doc.xref_get_key(page.xref, key)[0] != "null":
                doc.xref_set_key(page.xref, key, "null")
    return SanitizeResult(removed)


def _remove_structure(doc: pymupdf.Document) -> int:
    """The tag tree (alternate text /Alt, replacement text /ActualText, expansions /E live in it)
    and the keys pointing into it. Returns 1 if the file had one."""
    catalog = doc.pdf_catalog()
    had = doc.xref_get_key(catalog, "StructTreeRoot")[0] != "null"
    for key in ("StructTreeRoot", "MarkInfo"):
        if doc.xref_get_key(catalog, key)[0] != "null":
            doc.xref_set_key(catalog, key, "null")
    for page in doc:
        if doc.xref_get_key(page.xref, "StructParents")[0] != "null":
            doc.xref_set_key(page.xref, "StructParents", "null")
        for annot_xref in [a[0] for a in page.annot_xrefs()]:
            if doc.xref_get_key(annot_xref, "StructParent")[0] != "null":
                doc.xref_set_key(annot_xref, "StructParent", "null")
    return int(had)


def _clear_form_values(doc: pymupdf.Document) -> int:
    count = 0
    parents: set[int] = set()
    for page in doc:
        for widget in page.widgets():
            if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                continue
            is_button = widget.field_type in (
                pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
                pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
            )
            if is_button:
                widget.field_value = False
            else:
                # PyMuPDF ignores an empty value on update() (the old /V and appearance
                # stay), so write a space to force a blank appearance, then clear /V.
                widget.field_value = " "
            widget.update()  # regenerates the appearance, so the old value isn't drawn either
            if not is_button:
                doc.xref_set_key(widget.xref, "V", "()")
            doc.xref_set_key(widget.xref, "DV", "null")  # default values can hold data too
            kind, value = doc.xref_get_key(widget.xref, "Parent")
            if kind == "xref":
                parents.add(int(value.split()[0]))
            count += 1
    for xref in parents:
        doc.xref_set_key(xref, "DV", "null")
        if doc.xref_get_key(xref, "FT")[1] != "/Btn":
            doc.xref_set_key(xref, "V", "null")
        else:
            doc.xref_set_key(xref, "V", "/Off")
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "AcroForm/XFA")[0] != "null":
        doc.xref_set_key(catalog, "AcroForm/XFA", "null")  # XFA datasets hold the values too
    return count


def _remove_javascript(doc: pymupdf.Document) -> int:
    count = 0
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "Names/JavaScript")[0] != "null":
        doc.xref_set_key(catalog, "Names/JavaScript", "null")
        count += 1
    for key in ("OpenAction", "AA"):
        kind, value = doc.xref_get_key(catalog, key)
        if kind != "null" and (key == "AA" or "JavaScript" in _deref_text(doc, kind, value)):
            doc.xref_set_key(catalog, key, "null")
            count += 1
    for page in doc:
        if doc.xref_get_key(page.xref, "AA")[0] != "null":
            doc.xref_set_key(page.xref, "AA", "null")
            count += 1
        for annot in page.annots():
            for key in ("AA", "A"):
                kind, value = doc.xref_get_key(annot.xref, key)
                if kind != "null" and (key == "AA" or "JavaScript" in _deref_text(doc, kind, value)):
                    doc.xref_set_key(annot.xref, key, "null")
                    count += 1
    # Anything left (e.g. actions on field parents, named-destination actions): empty the
    # script so no JavaScript text survives, even in unreferenced-but-kept objects.
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "S")[1] == "/JavaScript":
                doc.xref_set_key(xref, "JS", "()")
                count += 1
        except (RuntimeError, ValueError):
            continue
    return count


def _deref_text(doc: pymupdf.Document, kind: str, value: str) -> str:
    if kind == "xref":
        try:
            return doc.xref_object(int(value.split()[0]))
        except (RuntimeError, ValueError):
            return ""
    return value
