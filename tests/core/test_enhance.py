"""Scan & OCR ▸ Enhance: Scanned Document and Camera Image (OpenCV)."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

from planetpdf.core import enhance as E
from planetpdf.core.document import PdfSession


def _scan(tilt: float = 4.0, shade: bool = True):
    """A gray, unevenly lit, tilted "scan" of black text lines."""
    image = np.full((1100, 850, 3), 200, np.uint8)
    for y in range(150, 950, 60):
        cv2.putText(image, "PLANET NETWORKS FIBER INVOICE", (80, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
    if shade:
        ramp = np.linspace(0.7, 1.0, image.shape[1])[None, :, None]
        image = (image * ramp).astype(np.uint8)
    matrix = cv2.getRotationMatrix2D((425, 550), tilt, 1.0)
    return cv2.warpAffine(image, matrix, (850, 1100), borderValue=(200, 200, 200))


def test_deskew_finds_the_tilt():
    gray = cv2.cvtColor(_scan(tilt=4.0, shade=False), cv2.COLOR_BGR2GRAY)
    assert abs(abs(E.deskew_angle(gray)) - 4.0) < 1.0


def test_clean_scan_makes_the_background_white():
    cleaned = E.clean_scan(_scan())
    assert cleaned.ndim == 2  # (grayscale)
    background = np.percentile(cleaned, 75)
    assert background > 240  # (the gray, shaded paper is now white)
    assert cleaned.min() < 80  # (and the text is still dark)


def test_camera_photo_is_flattened_to_the_sheet():
    photo = np.full((900, 1200, 3), 60, np.uint8)  # (a dark desk)
    sheet = np.array([[300, 120], [900, 160], [950, 800], [250, 760]], np.int32)
    cv2.fillPoly(photo, [sheet], (235, 235, 235))
    corners = E.find_sheet(photo)
    assert corners is not None and np.allclose(corners[0], [300, 120], atol=15)
    flat = E.flatten_photo(photo)
    assert flat.shape[0] > 500 and flat.shape[1] > 500 and flat.mean() > 200  # (just the sheet)


@pytest.mark.parametrize("mode", ["scan", "camera"])
def test_enhance_pages_replaces_the_page_with_the_clean_picture(tmp_path, mode):
    ok, png = cv2.imencode(".png", _scan())
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=png.tobytes())
    doc.new_page(width=612, height=792).insert_text((72, 72), "Untouched page")
    doc.save(tmp_path / "scan.pdf")
    session = PdfSession.open(tmp_path / "scan.pdf")
    try:
        seen = []
        assert E.enhance_pages(session, [0], mode, progress=lambda d, t, s="": seen.append((d, t, s)) or True) == 1
        assert seen[0] == (0, 1, "Enhancing page")
        page = session.doc[0]
        assert len(page.get_image_info()) == 1  # (drawn: the old picture is left unused, dropped on save)
        pix = page.get_pixmap(dpi=36)
        corner = pix.pixel(pix.width - 3, 3)
        assert min(corner) > 230  # (the shaded gray corner is white now)
        assert "Untouched page" in session.doc[1].get_text()
    finally:
        session.close()
