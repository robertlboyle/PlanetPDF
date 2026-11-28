"""Document sanitizing offered alongside redaction. All options default to on."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

import pymupdf

from planetpdf.core.contents import replace_contents
from planetpdf.core.redaction.ocg import (
    _dict_entries,
    _page_resources_xref_key,
    _refs,
    remove_hidden_layers,
    tokenize,
)

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

# What a sanitize reports removed (the options, and the signatures when asked for).
REMOVED_LABELS = {**LABELS, "signatures": ("Digital signatures", "The signatures, so the file is saved as a whole new file.")}


@dataclass
class SanitizeResult:
    removed: dict[str, int]


# MuPDF's own errors (``FzErrorFormat`` for a broken reference…) aren't RuntimeErrors
# (security review 2026-11-25: Sanitize stopped on one in a file PlanetPDF had signed).
OBJECT_ERRORS = (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase)


def sanitize(doc: pymupdf.Document, options: SanitizeOptions, *, remove_signatures: bool = False) -> SanitizeResult:
    """Removes what ``options`` names. ``remove_signatures``: a signed document's signatures go
    too; the caller must then save it as a whole new file (``PdfSession.signatures_changed``), since
    a signed file saved the usual way only has the changes added, which keeps everything removed
    in its earlier, signed version (security review 2026-11-25)."""
    removed: dict[str, int] = {}
    catalog = doc.pdf_catalog()

    if remove_signatures:
        removed["signatures"] = _remove_signatures(doc)

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


def signed_fields(doc: pymupdf.Document) -> list[tuple[int, int]]:
    """Signature fields holding a signature (their own value, or their parent's)."""
    found = []
    for page in doc:
        for widget in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
            owner = widget.xref
            if doc.xref_get_key(owner, "V")[0] == "null":
                kind, value = doc.xref_get_key(owner, "Parent")
                owner = int(value.split()[0]) if kind == "xref" else 0
            if owner and doc.xref_get_key(owner, "V")[0] != "null":
                found.append((widget.xref, owner))
    return found


def _remove_signatures(doc: pymupdf.Document) -> int:
    """Each signature (the field's value) and its picture, the certification (/Perms), and
    /SigFlags. The fields stay, empty, ready to be signed again."""
    from planetpdf.core.forms.fields import draw_empty_signature

    found = signed_fields(doc)
    for widget_xref, owner in found:
        doc.xref_set_key(owner, "V", "null")
        draw_empty_signature(doc, widget_xref)  # (the old picture showed who signed, and when)
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "Perms")[0] != "null":
        doc.xref_set_key(catalog, "Perms", "null")
    if doc.xref_get_key(catalog, "AcroForm/SigFlags")[0] != "null":
        doc.xref_set_key(catalog, "AcroForm/SigFlags", "null")
    return len({owner for _w, owner in found})


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
    marked = _remove_marked_text(doc)
    return int(had) + marked


MARKED_TEXT_KEYS = (b"/Alt", b"/ActualText", b"/E")


def _plain_name(value: bytes) -> bytes:
    """A PDF name as it reads: ``/Act#75alText`` is ``/ActualText`` (security review 2026-11-27,
    finding 8: the escaped spelling was left by Sanitize)."""
    return re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), value)


def _strip_marked_text(data: bytes, depth_from: int = 1) -> tuple[bytes, int]:
    """Content with every /Alt, /ActualText, and /E (and its text) taken out of marked-content
    property lists (``/Span << /ActualText (…) >> BDC``): the only dictionaries in page content.
    ``depth_from``: how deep in dictionaries such a list starts (2 for a /Properties dictionary,
    whose own keys are the lists' names). The text may be a string or a reference to one."""
    cuts, depth = [], 0
    tokens = list(tokenize(data))
    for i, tok in enumerate(tokens):
        if tok.kind == "dict_start":
            depth += 1
        elif tok.kind == "dict_end":
            depth = max(0, depth - 1)
        elif depth >= depth_from and tok.kind == "name" and _plain_name(tok.value) in MARKED_TEXT_KEYS \
                and i + 1 < len(tokens):
            after = tokens[i + 1]
            if after.kind in ("str", "hex"):
                cuts.append((tok.start, after.end))
            elif after.kind == "num" and i + 3 < len(tokens) and tokens[i + 3].value == b"R":
                cuts.append((tok.start, tokens[i + 3].end))
    if not cuts:
        return data, 0
    out, pos = bytearray(), 0
    for start, end in cuts:
        out += data[pos:start] + b" "
        pos = end
    return bytes(out + data[pos:]), len(cuts)


