"""The verifier must catch planted leaks (not just pass clean files)."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core.redaction.verify import Status, verify_redaction
from tests.fixtures import generate as g


def _save(doc, path):
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


def _statuses(report):
    return {c.status for c in report.checks}


def test_clean_file_passes(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Nothing sensitive here", fontsize=12)
    report = verify_redaction(_save(doc, tmp_path / "clean.pdf"), ["SECRET-VALUE-1"], [(0, (300, 300, 400, 320))])
    assert report.passed, report.as_text()
    assert Status.FAIL not in _statuses(report)


def test_text_left_under_area_fails(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "SECRET-VALUE-1", fontsize=12)
    area = tuple(page.search_for("SECRET-VALUE-1")[0])
    report = verify_redaction(_save(doc, tmp_path / "leak.pdf"), ["SECRET-VALUE-1"], [(0, area)])
    assert not report.passed
    titles = " ".join(c.title for c in report.failures)
    assert "still readable under a redaction" in titles
    assert "text remains under the box" in titles


def test_leftover_report_says_what_and_where(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 144), "LEFT-BEHIND words", fontsize=12)  # 1 in from left, 2 in down
    other = (300, 600, 400, 620)
    area = tuple(page.search_for("LEFT-BEHIND words")[0])
    report = verify_redaction(_save(doc, tmp_path / "leak.pdf"), [], [(0, other), (0, area)])
    [failure] = report.failures
    assert failure.title == "Page 1, redaction 2 of 2: text remains under the box"
    assert '"LEFT-BEHIND words"' in failure.detail
    assert "1.0–" in failure.detail and "in from the left" in failure.detail and "1.8–2.0 in from the top" in failure.detail


def test_overlay_label_is_not_a_leftover(tmp_path):
    """Regression (user report): the REDACTED label printed on the box was reported as
    'text remains under a redaction'."""
    doc = pymupdf.open()
    page = doc.new_page()
    area = pymupdf.Rect(72, 72, 300, 100)
    page.draw_rect(area, fill=(0, 0, 0))
    page.insert_textbox(area, "REDACTED", color=(1, 1, 1), align=1)
    path = _save(doc, tmp_path / "overlay.pdf")
    assert verify_redaction(path, [], [(0, tuple(area))], overlay_text="REDACTED").passed
    assert not verify_redaction(path, [], [(0, tuple(area))]).passed  # without being told, it's text


def test_area_position_uses_the_page_as_displayed(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "ROTATED-LEFTOVER", fontsize=12)
    page.set_rotation(90)
    area = tuple(page.search_for("ROTATED-LEFTOVER")[0])
    report = verify_redaction(_save(doc, tmp_path / "rot.pdf"), [], [(0, area)])
    detail = report.failures[0].detail
    shown = (pymupdf.Rect(area) * pymupdf.open(tmp_path / "rot.pdf")[0].rotation_matrix).normalize()
    assert f"{shown.x0 / 72:.1f}–{shown.x1 / 72:.1f} in from the left" in detail


def test_leak_only_in_metadata_fails(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Body", fontsize=12)
    doc.set_metadata({"keywords": "SECRET-VALUE-1"})
    report = verify_redaction(_save(doc, tmp_path / "meta.pdf"), ["SECRET-VALUE-1"], [])
    assert not report.passed
    assert "file data" in report.failures[0].detail


def test_leak_only_in_utf16_bookmark_fails(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    doc.set_toc([[1, "Café SECRET-VALUE-1 ✓", 1]])  # non-Latin-1 char forces UTF-16
    report = verify_redaction(_save(doc, tmp_path / "toc.pdf"), ["SECRET-VALUE-1"], [])
    assert not report.passed


def test_leak_only_in_hidden_layer_found_by_second_parser(fixture_dir):
    # PyMuPDF can't see hidden-layer text; pypdf and the byte scan must.
    path = fixture_dir / "sanitize.pdf"
    secret = g.SANITIZE_SECRETS["hidden_layers"]
    with pymupdf.open(path) as doc:
        assert secret not in doc[0].get_text()
    report = verify_redaction(path, [secret], [])
    assert not report.passed
    assert any("pypdf" in c.detail for c in report.failures), report.as_text()


def test_earlier_revision_fails(fixture_dir):
    report = verify_redaction(fixture_dir / "multirevision.pdf", [g.REV2_TEXT], [])
    assert any(c.title == "Earlier revisions present" for c in report.failures)


def test_unapplied_marks_fail(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.add_redact_annot(pymupdf.Rect(72, 72, 200, 90))
    report = verify_redaction(_save(doc, tmp_path / "pending.pdf"), [], [(0, (72, 72, 200, 90))])
    assert any("Unapplied" in c.title for c in report.failures)


@pytest.mark.parametrize("quadrant", ["top_left", "bottom_right"])
def test_unredacted_image_region_fails(tmp_path, quadrant):
    """Image pixels under an area that weren't blanked must be caught. Off-center
    quadrants catch a flipped image-to-page mapping."""
    size = 200
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    detail = pymupdf.IRect(0, 0, 100, 100) if quadrant == "top_left" else pymupdf.IRect(100, 100, 200, 200)
    # A checkerboard in one quadrant only: non-uniform pixels there, white elsewhere.
    for y in range(detail.y0, detail.y1, 10):
        for x in range(detail.x0, detail.x1, 10):
            if (x // 10 + y // 10) % 2:
                pix.set_rect(pymupdf.IRect(x, y, x + 10, y + 10), (0, 0, 0))
    doc = pymupdf.open()
    page = doc.new_page()
    placed = pymupdf.Rect(100, 100, 300, 300)
    page.insert_image(placed, pixmap=pix)
    path = _save(doc, tmp_path / "img.pdf")

    covered = pymupdf.Rect(placed.x0 + detail.x0, placed.y0 + detail.y0, placed.x0 + detail.x1, placed.y0 + detail.y1)
    other = pymupdf.Rect(placed.x0 + 200 - detail.x1, placed.y0 + 200 - detail.y1, placed.x0 + 200 - detail.x0, placed.y0 + 200 - detail.y0)

    leak = verify_redaction(path, [], [(0, tuple(covered))])
    assert any("image content remains" in c.title for c in leak.failures), leak.as_text()
    blank = verify_redaction(path, [], [(0, tuple(other))])  # the plain white quadrant
    assert blank.passed, blank.as_text()


def test_short_terms_skip_text_search(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "a b c 12", fontsize=12)
    report = verify_redaction(_save(doc, tmp_path / "s.pdf"), ["12"], [])
    assert report.passed
    assert any("(short)" in c.title for c in report.checks)
