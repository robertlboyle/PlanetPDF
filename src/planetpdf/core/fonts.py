"""Fonts for text PlanetPDF writes (user 2026-10-03: "I should be able to change the font
and size and characteristics", placed text and Edit PDF's FORMAT).

Three kinds of font, listed in this order (``families()``):

* the standard PDF fonts Helvetica, Times Roman and Courier: never embedded, every viewer
  has them, exact bold / italic faces;
* PlanetPDF's own fonts (``resources/fonts``: Noto Sans / Serif, the handwriting fonts);
* the fonts installed on this computer (Windows: the registry, ``windows_fonts``; other
  systems: the usual font folders).

``face(family, bold, italic)`` says how to draw with a family: the PDF font name and, for
embedded fonts, the font program; when a family has no bold or italic file the style is
drawn by PlanetPDF ("synthetic", like Word). It also gives the line metrics the on-page
editor uses, so typed text lands where it was typed.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_FAMILY = "Helvetica"

# The standard fonts: (regular, bold, italic, bold italic) PDF names, and the metric-
# compatible fonts the on-page editor shows them in, with their line metrics (em):
# first baseline below the top, and line spacing (Arial, Times New Roman, Courier New).
STANDARD = {
    "Helvetica": (("helv", "hebo", "heit", "hebi"), ["Helvetica", "Arial", "Liberation Sans", "Nimbus Sans"], 0.905, 1.15),
    "Times Roman": (("tiro", "tibo", "tiit", "tibi"), ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman"], 0.891, 1.149),
    "Courier": (("cour", "cobo", "coit", "cobi"), ["Courier New", "Courier", "Liberation Mono", "Nimbus Mono PS"], 0.833, 1.133),
}

# PlanetPDF's own fonts: family -> resources.FONTS key
BUNDLED = {"Noto Sans": "sans", "Noto Serif": "serif", "Caveat": "handwriting", "Dancing Script": "script",
           "Great Vibes": "script_formal"}

# Edit PDF's page text keeps its old family keys (content_edit.TextLine.family)
FAMILY_OF_KEY = {"sans": "Helvetica", "serif": "Times Roman", "mono": "Courier"}


class FontError(Exception):
    pass


@dataclass(frozen=True)
class Face:
    family: str
    fontname: str  # PDF resource name: a standard font's name, or PPDF<crc32> for an embedded one
    buffer: bytes | None  # the font program (None: a standard font)
    path: Path | None
    fake_bold: bool  # no bold file: drawn thicker
    fake_italic: bool  # no italic file: drawn slanted
    ascent: float  # em: first baseline below the top of the line
    leading: float  # em: line spacing
    editor_families: tuple[str, ...]  # (standard fonts: what the on-page editor shows them in)
    fallback: str | None = None  # a font only in this document: the closest font for letters it lacks

    @property
    def in_document(self) -> bool:
        return self.fallback is not None

    def missing(self, text: str) -> list[str]:
        """The letters of ``text`` this font can't draw (each once, in order)."""
        return [c for c in dict.fromkeys(text) if not self.has_glyphs(c)]

    def width(self, text: str, size: float) -> float:
        import pymupdf

        if self.buffer is None:
            return pymupdf.get_text_length(text, fontname=self.fontname, fontsize=size)
        return _font(self.buffer).text_length(text, fontsize=size) * (1.03 if self.fake_bold else 1.0)

    def has_glyphs(self, text: str) -> bool:
        if self.buffer is None:
            return all(ord(c) < 256 for c in text)
        font = _font(self.buffer)
        return all(c in "\n\r\t " or font.has_glyph(ord(c)) for c in text)


@lru_cache(maxsize=32)
def _font_cached(crc: int, buffer: bytes):
    import pymupdf

    return pymupdf.Font(fontbuffer=buffer)


def _font(buffer: bytes):
    return _font_cached(zlib.crc32(buffer), buffer)


def subset(buffer: bytes, text: str) -> bytes:
    """The font program cut down to the letters of ``text`` (a whole installed font is
    megabytes; generated appearance PDFs are subset by `appearance.finish_pdf` instead)."""
    import io
    import logging

    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)  # ("meta NOT subset…": nothing to act on)
    try:
        from fontTools import subset as S

        options = S.Options()
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.notdef_outline = True
        options.recalc_bounds = True
        font = S.load_font(io.BytesIO(buffer), options)
        subsetter = S.Subsetter(options)
        subsetter.populate(text=text + " ")
        subsetter.subset(font)
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    except Exception:  # (a font fontTools can't subset: the whole program)
        return buffer


