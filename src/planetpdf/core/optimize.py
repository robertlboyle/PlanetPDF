"""Make a PDF smaller (the Compress PDF tool; user 2026-10-03: "Some files especially
scanned files are huge").

Steps, each reported with its real size:

1. **Scanned pages** (a picture covering most of a page): one that is only black text on
   white is stored as a black & white picture (CCITT Group 4, what scanners and faxes
   use: a text page typically drops from several hundred KB to tens of KB); one with no
   real color is stored in gray. Pages with color, photos or shading (found block by
   block) are left to step 2.
2. **Pictures**: MuPDF downsamples ones above the chosen resolution and recompresses them
   (JPEG at the chosen quality).
3. **Fonts**: cut down to the letters used.
4. The file is written compactly (unused objects dropped, streams compressed, object
   streams).

``compress(doc, options)`` changes a document in place; ``reduce_size`` writes the
result to a new file; ``compressed_bytes`` returns it (the tool's "this document":
one undo step). ``space_usage`` says what takes the room (Acrobat's Audit Space Usage).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

PRESETS = {
    "screen": (96, 60),  # (image dpi, JPEG quality): emailing / on-screen
    "standard": (150, 75),  # good for most documents
    "print": (300, 85),  # keeps print quality
}
SCAN_COVER = 0.5  # a picture covering at least this much of its page counts as a scanned page
MIN_SCAN_PIXELS = 400_000  # (smaller pictures aren't scans worth the work)


@dataclass
class ReduceOptions:
    image_dpi: int | None = 150  # None = leave images alone
    jpeg_quality: int = 75
    subset_fonts: bool = True
    scans_black_white: bool = True  # text-only scanned pages -> black & white (CCITT G4)
    scans_gray: bool = True  # scanned pages without real color -> gray


@dataclass
class ReduceResult:
    before: int
    after: int
    black_white: int = 0  # scanned pictures stored as black & white
    gray: int = 0  # … as gray

    @property
    def saved_percent(self) -> float:
        return 0.0 if self.before == 0 else 100.0 * (self.before - self.after) / self.before


@dataclass
class _Done:
    black_white: int = 0
    gray: int = 0


# --- scanned pages ---------------------------------------------------------------------------------------


def scanned_images(doc: pymupdf.Document) -> dict[int, int]:
    """Pictures that are scanned pages: xref -> the first page it's on. Already black &
    white pictures, masks and pictures with transparency are left out."""
    found: dict[int, int] = {}
    for page in doc:
        area = abs(page.rect)
        if not area:
            continue
        for xref, smask, width, height, bpc, _cs, _alt, _name, filter_, *_rest in page.get_images(full=True):
            if xref in found or smask or bpc == 1 or width * height < MIN_SCAN_PIXELS:
                continue
            if filter_ in ("CCITTFaxDecode", "JBIG2Decode") or doc.xref_get_key(xref, "ImageMask")[1] == "true":
                continue
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue
            if rects and max(abs(r) for r in rects) >= SCAN_COVER * area:
                found[xref] = page.number
    return found


def classify(gray, rgb=None) -> str:
    """"bw" (black text on white), "gray" (no real color) or "color", for a scanned page
    as a PIL image (``rgb``: the color one, when it has color channels)."""
    import numpy as np

    if rgb is not None:
        small = rgb.copy()
        small.thumbnail((900, 900))
        hsv = np.asarray(small.convert("HSV"))
        colorful = (hsv[..., 1] > 60) & (hsv[..., 2] > 60)
        if colorful.mean() > 0.003:
            return "color"
    pixels = np.asarray(gray)
    paper = float(np.percentile(pixels, 90))  # (most of a text page is paper)
    ink = (pixels < min(80, paper - 60))
    mid = (pixels >= 80) & (pixels < paper - 40)
    if mid.mean() > 0.08 or ink.mean() < 0.001:
        return "gray"  # (pictures, or nothing dark at all)
    # a photo or a shaded box: whole blocks clearly darker than the paper but not ink
    block = 48
    h, w = (pixels.shape[0] // block) * block, (pixels.shape[1] // block) * block
    if h and w:
        grid = pixels[:h, :w].reshape(h // block, block, w // block, block).swapaxes(1, 2).reshape(
            h // block, w // block, block * block)
        medians = np.median(grid, axis=2)
        shaded = (medians < paper - 25) & (medians > 60)
        if shaded.mean() > 0.01:
            return "gray"
    return "bw"


def _otsu(gray) -> int:
    """The gray level that best splits ink from paper."""
    hist = gray.histogram()
    total = sum(hist)
    weighted = sum(i * h for i, h in enumerate(hist))
    best, best_t, below, below_sum = -1.0, 128, 0, 0.0
    for t in range(256):
        below += hist[t]
        below_sum += t * hist[t]
        above = total - below
        if not below or not above:
            continue
        mean_b, mean_a = below_sum / below, (weighted - below_sum) / above
        between = below * above * (mean_b - mean_a) ** 2
        if between > best:
            best, best_t = between, t
    return best_t


def _group4(gray) -> tuple[bytes, bool]:
    """(CCITT Group 4 data, BlackIs1) for a gray PIL image, thresholded."""
    from PIL import Image, TiffImagePlugin

    threshold = _otsu(gray)
    bw = gray.point(lambda v: 255 if v > threshold else 0, mode="1")
    old = TiffImagePlugin.STRIP_SIZE
    TiffImagePlugin.STRIP_SIZE = 1 << 30  # (one strip: the data is then one CCITT stream)
    try:
        buf = io.BytesIO()
        bw.save(buf, format="TIFF", compression="group4")
    finally:
        TiffImagePlugin.STRIP_SIZE = old
    data = buf.getvalue()
    tiff = Image.open(io.BytesIO(data))
    offsets, counts = tiff.tag_v2[273], tiff.tag_v2[279]
    if len(offsets) != 1:
        raise ValueError("more than one strip")
    return data[offsets[0]:offsets[0] + counts[0]], tiff.tag_v2.get(262, 0) == 1


def _pil(doc, xref):
    """(gray, rgb or None) PIL images of a picture."""
    from PIL import Image

    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    samples = pix.samples
    if pix.n == 1:
        return Image.frombytes("L", (pix.width, pix.height), samples), None
    rgb = Image.frombytes("RGB", (pix.width, pix.height), samples)
    return rgb.convert("L"), rgb


def _replace(doc, xref, data: bytes, keys: dict[str, str]) -> None:
    doc.update_stream(xref, data, compress=False)
    for key in ("Decode", "SMask", "Mask", "Intent", "DecodeParms", "Filter"):
        doc.xref_set_key(xref, key, "null")
    for key, value in keys.items():
        doc.xref_set_key(xref, key, value)


def compress_scans(doc, options: ReduceOptions, progress: Progress | None = None, step: str = "") -> _Done:
    """Step 1: scanned pages as black & white / gray (see the module doc)."""
    done = _Done()
    scans = scanned_images(doc)
    if not scans:
        report(progress, 0, 0, f"{step}no scanned pages")
    for n, xref in enumerate(scans):
        report(progress, n, len(scans), f"{step}checking scanned picture")
        try:
            gray, rgb = _pil(doc, xref)
            kind = classify(gray, rgb)
            width, height = gray.size
            if kind == "bw" and options.scans_black_white:
                data, black_is_1 = _group4(gray)
                _replace(doc, xref, data, {
                    "Filter": "/CCITTFaxDecode", "BitsPerComponent": "1", "ColorSpace": "/DeviceGray",
                    "DecodeParms": f"<< /K -1 /Columns {width} /Rows {height} /BlackIs1 {'true' if black_is_1 else 'false'} >>",
                })
                done.black_white += 1
            elif kind != "color" and rgb is not None and options.scans_gray:
                buf = io.BytesIO()
                gray.save(buf, format="JPEG", quality=int(options.jpeg_quality if options.image_dpi else 90),
                          optimize=True)
                _replace(doc, xref, buf.getvalue(), {
                    "Filter": "/DCTDecode", "BitsPerComponent": "8", "ColorSpace": "/DeviceGray"})
                done.gray += 1
        except Exception:
            continue  # (a picture MuPDF or Pillow can't read: left as it is)
    return done


# --- pictures ---------------------------------------------------------------------------------------------------


def _rewrite_images(doc: pymupdf.Document, options: ReduceOptions) -> None:
    """Step 2. PyMuPDF's ``rewrite_images`` uses MuPDF's "average" subsampling, which only
    halves: a 300 dpi scan never reached 150 dpi (it would land a hair under), so nothing
    was downsampled at the Balanced setting (found 2026-10-03). Bicubic resamples to the
    exact resolution. Black & white pictures are left alone (already as small as they get)."""
    from pymupdf import mupdf

    opts = mupdf.PdfImageRewriterOptions()
    threshold, target, quality = int(options.image_dpi * 1.3), int(options.image_dpi), str(int(options.jpeg_quality))
    for kind in ("color_lossy", "color_lossless", "gray_lossy", "gray_lossless"):
        setattr(opts, f"{kind}_image_recompress_method", mupdf.FZ_RECOMPRESS_JPEG)
        setattr(opts, f"{kind}_image_recompress_quality", quality)
        setattr(opts, f"{kind}_image_subsample_method", mupdf.FZ_SUBSAMPLE_BICUBIC)
        setattr(opts, f"{kind}_image_subsample_threshold", threshold)
        setattr(opts, f"{kind}_image_subsample_to", target)
    mupdf.pdf_rewrite_images(pymupdf._as_pdf_document(doc), opts)


# --- the whole job ---------------------------------------------------------------------------------------------


def compress(doc: pymupdf.Document, options: ReduceOptions, progress: Progress | None = None) -> _Done:
    """Steps 1-3 on ``doc`` (changed in place)."""
    scans_on = options.scans_black_white or options.scans_gray
    steps = [s for s, on in (("scans", scans_on), ("images", bool(options.image_dpi)),
                             ("fonts", options.subset_fonts), ("save", True)) if on]

    def label(name: str) -> str:
        return f"Step {steps.index(name) + 1} of {len(steps)}: "

    done = compress_scans(doc, options, progress, label("scans")) if scans_on else _Done()
    if options.image_dpi:
        images = {img[0] for page in doc for img in page.get_images(full=True)}
        report(progress, 0, 0, f"{label('images')}recompressing {len(images)} image{'s' if len(images) != 1 else ''} "
                               f"on {doc.page_count} page{'s' if doc.page_count != 1 else ''}…")
        try:
            _rewrite_images(doc, options)
        except Exception as exc:  # MuPDF rejects some unusual images; keep going without
            raise PdfError(f"Images couldn't be recompressed: {exc}") from exc
    if options.subset_fonts:
        fonts = {font[0] for page in doc for font in page.get_fonts(full=True)}
        report(progress, 0, 0, f"{label('fonts')}subsetting {len(fonts)} font{'s' if len(fonts) != 1 else ''}…")
        try:
            doc.subset_fonts()
        except Exception:
            pass  # fonts that can't be subset stay as they are
    report(progress, 0, 0, f"{label('save')}writing the smaller file ({doc.page_count} "
                           f"page{'s' if doc.page_count != 1 else ''})…")
    from planetpdf.core.font_embedding import embed_quietly

    embed_quietly(doc)  # (fonts it only names, from this computer: cut down to the letters used)
    return done


SAVE_ARGS = dict(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)


def compressed_bytes(doc: pymupdf.Document, options: ReduceOptions, progress: Progress | None = None
                     ) -> tuple[bytes, _Done]:
    """The compressed document (its own encryption kept: ``doc`` opened from a snapshot)."""
    done = compress(doc, options, progress)
    return doc.tobytes(**SAVE_ARGS, encryption=pymupdf.PDF_ENCRYPT_KEEP), done


def reduce_size(doc: pymupdf.Document, target: Path, options: ReduceOptions, before_size: int,
                progress: Progress | None = None) -> ReduceResult:
    """Write a smaller copy of ``doc`` (a private copy: it is modified) to ``target``."""
    done = compress(doc, options, progress)
    tmp = temp_path_beside(Path(target))
    try:
        doc.save(str(tmp), **SAVE_ARGS, encryption=pymupdf.PDF_ENCRYPT_NONE)
        after = tmp.stat().st_size
        replace_with_retry(tmp, Path(target))
    finally:
        remove_quietly(tmp)
    return ReduceResult(before_size, after, done.black_white, done.gray)


# --- what takes the room -------------------------------------------------------------------------------------


@dataclass
class SpaceUsage:
    parts: dict[str, int] = field(default_factory=dict)  # what -> bytes (as stored)
    scanned_pages: int = 0
    total: int = 0

    def rows(self) -> list[tuple[str, int, float]]:
        """(what, bytes, percent), largest first."""
        total = sum(self.parts.values()) or 1
        return [(k, v, 100.0 * v / total) for k, v in sorted(self.parts.items(), key=lambda kv: -kv[1]) if v]


def space_usage(doc: pymupdf.Document) -> SpaceUsage:
    """How much of the file each kind of thing takes (stored, compressed sizes)."""
    usage = SpaceUsage()
    font_files: set[int] = set()
    contents: set[int] = set()
    for page in doc:
        contents.update(page.get_contents())
        for font in page.get_fonts(full=True):
            descriptor = doc.xref_get_key(font[0], "FontDescriptor")
            if descriptor[0] == "xref":
                d = int(descriptor[1].split()[0])
                for key in ("FontFile", "FontFile2", "FontFile3"):
                    k = doc.xref_get_key(d, key)
                    if k[0] == "xref":
                        font_files.add(int(k[1].split()[0]))
    usage.scanned_pages = len(set(scanned_images(doc).values()))
    parts = {"Pictures": 0, "Fonts": 0, "Page content (text and drawings)": 0, "Comments and form fields": 0,
             "Document structure and other": 0}
    for xref in range(1, doc.xref_length()):
        try:
            size = len(doc.xref_object(xref, compressed=True))
            if doc.xref_is_stream(xref):
                size += len(doc.xref_stream_raw(xref) or b"")
        except Exception:
            continue
        subtype = doc.xref_get_key(xref, "Subtype")[1]
        kind = doc.xref_get_key(xref, "Type")[1]
        if subtype == "/Image":
            part = "Pictures"
        elif xref in font_files or kind in ("/Font", "/FontDescriptor"):
            part = "Fonts"
        elif xref in contents or subtype == "/Form":
            part = "Page content (text and drawings)"
        elif kind == "/Annot" or doc.xref_get_key(xref, "FT")[0] != "null":
            part = "Comments and form fields"
        else:
            part = "Document structure and other"
        parts[part] += size
    usage.parts = parts
    usage.total = sum(parts.values())
    return usage
