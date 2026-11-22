"""Saved visual signatures and initials ("fill & sign").

A visual signature is just a picture of a signature: drawn, typed in a script font,
or imported. It proves nothing cryptographically (that's ``digital.py``). Images are
kept as transparent PNGs in a directory the caller supplies (per-user app data).
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pymupdf

from planetpdf.core.appearance import finish_pdf
from planetpdf.core.document import PdfError
from planetpdf.core.resources import font_path
from planetpdf.core.storage.files import write_bytes_atomic

log = logging.getLogger(__name__)
INDEX_FILE = "signatures.json"
KINDS = ("signature", "initials")
INK_BLUE = (0.05, 0.15, 0.55)
INK_BLACK = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SavedSignature:
    sig_id: str
    kind: str  # "signature" | "initials"
    name: str
    file: str
    created: str


# --- images ------------------------------------------------------------------------------------


def trim_and_clear_background(png: bytes, *, threshold: int = 235) -> bytes:
    """Make near-white pixels transparent (scanned/photographed signatures) and crop
    to the ink with a small margin."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(png))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PdfError(f"That image couldn't be read: {exc}") from exc
    from PIL import ImageChops

    image = image.convert("RGBA")
    gray = image.convert("L")
    # Alpha from darkness: white -> transparent, ink -> opaque (keeps smooth edges),
    # never more opaque than the image already was.
    ink_alpha = gray.point(lambda v: 0 if v >= threshold else min(255, int((threshold - v) * 255 / max(threshold - 60, 1))))
    alpha = ImageChops.darker(image.getchannel("A"), ink_alpha)
    image.putalpha(alpha)
    box = alpha.point(lambda a: 255 if a > 8 else 0).getbbox()
    if box is None:
        raise PdfError("The image looks empty (no ink found).")
    margin = max(4, (box[3] - box[1]) // 20)
    box = (max(0, box[0] - margin), max(0, box[1] - margin), min(image.width, box[2] + margin), min(image.height, box[3] + margin))
    out = io.BytesIO()
    image.crop(box).save(out, format="PNG")
    return out.getvalue()


def recolor_ink(png: bytes, color) -> bytes:
    """Repaint every ink pixel in ``color`` (0-1 RGB), keeping each pixel's transparency,
    so smooth edges stay smooth. Used for imported scans (black/gray pen) and to change
    the color of a saved signature."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(png))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PdfError(f"That image couldn't be read: {exc}") from exc
    image = image.convert("RGBA")
    rgb = tuple(max(0, min(255, round(c * 255))) for c in color)
    out_image = Image.new("RGBA", image.size, rgb + (0,))
    out_image.putalpha(image.getchannel("A"))
    out = io.BytesIO()
    out_image.save(out, format="PNG")
    return out.getvalue()


def ink_color(png: bytes) -> tuple[float, float, float] | None:
    """The main ink color of a signature image (0-1 RGB), or None if it has no ink."""
    from PIL import Image

    data = Image.open(io.BytesIO(png)).convert("RGBA").tobytes()
    pixels = [data[i : i + 4] for i in range(0, len(data), 4) if data[i + 3] > 200]
    if not pixels:
        return None
    n = len(pixels)
    return tuple(sum(p[i] for p in pixels) / n / 255 for i in range(3))


def render_typed(text: str, font: str = "script", color=INK_BLUE, *, height_px: int = 160) -> bytes:
    """A typed signature as a transparent PNG, drawn with a bundled script font."""
    text = text.strip()
    if not text:
        raise PdfError("Type your name first.")
    fontfile = str(font_path(font))
    size = 48.0
    width = pymupdf.Font(fontfile=fontfile).text_length(text, size) + size
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=size * 1.8)
    page.insert_text((size / 2, size * 1.25), text, fontsize=size, fontfile=fontfile, fontname="Sig", color=color)
    zoom = height_px / page.rect.height
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    return trim_and_clear_background(pix.tobytes("png"), threshold=250)


def signature_source_pdf(png: bytes) -> bytes:
    """Upright source PDF (for ``core.appearance``) showing the signature image."""
    pix = pymupdf.Pixmap(png)
    width, height = pix.width, pix.height
    points_per_px = 144.0 / max(width, 1)  # nominal 2 in wide; placement scales it
    doc = pymupdf.open()
    page = doc.new_page(width=width * points_per_px, height=height * points_per_px)
    page.insert_image(page.rect, stream=png)
    return finish_pdf(doc)


# --- library -------------------------------------------------------------------------------------


@dataclass
class SignatureLibrary:
    directory: Path
    items: list[SavedSignature] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path) -> SignatureLibrary:
        library = cls(Path(directory))
        try:
            raw = json.loads((library.directory / INDEX_FILE).read_text(encoding="utf-8"))
            library.items = [
                SavedSignature(**{k: d[k] for k in SavedSignature.__dataclass_fields__})
                for d in raw.get("signatures", [])
                if isinstance(d, dict) and (library.directory / d.get("file", "")).is_file()
            ]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Ignoring unreadable signature library: %s", exc)
        return library

    def _save(self) -> None:
        payload = {"version": 1, "signatures": [asdict(s) for s in self.items]}
        write_bytes_atomic(self.directory / INDEX_FILE, json.dumps(payload, indent=2).encode("utf-8"))

    def of_kind(self, kind: str) -> list[SavedSignature]:
        return [s for s in self.items if s.kind == kind]

    def default(self, kind: str) -> SavedSignature | None:
        found = self.of_kind(kind)
        return found[0] if found else None

    def add(self, png: bytes, kind: str, name: str = "") -> SavedSignature:
        if kind not in KINDS:
            raise PdfError(f"Unknown signature kind {kind!r}.")
        sig_id = uuid.uuid4().hex[:12]
        filename = f"{kind}-{sig_id}.png"
        write_bytes_atomic(self.directory / filename, png)
        item = SavedSignature(sig_id, kind, name or kind.title(), filename, datetime.now().isoformat(timespec="seconds"))
        # Newest first: it becomes the default for its kind.
        self.items.insert(0, item)
        self._save()
        return item

    def make_default(self, sig_id: str) -> None:
        item = self.get(sig_id)
        self.items = [item] + [s for s in self.items if s.sig_id != sig_id]
        self._save()

    def get(self, sig_id: str) -> SavedSignature:
        for s in self.items:
            if s.sig_id == sig_id:
                return s
        raise PdfError("That signature is no longer saved.")

    def png(self, item: SavedSignature) -> bytes:
        return (self.directory / item.file).read_bytes()

    def recolor(self, sig_id: str, color) -> None:
        """Change a saved signature's ink color (placed ones already on pages keep theirs)."""
        item = self.get(sig_id)
        write_bytes_atomic(self.directory / item.file, recolor_ink(self.png(item), color))

    def remove(self, sig_id: str) -> None:
        item = self.get(sig_id)
        self.items = [s for s in self.items if s.sig_id != sig_id]
        self._save()
        try:
            (self.directory / item.file).unlink(missing_ok=True)
        except OSError:
            pass
