import pymupdf
import pytest

from planetpdf.core import compliance as C
from planetpdf.core import ghostscript


def _plain() -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Hello", fontsize=12)  # (Helvetica: not embedded)
    page.draw_rect(pymupdf.Rect(72, 100, 200, 150), color=(1, 0, 0))  # (an RGB color)
    annot = page.add_text_annot((300, 300), "note")
    annot.set_flags(0)  # (not printed)
    annot.update()
    doc.set_metadata({"title": ""})
    return doc


def _troubled() -> pymupdf.Document:
    doc = _plain()
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(10, 10, 50, 50), "file": "x.exe"})
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(300, 400, 400, 500))
    shape.finish(fill=(0, 0, 1), fill_opacity=0.5)  # (see-through, RGB)
    shape.commit()
    return doc


def _status(result, title):
    return next(c for c in result.checks if c.title == title).status


def test_only_real_problems_fail():
    result = C.check(_plain(), "pdfa")
    failed = [c.title for c in result.checks if c.status == C.FAIL]
    assert failed == ["All fonts are embedded", "Comments and fields print as they look"]
    # what the standard adds to every file isn't a problem
    assert _status(result, "The file is marked as PDF/A") == C.ADDED
    assert _status(result, "Color profile (output intent)") == C.ADDED
    # nothing of the kind in the file: N/A, a pass
    assert _status(result, "No scripts or forbidden actions") == C.NA
    assert _status(result, "No sound, video, 3D, or attached files") == C.NA
    assert not result.passed and "2 problems to fix" in result.summary()
    fonts = next(c for c in result.checks if c.title == "All fonts are embedded")
    assert fonts.pages == [0] and "Helvetica" in fonts.details[0]


def test_empty_page_passes_with_na():
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    result = C.check(doc, "pdfa")
    assert result.passed and not result.complete
    assert _status(result, "All fonts are embedded") == C.NA
    assert _status(result, "Comments and fields print as they look") == C.NA
    assert "No problems found" in result.summary() and "Saving as PDF/A adds" in result.summary()


def test_pdfx_checks_press_rules():
    result = C.check(_troubled(), "pdfx")
    assert _status(result, "No RGB colors (CMYK, gray, or color-managed only)") == C.FAIL
    assert _status(result, "No transparency") == C.FAIL
    assert _status(result, "Every page has a trim box") == C.ADDED
    assert _status(result, "Document title") == C.ADDED
    text = result.as_text("x.pdf")
    assert "Compliance check of x.pdf: PDF/X-3" in text and "[ADDED WHEN SAVED]" in text
    assert "How to fix: Fix and Save converts the colors to CMYK." in text
    for c in result.checks:
        if c.status == C.FAIL:
            assert c.fix, c.title  # (every problem says how it gets fixed)


def test_launch_action_found():
    result = C.check(_troubled(), "pdfa")
    actions = next(c for c in result.checks if c.title == "No scripts or forbidden actions")
    assert actions.status == C.FAIL and "Launch" in actions.details[0]


def test_fix_copy_does_what_ghostscript_doesnt():
    doc = _troubled()
    changes = C.fix_copy(doc, "pdfx")
    assert any("forbidden action" in c for c in changes)
    assert any("to print" in c for c in changes)
    assert any("trim box" in c for c in changes)
    result = C.check(doc, "pdfx")
    assert _status(result, "No scripts or forbidden actions") != C.FAIL
    assert _status(result, "Comments and fields print as they look") == C.PASS
    assert _status(result, "Every page has a trim box") == C.PASS


@pytest.mark.skipif(ghostscript.find() is None, reason="needs Ghostscript")
@pytest.mark.parametrize("profile", ["pdfa", "pdfx"])
def test_fix_and_convert_meets_the_standard(tmp_path, profile):
    doc = _troubled()
    doc.set_metadata({"title": "Troubled"})
    C.fix_copy(doc, profile)
    target = tmp_path / f"{profile}.pdf"
    ghostscript.convert_standard(doc, target, profile)
    with pymupdf.open(target) as converted:
        result = C.check(converted, profile)
        assert result.complete, result.as_text(target.name)
        assert "This file meets" in result.summary()