def _strip_properties(doc: pymupdf.Document, owner: int, key: str) -> int:
    """The same keys in the named property lists of a resource dictionary (``/Properties``): lists
    kept as objects of their own, and lists written into the dictionary itself (security review
    2026-11-27, finding 8: ``/Properties << /P1 << /ActualText (…) >> >>`` was left)."""
    count = 0
    for _name, xref in _dict_entries(doc, owner, f"{key}/Properties").items():
        for k in ("Alt", "ActualText", "E"):
            if doc.xref_get_key(xref, k)[0] != "null":
                doc.xref_set_key(xref, k, "null")
                count += 1
    kind, value = doc.xref_get_key(owner, f"{key}/Properties")
    if kind == "dict":
        data, cut = _strip_marked_text(value.encode("utf-8"), depth_from=2)
        if cut:
            doc.xref_set_key(owner, f"{key}/Properties", data.decode("utf-8"))
            count += cut
    elif kind == "xref" and _refs(value):
        target = _refs(value)[0]
        data, cut = _strip_marked_text(doc.xref_object(target).encode("utf-8"), depth_from=2)
        if cut:
            doc.update_object(target, data.decode("utf-8"))
            count += cut
    return count


def _remove_marked_text(doc: pymupdf.Document) -> int:
    """Alternate text, replacement text, and expansions written into the pages' own content and
    their pictures' and forms' content, not only in the tag tree (security review 2026-11-25:
    they were still there after Sanitize). Returns how many were removed."""
    removed, seen = 0, set()

    def forms(owner: int, key: str) -> None:
        nonlocal removed
        removed += _strip_properties(doc, owner, key)
        for _name, xref in _dict_entries(doc, owner, f"{key}/XObject").items():
            if xref in seen or doc.xref_get_key(xref, "Subtype")[1] != "/Form":
                continue
            seen.add(xref)
            data, count = _strip_marked_text(doc.xref_stream(xref) or b"")
            if count:
                doc.update_stream(xref, data)
                removed += count
            forms(xref, "Resources")

    for page in doc:
        data, count = _strip_marked_text(page.read_contents())
        if count:
            replace_contents(doc, page.number, data)  # (never in place: streams can be shared)
            removed += count
        owner, key = _page_resources_xref_key(doc, page.xref)
        forms(owner, key)
    # Then every other object in the file that has resources or is a form (comments' looks,
    # patterns, Type 3 fonts): not only what the pages lead to (security review 2026-11-27).
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Resources")[0] != "null":
                removed += _strip_properties(doc, xref, "Resources")
            if xref not in seen and doc.xref_is_stream(xref) and (
                    doc.xref_get_key(xref, "Subtype")[1] == "/Form" or doc.xref_get_key(xref, "PatternType")[1] == "1"):
                seen.add(xref)
                data, count = _strip_marked_text(doc.xref_stream(xref) or b"")
                if count:
                    doc.update_stream(xref, data)
                    removed += count
        except Exception:  # noqa: BLE001 (a free or broken object, FzErrorFormat: nothing of it is shown either)
            continue
    return removed


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
        except OBJECT_ERRORS:
            continue
    return count


def _deref_text(doc: pymupdf.Document, kind: str, value: str) -> str:
    if kind == "xref":
        try:
            return doc.xref_object(int(value.split()[0]))
        except OBJECT_ERRORS:
            return ""
    return value
