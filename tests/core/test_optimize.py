"""Compress PDF (user 2026-10-03: "Some files especially scanned files are huge")."""

from __future__ import annotations

import io

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageFilter

from planetpdf.core import optimize as O
from planetpdf.core.document import PdfSession


def _scan(kind: str, dpi: int = 120) -> Image.Image:
    """A page as a scanner gives it: text, a little blur, tinted paper and noise;
    "gray": with a shaded box; "color": with a red box."""
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(60, 60, 552, 740), "The quick brown fox jumps over the lazy dog. " * 50,
                        fontsize=12)
    pix = page.get_pixmap(dpi=dpi)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).filter(ImageFilter.GaussianBlur(0.5))
    pixels = np.asarray(image).astype(np.float32) * np.array([0.97, 0.96, 0.92])
    pixels += np.random.default_rng(1).normal(0, 5, pixels.shape)
    pixels = np.clip(pixels, 0, 255).astype(np.uint8)
    h, w = pixels.shape[:2]
    if kind == "gray":
        pixels = np.repeat(np.asarray(Image.fromarray(pixels).convert("L"))[..., None], 3, axis=2).copy()
        pixels[h // 2:h * 3 // 4, w // 8:w * 7 // 8] = (pixels[h // 2:h * 3 // 4, w // 8:w * 7 // 8] * 0.75).astype(np.uint8)
    elif kind == "color":
        pixels[h // 10:h // 5, w // 8:w // 2] = [200, 40, 40]
    return Image.fromarray(pixels)


def scanned_pdf(path, kinds=("text", "gray", "color")):
    doc = pymupdf.open()
    for kind in kinds:
        buf = io.BytesIO()
        _scan(kind).save(buf, "JPEG", quality=85)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    return path


def _filters(doc):
    return [img[8] for page in doc for img in page.get_images(full=True)]


def test_scanned_pages_are_told_apart(tmp_path):
    doc = pymupdf.open(scanned_pdf(tmp_path / "s.pdf"))
    kinds = {page: O.classify(*O._pil(doc, xref)) for xref, page in O.scanned_images(doc).items()}
    assert kinds == {0: "bw", 1: "gray", 2: "color"}


def test_a_text_scan_becomes_black_and_white_and_stays_readable(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", ("text",))
    before = path.stat().st_size
    result = O.reduce_size(pymupdf.open(path), tmp_path / "small.pdf", O.ReduceOptions(), before)
    assert result.black_white == 1 and result.after < before / 5
    with pymupdf.open(tmp_path / "small.pdf") as small, pymupdf.open(path) as original:
        assert _filters(small) == ["CCITTFaxDecode"]
        a = np.asarray(Image.frombytes("L", (612, 792), original[0].get_pixmap(colorspace=pymupdf.csGRAY).samples))
        b = np.asarray(Image.frombytes("L", (612, 792), small[0].get_pixmap(colorspace=pymupdf.csGRAY).samples))
        ink_a, ink_b = a < 128, b < 128  # (the same ink, where it was; soft edges turn black: a bit bolder)
        assert 0.8 < ink_b.mean() / ink_a.mean() < 1.4
        assert (ink_b & (a < 200)).sum() / ink_b.sum() > 0.95
        assert b[400:420, 590:600].mean() > 250  # (the paper's tint is gone: white)


def test_shading_and_color_are_never_made_black_and_white(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", ("gray", "color"))
    result = O.reduce_size(pymupdf.open(path), tmp_path / "small.pdf", O.ReduceOptions(), path.stat().st_size)
    assert result.black_white == 0 and result.gray == 1
    with pymupdf.open(tmp_path / "small.pdf") as small:
        assert [img[5] for page in small for img in page.get_images(full=True)][0] == "DeviceGray"
        assert all(f == "DCTDecode" for f in _filters(small))


def test_scans_can_be_left_alone(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", ("text",))
    options = O.ReduceOptions(image_dpi=None, scans_black_white=False, scans_gray=False)
    result = O.reduce_size(pymupdf.open(path), tmp_path / "small.pdf", options, path.stat().st_size)
    assert result.black_white == result.gray == 0
    with pymupdf.open(tmp_path / "small.pdf") as small:
        assert _filters(small) == ["DCTDecode"]


def test_pictures_really_come_down_to_the_chosen_resolution(tmp_path):
    """MuPDF's default "average" subsampling never took a 300 dpi picture to 150 dpi."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(2).integers(0, 255, (1650, 1275, 3), dtype=np.uint8)).save(buf, "JPEG")
    page.insert_image(pymupdf.Rect(0, 0, 306, 396), stream=buf.getvalue())  # (300 dpi as placed)
    doc.save(tmp_path / "photo.pdf")
    O.reduce_size(pymupdf.open(tmp_path / "photo.pdf"), tmp_path / "small.pdf", O.ReduceOptions(image_dpi=150), 1)
    with pymupdf.open(tmp_path / "small.pdf") as small:
        [(_xref, _smask, width, *_rest)] = small[0].get_images(full=True)
    assert width == pytest.approx(637, abs=3)  # (306 pt at 150 dpi)


def test_compressing_in_place_keeps_the_password(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", ("text",))
    with pymupdf.open(path) as doc:
        doc.save(tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    session = PdfSession.open(tmp_path / "locked.pdf", password="secret")
    try:
        data, done = O.compressed_bytes(session.open_snapshot(session.snapshot()), O.ReduceOptions())
        assert done.black_white == 1
        with pymupdf.open(stream=data) as small:
            assert small.needs_pass
    finally:
        session.close()


def test_space_usage_says_what_takes_the_room(tmp_path):
    doc = pymupdf.open(scanned_pdf(tmp_path / "s.pdf", ("text", "color")))
    doc[0].insert_text((50, 50), "a note", fontname="helv")
    usage = O.space_usage(doc)
    (top, _size, percent), *_ = usage.rows()
    assert top == "Pictures" and percent > 90 and usage.scanned_pages == 2
