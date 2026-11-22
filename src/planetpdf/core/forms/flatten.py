"""Flatten form fields into page content (values become ordinary page text)."""

from __future__ import annotations

from planetpdf.core.document import PdfSession
from planetpdf.core.forms.xfa import remove_xfa


def flatten_forms(session: PdfSession) -> int:
    """Draw every form field's current appearance into its page and remove the fields.

    Other annotations (comments, stamps, redaction marks) are left as they are.
    Returns the number of widgets flattened. Not undoable at the core level: the UI
    treats it as a commit point.
    """
    doc = session.doc
    count = sum(1 for page in doc for _ in page.widgets())
    if not count:
        return 0
    doc.bake(annots=False, widgets=True)
    remove_xfa(doc)  # an XFA copy of the form would otherwise still claim the fields exist
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "AcroForm")[0] != "null":
        doc.xref_set_key(catalog, "AcroForm", "null")
    session.mark_modified()
    return count
