"""Optimize Scans (user 2026-10-03: a tool of its own with every scan clean-up step, black
& white, straightening, page direction, blank pages, "partial bleed through on thin
paper for the backs of scans").

Works on **scanned pages**: a page whose content is one picture covering it (plus, maybe,
an invisible recognized-text layer). The picture itself is replaced, so the page keeps
everything else (its OCR text, comments, links); other pages are left alone. Steps, in
order, each optional (``CleanupOptions``):

* resolution: pictures above ``dpi`` are reduced to it;
* straighten: the tilt of the text lines is taken out;
* dark borders: the black edges copiers and scanners leave are made white;
* clean: the paper made white (shadows, yellowing), specks and dust removed;
* bleed-through: faint text showing through from the back of thin paper made white;
* color: black & white (CCITT Group 4, by far the smallest; ``darkness`` moves the
  threshold), gray, color, or "auto" (``optimize.classify``);
* page direction: pages scanned sideways or upside down get their page rotation fixed
  (lossless), found by recognizing a strip of text both ways and counting real words.

``find_blank_pages`` lists empty pages (the backs of double-sided scans), for the user to
confirm before deleting them. ``looks_rough`` tells Recognize Text when straightening and
cleaning first would help.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report


# darker than this (once the paper is white) is ink on this side, not show-through: faint pencil
# writing reaches ~160, show-through from the back stays lighter than ~195
REAL_INK = 185
AUTO_SPECK_MM = 0.19  # specks up to this wide go (about 5 pixels at 300 dpi; a period is bigger)

# the steps, in the order they run; each "off", "auto" or "manual" (user 2026-10-03: automatic
# by default, manual in the advanced view, with a before / after preview of each)
STEPS = ("direction", "borders", "straighten", "clean", "bleed_through", "color")
STEP_NAMES = {"direction": "Page direction", "straighten": "Straighten", "borders": "Dark borders",
              "clean": "Clean up", "bleed_through": "Bleed-through", "color": "Color"}


@dataclass
class CleanupOptions:
    direction: str | bool = "auto"  # manual: ``turn`` (degrees clockwise, every page)
    turn: int = 0
    straighten: str | bool = "auto"  # manual: ``angle`` (degrees counterclockwise taken out)
    angle: float = 0.0
    borders: str | bool = "auto"  # manual: a band ``border_mm`` wide made white at every edge
    border_mm: float = 5.0
    clean: str | bool = "auto"  # white paper + specks; manual: specks up to ``speck_mm`` wide
    speck_mm: float = 0.3
    bleed_through: str | bool = "auto"  # auto: the cut-off found per page; manual: ``bleed_cutoff``
    bleed_cutoff: int = 222
    color: str = "auto"  # "auto", "bw", "gray", "color" or "keep" (as scanned: gray stays gray, color color)
    bw_threshold: int | None = None  # black & white: None = found per page (Otsu), else 1-254
    darkness: int = 0  # black & white, found threshold moved by this: -50 (lighter) … 50 (darker)
    dpi: int | None = 300  # pictures above this are reduced to it (None: kept as they are)
    language: str = "eng"  # (page direction: the words it looks for)

    def __post_init__(self) -> None:
        for step in STEPS[:-1]:  # (True / False: automatic / off)
            value = getattr(self, step)
            if value is True:
                setattr(self, step, "auto")
            elif value is False:
                setattr(self, step, "off")

    def mode(self, step: str) -> str:
        return getattr(self, step)

    def on(self, step: str) -> bool:
        return getattr(self, step) != "off"

    @property
    def changes_picture(self) -> bool:
        """False when only the page direction is fixed (the pictures stay as they are)."""
        return any(self.on(s) for s in STEPS[1:-1]) or self.color != "keep" or self.dpi is not None


@dataclass
class StepView:
    """One step of the preview: the picture before and after it, and what it did."""

    step: str
    before: object
    after: object
    detail: str


@dataclass
class CleanupReport:
    changed: list[int] = field(default_factory=list)  # pages cleaned
    not_scanned: list[int] = field(default_factory=list)  # pages left alone (not a scan)
    turned: dict[int, int] = field(default_factory=dict)  # page -> degrees turned (clockwise)
    straightened: dict[int, float] = field(default_factory=dict)  # page -> degrees taken out
    modes: dict[int, str] = field(default_factory=dict)  # page -> "bw" / "gray" / "color"


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise PdfError("Optimizing scans needs OpenCV, which isn't installed.") from exc
    return cv2, np


# --- which pages are scans -------------------------------------------------------------------------------


def scan_picture(doc: pymupdf.Document, index: int) -> tuple[int, pymupdf.Rect] | None:
    """(image xref, where it's drawn) when the page is a scanned page: one picture covering
    most of it, no visible text or drawings (an invisible OCR layer is fine)."""
    page = doc[index]
    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    xref = images[0][0]
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if len(rects) != 1 or abs(rects[0] & page.rect) < 0.85 * abs(page.rect):
        return None
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip() and span.get("alpha", 255) != 0:
                    return None  # (real text on the page: not just a scan)
    if page.get_drawings():
        return None
    return xref, rects[0]


def scanned_pages(doc: pymupdf.Document, pages=None) -> list[int]:
    return [i for i in (range(doc.page_count) if pages is None else pages) if scan_picture(doc, i)]


def _native_dpi(width_px: int, rect: pymupdf.Rect) -> float:
    return width_px / max(rect.width, 1) * 72


def load_picture(doc: pymupdf.Document, xref: int):
    """The picture as an OpenCV array (BGR, or gray for gray pictures)."""
    cv2, np = _cv()
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        return array[..., 0].copy()
    return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)


# --- the steps ---------------------------------------------------------------------------------------------


def _gray(image):
    cv2, _np = _cv()
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def skew_angle(gray, limit: float = 10.0) -> float:
    """The tilt of the text lines in degrees (counterclockwise is positive), found as the
    angle at which the rows of ink line up best: the row profile is most uneven when the
    lines are level. Coarse (0.5°) then fine (0.05°), on a small copy."""
    cv2, np = _cv()
    scale = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    ink = (small < cv2.threshold(small, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0]).astype(np.uint8) * 255
    if ink.mean() < 0.5:
        return 0.0
    h, w = ink.shape
    center = (w / 2, h / 2)

    def score(angle: float) -> float:
        rotated = cv2.warpAffine(ink, cv2.getRotationMatrix2D(center, angle, 1.0), (w, h), flags=cv2.INTER_NEAREST)
        rows = rotated.sum(axis=1, dtype=np.float64)
        return float(np.diff(rows).var())

    coarse = max(np.arange(-limit, limit + 0.01, 0.5), key=score)
    fine = max(np.arange(coarse - 0.5, coarse + 0.51, 0.05), key=score)
    return round(float(fine), 2)


def straighten(image, angle: float | None = None) -> tuple[object, float]:
    """(the picture with the text lines level, degrees taken out). ``angle``: a set angle
    instead of the measured one."""
    cv2, _np = _cv()
    if angle is None:
        angle = skew_angle(_gray(image))
    if abs(angle) < 0.15:
        return image, 0.0
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    white = 255 if image.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=white), angle


def remove_borders(image):
    """Black edges (from a copier lid or a scanner bed) made white: dark areas that touch
    the picture's edge and reach well along it."""
    cv2, np = _cv()
    gray = _gray(image)
    h, w = gray.shape
    dark = (gray < 90).astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    out = image.copy()
    for n in range(1, count):
        x, y, bw, bh, area = stats[n]
        along_side = (x == 0 or x + bw >= w) and bh > 0.25 * h and bw < 0.08 * w  # (a strip down a side)
        along_top = (y == 0 or y + bh >= h) and bw > 0.25 * w and bh < 0.08 * h  # (… across the top or bottom)
        solid = area > 0.8 * bw * bh  # (a copier edge is solid; a banner has text in it, and is wider)
        if (along_side or along_top) and solid and area > 0.002 * w * h:
            out[labels == n] = 255
    return out


def clear_edges(image, mm: float, dpi: float):
    """Manual dark borders: a band ``mm`` wide made white at every edge."""
    band = max(0, int(round(mm / 25.4 * dpi)))
    if not band:
        return image
    out = image.copy()
    out[:band] = 255
    out[-band:] = 255
    out[:, :band] = 255
    out[:, -band:] = 255
    return out


def remove_specks_from(image, mm: float, dpi: float):
    """Dark specks up to ``mm`` wide made white in a picture (gray or color)."""
    cv2, np = _cv()
    ink = (_gray(image) < 128).astype(np.uint8)
    keep = remove_specks(ink, dpi, mm)
    out = image.copy()
    out[(ink > 0) & (keep == 0)] = 255
    return out


def find_bleed_cutoff(image) -> int | None:
    """Automatic bleed-through: the cut-off for this page, or None when nothing shows
    through. Show-through is light marks that aren't next to real ink (a letter's soft
    edges are): the lightest bit of real ink stays, everything lighter than the
    show-through's darkest part goes."""
    cv2, np = _cv()
    gray = _gray(image)
    ink = (gray < 160).astype(np.uint8)  # (thin strokes of small type: show-through is never this dark)
    # (a letter's soft edges, a pixel or two: show-through often sits between the front's lines)
    near_ink = cv2.dilate(ink, np.ones((5, 5), np.uint8)) > 0
    smooth = cv2.GaussianBlur(gray, (0, 0), 1.2)  # (scanner noise in the paper is single pixels; show-through is strokes)
    light = (smooth >= 170) & (smooth < 238) & ~near_ink & ~flat_areas(gray)
    if light.mean() < 0.003:
        return None
    # Faint writing (pencil, a light copy) looks just like show-through: only take light marks
    # away when the page also has real dark ink to compare them with.
    if (gray < 120).mean() < 0.002:
        return None
    return int(np.clip(np.percentile(gray[light], 3) - 3, 195, 240))


def only_show_through(image, dpi: float) -> bool:
    """An empty back of a page: light marks only (nothing as dark as real ink, faint pencil
    included), and no shaded boxes."""
    _cv2, np = _cv()
    gray = _gray(image)
    some_marks = (gray < 240).mean() > 0.003
    ink = remove_specks((gray < REAL_INK).astype(np.uint8), dpi)
    return some_marks and ink.mean() < 0.0005 and not flat_areas(gray).any()


def flat_areas(gray):
    """Light areas that are solid, not strokes (shaded boxes, light fills): show-through is
    made of thin strokes, so it's never one of these."""
    cv2, np = _cv()
    size = max(9, int(min(gray.shape) / 120) | 1)  # (wider than any stroke)
    window = (size // 2 | 1, size // 2 | 1)
    values = gray.astype(np.float32)
    mean = cv2.blur(values, window)  # (scanner noise would punch holes in a solid area)
    spread = np.sqrt(np.maximum(cv2.blur(values * values, window) - mean * mean, 0))
    # tinted and even: a fill varies only by the scanner's noise; lines of text (or show-through)
    # blurred together look tinted too, but vary a lot
    tinted = ((mean < 244) & (mean >= 120) & (spread < 14)).astype(np.uint8)
    solid = cv2.morphologyEx(tinted, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (size, size)))
    return cv2.dilate(solid, np.ones(window, np.uint8)) > 0  # (and its soft edges)


def paper_light(image):
    """How bright the bare paper is across the page (per channel, float32, the picture's
    size): the paper's own pixels, smoothed over a wide area and filled in under text,
    pictures and shaded boxes. Only the lighting and the paper's tint, never a fill."""
    cv2, np = _cv()
    h, w = image.shape[:2]
    scale = 160 / max(h, w)
    small = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    gray = _gray(small).astype(np.float32)
    # the paper: within a few levels of the brightest paper nearby (the light changes slowly
    # across a page; a shaded row or box sits clearly below the white paper next to it)
    reach = max(3, int(max(small.shape[:2]) / 16) | 1)
    nearby = cv2.dilate(gray, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    # (… and near the page's usual paper brightness: deep inside a big dark banner, "the
    # brightest nearby" is the banner itself)
    usual = float(np.percentile(gray, 90))
    paper = ((gray > nearby - 9) & (gray > usual - 45)).astype(np.float32)
    sigma = max(small.shape[:2]) / 10
    weight = cv2.GaussianBlur(paper, (0, 0), sigma)
    channels = [small.astype(np.float32)] if small.ndim == 2 else list(cv2.split(small.astype(np.float32)))
    fields = []
    for channel in channels:
        everywhere = float(np.percentile(channel[paper > 0], 60)) if paper.any() else 255.0
        field = cv2.GaussianBlur(channel * paper, (0, 0), sigma) / np.maximum(weight, 1e-3)
        field = np.where(weight > 0.05, field, everywhere)  # (under a big fill: the page's usual paper)
        fields.append(cv2.resize(np.maximum(field, 1.0), (w, h), interpolation=cv2.INTER_LINEAR))
    return fields[0] if image.ndim == 2 else cv2.merge(fields)


def whiten_paper(image):
    """The paper made white: its lighting and tint divided out (shadows, yellowing,
    uneven light go), while text, pictures, colored areas and shaded boxes keep their
    color relative to the paper. (A large closing as the background, as before, turned
    every big solid area white: the brochure's banner, the invoice's shaded rows.)"""
    cv2, np = _cv()
    field = paper_light(image)
    flat = image.astype(np.float32) * (255.0 / field)
    return np.clip(flat, 0, 255).astype(np.uint8)


def remove_bleed_through(image, cutoff: int = 222, dpi: float = 300):
    """Faint marks from the back of thin paper (lighter than ``cutoff`` once the paper is
    white) made white; the rest stretched so ink keeps its darkness. A page with no real
    ink at all (the empty back of a page, only show-through on it) becomes white."""
    cv2, np = _cv()
    gray = _gray(image)
    ink = remove_specks((gray < REAL_INK).astype(np.uint8), dpi)
    if ink.mean() < 0.0005 and not flat_areas(gray).any():
        return np.full_like(image, 255)  # (only very light marks: an empty back with the front showing through)
    faint = (gray >= cutoff) & ~flat_areas(gray)  # (shaded boxes stay)
    out = image.copy()  # (nothing else brightened: that washed out shaded boxes)
    out[faint] = 255
    return out


DUST_MM = 0.6  # isolated marks up to this wide are dust (a period or an i's dot sits next to a letter)
DUST_GAP_MM = 1.2  # … when nothing else is this close


def remove_specks(binary_ink, dpi: float, mm: float = AUTO_SPECK_MM, *, dust: bool = True):
    """Ink specks removed (``binary_ink``: 1 = ink): anything up to ``mm`` wide, and,
    with ``dust``, marks up to ``DUST_MM`` wide that stand alone (dust between lines and
    in margins; a period or the dot on an i is next to other ink, so it stays)."""
    cv2, np = _cv()
    px = dpi / 25.4
    tiny_limit = max(2, int(round((mm * px) ** 2)))
    dust_limit = max(tiny_limit, int(round((DUST_MM * px) ** 2)))
    count, labels, stats, _c = cv2.connectedComponentsWithStats(binary_ink, connectivity=8)
    areas = stats[:, cv2.CC_STAT_AREA]
    drop = np.zeros(count, bool)
    drop[1:] = areas[1:] <= tiny_limit
    if dust:
        small = np.zeros(count, bool)
        small[1:] = areas[1:] <= dust_limit
        big = (~small[labels]) & (binary_ink > 0)
        reach = 2 * max(1, int(round(DUST_GAP_MM * px))) + 1  # (DUST_GAP_MM all round: a diameter of twice it)
        near_big = cv2.dilate(big.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))) > 0
        for n in np.nonzero(small)[0]:
            x, y, w, h = stats[n, :4]
            if not near_big[y:y + h, x:x + w][labels[y:y + h, x:x + w] == n].any():
                drop[n] = True
    out = binary_ink.copy()
    out[drop[labels]] = 0
    return out