# --- the catalog -------------------------------------------------------------------------------------


def installed() -> dict[str, dict[tuple[bool, bool], Path]]:
    """Fonts installed on this computer: family -> {(bold, italic): file}."""
    return dict(_installed())


@lru_cache(maxsize=1)
def _installed() -> tuple:
    if sys.platform == "win32":
        from planetpdf.core.windows_fonts import collection_files, installed_fonts

        found = installed_fonts()
        # (Cambria, MS Gothic, Sitka… come in collections: user 2026-10-23, "as on the Mac"; a
        # family the registry already gave as single files keeps its files)
        singles = {(family, style) for family, styles in found.items() for style in styles}
        collections: dict[str, dict[tuple[bool, bool], Path]] = {}
        standard: set[tuple[str, bool, bool]] = set()
        for path in collection_files():
            _add_collection(collections, standard, path)
        for family, styles in collections.items():
            for style, path in styles.items():
                if (family, style) not in singles:
                    found.setdefault(family, {})[style] = path
    else:
        found = _scan_font_folders()
    return tuple(found.items())


def families() -> list[str]:
    """Every family that can be chosen: standard, PlanetPDF's own, then installed (A-Z)."""
    seen = set(STANDARD) | set(BUNDLED)
    others = sorted((name for name in installed() if name not in seen), key=str.casefold)
    return [*STANDARD, *BUNDLED, *others]


def is_standard(family: str) -> bool:
    return family in STANDARD


def face(family: str, bold: bool = False, italic: bool = False, doc=None, text: str = "") -> Face:
    """How to draw with ``family`` in this style. ``doc``: also the fonts only in that
    document (``document_fonts``; ``text``: what will be drawn, so the weight that has
    its letters is chosen). A family that isn't there (text made elsewhere) falls back to
    Helvetica."""
    if family in STANDARD:
        names, editor, ascent, leading = STANDARD[family]
        return Face(family, names[bold * 1 + italic * 2], None, None, False, False, ascent, leading, tuple(editor))
    if family in BUNDLED:
        from planetpdf.core.resources import font_path

        return _file_face(family, font_path(BUNDLED[family]), bold, italic)
    files = installed().get(family)
    if not files and doc is not None:
        found = document_fonts(doc).get(family)
        if found is not None:
            return found.face(bold, italic, text)
    if not files:
        return face(DEFAULT_FAMILY, bold, italic)
    exact = files.get((bold, italic))
    if exact is not None:
        return _file_face(family, exact, False, False)
    base = files.get((bold, False)) or files.get((False, italic)) or files.get((False, False)) or next(iter(files.values()))
    for (b, i), path in files.items():
        if path == base:
            return _file_face(family, path, bold and not b, italic and not i)
    return _file_face(family, base, bold, italic)


def _file_face(family: str, path: Path, fake_bold: bool, fake_italic: bool) -> Face:
    path = font_file(path)  # (a face from a collection: lifted out into its own file)
    data = _read(path)
    ascent, leading, embeddable = _metrics(data)
    if not embeddable:
        raise FontError(f"The license of the font “{family}” doesn't allow it to be put in a PDF. "
                        "Choose another font.")
    return Face(family, f"PPDF{zlib.crc32(data):08x}", data, path, fake_bold, fake_italic, ascent, leading, ())


# --- font collections (.ttc / .otc) -------------------------------------------------------------------
# A collection holds several faces in one file (macOS keeps Helvetica, Avenir, Futura, Menlo… this
# way: 128 files on the Mac mini, 2026-10-14). Each face is listed on its own; the first time one is
# used it's lifted out into a file of its own here, so everything else (screen previews, embedding,
# subsetting) works with it like any other font file.
FACE_CACHE = Path(tempfile.gettempdir()) / "planetpdf-font-faces"
_COLLECTION_FACES: dict[Path, tuple[Path, int]] = {}


def collection_face(collection: Path, index: int, cff: bool) -> Path:
    """Where face ``index`` of ``collection`` is (or will be) as a file of its own."""
    try:
        stat = collection.stat()
        stamp = f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        stamp = "0"
    key = zlib.crc32(f"{collection}|{stamp}".encode())
    path = FACE_CACHE / f"{collection.stem}-{index}-{key:08x}{'.otf' if cff else '.ttf'}"
    _COLLECTION_FACES[path] = (collection, index)
    return path


