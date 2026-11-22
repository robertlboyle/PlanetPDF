"""Stamps and seals: specs, dynamic fields, rendering, and the persistent library.

A text stamp is drawn as vector text in a bundled font (subset when saved) inside an
optional border, turned by any angle, with an opacity. An image seal is a PNG with
transparency. Both become appearance items (``core.markup`` kinds STAMP / SEAL).

The library (custom stamps and imported seals) is JSON + PNG files in a directory the
caller supplies (the UI passes the per-user app data location).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

import pymupdf

from planetpdf.core.appearance import finish_pdf, rotate_pdf
from planetpdf.core.document import PdfError
from planetpdf.core.resources import font_path
from planetpdf.core.storage.files import write_bytes_atomic

log = logging.getLogger(__name__)
Color = tuple[float, float, float]

BORDERS = ("none", "solid", "double", "rounded")
TOKENS = {
    "{date}": "today's date",
    "{time}": "the current time",
    "{user}": "your name (Preferences)",
    "{company}": "your company name (Preferences)",
}
DEFAULT_DATE_FORMAT = "%b %d, %Y"
DEFAULT_TIME_FORMAT = "%I:%M %p"
RED = (0.80, 0.05, 0.05)
GREEN = (0.05, 0.50, 0.15)
BLUE = (0.05, 0.25, 0.70)
GRAY = (0.35, 0.35, 0.35)


@dataclass(frozen=True)
class StampSpec:
    stamp_id: str
    name: str
    kind: str = "text"  # "text" | "image"
    lines: tuple[str, ...] = ("APPROVED",)  # first line large, the rest smaller
    color: Color = RED
    font: str = "sans"  # key in core.resources.FONTS
    bold: bool = True
    border: str = "rounded"
    rotation: float = 0.0  # degrees, counterclockwise positive (as shown)
    opacity: float = 1.0
    image: str = ""  # image seals: file name inside the library's seals folder
    builtin: bool = False
    # Acrobat look (user screenshots 2026-10-01): "classic" (outlined text, our original),
    # "tile" (gradient tile, condensed bold italic), "flag" (Sign Here arrow), "check", "cross"
    style: str = "classic"
    category: str = "Custom"  # Dynamic | Sign Here | Standard Business | Custom
    tint: str = ""  # palette for tile/flag: green, red, blue, yellow, purple, salmon
    big: int = 0  # which line is the large one (Company Confidential: the 2nd)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["lines"] = list(self.lines)
        data["color"] = list(self.color)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> StampSpec:
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in data.items() if k in known}
        values["lines"] = tuple(values.get("lines", ()))
        values["color"] = tuple(values.get("color", RED))
        return cls(**values)


# tint -> (border, text, fill at the left, fill at the right): Acrobat's stamp colors
PALETTES: dict[str, tuple[Color, Color, Color, Color]] = {
    "green": ((0.24, 0.40, 0.13), (0.25, 0.43, 0.13), (0.80, 0.88, 0.74), (0.95, 0.97, 0.93)),
    "red": ((0.55, 0.08, 0.08), (0.62, 0.07, 0.07), (0.96, 0.76, 0.76), (0.99, 0.92, 0.92)),
    "blue": ((0.13, 0.17, 0.42), (0.11, 0.15, 0.40), (0.78, 0.80, 0.90), (0.94, 0.95, 0.98)),
    "yellow": ((0.80, 0.65, 0.20), (0.15, 0.14, 0.12), (0.98, 0.93, 0.55), (0.99, 0.97, 0.78)),
    "purple": ((0.30, 0.24, 0.62), (0.13, 0.12, 0.22), (0.72, 0.68, 0.95), (0.62, 0.57, 0.90)),
    "salmon": ((0.55, 0.12, 0.07), (0.15, 0.09, 0.08), (0.95, 0.55, 0.45), (0.86, 0.32, 0.24)),
}
TINT_COLORS = {"green": GREEN, "red": RED, "blue": BLUE}
CATEGORIES = ("Dynamic", "Sign Here", "Standard Business")
DYNAMIC_LINE = "By {user} at {time}, {date}"


def _stamp(key: str, name: str, category: str, lines: tuple[str, ...], tint: str, style: str = "tile", big: int = 0) -> StampSpec:
    color = PALETTES[tint][1] if tint in PALETTES else BLUE
    return StampSpec(stamp_id=f"builtin-{key}", name=name, lines=lines, color=color, font="stamp", builtin=True,
                     style=style, category=category, tint=tint, big=big, border="none")


# Acrobat's built-in stamps (user screenshots 2026-10-01), plus Company Confidential: the company
# name from Preferences over CONFIDENTIAL (just CONFIDENTIAL while it's blank).
BUILTIN_STAMPS: tuple[StampSpec, ...] = (
    # Dynamic: who and when are filled in as you place it
    _stamp("revised", "Revised", "Dynamic", ("REVISED", "{time}, {date}"), "blue"),
    _stamp("reviewed", "Reviewed", "Dynamic", ("REVIEWED", DYNAMIC_LINE), "blue"),
    _stamp("received", "Received", "Dynamic", ("RECEIVED", DYNAMIC_LINE), "blue"),
    _stamp("approved-dynamic", "Approved (dated)", "Dynamic", ("APPROVED", DYNAMIC_LINE), "green"),
    _stamp("company-confidential", "Company Confidential", "Dynamic", ("{company}", "CONFIDENTIAL"), "red", big=1),
    # Sign Here
    _stamp("witness", "Witness", "Sign Here", ("WITNESS",), "yellow", style="flag"),
    _stamp("initial-here", "Initial Here", "Sign Here", ("INITIAL HERE",), "purple", style="flag"),
    _stamp("sign-here", "Sign Here", "Sign Here", ("SIGN HERE",), "salmon", style="flag"),
    _stamp("accepted", "Accepted", "Sign Here", ("",), "green", style="check"),
    _stamp("rejected", "Rejected", "Sign Here", ("",), "red", style="cross"),
    # Standard Business
    _stamp("approved", "Approved", "Standard Business", ("APPROVED",), "green"),
    _stamp("not-approved", "Not Approved", "Standard Business", ("NOT APPROVED",), "red"),
    _stamp("draft", "Draft", "Standard Business", ("DRAFT",), "blue"),
    _stamp("final", "Final", "Standard Business", ("FINAL",), "green"),
    _stamp("completed", "Completed", "Standard Business", ("COMPLETED",), "green"),
    _stamp("confidential", "Confidential", "Standard Business", ("CONFIDENTIAL",), "blue"),
    _stamp("for-public-release", "For Public Release", "Standard Business", ("FOR PUBLIC RELEASE",), "blue"),
    _stamp("not-for-public-release", "Not For Public Release", "Standard Business", ("NOT FOR PUBLIC RELEASE",), "blue"),
    _stamp("for-comment", "For Comment", "Standard Business", ("FOR COMMENT",), "blue"),
    _stamp("void", "Void", "Standard Business", ("VOID",), "red"),
    _stamp("preliminary-results", "Preliminary Results", "Standard Business", ("PRELIMINARY RESULTS",), "blue"),
    _stamp("information-only", "Information Only", "Standard Business", ("INFORMATION ONLY",), "blue"),
)


# Built-in stamps whose id changed (settings may still name the old one)
RENAMED = {"builtin-planet-confidential": "builtin-company-confidential"}


def builtin(key: str) -> StampSpec:
    """A built-in stamp by key, e.g. ``builtin("approved")``."""
    return next(s for s in BUILTIN_STAMPS if s.stamp_id == f"builtin-{key}")


# --- dynamic fields ------------------------------------------------------------------------------


def resolve_text(
    text: str,
    *,
    user: str = "",
    company: str = "",
    now: datetime | None = None,
    date_format: str = DEFAULT_DATE_FORMAT,
    time_format: str = DEFAULT_TIME_FORMAT,
) -> str:
    """Fill in {date}, {time}, {user} and {company}; tidy separators around empty values."""
    now = now or datetime.now()
    if not user:  # "By {user} at 12:16 pm" reads oddly without a name
        text = text.replace("By {user} at ", "").replace("By {user}", "")
    out = (
        text.replace("{date}", now.strftime(date_format))
        .replace("{time}", now.strftime(time_format).lstrip("0"))
        .replace("{user}", user)
        .replace("{company}", company)
    )
    out = " ".join(out.split())
    return out.strip(" ·-,")


def resolve_lines(spec: StampSpec, **kwargs) -> list[str]:
    lines = [resolve_text(l, **kwargs) for l in spec.lines]
    if spec.style in ("tile", "flag"):  # Acrobat writes "12:16 pm"
        lines = [l.replace(" AM", " am").replace(" PM", " pm") for l in lines]
    if spec.style in ("check", "cross"):
        return [""]
    return [line for line in lines if line]


# --- rendering -----------------------------------------------------------------------------------

MAIN_SIZE = 24.0
SUB_SIZE = 10.0
PAD_X, PAD_Y = 10.0, 6.0


def render_text_stamp(spec: StampSpec, lines: list[str]) -> bytes:
    """Upright source PDF for a text stamp (already rotated by spec.rotation)."""
    if spec.style in ("tile", "flag", "check", "cross"):
        from planetpdf.core import stamp_art

        return rotate_pdf(stamp_art.render(spec, lines), spec.rotation)
    if not lines:
        raise PdfError("The stamp has no text.")
    fontfile = str(font_path(spec.font))
    font = pymupdf.Font(fontfile=fontfile)
    sizes = [MAIN_SIZE] + [SUB_SIZE] * (len(lines) - 1)
    widths = [font.text_length(line, size) for line, size in zip(lines, sizes)]
    heights = [size * 1.25 for size in sizes]
    border = spec.border if spec.border in BORDERS else "solid"
    inset = 0.0 if border == "none" else (7.0 if border == "double" else 4.0)
    width = max(widths) + 2 * (PAD_X + inset)
    height = sum(heights) + 2 * (PAD_Y + inset)

    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    alpha = max(0.05, min(1.0, spec.opacity))
    shape = page.new_shape()
    frame = page.rect + (1.5, 1.5, -1.5, -1.5)
    if border == "solid":
        shape.draw_rect(frame)
        shape.finish(color=spec.color, width=2.5, stroke_opacity=alpha)
    elif border == "rounded":
        shape.draw_rect(frame, radius=0.18)
        shape.finish(color=spec.color, width=2.5, stroke_opacity=alpha)
    elif border == "double":
        shape.draw_rect(frame)
        shape.finish(color=spec.color, width=2.0, stroke_opacity=alpha)
        shape.draw_rect(frame + (4, 4, -4, -4))
        shape.finish(color=spec.color, width=1.0, stroke_opacity=alpha)
    y = PAD_Y + inset
    for line, size, line_width, line_height in zip(lines, sizes, widths, heights):
        x = (width - line_width) / 2
        baseline = y + size * 1.02
        shape.insert_text(
            (x, baseline),
            line,
            fontsize=size,
            fontfile=fontfile,
            fontname="StampFont",
            color=spec.color,
            fill=spec.color,
            render_mode=2 if spec.bold else 0,  # fill + thin stroke = bold look from one font
            border_width=0.035 if spec.bold else 0.05,
            fill_opacity=alpha,
            stroke_opacity=alpha,
        )
        y += line_height
    shape.commit()
    return rotate_pdf(finish_pdf(doc), spec.rotation)


def render_image_seal(png: bytes, *, opacity: float = 1.0, rotation: float = 0.0, max_side: float = 144.0) -> bytes:
    """Upright source PDF for an image (PNG transparency kept), longest side ``max_side``."""
    png = with_opacity(png, opacity)
    pix = pymupdf.Pixmap(png)
    pixel_w, pixel_h = pix.width, pix.height
    scale = max_side / max(pixel_w, pixel_h)
    doc = pymupdf.open()
    page = doc.new_page(width=pixel_w * scale, height=pixel_h * scale)
    page.insert_image(page.rect, stream=png)
    return rotate_pdf(finish_pdf(doc), rotation)


def with_opacity(png: bytes, opacity: float) -> bytes:
    """PNG with its alpha channel scaled by ``opacity`` (validates the image too)."""
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(png))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PdfError(f"That image couldn't be read: {exc}") from exc
    if opacity >= 1.0 and image.format == "PNG":
        return png
    image = image.convert("RGBA")
    if opacity < 1.0:
        alpha = image.getchannel("A").point(lambda a: int(a * max(0.0, opacity)))
        image.putalpha(alpha)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# --- library -----------------------------------------------------------------------------------------

LIBRARY_FILE = "stamps.json"
SEALS_DIR = "seals"


@dataclass
class StampLibrary:
    """Built-in stamps plus the user's custom stamps and seals (persisted)."""

    directory: Path
    custom: list[StampSpec] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path) -> StampLibrary:
        library = cls(Path(directory))
        path = library.directory / LIBRARY_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            library.custom = [StampSpec.from_dict(d) for d in raw.get("stamps", []) if isinstance(d, dict)]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Ignoring unreadable stamp library %s: %s", path, exc)
        return library

    def save(self) -> None:
        payload = {"version": 1, "stamps": [s.to_dict() for s in self.custom]}
        write_bytes_atomic(self.directory / LIBRARY_FILE, json.dumps(payload, indent=2).encode("utf-8"))

    @property
    def all(self) -> list[StampSpec]:
        return list(BUILTIN_STAMPS) + list(self.custom)

    def get(self, stamp_id: str) -> StampSpec:
        stamp_id = RENAMED.get(stamp_id, stamp_id)
        for spec in self.all:
            if spec.stamp_id == stamp_id:
                return spec
        raise PdfError("That stamp is no longer in the library.")

    def add_text_stamp(self, name: str, lines: list[str], **style) -> StampSpec:
        spec = StampSpec(stamp_id=uuid.uuid4().hex[:12], name=name, lines=tuple(lines), **style)
        self.custom.append(spec)
        self.save()
        return spec

    def add_seal(self, name: str, png: bytes, **style) -> StampSpec:
        png = with_opacity(png, 1.0)  # validates, and converts JPG etc. to PNG
        stamp_id = uuid.uuid4().hex[:12]
        filename = f"{stamp_id}.png"
        write_bytes_atomic(self.directory / SEALS_DIR / filename, png)
        spec = StampSpec(stamp_id=stamp_id, name=name, kind="image", lines=(), image=filename, **style)
        self.custom.append(spec)
        self.save()
        return spec

    def update(self, spec: StampSpec) -> None:
        if spec.builtin:
            raise PdfError("Built-in stamps can't be changed; make a copy instead.")
        self.custom = [spec if s.stamp_id == spec.stamp_id else s for s in self.custom]
        self.save()

    def remove(self, stamp_id: str) -> None:
        spec = self.get(stamp_id)
        if spec.builtin:
            raise PdfError("Built-in stamps can't be removed.")
        self.custom = [s for s in self.custom if s.stamp_id != stamp_id]
        self.save()
        if spec.image:
            try:
                (self.directory / SEALS_DIR / spec.image).unlink(missing_ok=True)
            except OSError:
                pass

    def copy_of(self, spec: StampSpec, name: str | None = None) -> StampSpec:
        new = replace(spec, stamp_id=uuid.uuid4().hex[:12], name=name or f"{spec.name} (copy)", builtin=False,
                      category="Custom")
        self.custom.append(new)
        self.save()
        return new

    def seal_png(self, spec: StampSpec) -> bytes:
        return (self.directory / SEALS_DIR / spec.image).read_bytes()

    def render(self, spec: StampSpec, **resolve) -> bytes:
        """Source PDF for placing ``spec`` now (dynamic fields filled in)."""
        if spec.kind == "image":
            return render_image_seal(self.seal_png(spec), opacity=spec.opacity, rotation=spec.rotation)
        return render_text_stamp(spec, resolve_lines(spec, **resolve))