def bw_threshold(gray, darkness: int = 0, threshold: int | None = None) -> int:
    """The black and white threshold: ``threshold`` when set, else Otsu's moved by
    ``darkness``."""
    cv2, _np = _cv()
    if threshold is None:
        threshold = int(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0]) + darkness
    return max(1, min(254, int(threshold)))


def black_and_white(gray, darkness: int = 0, *, dpi: float = 300, despeckle: bool = True,
                    threshold: int | None = None, speck_mm: float = AUTO_SPECK_MM):
    """1 = ink. Otsu's threshold moved by ``darkness`` (-50 … 50), or a set ``threshold``."""
    _cv2, np = _cv()
    ink = (gray < bw_threshold(gray, darkness, threshold)).astype(np.uint8)
    return remove_specks(ink, dpi, speck_mm) if despeckle else ink


def run_steps(image, options: CleanupOptions, dpi: float, *, turn: int = 0, record: list | None = None):
    """The picture steps in order. Returns (result, mode, degrees straightened): ``result``
    is the 0/1 ink array for "bw", else a gray / BGR picture. ``turn``: the page direction
    (only shown, in ``record``: on a real page the page's rotation is changed instead).
    ``record``: gets a ``StepView`` per step that's on (the preview)."""
    cv2, np = _cv()
    from PIL import Image

    def note(step: str, before, after, detail: str) -> None:
        if record is not None:
            record.append(StepView(step, before, after, detail))

    if options.on("direction") and record is not None:
        turned = np.ascontiguousarray(np.rot90(image, k=-(turn // 90))) if turn else image
        note("direction", image, turned,
             f"turned {turn}° clockwise" if turn else "already the right way up")
        image = turned
    angle = 0.0
    if options.on("borders"):  # (first: a black edge throws off the straightening)
        before = image
        if options.borders == "manual":
            image = clear_edges(image, options.border_mm, dpi)
            note("borders", before, image, f"a {options.border_mm:g} mm band cleared at every edge")
        else:
            image = remove_borders(image)
            changed = not np.array_equal(before, image)
            note("borders", before, image, "dark borders removed" if changed else "no dark borders found")
    if options.on("straighten"):
        before = image
        wanted = options.angle if options.straighten == "manual" else None
        if wanted is None and record is None and turn % 180 == 90:
            # (a sideways page: its text lines run down the picture until the page is turned,
            # so the tilt is measured as it will be shown; turning doesn't change the angle)
            wanted = skew_angle(np.ascontiguousarray(np.rot90(_gray(image), k=-(turn // 90))))
        image, angle = straighten(image, wanted)
        note("straighten", before, image, f"{abs(angle):.1f}° {'clockwise' if angle < 0 else 'counterclockwise'}"
             if angle else "already level")
    speck_mm = options.speck_mm if options.clean == "manual" else AUTO_SPECK_MM
    if options.on("clean"):
        before = image
        image = remove_specks_from(whiten_paper(image), speck_mm, dpi)
        note("clean", before, image, f"paper made white, specks up to {speck_mm:g} mm removed")
    if options.on("bleed_through"):
        before = image
        if options.bleed_through == "manual":
            cutoff = options.bleed_cutoff
        else:
            cutoff = find_bleed_cutoff(image)
            if cutoff is None and only_show_through(image, dpi):
                cutoff = 255  # (an empty back: made white below)
        if cutoff is None:
            note("bleed_through", before, image, "nothing showing through")
        else:
            image = remove_bleed_through(image, cutoff, dpi)
            blank = bool(image.min() == 255)
            note("bleed_through", before, image, "only show-through on this page: made white" if blank
                 else f"marks lighter than {cutoff} made white")
    mode = options.color
    if mode == "keep":
        mode = "gray" if image.ndim == 2 else "color"
    if mode == "auto":
        from planetpdf.core.optimize import classify

        gray_pil = Image.fromarray(_gray(image))
        rgb_pil = None if image.ndim == 2 else Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        mode = classify(gray_pil, rgb_pil)
    before = image
    if mode == "bw":
        gray = _gray(image)
        threshold = bw_threshold(gray, options.darkness, options.bw_threshold)
        result = black_and_white(gray, dpi=dpi, despeckle=options.on("clean"), threshold=threshold,
                                 speck_mm=speck_mm)
        note("color", before, np.where(result > 0, 0, 255).astype(np.uint8),
             f"black and white (threshold {threshold})")
        return result, "bw", angle
    if mode == "gray" or image.ndim == 2:
        gray = _gray(image)
        if options.on("clean"):
            gray = cv2.medianBlur(gray, 3)
        note("color", before, gray, "grayscale")
        return gray, "gray", angle
    note("color", before, image, "color kept")
    return image, "color", angle


def clean_picture(image, options: CleanupOptions, dpi: float):
    """All the picture steps (``run_steps`` without the preview)."""
    return run_steps(image, options, dpi)


# --- page direction ------------------------------------------------------------------------------------------


_WORDS = None


def _real_words(text: str) -> int:
    global _WORDS
    if _WORDS is None:
        from spellchecker import SpellChecker

        _WORDS = SpellChecker(language="en").word_frequency
    import re

    return sum(1 for w in re.findall(r"[A-Za-z]{3,}", text) if w.lower() in _WORDS)


def _ocr_text(gray, language: str, tessdata) -> str:
    cv2, _np = _cv()
    ok, png = cv2.imencode(".png", gray)
    doc = pymupdf.open()
    h, w = gray.shape
    page = doc.new_page(width=w * 72 / 150, height=h * 72 / 150)
    page.insert_image(page.rect, stream=png.tobytes())
    return page.get_textpage_ocr(language=language, dpi=150, full=True, tessdata=str(tessdata)).extractText()


def page_direction(image, dpi: float, language: str = "eng") -> int:
    """Degrees (clockwise) to turn the page so its text reads upright: 0, 90, 180 or 270.
    Lines of text make the ink's row profile far more uneven than its column profile,
    which tells upright / upside down from sideways; recognizing a strip of text both
    ways then tells which. 0 when unsure (little text, or no OCR here)."""
    cv2, np = _cv()
    from planetpdf.core.ocr import find_tessdata

    tessdata = find_tessdata()
    gray = _gray(remove_borders(image))  # (a black edge would look like a column of text)
    scale = 150 / dpi
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ink = (gray < cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0]).astype(np.float32)
    if ink.mean() < 0.002:
        return 0
    sideways = ink.sum(axis=0).var() > 1.5 * ink.sum(axis=1).var()
    candidates = (90, 270) if sideways else (0, 180)
    if tessdata is None:
        return 0  # (can't tell which way up without reading it)
    scores = {}
    for turn in candidates:
        view = np.rot90(gray, k=-turn // 90)
        h = view.shape[0]
        strip = view[int(h * 0.3):int(h * 0.6)]
        try:
            scores[turn] = _real_words(_ocr_text(np.ascontiguousarray(strip), language, tessdata))
        except Exception:
            return 0
    # (both ways, always: upside-down text still reads as a few dictionary-looking scraps)
    best = max(scores, key=scores.get)
    other = min(scores.values())
    return best if scores[best] >= 3 and scores[best] > 2 * other else 0


# --- whole pages ----------------------------------------------------------------------------------------------


def _store(doc, xref: int, result, mode: str) -> None:
    """The cleaned picture into the page's image object (same place on the page)."""
    from planetpdf.core.optimize import _group4, _replace

    cv2, np = _cv()
    from PIL import Image

    if mode == "bw":
        gray = Image.fromarray(np.where(result > 0, 0, 255).astype(np.uint8))
        data, black_is_1 = _group4(gray)
        h, w = result.shape
        _replace(doc, xref, data, {
            "Filter": "/CCITTFaxDecode", "BitsPerComponent": "1", "ColorSpace": "/DeviceGray",
            "Width": str(w), "Height": str(h),
            "DecodeParms": f"<< /K -1 /Columns {w} /Rows {h} /BlackIs1 {'true' if black_is_1 else 'false'} >>",
        })
        return
    if mode == "gray":
        buf = io.BytesIO()
        Image.fromarray(result).save(buf, "JPEG", quality=85, optimize=True)
        h, w = result.shape
        _replace(doc, xref, buf.getvalue(), {"Filter": "/DCTDecode", "BitsPerComponent": "8",
                                              "ColorSpace": "/DeviceGray", "Width": str(w), "Height": str(h)})
        return
    ok, jpeg = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, 85])
    h, w = result.shape[:2]
    _replace(doc, xref, jpeg.tobytes(), {"Filter": "/DCTDecode", "BitsPerComponent": "8",
                                         "ColorSpace": "/DeviceRGB", "Width": str(w), "Height": str(h)})


def cleanup_pages(doc: pymupdf.Document, pages, options: CleanupOptions,
                  progress: Progress | None = None) -> CleanupReport:
    """Every step on the scanned pages among ``pages`` (``doc`` changed in place)."""
    cv2, _np = _cv()
    result = CleanupReport()
    pages = list(pages)
    for n, index in enumerate(pages):
        if not report(progress, n, len(pages), "Optimizing page"):
            break
        found = scan_picture(doc, index)
        if found is None:
            result.not_scanned.append(index)
            continue
        xref, rect = found
        image = load_picture(doc, xref)
        dpi = _native_dpi(image.shape[1], rect)
        if options.changes_picture and options.dpi and dpi > options.dpi * 1.1:
            factor = options.dpi / dpi
            image = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            dpi = options.dpi
        turn = 0
        if options.on("direction"):
            turn = options.turn % 360 if options.direction == "manual" else page_direction(image, dpi, options.language)
            if turn:
                page = doc[index]
                page.set_rotation((page.rotation + turn) % 360)
                result.turned[index] = turn
        if not options.changes_picture:
            continue
        cleaned, mode, angle = run_steps(image, options, dpi, turn=turn)
        _store(doc, xref, cleaned, mode)
        result.changed.append(index)
        result.modes[index] = mode
        if angle:
            result.straightened[index] = angle
    return result


@dataclass
class Preview:
    original: object
    final: object
    steps: list[StepView]

    def step(self, name: str) -> StepView | None:
        return next((s for s in self.steps if s.step == name), None)


def preview(doc: pymupdf.Document, index: int, options: CleanupOptions, *, width: int = 600,
            known: dict | None = None) -> Preview | None:
    """One page through the steps, as small gray / BGR arrays: the original, the result,
    and before / after each step (for the dialog). Worked at 150 dpi at most (letters at
    60 dpi would read as specks), shrunk to ``width`` last. ``known``: per page, the
    automatic page direction found before (reading text both ways is slow)."""
    cv2, np = _cv()
    found = scan_picture(doc, index)
    if found is None:
        return None
    xref, rect = found
    image = load_picture(doc, xref)
    dpi = _native_dpi(image.shape[1], rect)
    work = min(dpi, 150.0, *(v for v in [options.dpi] if v))
    if work < dpi:
        image = cv2.resize(image, None, fx=work / dpi, fy=work / dpi, interpolation=cv2.INTER_AREA)
    turn = 0
    if options.direction == "manual":
        turn = options.turn % 360
    elif options.on("direction"):
        known = {} if known is None else known
        if index not in known:
            known[index] = page_direction(image, work, options.language)
        turn = known[index]
    steps: list[StepView] = []
    cleaned, mode, _angle = run_steps(image, options, work, turn=turn, record=steps)
    if mode == "bw":
        cleaned = np.where(cleaned > 0, 0, 255).astype(np.uint8)

    def small(array):
        factor = width / max(array.shape[:2])
        if factor >= 1:
            return array
        return cv2.resize(array, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)

    for step in steps:
        step.before, step.after = small(step.before), small(step.after)
    return Preview(small(image), small(cleaned), steps)


# --- blank pages, and is a scan rough? --------------------------------------------------------------------


def is_blank(doc: pymupdf.Document, index: int) -> bool:
    """A page with nothing on it once cleaned (no ink beyond specks and show-through)."""
    cv2, np = _cv()
    page = doc[index]
    if page.get_text().strip() or page.get_drawings():
        return False
    found = scan_picture(doc, index)
    if found is None:
        return not page.get_images()
    image = load_picture(doc, found[0])
    dpi = _native_dpi(image.shape[1], found[1])
    if dpi > 150:  # (150 dpi: smaller, thin pencil strokes would average out to white)
        image = cv2.resize(image, None, fx=150 / dpi, fy=150 / dpi, interpolation=cv2.INTER_AREA)
        dpi = 150.0
    paper = whiten_paper(remove_borders(image))
    if only_show_through(paper, dpi):
        return True  # (an empty back with the front showing through)
    cutoff = find_bleed_cutoff(paper)
    gray = _gray(remove_bleed_through(paper, cutoff, dpi) if cutoff else paper)
    ink = remove_specks((gray < REAL_INK).astype(np.uint8), dpi)  # (faint pencil counts: it isn't blank)
    return ink.mean() < 0.0005


def find_blank_pages(doc: pymupdf.Document, pages=None, progress: Progress | None = None) -> list[int]:
    pages = list(range(doc.page_count) if pages is None else pages)
    blank = []
    for n, index in enumerate(pages):
        if not report(progress, n, len(pages), "Checking page"):
            break
        if is_blank(doc, index):
            blank.append(index)
    return blank


def looks_rough(doc: pymupdf.Document, pages, sample: int = 3) -> tuple[float, float] | None:
    """For Recognize Text: (largest tilt in degrees, specks per page) on a few scanned
    pages, when straightening or cleaning first would help; None when the scans look fine."""
    cv2, np = _cv()
    scans = [i for i in pages if scan_picture(doc, i)][:sample]
    tilt, specks = 0.0, 0.0
    for index in scans:
        xref, rect = scan_picture(doc, index)
        image = load_picture(doc, xref)
        dpi = _native_dpi(image.shape[1], rect)
        gray = _gray(image)
        tilt = max(tilt, abs(skew_angle(gray)))
        ink = (gray < cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0]).astype(np.uint8)
        count, _labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
        limit = max(2, int(5 * (dpi / 300) ** 2))
        specks = max(specks, float((stats[1:, cv2.CC_STAT_AREA] <= limit).sum()))
    if tilt >= 0.5 or specks >= 300:
        return tilt, specks
    return None
