"""Embed the fonts a PDF only names (user 2026-10-03: "Embed any local fonts that are used
into the final output so the PDF looks right", then "Don't embed the ones that are built
in. Just anything new").

A PDF can refer to a font without carrying it. The 14 built-in fonts (Helvetica, Times,
Courier, Symbol, ZapfDingbats) every viewer has: they're left as they are. Any other font
named but not carried (the BE Filing letter's "Arial" and "Arial,Bold") makes each viewer
substitute something of its own, so before a save every such font that is on this
computer (``core.fonts``, same family) is put into the file, cut down to the letters used:

* the letters: from the text drawn with that font on the pages (``get_texttrace``); a
  font used only by comments / stamps gets its whole character set (32-255);
* the font dictionary keeps its encoding and, when it has them, its widths (so no text
  moves); without widths it gets the local font's;
* left alone: the built-in 14 (also as "Helvetica,Bold" etc.), Type 0 / Type 3 fonts,
  form-field fonts (``/DR``: viewers type new letters with them), fonts with no local
  match, OpenType-CFF files, and fonts whose license forbids embedding. ``embed_fonts``
  reports what it did.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field

import pymupdf

from planetpdf.core import fonts
from planetpdf.core.progress import Progress
from planetpdf.core.progress import report as report_progress

# the 14 fonts every PDF viewer has (left as they are), and short names programs use for them
BUILT_IN = ("Helvetica", "Times", "Courier", "Symbol", "ZapfDingbats", "Helv", "TiRo", "Cour", "ZaDb", "Symb")


def is_built_in(name: str) -> bool:
    """"Helvetica", "Helvetica-BoldOblique", "Times-Roman", "Helvetica,Bold"… (not
    "TimesNewRoman": that's an ordinary font)."""
    base = re.split(r"[-,]", name.split("+")[-1])[0]
    return base in BUILT_IN


@dataclass
class FontReport:
    embedded: dict[str, str] = field(default_factory=dict)  # PDF font name -> local family used
    missing: list[str] = field(default_factory=list)  # names with no local font (or not allowed)

    def message(self) -> str:
        if not self.missing:
            return ""
        names = ", ".join(sorted(set(self.missing))[:6])
        return (f"Fonts not on this computer stay as they were (other computers may show them differently): "
                f"{names}.")


def _name(value: str) -> str:
    """A PDF name object's text: "/Arial#2CBold" -> "Arial,Bold"."""
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), value.lstrip("/"))


def _key(doc, xref: int, key: str) -> tuple[str, str]:
    return doc.xref_get_key(xref, key)


def _is_embedded(doc, xref: int) -> bool:
    kind, value = _key(doc, xref, "FontDescriptor")
    if kind != "xref":
        return False
    descriptor = int(value.split()[0])
    return any(_key(doc, descriptor, k)[0] != "null" for k in ("FontFile", "FontFile2", "FontFile3"))


def _form_fonts(doc) -> set[int]:
    """Fonts of the form fields' default resources (/AcroForm /DR /Font): left alone."""
    kind, value = _key(doc, doc.pdf_catalog(), "AcroForm/DR/Font")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]))
    elif kind != "dict":
        return set()
    return {int(n) for n in re.findall(r"(\d+)\s+0\s+R", value)}


def _local(base: str, doc, xref: int) -> tuple[str, bool, bool] | None:
    """The family on this computer (and style) for a PDF font name, or None."""
    name = base.split("+")[-1]
    family = fonts.known_family(name)
    if family is None or family not in fonts.installed():
        return None
    lower = name.lower()
    bold = any(w in lower for w in ("bold", "black", "heavy"))
    italic = "italic" in lower or "oblique" in lower
    kind, value = _key(doc, xref, "FontDescriptor")
    if kind == "xref":
        flags = _key(doc, int(value.split()[0]), "Flags")
        if flags[0] == "int":
            italic = italic or bool(int(flags[1]) & 64)
            bold = bold or bool(int(flags[1]) & 0x40000)  # (ForceBold)
    return family, bold, italic


# --- character codes -> Unicode ---------------------------------------------------------------------------


