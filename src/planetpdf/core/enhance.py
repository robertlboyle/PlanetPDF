"""Scan & OCR ▸ Enhance (Acrobat; backlog Phase C, built 2026-10-02), with OpenCV:

* Scanned Document: straighten (deskew), even out the background to white (shadows,
  yellowed paper), sharpen the text.
* Camera Image: find the sheet of paper in a photo, flatten the perspective to a
  rectangle, then the same clean-up.

The page is rendered, cleaned up, and its content replaced by the new picture (same page
size). Text on it (e.g. an OCR layer) goes: run Recognize Text again afterwards.
Undo: ``core.snapshots``.
"""

from __future__ import annotations

from collections.abc import Sequence

import pymupdf

from planetpdf.core.contents import replace_contents
from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.progress import Progress, report

DPI = 200


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise PdfError("Enhancing scans needs OpenCV, which isn't installed.") from exc
    return cv2, np


def _page_image(page: pymupdf.Page):
    cv2, np = _cv()
    pix = page.get_pixmap(dpi=DPI, alpha=False, annots=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def deskew_angle(gray) -> float:
    """The tilt of the text lines, in degrees (small angles only: a scan, not a sideways page)."""
    cv2, np = _cv()
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    points = np.column_stack(np.where(ink > 0))
    if len(points) < 50:
        return 0.0
    angle = cv2.minAreaRect(points[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    return float(angle) if abs(angle) <= 15 else 0.0


def clean_scan(image, *, color: bool = False):
    """Straighten, flatten the background to white, sharpen. ``color``: keep the colors
    (default: grayscale, like Acrobat's "Scanned Document" text settings)."""
    cv2, np = _cv()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    angle = deskew_angle(gray)
    if abs(angle) > 0.1:
        h, w = gray.shape
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        image = cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # the background, by a large closing (removes the text), divided out
    size = max(15, (min(gray.shape) // 40) | 1)
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    flat = cv2.divide(gray, background, scale=255)
    flat = cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX)
    sharp = cv2.addWeighted(flat, 1.5, cv2.GaussianBlur(flat, (0, 0), 1.2), -0.5, 0)
    if not color:
        return sharp
    # color: the same flattening on each channel
    channels = [cv2.normalize(cv2.divide(c, cv2.morphologyEx(c, cv2.MORPH_CLOSE, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (size, size))), scale=255), None, 0, 255, cv2.NORM_MINMAX) for c in cv2.split(image)]
    return cv2.merge(channels)


def find_sheet(image):
    """The four corners of the sheet of paper in a photo (top-left, top-right,
    bottom-right, bottom-left), or None."""
    cv2, np = _cv()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area = gray.shape[0] * gray.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4 and cv2.contourArea(approx) > 0.2 * area:
            pts = approx.reshape(4, 2).astype(np.float32)
            s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
            return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)
    return None


def flatten_photo(image):
    """Camera Image: the sheet straightened to a rectangle (the photo as it is when no
    sheet is found)."""
    cv2, np = _cv()
    corners = find_sheet(image)
    if corners is None:
        return image
    tl, tr, br, bl = corners
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(corners, target), (width, height))


def enhance_pages(session: PdfSession, pages: Sequence[int], mode: str = "scan", *, color: bool = False,
                  progress: Progress | None = None) -> int:
    """``mode`` "scan" (Scanned Document) or "camera" (Camera Image). Returns pages done."""
    cv2, _np = _cv()
    if mode not in ("scan", "camera"):
        raise PdfError(f"Unknown enhancement {mode!r}.")
    done = 0
    for n, index in enumerate(pages):
        if not report(progress, n, len(pages), "Enhancing page"):
            break
        page = session.doc[index]
        image = _page_image(page)
        if mode == "camera":
            image = flatten_photo(image)
        cleaned = clean_scan(image, color=color)
        ok, encoded = cv2.imencode(".png" if not color else ".jpg", cleaned,
                                   [] if not color else [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise PdfError(f"Page {index + 1} couldn't be enhanced.")
        replace_contents(session.doc, index, b"")  # (the page becomes just the cleaned picture)
        page = session.doc[index]
        shown = page.rect
        box = (pymupdf.Rect(0, 0, shown.width, shown.height) * page.derotation_matrix).normalize()
        page.insert_image(box, stream=encoded.tobytes(), rotate=page.rotation, keep_proportion=mode == "camera")
        done += 1
    if done:
        session.mark_modified()
    return done
