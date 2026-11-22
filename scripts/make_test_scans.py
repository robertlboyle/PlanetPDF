"""Make test-scans/scan-test-pages.pdf: realistic scanned pages for trying Optimize Scans and
Compress PDF (user 2026-10-03: "Give me a few test pages"). Each page is a 300 dpi JPEG
picture of a page, roughened like a real scan, testing one thing (see PAGES).

    .venv\\Scripts\\python.exe scripts\\make_test_scans.py
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

DPI = 300
LETTER = (612, 792)
OUT = Path(__file__).resolve().parent.parent / "test-scans"

LETTER_TEXT = (
    "Planet Networks\n123 Example Road, Anytown, PA 17000\n\nOctober 3, 2026\n\nDear Customer,\n\n"
    "Thank you for your recent order. Please review the enclosed agreement, sign where indicated, and "
    "return it to our office before the end of the month. If you have any questions about the terms, "
    "your account manager will be happy to help.\n\n"
    "The service will begin on the first business day after we receive the signed agreement. Invoices "
    "are sent monthly and are due within thirty days. Late payments may incur a fee as described in "
    "section 4 of the agreement.\n\n"
    "We appreciate your business and look forward to working with you.\n\nSincerely,\n\n\n\nAccount Services"
)
BACK_TEXT = ("Terms and Conditions (continued)\n\n" + "4. Payment. Invoices are due within thirty days of the "
             "invoice date. Amounts not paid when due may incur a late fee of 1.5% per month. " * 9)


def _render(build, size=LETTER) -> np.ndarray:
    """A page drawn with PyMuPDF, as a 300 dpi RGB array."""
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    build(page)
    pix = page.get_pixmap(dpi=DPI)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def _letter(page) -> None:
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), LETTER_TEXT, fontsize=11, fontname="tiro")


def _back(page) -> None:
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), BACK_TEXT, fontsize=10.5, fontname="helv")


def _invoice(page) -> None:
    page.insert_text((72, 90), "INVOICE 10442", fontsize=20, fontname="hebo")
    page.draw_rect(pymupdf.Rect(72, 130, 540, 160), fill=(0.82, 0.82, 0.82), color=None)  # (a shaded header row)
    for n, head in enumerate(("Item", "Qty", "Price", "Total")):
        page.insert_text((80 + n * 115, 150), head, fontsize=11, fontname="hebo")
    for row in range(8):
        y = 180 + row * 26
        if row % 2:
            page.draw_rect(pymupdf.Rect(72, y - 17, 540, y + 7), fill=(0.93, 0.93, 0.93), color=None)
        for n, cell in enumerate((f"Service plan {row + 1}", str(row + 1), "$49.00", f"${49 * (row + 1)}.00")):
            page.insert_text((80 + n * 115, y), cell, fontsize=10.5, fontname="helv")
    page.draw_rect(pymupdf.Rect(360, 420, 540, 460), fill=(0.75, 0.75, 0.75), color=None)
    page.insert_text((370, 446), "Amount due: $1,764.00", fontsize=11, fontname="hebo")


def _brochure(page) -> None:
    page.draw_rect(pymupdf.Rect(0, 0, 612, 120), fill=(0.05, 0.25, 0.55), color=None)
    page.insert_text((72, 80), "Faster Internet for Your Business", fontsize=24, fontname="hebo", color=(1, 1, 1))
    page.draw_circle((480, 300), 70, fill=(0.95, 0.55, 0.1), color=None)
    page.insert_textbox(pymupdf.Rect(72, 180, 380, 700), LETTER_TEXT, fontsize=10.5, fontname="helv")


def _signed(page) -> None:
    _letter(page)
    shape = page.new_shape()  # (a signature in blue ink, and a red "RECEIVED" stamp)
    points = [(110 + t * 1.6, 655 - 12 * np.sin(t / 7) - 6 * np.sin(t / 2.3)) for t in range(110)]
    for a, b in zip(points, points[1:]):
        shape.draw_line(a, b)
    shape.finish(color=(0.1, 0.2, 0.75), width=1.8)
    shape.commit()
    page.draw_rect(pymupdf.Rect(380, 600, 530, 650), color=(0.8, 0.1, 0.1), width=3)
    page.insert_text((392, 632), "RECEIVED", fontsize=20, fontname="hebo", color=(0.8, 0.1, 0.1))


def _faint(page) -> None:
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), "Meeting notes (pencil, faint)\n\n" + LETTER_TEXT,
                        fontsize=12, fontname="tiro", color=(0.62, 0.62, 0.62))


# --- the scanner's touch ---------------------------------------------------------------------------------


def scan(picture: np.ndarray, *, tint=(0.95, 0.93, 0.86), noise=5.0, blur=0.7, specks=150, seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = cv2.GaussianBlur(picture, (0, 0), blur).astype(np.float32) * np.array(tint)
    h, w = image.shape[:2]
    shade = np.linspace(1.0, 0.94, w)[None, :, None]  # (uneven light across the glass)
    image = image * shade + rng.normal(0, noise, image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)
    for _ in range(specks):  # (dust: small dark dots)
        y, x = rng.integers(0, h), rng.integers(0, w)
        cv2.circle(image, (int(x), int(y)), int(rng.integers(1, 3)), (40, 40, 40), -1)
    return image


def tilt(picture: np.ndarray, degrees: float) -> np.ndarray:
    h, w = picture.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(picture, matrix, (w, h), borderValue=(255, 255, 255))


def black_edge(picture: np.ndarray, side: str = "left", width: int = 110) -> np.ndarray:
    out = picture.copy()
    if side == "left":
        out[:, :width] = 25
    else:
        out[-width:, :] = 25
    return out


def show_through(front: np.ndarray, back: np.ndarray, strength: float = 0.14) -> np.ndarray:
    mirrored = np.roll(back[:, ::-1], 18, axis=0)
    ghost = (255 - (255 - mirrored.astype(np.float32)) * strength).astype(np.uint8)
    return np.minimum(front, ghost)


PAGES = [
    ("Crooked page (3°) with a black copier edge and yellowed paper",
     lambda: scan(black_edge(tilt(_render(_letter), 3.0)), tint=(0.96, 0.92, 0.78))),
    ("Upside-down page", lambda: scan(np.rot90(_render(_letter), 2).copy(), seed=2)),
    ("Sideways page (scanned turned 90°)", lambda: scan(np.rot90(_render(_letter), 1).copy(), seed=3)),
    ("Thin paper, printed on both sides: the back shows through",
     lambda: scan(show_through(_render(_letter), _render(_back)), seed=4)),
    ("Empty back of a page, with the front showing through (blank page)",
     lambda: scan(show_through(np.full_like(_render(_letter), 255), _render(_letter), 0.12), seed=5)),
    ("Truly blank page with dust (blank page)", lambda: scan(np.full_like(_render(_letter), 255), specks=600, seed=6)),
    ("Invoice with gray shaded rows (should stay grayscale)", lambda: scan(_render(_invoice), seed=7)),
    ("Color brochure (should stay in color)", lambda: scan(_render(_brochure), seed=8)),
    ("Letter with a blue signature and a red stamp (color; try Black and white to see it go)",
     lambda: scan(_render(_signed), seed=9)),
    ("Faint pencil-gray writing (try the Lighter / Darker slider)", lambda: scan(_render(_faint), seed=10)),
    ("Very dusty, slightly crooked page", lambda: scan(tilt(_render(_letter), -1.2), specks=2500, noise=8, seed=11)),
    ("Crooked page with the black edge along the bottom", lambda: scan(black_edge(tilt(_render(_back), -2.0), "bottom"),
                                                                       seed=12)),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    doc = pymupdf.open()
    lines = ["Optimize Scans test pages (made by scripts/make_test_scans.py)", ""]
    for n, (what, make) in enumerate(PAGES, start=1):
        picture = make()
        h, w = picture.shape[:2]
        buf = io.BytesIO()
        Image.fromarray(picture).save(buf, "JPEG", quality=88, dpi=(DPI, DPI))
        page = doc.new_page(width=w * 72 / DPI, height=h * 72 / DPI)
        page.insert_image(page.rect, stream=buf.getvalue())
        lines.append(f"Page {n}: {what}")
        print(f"page {n} of {len(PAGES)}: {what}")
    doc.new_page(width=LETTER[0], height=LETTER[1]).insert_textbox(
        pymupdf.Rect(72, 72, 540, 720), "Page 13: a typed page, not a scan. Optimize Scans leaves it alone.\n\n"
        + LETTER_TEXT, fontsize=11)
    lines.append("Page 13: A typed page, not a scan (Optimize Scans should leave it alone)")
    target = OUT / "scan-test-pages.pdf"
    doc.save(target, garbage=3, deflate=True)
    (OUT / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target} ({target.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