def _encoding(doc, xref: int, subtype: str) -> dict[int, str]:
    """Code (0-255) -> Unicode character, from the font's /Encoding."""
    from fontTools import agl
    from fontTools.encodings.StandardEncoding import StandardEncoding

    def table(name: str) -> dict[int, str]:
        if name == "MacRomanEncoding":
            return {c: bytes([c]).decode("mac_roman") for c in range(32, 256)}
        if name == "StandardEncoding":
            return {c: agl.toUnicode(g) for c, g in enumerate(StandardEncoding) if g != ".notdef" and agl.toUnicode(g)}
        out = {}
        for c in range(32, 256):  # (WinAnsiEncoding; also the default for TrueType here)
            try:
                out[c] = bytes([c]).decode("cp1252")
            except UnicodeDecodeError:
                pass
        return out

    kind, value = _key(doc, xref, "Encoding")
    default = "StandardEncoding" if subtype == "/Type1" else "WinAnsiEncoding"
    if kind == "name":
        return table(_name(value))
    if kind in ("xref", "dict"):
        text = doc.xref_object(int(value.split()[0])) if kind == "xref" else value
        base = re.search(r"/BaseEncoding\s*/(\w+)", text)
        codes = table(base.group(1) if base else default)
        differences = re.search(r"/Differences\s*\[(.*?)\]", text, re.S)
        if differences:
            code = 0
            for token in re.findall(r"\d+|/[^\s/\[\]]+", differences.group(1)):
                if token[0] == "/":
                    char = agl.toUnicode(_name(token))
                    if char:
                        codes[code] = char
                    code += 1
                else:
                    code = int(token)
        return codes
    return table(default)


# --- the letters used ----------------------------------------------------------------------------------------


def _used_letters(doc, wanted: set[str], counted=None) -> dict[str, set[str]]:
    """Font name (as the text trace gives it) -> letters drawn with it, on the pages that
    use any of the ``wanted`` font names. ``counted(done, total)`` before each page."""
    used: dict[str, set[str]] = {}
    for n, page in enumerate(doc):
        if counted is not None:
            counted(n, doc.page_count)
        names = {_name(f[3]).split("+")[-1] for f in page.get_fonts(full=True)}
        if not names & wanted:
            continue
        for span in page.get_texttrace():
            name = span["font"].split("+")[-1]
            if name in wanted:
                used.setdefault(name, set()).update(chr(c[0]) for c in span["chars"] if c[0] > 0)
    return used


# --- embedding ---------------------------------------------------------------------------------------------


def _subset(data: bytes, letters: set[str]) -> bytes:
    import logging

    logging.getLogger("fontTools").setLevel(logging.ERROR)
    from fontTools import subset as S

    options = S.Options()
    options.layout_features = []
    options.name_IDs = [1, 2, 3, 4, 6]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables += ["DSIG", "GSUB", "GPOS", "GDEF", "kern", "hdmx", "VDMX", "LTSH", "JSTF"]
    font = S.load_font(io.BytesIO(data), options)
    subsetter = S.Subsetter(options)
    subsetter.populate(text="".join(letters) + " ")
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _tag(data: bytes) -> str:
    n = zlib.crc32(data)
    return "".join(chr(65 + (n >> (5 * i)) % 26) for i in range(6))


