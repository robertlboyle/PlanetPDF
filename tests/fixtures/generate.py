"""Programmatic test PDFs. Every fixture the test suite uses is built here.

Constants describe what was placed where, so tests can assert against them.
All coordinates are PDF points with the origin at the top left (PyMuPDF convention).
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

LETTER = pymupdf.paper_rect("letter")

# --- text PDF --------------------------------------------------------------------

SSN = "123-45-6789"
PHONE = "(555) 867-5309"
EMAIL = "john.public@example.com"
ACCOUNT = "0012345678901"
TEXT_LINES = [
    "Customer record",
    "Name: John Q. Public",
    f"SSN: {SSN}",
    f"Phone: {PHONE}",
    f"Email: {EMAIL}",
    f"Account: {ACCOUNT}",
    "This line contains no sensitive data.",
]
TEXT_PAGE_COUNT = 3


def make_text_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for page_no in range(TEXT_PAGE_COUNT):
        page = doc.new_page(width=LETTER.width, height=LETTER.height)
        y = 72
        for line in TEXT_LINES:
            page.insert_text((72, y), line, fontsize=12, fontname="helv")
            y += 20
        page.insert_text((72, LETTER.height - 36), f"Page {page_no + 1}", fontsize=9)
    doc.set_metadata({"title": "Text fixture", "author": "Fixture Author", "subject": SSN})
    doc.save(path)
    doc.close()
    return path


# --- image PDF --------------------------------------------------------------------

IMAGE_RECT = pymupdf.Rect(72, 72, 272, 272)  # where the image is drawn on the page
IMAGE_SIZE = 200  # image is 200x200 px, so 1 px == 1 pt on the page
IMAGE_MARK_RECT = pymupdf.IRect(50, 50, 150, 150)  # red square inside the (green) image, in image px
IMAGE_BASE_RGB = (0, 160, 0)
IMAGE_MARK_RGB = (220, 0, 0)


def _fixture_pixmap() -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, IMAGE_SIZE, IMAGE_SIZE), False)
    pix.set_rect(pix.irect, IMAGE_BASE_RGB)
    pix.set_rect(IMAGE_MARK_RECT, IMAGE_MARK_RGB)
    return pix


def make_image_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_image(IMAGE_RECT, stream=_fixture_pixmap().tobytes("png"))
    page.insert_text((72, 320), "Caption under the image", fontsize=11)
    doc.save(path)
    doc.close()
    return path


# --- vector PDF -------------------------------------------------------------------

# A redaction box used by vector tests, plus shapes relative to it.
VECTOR_REDACT_RECT = pymupdf.Rect(200, 200, 400, 400)
VECTOR_INSIDE = pymupdf.Rect(250, 250, 350, 350)  # fully inside the box
VECTOR_CROSSING = pymupdf.Rect(350, 150, 450, 250)  # partly overlaps the box
VECTOR_OUTSIDE = pymupdf.Rect(72, 500, 172, 600)  # well away from the box


def make_vector_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.draw_rect(VECTOR_INSIDE, color=(0, 0, 1), fill=(0.6, 0.6, 1))
    page.draw_rect(VECTOR_CROSSING, color=(1, 0, 0), fill=(1, 0.6, 0.6))
    page.draw_rect(VECTOR_OUTSIDE, color=(0, 0.5, 0), fill=(0.6, 1, 0.6))
    page.draw_line((72, 700), (540, 700), color=(0, 0, 0), width=2)
    doc.save(path)
    doc.close()
    return path


# --- AcroForm PDF -----------------------------------------------------------------

FORM_FIELDS = {
    "full_name": "text",
    "subscribe": "checkbox",
    "plan": "radio",  # two buttons: "basic", "premium"
    "state": "combobox",
    "services": "listbox",
    "start_date": "date",  # text field with AFDate_FormatEx JavaScript
}
FORM_RADIO_CHOICES = ["basic", "premium"]
FORM_STATE_CHOICES = ["NY", "NJ", "CT"]
FORM_SERVICE_CHOICES = ["Internet", "Phone", "TV"]
FORM_DATE_FORMAT = "mm/dd/yyyy"

_RADIO_FLAGS = 32768 | 16384  # Radio | NoToggleToOff


def _drop_keys(doc: pymupdf.Document, xref: int, keys: list[str]) -> None:
    """Really remove dictionary keys. (``xref_set_key(x, k, "null")`` leaves an explicit
    ``/k null`` entry, which the spec treats as absent but pypdf's field-name code
    doesn't.) Only for the simple values these fixtures use."""
    source = doc.xref_object(xref, compressed=True)
    for key in keys:
        source = re.sub(rf"/{key}(?=[\s/(\[<])\s*(\([^)]*\)|/[^\s/<>\[\]()]+|-?[\d.]+(?:\s+0\s+R)?|null)", "", source)
    doc.update_object(xref, source)


def _make_radio_group(doc: pymupdf.Document, name: str, kids: list[tuple[int, str]]) -> None:
    """Turn standalone checkbox widgets into kids of one radio-button field."""
    parent = doc.get_new_xref()
    kid_refs = " ".join(f"{xref} 0 R" for xref, _ in kids)
    doc.update_object(
        parent, f"<< /FT /Btn /Ff {_RADIO_FLAGS} /T ({name}) /V /Off /Kids [{kid_refs}] >>"
    )
    for xref, on_state in kids:
        _, normal = doc.xref_get_key(xref, "AP/N")
        doc.xref_set_key(xref, "AP/N", normal.replace("/Yes", f"/{on_state}"))
        _, down = doc.xref_get_key(xref, "AP/D")
        if down.startswith("<<"):
            doc.xref_set_key(xref, "AP/D", down.replace("/Yes", f"/{on_state}"))
        _drop_keys(doc, xref, ["T", "FT", "Ff", "V"])
        doc.xref_set_key(xref, "Parent", f"{parent} 0 R")

    catalog = doc.pdf_catalog()
    _, fields = doc.xref_get_key(catalog, "AcroForm/Fields")
    kid_xrefs = {xref for xref, _ in kids}
    refs = [int(x) for x in re.findall(r"(\d+) 0 R", fields) if int(x) not in kid_xrefs]
    refs.append(parent)
    doc.xref_set_key(catalog, "AcroForm/Fields", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")


def make_acroform_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    y = 72

    def add(widget: pymupdf.Widget, label: str, height: float = 20, width: float = 200) -> int:
        nonlocal y
        page.insert_text((72, y + 14), label, fontsize=11)
        widget.rect = pymupdf.Rect(200, y, 200 + width, y + height)
        xref = page.add_widget(widget).xref
        y += height + 16
        return xref

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "full_name"
    add(w, "Full name")

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    w.field_name = "subscribe"
    add(w, "Subscribe", height=16, width=16)

    # PyMuPDF 1.28 can't create radio buttons via add_widget, so build the group by
    # hand: checkboxes converted into kids of one radio parent field.
    kids = []
    for choice in FORM_RADIO_CHOICES:
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = f"plan_{choice}"
        kids.append(add(w, f"Plan: {choice}", height=16, width=16))
    _make_radio_group(doc, "plan", list(zip(kids, FORM_RADIO_CHOICES)))

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
    w.field_name = "state"
    w.choice_values = FORM_STATE_CHOICES
    add(w, "State")

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_LISTBOX
    w.field_name = "services"
    w.choice_values = FORM_SERVICE_CHOICES
    add(w, "Services", height=48)

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "start_date"
    w.script_format = f'AFDate_FormatEx("{FORM_DATE_FORMAT}");'
    add(w, "Start date")

    doc.save(path)
    doc.close()
    return path


# --- multi-revision PDF -------------------------------------------------------------

REV1_SECRET = "SECRET-REV1"
REV2_TEXT = "PUBLIC-REV2"


def make_multirevision_pdf(path: Path) -> Path:
    """Revision 1 shows REV1_SECRET; an incremental update replaces it with REV2_TEXT.

    Revision 1 is saved uncompressed, so the secret is still present in the file bytes
    even though the current revision no longer displays it. Note MuPDF writes text as a
    hex string (``<5345...>``), so byte scans must look for hex as well as literal forms.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_text((72, 72), f"Account note: {REV1_SECRET}", fontsize=12)
    doc.save(path, expand=255)
    doc.close()

    doc = pymupdf.open(path)
    page = doc[0]
    for rect in page.search_for(REV1_SECRET):
        page.add_redact_annot(rect)
    page.apply_redactions()
    page.insert_text((72, 100), f"Account note: {REV2_TEXT}", fontsize=12)
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


# --- encrypted PDF ------------------------------------------------------------------

ENCRYPTED_USER_PW = "open-sesame"
ENCRYPTED_OWNER_PW = "owner-secret"
ENCRYPTED_TEXT = "Encrypted fixture body"


def make_encrypted_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_text((72, 72), ENCRYPTED_TEXT, fontsize=12)
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=ENCRYPTED_USER_PW,
        owner_pw=ENCRYPTED_OWNER_PW,
    )
    doc.close()
    return path


# --- mixed page sizes -------------------------------------------------------------------

MIXED_SIZES = [(612, 792), (792, 612), (420, 595)]


def make_mixed_sizes_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for i, (w, h) in enumerate(MIXED_SIZES):
        page = doc.new_page(width=w, height=h)
        page.insert_text((36, 48), f"Page {i + 1}: {w}x{h}", fontsize=14)
    doc.save(path)
    doc.close()
    return path


# --- tricky redaction PDF --------------------------------------------------------------

HIDDEN_SSN = "987-65-4321"  # invisible text (render mode 3) under the visible SSN line
COMPLEX_SSN_Y = 100  # baseline of the SSN line on page 1
NOTE_TEXT = f"Customer SSN is {SSN}"  # sticky-note content overlapping the SSN
ROTATED_SECRET = "ROTATED-SECRET-77"
ALPHA_IMAGE_RECT = pymupdf.Rect(72, 200, 272, 400)


def make_redact_complex_pdf(path: Path) -> Path:
    doc = pymupdf.open()

    # Page 1: text with things layered over it.
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_text((72, COMPLEX_SSN_Y), f"SSN: {SSN}", fontsize=12)
    page.insert_text((72, COMPLEX_SSN_Y), f"     {HIDDEN_SSN}", fontsize=12, render_mode=3)
    page.insert_text((72, COMPLEX_SSN_Y + 24), f"Email: {EMAIL}", fontsize=12)
    page.insert_text((72, COMPLEX_SSN_Y + 48), f"Account: {ACCOUNT}", fontsize=12)
    page.insert_text((72, COMPLEX_SSN_Y + 72), "Keep this line intact.", fontsize=12)
    ssn_rect = page.search_for(SSN)[0]
    page.add_text_annot(ssn_rect.tl, NOTE_TEXT)
    email_rect = page.search_for(EMAIL)[0]
    page.insert_link({"kind": pymupdf.LINK_URI, "from": email_rect, "uri": f"mailto:{EMAIL}"})
    acct_rect = page.search_for(ACCOUNT)[0]
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "account_copy"
    w.field_value = ACCOUNT
    w.rect = pymupdf.Rect(acct_rect.x0, acct_rect.y0, acct_rect.x1 + 40, acct_rect.y1)
    page.add_widget(w)

    # Page 2: an image with transparency (soft mask), with text over it.
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, IMAGE_SIZE, IMAGE_SIZE), True)
    pix.set_rect(pix.irect, (*IMAGE_BASE_RGB, 255))
    pix.set_rect(IMAGE_MARK_RECT, (*IMAGE_MARK_RGB, 128))
    page.insert_image(ALPHA_IMAGE_RECT, stream=pix.tobytes("png"))
    page.insert_text((80, 220), f"Over image: {PHONE}", fontsize=11)

    # Page 3: rotated 90 degrees.
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_text((72, 72), ROTATED_SECRET, fontsize=14)
    page.insert_text((72, 100), "Rotated page, other text.", fontsize=12)
    page.set_rotation(90)

    doc.save(path)
    doc.close()
    return path


# --- sanitize PDF ------------------------------------------------------------------------

SANITIZE_SECRETS = {
    "metadata": "META-SECRET-1",
    "xmp_metadata": "XMP-SECRET-2",
    "bookmarks": "BOOKMARK-SECRET-3",
    "embedded_files": "ATTACH-SECRET-4",
    "javascript": "SCRIPT-SECRET-5",
    "comments": "COMMENT-SECRET-6",
    "form_values": "FIELD-SECRET-7",
    "hidden_layers": "LAYER-SECRET-8",
    "structure": "ALT-SECRET-9",
}
VISIBLE_LAYER_TEXT = "LAYER-VISIBLE-TEXT"
BODY_TEXT = "Ordinary body text stays."


def make_sanitize_pdf(path: Path) -> Path:
    s = SANITIZE_SECRETS
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_text((72, 72), BODY_TEXT, fontsize=12)

    doc.set_metadata({"title": s["metadata"], "author": "Fixture", "keywords": s["metadata"]})
    doc.set_xml_metadata(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:description>{s['xmp_metadata']}</dc:description>"
        "</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
    )
    doc.set_toc([[1, f"Chapter {s['bookmarks']}", 1]])
    doc.embfile_add("notes.txt", f"attachment body {s['embedded_files']}".encode(), filename="notes.txt")

    page.add_text_annot((300, 300), f"Reviewer note {s['comments']}")

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "secret_field"
    w.field_value = s["form_values"]
    w.rect = pymupdf.Rect(72, 400, 300, 420)
    page.add_widget(w)

    visible = doc.add_ocg("Visible layer", on=True)
    hidden = doc.add_ocg("Hidden layer", on=False)
    page.insert_text((72, 500), VISIBLE_LAYER_TEXT, fontsize=12, oc=visible)
    page.insert_text((72, 530), s["hidden_layers"], fontsize=12, oc=hidden)

    doc.save(path)
    doc.close()

    # Document-level JavaScript (PyMuPDF has no API for it): add after the first save so
    # the Names dictionary created by the attachment exists.
    doc = pymupdf.open(path)
    js = doc.get_new_xref()
    doc.update_object(js, f"<< /S /JavaScript /JS (app.alert\\('{s['javascript']}'\\);) >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names/JavaScript", f"<< /Names [(init) {js} 0 R] >>")
    # A tag tree whose picture has alternate text (Sanitize's "Tags and alternate text").
    tree, figure = doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(figure, f"<< /Type /StructElem /S /Figure /P {tree} 0 R /Alt ({s['structure']}) >>")
    doc.update_object(tree, f"<< /Type /StructTreeRoot /K [{figure} 0 R] >>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{tree} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


# --- scanned PDF (image-only pages) ---------------------------------------------------------

SCAN_LINES = [f"Scanned invoice SSN {SSN}", f"Contact {EMAIL}", "Total due 1250 dollars"]
SCAN_ROTATIONS = [0, 90, 180, 270]  # one page per rotation, each displayed upright


def _scan_pixmap() -> pymupdf.Pixmap:
    src = pymupdf.open()
    page = src.new_page(width=LETTER.width, height=LETTER.height)
    for i, line in enumerate(SCAN_LINES):
        page.insert_text((72, 100 + 30 * i), line, fontsize=16)
    return page.get_pixmap(dpi=200)


def make_scanned_pdf(path: Path) -> Path:
    """Image-only pages (like a scanner makes), one per rotation in SCAN_ROTATIONS.

    Each page stores the image sideways/upside down as needed and uses /Rotate so it
    *displays* upright, the way rotated scans usually arrive.
    """
    upright = _scan_pixmap()
    doc = pymupdf.open()
    for rotation in SCAN_ROTATIONS:
        swap = rotation in (90, 270)
        width, height = (LETTER.height, LETTER.width) if swap else (LETTER.width, LETTER.height)
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, pixmap=upright, rotate=rotation)
        page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


# --- advanced form PDF --------------------------------------------------------------------------

# Page 1 uses /Tabs /R; its widgets are *added* out of visual order, so tab order must
# come from positions, not from creation order.
TAB_ROW_ORDER = ["r1_left", "r1_right", "r2_left", "r2_right"]
SHARED_FIELD = "customer_ref"  # one text field with a widget on page 1 and page 2
READ_ONLY_FIELD = "account_id"
READ_ONLY_VALUE = "ACC-42"
REQUIRED_FIELD = "signature_name"
MULTI_LIST = "interests"
MULTI_CHOICES = ["Fiber", "Voice", "TV", "Security"]
PAIR_COMBO = "region"
PAIR_CHOICES = [["NE", "Northeast"], ["MW", "Midwest"], ["W", "West"]]


def _text_widget(page, name, rect, *, flags=0, value=None):
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.field_flags = flags
    if value is not None:
        w.field_value = value
    return page.add_widget(w)


def make_form_extra_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    positions = {
        "r1_left": (72, 72, 250, 92),
        "r1_right": (320, 72, 500, 92),
        "r2_left": (72, 120, 250, 140),
        "r2_right": (320, 120, 500, 140),
    }
    for name in ["r2_right", "r1_left", "r2_left", "r1_right"]:  # scrambled creation order
        _text_widget(page, name, positions[name])
    doc.xref_set_key(page.xref, "Tabs", "/R")
    _text_widget(page, SHARED_FIELD, (72, 200, 250, 220))
    _text_widget(page, READ_ONLY_FIELD, (72, 240, 250, 260), flags=1, value=READ_ONLY_VALUE)
    _text_widget(page, REQUIRED_FIELD, (72, 280, 250, 300), flags=2)

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_LISTBOX
    w.field_name = MULTI_LIST
    w.choice_values = MULTI_CHOICES
    w.field_flags = 1 << 21  # multi-select
    w.rect = pymupdf.Rect(72, 320, 250, 400)
    page.add_widget(w)

    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
    w.field_name = PAIR_COMBO
    w.choice_values = PAIR_CHOICES
    w.rect = pymupdf.Rect(72, 420, 250, 440)
    page.add_widget(w)

    page2 = doc.new_page(width=LETTER.width, height=LETTER.height)
    _text_widget(page2, "page2_first", (72, 72, 250, 92))
    doc.save(path)
    doc.close()

    # Second widget for the shared field: a kid of the same parent field.
    doc = pymupdf.open(path)
    first = next(w for w in doc[0].widgets() if w.field_name == SHARED_FIELD)
    parent = doc.get_new_xref()
    first_xref = first.xref
    doc.update_object(parent, f"<< /FT /Tx /T ({SHARED_FIELD}) /Kids [{first_xref} 0 R] >>")
    _drop_keys(doc, first_xref, ["T", "FT", "Ff", "V"])
    doc.xref_set_key(first_xref, "Parent", f"{parent} 0 R")
    page2 = doc[1]
    kid = page2.add_widget(_placeholder_text_widget((72, 120, 250, 140))).xref
    _drop_keys(doc, kid, ["T", "FT", "Ff", "V"])
    doc.xref_set_key(kid, "Parent", f"{parent} 0 R")
    doc.xref_set_key(parent, "Kids", f"[{first_xref} 0 R {kid} 0 R]")
    catalog = doc.pdf_catalog()
    _, fields = doc.xref_get_key(catalog, "AcroForm/Fields")
    refs = [int(x) for x in re.findall(r"(\d+) 0 R", fields) if int(x) not in (first_xref, kid)]
    refs.append(parent)
    doc.xref_set_key(catalog, "AcroForm/Fields", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")
    doc.saveIncr()
    doc.close()
    return path


def _placeholder_text_widget(rect):
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "tmp_kid"
    w.rect = pymupdf.Rect(rect)
    return w


# --- XFA PDFs -----------------------------------------------------------------------------------

XFA_DATA_NS = "http://www.xfa.org/schema/xfa-data/1.0/"
XFA_DATASETS = (
    f'<xfa:datasets xmlns:xfa="{XFA_DATA_NS}"><xfa:data><form1>'
    "<full_name/><subscribe>0</subscribe><plan/><state/><start_date/>"
    "</form1></xfa:data></xfa:datasets>"
)
XFA_TEMPLATE = '<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/"><subform name="form1"/></template>'
PURE_XFA_TEXT = "Please wait... If this message is not eventually replaced, your viewer can't display this form."


def _add_xfa(doc: pymupdf.Document) -> None:
    template = doc.get_new_xref()
    doc.update_object(template, "<<>>")
    doc.update_stream(template, XFA_TEMPLATE.encode())
    datasets = doc.get_new_xref()
    doc.update_object(datasets, "<<>>")
    doc.update_stream(datasets, XFA_DATASETS.encode())
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "AcroForm")[0] == "null":
        doc.xref_set_key(catalog, "AcroForm", "<< /Fields [] >>")
    doc.xref_set_key(catalog, "AcroForm/XFA", f"[(template) {template} 0 R (datasets) {datasets} 0 R]")


def make_xfa_hybrid_pdf(path: Path) -> Path:
    make_acroform_pdf(path)
    doc = pymupdf.open(path)
    _add_xfa(doc)
    doc.saveIncr()
    doc.close()
    return path


def make_xfa_pure_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 200), PURE_XFA_TEXT, fontsize=12)
    _add_xfa(doc)
    doc.xref_set_key(doc.pdf_catalog(), "NeedsRendering", "true")
    doc.save(path)
    doc.close()
    return path


def bytes_contain_text(raw: bytes, text: str) -> bool:
    """True if ``text`` appears in raw PDF bytes as a literal or a hex string."""
    literal = text.encode("latin-1")
    return literal in raw or literal.hex().encode() in raw.lower()


ALL_FIXTURES = {
    "text.pdf": make_text_pdf,
    "image.pdf": make_image_pdf,
    "vector.pdf": make_vector_pdf,
    "acroform.pdf": make_acroform_pdf,
    "multirevision.pdf": make_multirevision_pdf,
    "encrypted.pdf": make_encrypted_pdf,
    "mixed_sizes.pdf": make_mixed_sizes_pdf,
    "redact_complex.pdf": make_redact_complex_pdf,
    "sanitize.pdf": make_sanitize_pdf,
    "scanned.pdf": make_scanned_pdf,
    "form_extra.pdf": make_form_extra_pdf,
    "xfa_hybrid.pdf": make_xfa_hybrid_pdf,
    "xfa_pure.pdf": make_xfa_pure_pdf,
}