def font_file(path: Path) -> Path:
    """The font's own file: a face from a collection is written out the first time it's needed."""
    path = Path(path)
    source = _COLLECTION_FACES.get(path)
    if source is None or path.is_file():
        return path
    from fontTools.ttLib import TTFont

    try:
        FACE_CACHE.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        TTFont(str(source[0]), fontNumber=source[1]).save(str(part))
        os.replace(part, path)
    except Exception as exc:
        raise FontError(f"The font in {source[0].name} can't be read ({exc}).") from exc
    return path


@lru_cache(maxsize=64)
def _read(path: Path) -> bytes:
    try:
        return font_file(path).read_bytes()
    except OSError as exc:
        raise FontError(f"The font file {path} can't be read.") from exc


@lru_cache(maxsize=64)
def _metrics(data: bytes) -> tuple[float, float, bool]:
    """(ascent, line spacing) in em from the font's hhea table, as Qt lays out lines, and
    whether its license allows embedding (OS/2 fsType: 2 = restricted)."""
    import io

    try:
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(data), lazy=True, fontNumber=0)
        upm = font["head"].unitsPerEm
        hhea = font["hhea"]
        ascent = hhea.ascent / upm
        leading = (hhea.ascent - hhea.descent + hhea.lineGap) / upm
        fs_type = font["OS/2"].fsType if "OS/2" in font else 0
        return ascent, leading, (fs_type & 0x000F) != 0x0002
    except Exception:  # (an unusual font: Arial's metrics, which most text fonts are close to)
        return 0.905, 1.15, True


# --- fonts only in a document ------------------------------------------------------------------------------
#
# User 2026-10-03: offer the fonts embedded in the open document ("in this document"),
# never installed (a PDF's copies are cut down to the letters it uses, and their licenses
# rarely allow installing). Embedded copies are usually subsets that keep every glyph
# slot but outlines for only some letters, and one document can hold several copies of a
# font (the BE Filing letter: five "BettoniVF" copies, two weights). Copies with the same
# letter widths are one weight: their outlines are merged into one program, whose
# character map then lists only the letters that really draw. That's what text is drawn
# with, and what tells which letters are missing (then the closest font is offered).

_STYLE_WORDS = re.compile(r"(bold|italic|oblique|regular|roman|book|medium|semibold|light|mt|ps|psmt)$", re.I)


def family_name(pdf_name: str) -> str:
    """A PDF font name as a family: "ABCDEF+Calibri-BoldItalic" -> "Calibri",
    "TimesNewRomanPSMT" -> "TimesNewRoman", "BettoniVF-Regular-Dark" -> "BettoniVF"."""
    name = pdf_name.split("+")[-1]
    words = re.split(r"[-,]", name)[0].replace("_", " ")
    while True:
        trimmed = _STYLE_WORDS.sub("", words).strip()
        if trimmed == words or not trimmed:
            return words
        words = trimmed


def name_candidates(pdf_name: str) -> list[str]:
    """Ways to match a PDF font name to a family name (most specific first)."""
    name = pdf_name.split("+")[-1]
    base = re.split(r"[-,]", name)[0]
    words = base.replace("_", " ")
    out = [name, base, words]
    while True:
        trimmed = _STYLE_WORDS.sub("", words).strip()
        if trimmed == words or not trimmed:
            return out
        words = trimmed
        out.append(words)


def known_family(pdf_name: str) -> str | None:
    """The standard, bundled or installed family a PDF font name is, or None."""
    lookup = _family_lookup()
    for candidate in name_candidates(pdf_name):
        found = lookup.get(re.sub(r"[\s-]", "", candidate).lower())
        if found:
            return found
    return None


@lru_cache(maxsize=1)
def _family_lookup() -> dict[str, str]:
    return {re.sub(r"\s", "", f).lower(): f for f in families()}


@dataclass(frozen=True)
class Instance:
    """One weight of a document font: its copies merged."""

    data: bytes
    weight: float  # mean letter width (heavier weights are wider)
    bold: bool  # (named or flagged bold)
    italic: bool
    letters: frozenset[str]  # what it can draw