def _embed_one(doc, xref: int, path, codes: dict[int, str], letters: set[str], keep_widths: bool) -> None:
    from fontTools.ttLib import TTFont

    whole = path.read_bytes()
    data = _subset(whole, letters)
    font = TTFont(io.BytesIO(data))
    upm = font["head"].unitsPerEm
    scale = 1000 / upm
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    os2 = font["OS/2"] if "OS/2" in font else None
    ps_name = re.sub(r"[^A-Za-z0-9-]", "", str(font["name"].getDebugName(6) or path.stem)) or "Font"
    base = f"{_tag(data)}+{ps_name}"

    stream = doc.get_new_xref()
    doc.update_object(stream, "<< >>")
    doc.update_stream(stream, data, compress=True)
    doc.xref_set_key(stream, "Length1", str(len(data)))

    head = font["head"]
    bbox = [round(v * scale) for v in (head.xMin, head.yMin, head.xMax, head.yMax)]
    ascent = round((os2.sTypoAscender if os2 else font["hhea"].ascent) * scale)
    descent = round((os2.sTypoDescender if os2 else font["hhea"].descent) * scale)
    cap = round((getattr(os2, "sCapHeight", 0) or 0) * scale) or round(ascent * 0.7)
    italic_angle = font["post"].italicAngle if "post" in font else 0
    flags = 32  # nonsymbolic
    if font["post"].isFixedPitch if "post" in font else False:
        flags |= 1
    if os2 is not None and 2 <= os2.panose.bSerifStyle <= 10:
        flags |= 2
    if italic_angle:
        flags |= 64
    kind, value = _key(doc, xref, "FontDescriptor")
    if kind == "xref":  # (the PDF already describes it: add the program to that)
        descriptor = int(value.split()[0])
        doc.xref_set_key(descriptor, "FontFile2", f"{stream} 0 R")
    else:
        descriptor = doc.get_new_xref()
        doc.update_object(descriptor, (
            f"<< /Type /FontDescriptor /FontName /{base} /Flags {flags} /FontBBox [{' '.join(map(str, bbox))}] "
            f"/ItalicAngle {italic_angle:g} /Ascent {ascent} /Descent {descent} /CapHeight {cap} "
            f"/StemV {120 if 'bold' in ps_name.lower() else 80} /FontFile2 {stream} 0 R >>"))
        doc.xref_set_key(xref, "BaseFont", f"/{base}")
        doc.xref_set_key(xref, "FontDescriptor", f"{descriptor} 0 R")
    doc.xref_set_key(xref, "Subtype", "/TrueType")
    if not keep_widths:
        first, last = 32, 255
        widths = []
        for code in range(first, last + 1):
            glyph = cmap.get(ord(codes[code])) if code in codes else None
            widths.append(round(hmtx[glyph][0] * scale) if glyph in hmtx.metrics else 0)
        doc.xref_set_key(xref, "FirstChar", str(first))
        doc.xref_set_key(xref, "LastChar", str(last))
        doc.xref_set_key(xref, "Widths", "[" + " ".join(map(str, widths)) + "]")


def embed_quietly(doc: pymupdf.Document, progress: Progress | None = None, step: str = "") -> FontReport:
    """``embed_fonts`` for saving: a problem with one font never stops a save."""
    try:
        return embed_fonts(doc, progress, step)
    except Exception:
        return FontReport()


def embed_fonts(doc: pymupdf.Document, progress: Progress | None = None, step: str = "") -> FontReport:
    """Put every font the document only names into it (see the module doc). ``progress`` hears
    "<step>finding the letters used on page 3 of 120", then "<step>embedding font 2 of 5"
    (``step``: e.g. "Step 1 of 3: "); it can't cancel (a save has begun)."""
    report = FontReport()
    skip = _form_fonts(doc)
    todo = []
    for xref in range(1, doc.xref_length()):
        try:
            if _key(doc, xref, "Type") != ("name", "/Font") or xref in skip:
                continue
            subtype = _key(doc, xref, "Subtype")[1]
            if subtype not in ("/Type1", "/TrueType", "/MMType1") or _is_embedded(doc, xref):
                continue
        except Exception:
            continue
        base = _name(_key(doc, xref, "BaseFont")[1])
        if not base or is_built_in(base):
            continue  # (every viewer has the built-in 14)
        local = _local(base, doc, xref)
        files = fonts.installed().get(local[0], {}) if local else {}
        path = files.get(local[1:]) if local else None
        if path is not None:
            try:
                path = fonts.font_file(path)  # (a face from a collection: its own file)
            except fonts.FontError:
                path = None
        if path is None or path.suffix.lower() != ".ttf":
            report.missing.append(base)
            continue
        try:
            if not fonts._metrics(path.read_bytes())[2]:  # (license: no embedding)
                report.missing.append(base)
                continue
        except OSError:
            report.missing.append(base)
            continue
        todo.append((xref, subtype, base, local[0], path))
    if not todo:
        return report
    used = _used_letters(doc, {b.split("+")[-1] for _x, _s, b, _f, _p in todo},
                         lambda done, total: report_progress(progress, done, total, f"{step}finding the letters used on page"))
    for n, (xref, subtype, base, family, path) in enumerate(todo):
        report_progress(progress, n, len(todo), f"{step}embedding font")
        codes = _encoding(doc, xref, subtype)
        letters = used.get(base.split("+")[-1]) or set(codes.values())  # (not on a page: comments, stamps…)
        keep_widths = _key(doc, xref, "Widths")[0] != "null"
        try:
            _embed_one(doc, xref, path, codes, letters, keep_widths)
        except Exception:
            report.missing.append(base)
            continue
        report.embedded[base] = family
    return report
