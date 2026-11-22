"""Check Compliance (PDF Standards tool, user 2026-10-04): check a document against
PDF/A-2b, PDF/X-3 or PDF/E-1 and say what's a problem.

Only real problems fail (user: "It should only fail if there is a problem"):

* **N/A**: the file doesn't use what the check is about (no comments, no scripts, no
  transparency…): nothing can be wrong, so it passes.
* **Added when saved**: what the standard itself requires of every file (its
  identification, the color profile, XMP metadata, PDF/X's title and trim boxes). A plain
  file never has them; Save as PDF/A / X / E adds them, so they aren't problems in the
  content.
* **Fail**: something in the file the standard doesn't allow (fonts not embedded,
  forbidden actions, comments that don't print, encryption…). Fix and Save fixes them in
  a copy (``fix_copy``, then Ghostscript's conversion) and checks the copy again.

It checks the rules that most often fail in practice; it isn't a certified validator
(veraPDF is one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from planetpdf.core.progress import report

PROFILES = {
    "pdfa": "PDF/A-2b (archiving)",
    "pdfx": "PDF/X-3 (printing press)",
    "pdfe": "PDF/E-1 (engineering)",
}
SHORT = {"pdfa": "PDF/A", "pdfx": "PDF/X", "pdfe": "PDF/E"}
PASS, NA, ADDED, WARN, FAIL = "pass", "n/a", "added", "warn", "fail"
LABELS = {PASS: "Pass", NA: "N/A", ADDED: "Added when saved", WARN: "Warning", FAIL: "Fail"}
# actions PDF/A forbids (ISO 19005-2, 6.5.1); PDF/X forbids the scripted / media ones
FORBIDDEN_ACTIONS = {"/Launch", "/Sound", "/Movie", "/ResetForm", "/ImportData", "/JavaScript", "/Hide",
                     "/SetOCGState", "/Rendition", "/Trans", "/GoTo3DView"}
BANNED_X = {"/JavaScript", "/Launch", "/Movie", "/Sound"}
MEDIA_ANNOTS = {"Sound", "Movie", "Screen", "3D", "RichMedia", "FileAttachment"}
MEDIA_REMOVED = {"Sound", "Movie", "Screen", "3D", "RichMedia"}
NOT_PRINTED_OK = {"Popup", "Link"}  # (annotations that needn't be printable)
_ACTION = re.compile(r"/S\s*/([A-Za-z0-9]+)")
_JS = re.compile(r"/JS[\s(<\[0-9]")
_ALPHA = re.compile(r"/(?:CA|ca)\s+([0-9]*\.?[0-9]+)")
_BLEND = re.compile(r"/BM\s*/([A-Za-z]+)")
_SOFT_MASK = re.compile(r"/SMask\s*<<")  # (a graphics state's soft mask; an image's /SMask is a reference)
_RGB_OPS = re.compile(rb"(?<![A-Za-z])(rg|RG)(?![A-Za-z])")


@dataclass
class Check:
    title: str
    status: str = PASS
    details: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)  # 0-based
    fix: str = ""  # how Fix and Save deals with a failure

    def fail(self, detail: str, page: int | None = None, status: str = FAIL) -> None:
        if self.status != FAIL:
            self.status = status
        if detail not in self.details and len(self.details) < 20:
            self.details.append(detail)
        if page is not None and page not in self.pages:
            self.pages.append(page)

    def not_used(self, detail: str) -> None:
        """Nothing in the file for this check to look at: N/A (a pass)."""
        if self.status == PASS:
            self.status = NA
            self.details = [detail]

    def added(self, detail: str) -> None:
        if self.status == PASS:
            self.status = ADDED
            self.details = [detail]


@dataclass
class Result:
    profile: str
    checks: list[Check]
    claimed: str  # what the file says it is ("" = no claim)

    @property
    def passed(self) -> bool:
        """No problems: what's left is added when it's saved in the standard."""
        return all(c.status != FAIL for c in self.checks)

    @property
    def complete(self) -> bool:
        """The file is already a finished one of the standard (nothing to add either)."""
        return self.passed and all(c.status != ADDED for c in self.checks)

    def summary(self) -> str:
        name, short = PROFILES[self.profile], SHORT[self.profile]
        failed = [c for c in self.checks if c.status == FAIL]
        warned = [c for c in self.checks if c.status == WARN]
        if failed:
            return f"{len(failed)} problem{'s' if len(failed) > 1 else ''} to fix for {name}."
        look = f" ({len(warned)} to look at)" if warned else ""
        if self.complete:
            return f"This file meets {name}{look}."
        return (f"No problems found{look}. Saving as {short} adds what every {short} file needs, "
                f"and the saved copy meets {name}.")

    def as_text(self, name: str) -> str:
        lines = [f"Compliance check of {name}: {PROFILES[self.profile]}",
                 f"The file says it meets: {self.claimed or 'no standard'}", self.summary(), ""]
        for c in self.checks:
            lines.append(f"[{LABELS[c.status].upper()}] {c.title}")
            lines += [f"    {d}" for d in c.details]
            if c.pages:
                lines.append("    Pages: " + ", ".join(str(p + 1) for p in sorted(c.pages)))
            if c.status in (FAIL, WARN) and c.fix:
                lines.append(f"    How to fix: {c.fix}")
        return "\n".join(lines) + "\n"


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # (a damaged object)
        return ("null", "null")


def claimed_standards(doc: pymupdf.Document) -> str:
    """What the file says it conforms to, from its XMP and Info ("PDF/A-2b, PDF/X-3")."""
    xmp = doc.get_xml_metadata() or ""
    found = []
    part = re.search(r"pdfaid:part(?:=[\"']|>)\s*(\d)", xmp)
    if part:
        level = re.search(r"pdfaid:conformance(?:=[\"']|>)\s*([A-Za-z])", xmp)
        found.append(f"PDF/A-{part.group(1)}{level.group(1).lower() if level else ''}")
    kind, value = _key(doc, _info_xref(doc), "GTS_PDFXVersion")
    if kind == "string" and value:
        found.append(value)
    match = re.search(r"pdfe:ISO_PDFEVersion(?:=[\"']|>)\s*([^\"'<]+)", xmp)
    if match:
        found.append(match.group(1).strip())
    return ", ".join(found)


def _info_xref(doc: pymupdf.Document) -> int:
    kind, value = _key(doc, -1, "Info")
    return int(value.split()[0]) if kind == "xref" else 0


def _output_intents(doc: pymupdf.Document) -> str:
    kind, value = _key(doc, doc.pdf_catalog(), "OutputIntents")
    if kind == "null":
        return ""
    text = value
    for ref in re.findall(r"(\d+) 0 R", value):
        text += doc.xref_object(int(ref), compressed=True)
    return text


def check(doc: pymupdf.Document, profile: str, progress=None) -> Result:
    if profile not in PROFILES:
        raise ValueError(profile)
    a, x, e = profile == "pdfa", profile == "pdfx", profile == "pdfe"
    short = SHORT[profile]
    claimed = claimed_standards(doc)
    checks: list[Check] = []

    claim = Check(f"The file is marked as {short}")
    if short not in claimed:
        claim.added(f"Saving as {short} marks it.")
    checks.append(claim)
    if a:  # (PDF/X-3 identifies itself in the Info dictionary; XMP isn't required there)
        meta = Check("XMP metadata")
        if not (doc.get_xml_metadata() or "").strip():
            meta.added(f"Saving as {short} adds it.")
        checks.append(meta)
    if a or x:
        intent = Check("Color profile (output intent)")
        if ("GTS_PDFA1" if a else "GTS_PDFX") not in _output_intents(doc):
            intent.added(f"Saving as {short} adds one ({'sRGB' if a else 'CMYK'}).")
        checks.append(intent)
        crypt = Check("Not password protected")
        if doc.metadata.get("encryption") or doc.is_encrypted:
            crypt.fail(f"{short} files can't be password protected or encrypted.")
        checks.append(crypt)
    if x:
        title = Check("Document title")
        if not (doc.metadata.get("title") or "").strip():
            title.added("Saving as PDF/X uses the file name (or set a title in File ▸ Properties first).")
        checks.append(title)

    fonts = Check("All fonts are embedded")
    actions = Check("No scripts or forbidden actions")
    media = Check("No sound, video, 3D, or attached files")
    annots = Check("Comments and fields print as they look")
    transparency = Check("No transparency")
    rgb = Check("No RGB colors (CMYK, gray, or color-managed only)")
    boxes = Check("Every page has a trim box")

    seen_fonts: set[int] = set()
    annotations = 0
    total = doc.page_count
    for index in range(total):
        report(progress, index, total, "Checking page")
        page = doc[index]
        for xref, ext, ftype, basefont, *_rest in page.get_fonts(full=False):
            if xref in seen_fonts or ftype == "Type3":
                continue
            seen_fonts.add(xref)
            if ext == "n/a":
                fonts.fail(f"“{basefont}” isn't embedded.", index)
        for annot in page.annots() or []:
            kind = annot.type[1]
            if kind in MEDIA_ANNOTS:
                media.fail(f"A {kind} annotation.", index, WARN if (e or kind == "FileAttachment") else FAIL)
            if kind in NOT_PRINTED_OK:
                continue
            annotations += 1
            if not (a or x):
                continue
            if _key(doc, annot.xref, "AP")[0] == "null":
                annots.fail(f"A {kind} annotation has no appearance (looks differ between viewers).", index)
            flags = annot.flags
            if flags & pymupdf.PDF_ANNOT_IS_HIDDEN or not flags & pymupdf.PDF_ANNOT_IS_PRINT:
                annots.fail(f"A {kind} annotation isn't set to print.", index)
        if x:
            if _key(doc, page.xref, "TrimBox")[0] == "null" and _key(doc, page.xref, "ArtBox")[0] == "null":
                boxes.pages.append(index)
            if _RGB_OPS.search(page.read_contents()):
                rgb.fail("Page content uses RGB colors.", index)
    report(progress, total, total, "Checking page")

    banned = FORBIDDEN_ACTIONS if a else BANNED_X
    any_action = see_through = False
    for xref in range(1, doc.xref_length()):
        if any(_key(doc, xref, k)[0] != "null" for k in ("A", "AA", "OpenAction")):
            any_action = True
        if a or x:  # (actions are usually direct objects inside a link or field: read the text)
            try:
                source = doc.xref_object(xref, compressed=True)
            except Exception:
                source = ""
            for name in _ACTION.findall(source):
                if "/" + name in banned:
                    actions.fail(f"A {name} action.")
            if "/JS" in source and _JS.search(source):
                actions.fail("A JavaScript script.")
        if not x:
            continue
        # graphics states are often written inline in a page's resources: read the text
        if any(float(v) < 1 for v in _ALPHA.findall(source)):
            transparency.fail("See-through fills or strokes (opacity below 100 %).")
        for mode in _BLEND.findall(source):
            if mode not in ("Normal", "Compatible"):
                transparency.fail(f"A {mode} blend mode.")
        if _SOFT_MASK.search(source):
            transparency.fail("A soft mask.")
        if _key(doc, xref, "Subtype")[1] == "/Image":
            if _key(doc, xref, "SMask")[0] == "xref":
                transparency.fail("A picture with transparency.")
            if _key(doc, xref, "ColorSpace")[1] == "/DeviceRGB":
                rgb.fail("A picture in RGB.")
    names = _key(doc, doc.pdf_catalog(), "Names")[1]
    if "/JavaScript" in names:
        any_action = True
        if a or x:
            actions.fail("Document-level JavaScript.")
    if "/EmbeddedFiles" in names:
        media.fail("Attached files (PDF/A-2 only allows attached PDF/A files).", None, WARN if not x else FAIL)

    # nothing there to check = N/A (a pass)
    if not seen_fonts:
        fonts.not_used("The file has no text fonts.")
    if not any_action:
        actions.not_used("The file has no links, buttons, or scripts with actions.")
    if not annotations:
        annots.not_used("The file has no comments or form fields.")
    if media.status == PASS:
        media.not_used("The file has none.")
    if transparency.status == PASS:
        transparency.not_used("The file has nothing see-through.")
    if boxes.pages:
        boxes.added(f"Saving as PDF/X sets it to the visible area on {len(boxes.pages)} page(s).")
        boxes.pages = []

    checks.append(fonts)
    if a or x:
        checks += [actions, annots]
    checks.append(media)
    if x:
        checks += [transparency, rgb, boxes]
    for c in checks:
        c.fix = FIXES.get(c.title, "")
    return Result(profile, checks, claimed)


# how Fix and Save fixes each problem (fix_copy, then Ghostscript's conversion)
FIXES = {
    "Not password protected": "The fixed copy is saved without a password (the standard doesn't allow one).",
    "All fonts are embedded": "Fix and Save embeds them; a font this computer doesn't have is replaced by a "
                              "similar one.",
    "No scripts or forbidden actions": "Fix and Save removes them (links and buttons that ran them stop doing so).",
    "No sound, video, 3D, or attached files": "Fix and Save removes them from the copy.",
    "Comments and fields print as they look": "Fix and Save sets them to print and draws their appearance.",
    "No transparency": "Fix and Save flattens it into the page.",
    "No RGB colors (CMYK, gray, or color-managed only)": "Fix and Save converts the colors to CMYK.",
}


def _has_banned(doc: pymupdf.Document, text: str, banned: set[str]) -> bool:
    """Does an action (``text`` = a key's value) use a banned type? Follows references."""
    for ref in re.findall(r"(\d+) 0 R", text):
        try:
            text += doc.xref_object(int(ref), compressed=True)
        except Exception:
            pass
    return any("/" + name in banned for name in _ACTION.findall(text)) or bool(_JS.search(text))


def fix_copy(doc: pymupdf.Document, profile: str) -> list[str]:
    """What Ghostscript's conversion doesn't fix, done on a copy before it: forbidden
    actions and scripts removed, media removed, comments set to print with an appearance,
    trim boxes, attached files. Returns what was changed, for the user."""
    a, x = profile == "pdfa", profile == "pdfx"
    changes: list[str] = []
    if not (a or x):
        return changes
    banned = FORBIDDEN_ACTIONS if a else BANNED_X
    removed_actions = 0
    for xref in range(1, doc.xref_length()):
        for key in ("A", "AA", "OpenAction"):
            kind, value = _key(doc, xref, key)
            if kind != "null" and _has_banned(doc, value, banned):
                doc.xref_set_key(xref, key, "null")
                removed_actions += 1
    catalog = doc.pdf_catalog()
    if "/JavaScript" in _key(doc, catalog, "Names")[1]:
        doc.xref_set_key(catalog, "Names/JavaScript", "null")
        removed_actions += 1
    if removed_actions:
        changes.append(f"Removed {removed_actions} script(s) or forbidden action(s).")
    media = printing = trimmed = 0
    for index in range(doc.page_count):
        page = doc[index]
        for annot in list(page.annots() or []):
            kind = annot.type[1]
            if kind in MEDIA_REMOVED or kind == "FileAttachment":
                page.delete_annot(annot)
                media += 1
                continue
            if kind in NOT_PRINTED_OK:
                continue
            flags = annot.flags
            wanted = (flags | pymupdf.PDF_ANNOT_IS_PRINT) & ~pymupdf.PDF_ANNOT_IS_HIDDEN & ~pymupdf.PDF_ANNOT_IS_NO_VIEW
            no_look = _key(doc, annot.xref, "AP")[0] == "null"
            if wanted != flags or no_look:
                annot.set_flags(wanted)
                if no_look:
                    try:
                        annot.update()
                    except Exception:
                        pass
                printing += 1
        if x and _key(doc, page.xref, "TrimBox")[0] == "null" and _key(doc, page.xref, "ArtBox")[0] == "null":
            page.set_trimbox(page.cropbox)
            trimmed += 1
    if media:
        changes.append(f"Removed {media} sound, video, 3D, or file attachment comment(s).")
    if printing:
        changes.append(f"Set {printing} comment(s) or field(s) to print.")
    if doc.embfile_count():
        count = doc.embfile_count()
        for name in list(doc.embfile_names()):
            doc.embfile_del(name)
        changes.append(f"Removed {count} attached file(s) (PDF/{'A' if a else 'X'} doesn't allow them).")
    if trimmed:
        changes.append(f"Set the trim box on {trimmed} page(s) to the visible area.")
    return changes