@dataclass(frozen=True)
class DocumentFont:
    family: str
    instances: tuple[Instance, ...]
    fallback: str  # closest standard family: "Times Roman", "Helvetica" or "Courier"

    def face(self, bold: bool = False, italic: bool = False, text: str = "") -> Face:
        """The weight to draw ``text`` with: one that has all its letters if any (else the
        one missing fewest); regular = the lightest of those, bold = a bold one or the
        heaviest, drawn bold by PlanetPDF when there's nothing heavier."""
        need = {c for c in text if not c.isspace()}
        pool = [i for i in self.instances if i.italic == italic] or list(self.instances)
        fake_italic = italic and not pool[0].italic
        fewest = min(len(need - i.letters) for i in pool)
        pool = [i for i in pool if len(need - i.letters) == fewest]
        regular = min(pool, key=lambda i: (i.bold, i.weight))
        fake_bold = False
        if not bold:
            chosen = regular
        elif any(i.bold for i in pool):
            chosen = max((i for i in pool if i.bold), key=lambda i: i.weight)
        else:
            chosen = max(pool, key=lambda i: i.weight)
            heavier = any(i.weight > chosen.weight for i in self.instances if i.bold)
            fake_bold = chosen is regular or heavier
        ascent, leading, _embeddable = _metrics(chosen.data)  # (already in the document: put in again)
        return Face(self.family, f"PPDF{zlib.crc32(chosen.data):08x}", chosen.data, None, fake_bold, fake_italic,
                    ascent, leading, (), self.fallback)


_DOC_FONTS: dict[tuple, dict[str, DocumentFont]] = {}


def document_fonts(doc) -> dict[str, DocumentFont]:
    """Fonts embedded in ``doc`` that aren't standard, bundled or installed, by family
    (cached while the document holds the same fonts: listing them is quick, reading
    them isn't)."""
    xrefs = frozenset(xref for pno in range(doc.page_count) for xref, ext, _t, _b, refname, *_r in doc.get_page_fonts(pno)
                      if xref and ext in ("ttf", "otf") and not refname.startswith("PPDF"))
    key = (id(doc), xrefs)
    if key not in _DOC_FONTS:
        if len(_DOC_FONTS) > 8:
            _DOC_FONTS.clear()
        _DOC_FONTS[key] = _collect_document_fonts(doc)
    return _DOC_FONTS[key]


def _collect_document_fonts(doc) -> dict[str, DocumentFont]:
    import io
    import logging

    logging.getLogger("fontTools").setLevel(logging.ERROR)
    from fontTools.ttLib import TTFont

    copies: dict[str, list] = {}  # family -> [(TTFont, bold, italic)]
    seen: set[int] = set()
    for pno in range(doc.page_count):
        for xref, ext, _type, basefont, refname, *_rest in doc.get_page_fonts(pno):
            if xref in seen or not xref or ext not in ("ttf", "otf") or refname.startswith("PPDF"):
                continue
            seen.add(xref)
            family = family_name(basefont)
            if not family or known_family(basefont):
                continue
            try:
                data = doc.extract_font(xref)[3]
                font = TTFont(io.BytesIO(data))
                if not font.getBestCmap():
                    continue  # (no character map: letters can't be typed with it)
            except Exception:
                continue
            words = basefont.lower()
            selection = font["OS/2"].fsSelection if "OS/2" in font else 0
            bold = bool(selection & 0x20) or any(w in words for w in ("bold", "black", "heavy"))
            italic = bool(selection & 0x01) or "italic" in words or "oblique" in words
            copies.setdefault(family, []).append((font, bold, italic))
    found = {}
    for family, fonts_ in copies.items():
        instances = _instances(fonts_)
        if instances:
            found[family] = DocumentFont(family, tuple(instances), _closest(fonts_[0][0]))
    return found


def _instances(copies: list) -> list[Instance]:
    """One instance per weight: copies with the same letter widths (and style) merged."""
    groups: dict[tuple, list] = {}
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for font, bold, italic in copies:
        cmap = font.getBestCmap() or {}
        names = [cmap[ord(c)] for c in letters if ord(c) in cmap]
        widths = tuple(font["hmtx"][n][0] for n in names) if "hmtx" in font else ()
        groups.setdefault((bold, italic, len(font.getGlyphOrder()), widths), []).append(font)
    out = []
    for (bold, italic, _count, widths), fonts_ in groups.items():
        merged = _merged(fonts_)
        if merged is not None:
            data, letters = merged
            out.append(Instance(data, sum(widths) / max(len(widths), 1), bold, italic, letters))
    return out


