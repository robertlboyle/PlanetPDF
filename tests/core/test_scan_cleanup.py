"""Optimize Scans (user 2026-10-03): straighten, borders, clean, bleed-through, black and
white, page direction, blank pages, and "is this scan rough?" for Recognize Text."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image

from planetpdf.core import scan_cleanup as S
from planetpdf.core.ocr import find_tessdata

needs_ocr = pytest.mark.skipif(find_tessdata() is None, reason="needs Tesseract's tessdata")
TEXT = "Please return the signed agreement to our office before the end of the month. " * 28


def _text_page(dpi: int = 150):
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(60, 60, 552, 740), TEXT, fontsize=12)
    pix = page.get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def _rough(a):
    a = cv2.GaussianBlur(a, (0, 0), 0.6).astype(np.float32) * np.array([0.93, 0.92, 0.85])
    a += np.random.default_rng(1).normal(0, 6, a.shape)
    a = np.clip(a, 0, 255).astype(np.uint8)
    specks = np.random.default_rng(2).integers(0, a.shape[0] * a.shape[1], 300)
    a.reshape(-1, 3)[specks] = 30
    return a


def _pdf(path, pictures, sizes=None):
    doc = pymupdf.open()
    for n, picture in enumerate(pictures):
        buf = io.BytesIO()
        Image.fromarray(picture).save(buf, "JPEG", quality=85)
        width, height = (sizes or {}).get(n, (612, 792))
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    return path


def _crooked(degrees: float = 2.0, border: bool = True):
    page = _text_page()
    h, w = page.shape[:2]
    page = cv2.warpAffine(page, cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1), (w, h), borderValue=(255, 255, 255))
    if border:
        page[:, :40] = 20  # (a copier's black edge)
    return _rough(page)


def _blank_back():
    front = _text_page()
    back = (255 - (255 - front[:, ::-1]) * 0.15).astype(np.uint8)  # (the front showing through, mirrored)
    return _rough(back)


def test_only_scanned_pages_count(tmp_path):
    path = _pdf(tmp_path / "s.pdf", [_rough(_text_page())])
    doc = pymupdf.open(path)
    doc.new_page().insert_text((72, 72), "A typed page")
    assert S.scanned_pages(doc) == [0]
    report = S.cleanup_pages(doc, [0, 1], S.CleanupOptions(direction=False))
    assert report.changed == [0] and report.not_scanned == [1]
    assert doc[1].get_text().strip() == "A typed page"


def test_a_crooked_page_with_a_black_edge_comes_out_straight_white_and_black_and_white(tmp_path):
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_crooked(2.0)]))
    report = S.cleanup_pages(doc, [0], S.CleanupOptions(direction=False))
    assert report.straightened[0] == pytest.approx(-2.0, abs=0.2)
    assert report.modes[0] == "bw"
    [(xref, *_rest)] = doc[0].get_images(full=True)
    assert doc[0].get_images(full=True)[0][8] == "CCITTFaxDecode"
    left_edge = np.asarray(Image.frombytes("L", (612, 792), doc[0].get_pixmap(colorspace=pymupdf.csGRAY).samples))[:, :8]
    assert left_edge.mean() > 245  # (the black edge is gone)
    assert S.skew_angle(S._gray(S.load_picture(doc, xref))) == pytest.approx(0, abs=0.2)  # (level now)


def test_the_paper_is_made_white_and_specks_go(tmp_path):
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_rough(_text_page())]))
    S.cleanup_pages(doc, [0], S.CleanupOptions(direction=False, color="gray"))
    gray = S._gray(S.load_picture(doc, doc[0].get_images()[0][0]))
    margin = gray[:, -60:]  # (no text there: paper and specks only)
    assert np.percentile(margin, 50) > 245 and (margin < 128).sum() < 5


def test_an_empty_back_with_show_through_is_blank(tmp_path):
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_rough(_text_page()), _blank_back()]))
    assert S.find_blank_pages(doc) == [1]
    S.cleanup_pages(doc, [1], S.CleanupOptions(direction=False))
    assert min(doc[1].get_pixmap(dpi=30).samples) == 255  # (the show-through is gone)


def test_black_and_white_darkness_moves_the_threshold():
    gray = np.tile(np.arange(256, dtype=np.uint8), (20, 1))
    gray[:, :40] = 0  # (some ink, so Otsu has two groups)
    lighter = S.black_and_white(gray, -40, despeckle=False).sum()
    darker = S.black_and_white(gray, 40, despeckle=False).sum()
    assert darker > lighter


@needs_ocr
def test_pages_scanned_upside_down_or_sideways_are_turned(tmp_path):
    upside = _rough(np.rot90(_text_page(), 2).copy())
    sideways = _rough(np.rot90(_text_page(), 1).copy())  # (turned counterclockwise: needs 90 clockwise)
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_rough(_text_page()), upside, sideways], {2: (792, 612)}))
    report = S.cleanup_pages(doc, [0, 1, 2], S.CleanupOptions(color="keep", straighten=False, borders=False,
                                                                clean=False, bleed_through=False, dpi=None))
    assert report.turned == {1: 180, 2: 90}
    assert (doc[1].rotation, doc[2].rotation) == (180, 90)
    assert report.changed == []  # (only the direction: the pictures stay as they were)


def test_every_step_can_be_set_by_hand(tmp_path):
    """User 2026-10-03: automatic and manual versions of each step (manual in the advanced
    view)."""
    page = _rough(_text_page())
    options = S.CleanupOptions(direction="manual", turn=180, straighten="manual", angle=1.5, borders="manual",
                               border_mm=10, clean="manual", speck_mm=0.5, bleed_through="manual",
                               bleed_cutoff=205, color="bw", bw_threshold=100)
    steps: list[S.StepView] = []
    result, mode, angle = S.run_steps(page, options, 150, turn=180, record=steps)
    assert [s.step for s in steps] == list(S.STEPS)
    details = {s.step: s.detail for s in steps}
    assert details["direction"] == "turned 180° clockwise"
    assert angle == 1.5 and "1.5°" in details["straighten"]
    assert "10 mm band" in details["borders"] and "0.5 mm" in details["clean"]
    assert "205" in details["bleed_through"] and "threshold 100" in details["color"]
    band = int(10 / 25.4 * 150)
    assert mode == "bw" and result[:band].sum() == 0 and result[:, :band].sum() == 0  # (edges cleared)


def test_automatic_is_the_default_and_true_false_still_mean_auto_off():
    options = S.CleanupOptions(straighten=False, clean=True)
    assert options.straighten == "off" and options.clean == "auto" and options.direction == "auto"
    assert not S.CleanupOptions(direction=False, straighten=False, borders=False, clean=False,
                                bleed_through=False, color="keep", dpi=None).changes_picture


def test_automatic_bleed_through_finds_its_own_cutoff(tmp_path):
    def cleaned(picture):  # (as the steps run: the paper made white and specks gone first)
        return S.remove_specks_from(S.whiten_paper(picture), S.AUTO_SPECK_MM, 150)

    front = _text_page()
    showing = _rough(np.minimum(front, (255 - (255 - front[:, ::-1]) * 0.12).astype(np.uint8)))
    cutoff = S.find_bleed_cutoff(cleaned(showing))
    assert cutoff is not None and 195 <= cutoff <= 240
    assert S.find_bleed_cutoff(cleaned(_rough(_text_page()))) is None  # (scanner noise isn't show-through)


def test_the_preview_shows_each_step_before_and_after(tmp_path):
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_crooked(2.0)]))
    shown = S.preview(doc, 0, S.CleanupOptions(direction="off"), width=300)
    assert [s.step for s in shown.steps] == ["borders", "straighten", "clean", "bleed_through", "color"]
    assert shown.step("straighten").detail.startswith("2.0°")
    assert max(shown.original.shape[:2]) <= 300 and max(shown.final.shape[:2]) <= 300
    assert shown.step("borders").before.mean() < shown.step("borders").after.mean()  # (the black edge went)


def test_rough_scans_are_noticed_for_recognize_text(tmp_path):
    doc = pymupdf.open(_pdf(tmp_path / "s.pdf", [_crooked(2.0, border=False), _text_page()]))
    tilt, _specks = S.looks_rough(doc, [0, 1])
    assert tilt == pytest.approx(2.0, abs=0.2)
    clean = pymupdf.open(_pdf(tmp_path / "c.pdf", [_text_page()]))
    assert S.looks_rough(clean, [0]) is None
