"""Page rendering to raw RGB bytes. The UI wraps the result in a QImage."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from planetpdf.core.document import PdfSession

# Guard against absurd allocations at extreme zoom levels on huge pages.
MAX_RENDER_PIXELS = 40_000_000


@dataclass(frozen=True)
class RenderedPage:
    width: int
    height: int
    stride: int
    samples: bytes  # RGB888, ``stride`` bytes per row


def render_page(session: PdfSession, index: int, scale: float) -> RenderedPage:
    """Render page ``index`` at ``scale`` pixels per PDF point (annotations included)."""
    page = session.doc[index]
    rect = page.rect
    pixels = rect.width * rect.height * scale * scale
    if pixels > MAX_RENDER_PIXELS:
        scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
    return RenderedPage(pix.width, pix.height, pix.stride, pix.samples)


def render_area(session: PdfSession, index: int, area: tuple[float, float, float, float], scale: float) -> RenderedPage:
    """Render only ``area`` (x0, y0, x1, y1 in points, as the page is displayed) of page
    ``index`` at ``scale``: the Loupe Tool's sharp close-up."""
    page = session.doc[index]
    clip = pymupdf.Rect(area) & page.rect
    if clip.is_empty:
        clip = pymupdf.Rect(page.rect.x0, page.rect.y0, page.rect.x0 + 1, page.rect.y0 + 1)
    pixels = clip.width * clip.height * scale * scale
    if pixels > MAX_RENDER_PIXELS:
        scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False, annots=True)
    return RenderedPage(pix.width, pix.height, pix.stride, pix.samples)


def content_bounds(session: PdfSession, index: int) -> tuple[float, float, float, float] | None:
    """Where page ``index`` has anything drawn (points, as displayed), for Fit Visible:
    the page's margins left out. None for a blank page."""
    from PIL import Image

    page = session.doc[index]
    scale = 0.5  # (1/144 in per pixel is plenty for fitting)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False, annots=True)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples, "raw", "L", pix.stride)
    box = image.point(lambda v: 255 if v < 245 else 0).getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    r = page.rect
    return (r.x0 + x0 / scale, r.y0 + y0 / scale, r.x0 + x1 / scale, r.y0 + y1 / scale)