def _merged(fonts_: list) -> tuple[bytes, frozenset[str]] | None:
    """One program from copies of one weight: outlines one copy lacks are taken from
    another; the character map keeps only letters that draw."""
    import io

    def inked(font, name) -> bool:
        if "glyf" not in font:
            return True  # (CFF: can't tell; trusted)
        glyph = font["glyf"][name]
        return glyph.numberOfContours != 0

    fonts_ = sorted(fonts_, key=lambda f: -sum(inked(f, g) for g in f.getGlyphOrder()))
    base = fonts_[0]
    if "glyf" in base:
        glyf = base["glyf"]
        for other in fonts_[1:]:
            if "glyf" not in other or other.getGlyphOrder() != base.getGlyphOrder():
                continue
            for name in base.getGlyphOrder():
                if not inked(base, name) and inked(other, name):
                    glyph = other["glyf"][name]
                    glyph.expand(other["glyf"])
                    glyf[name] = glyph
    order = set(base.getGlyphOrder())
    keep = {code: name for code, name in (base.getBestCmap() or {}).items()
            if name in order and (inked(base, name) or chr(code).isspace())}
    if not any(not chr(c).isspace() for c in keep):
        return None
    for table in base["cmap"].tables:
        if table.isUnicode():
            table.cmap = {c: n for c, n in table.cmap.items() if c in keep}
    out = io.BytesIO()
    try:
        base.save(out)
    except Exception:
        return None
    return out.getvalue(), frozenset(chr(c) for c in keep if not chr(c).isspace())


def _closest(font) -> str:
    """The standard family most like this font (its PANOSE serif style, fixed pitch)."""
    try:
        if font["post"].isFixedPitch:
            return "Courier"
        panose = font["OS/2"].panose
        if panose.bFamilyType == 2 and 2 <= panose.bSerifStyle <= 10:
            return "Times Roman"
        if panose.bFamilyType == 2 and panose.bSerifStyle in (11, 12, 13):
            return "Helvetica"
    except Exception:
        pass
    name = font["name"].getDebugName(4) or "" if "name" in font else ""
    return "Times Roman" if "serif" in name.lower() and "sans" not in name.lower() else "Helvetica"


# --- other systems: the usual font folders -------------------------------------------------------------


def _scan_font_folders(folders: list[Path] | None = None) -> dict[str, dict[tuple[bool, bool], Path]]:
    folders = folders or [Path.home() / ".fonts", Path.home() / ".local/share/fonts", Path("/usr/share/fonts"),
                          Path("/usr/local/share/fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts"),
                          Path.home() / "Library/Fonts"]
    found: dict[str, dict[tuple[bool, bool], Path]] = {}
    standard: set[tuple[str, bool, bool]] = set()  # (slots filled by a plain style from a collection)
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            suffix = path.suffix.lower()
            if suffix in (".ttc", ".otc"):
                _add_collection(found, standard, path)
                continue
            if suffix not in (".ttf", ".otf"):
                continue
            named = _name_of(path)
            if named:
                family, bold, italic = named
                found.setdefault(family, {}).setdefault((bold, italic), path)
    return found


def _add_collection(found: dict[str, dict[tuple[bool, bool], Path]], standard: set[tuple[str, bool, bool]],
                    path: Path) -> None:
    """Every face of the collection at ``path`` into ``found``. A collection holds Light, Medium,
    Heavy…: the plain styles fill the four slots, whatever order the faces are in (``standard``
    remembers the slots a plain style filled)."""
    for face, (family, bold, italic, style) in _collection_names(path):
        slot = found.setdefault(family, {})
        if (bold, italic) not in slot or (style in STANDARD_STYLES and (family, bold, italic) not in standard):
            slot[(bold, italic)] = face
            if style in STANDARD_STYLES:
                standard.add((family, bold, italic))


def _collection_names(path: Path) -> list[tuple[Path, tuple[str, bool, bool, str]]]:
    """Every face in a font collection: (its own file to be, (family, bold, italic, style))."""
    try:
        from fontTools.ttLib import TTCollection

        collection = TTCollection(str(path), lazy=True)
    except Exception:
        return []
    out = []
    for index, font in enumerate(collection.fonts):
        named = _names(font)
        if named:
            out.append((collection_face(path, index, "CFF " in font or "CFF2" in font), named))
    return out


STANDARD_STYLES = {"regular", "roman", "normal", "book", "plain", "bold", "italic", "oblique", "bold italic",
                   "bold oblique"}


def _name_of(path: Path) -> tuple[str, bool, bool] | None:
    try:
        from fontTools.ttLib import TTFont

        named = _names(TTFont(str(path), lazy=True))
    except Exception:
        return None
    return named[:3] if named else None


def _names(font) -> tuple[str, bool, bool, str] | None:
    """(family, bold, italic, style name) from a font's name table."""
    try:
        names = font["name"]
        family = str(names.getDebugName(16) or names.getDebugName(1) or "")
        style = str(names.getDebugName(17) or names.getDebugName(2) or "").lower()
    except Exception:
        return None
    if not family:
        return None
    return family, "bold" in style, "italic" in style or "oblique" in style, style
