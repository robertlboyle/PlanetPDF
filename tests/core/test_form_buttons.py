from __future__ import annotations

from dataclasses import replace

import pymupdf
import pytest

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.forms import buttons as B
from planetpdf.core.forms import design as D
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind


@pytest.fixture(params=[0, 90])
def session(tmp_path, request):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).set_rotation(request.param)
    path = tmp_path / "blank.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    yield s
    s.close()


def _png(color=(255, 0, 0)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


@pytest.mark.parametrize("action", ["reset", "print", "url:https://planet.net", ""])
def test_button_round_trip(session, action):
    spec = D.FieldSpec("Button1", FieldKind.BUTTON, 0, (72, 72, 172, 96), caption="Go", action=action)
    D.create_field(session, spec)
    back = D.field_spec(session, "Button1")
    assert (back.kind, back.caption, back.action) == (FieldKind.BUTTON, "Go", action)


def test_url_gets_https_and_needs_an_address(session):
    D.create_field(session, D.FieldSpec("B", FieldKind.BUTTON, 0, (72, 72, 172, 96), caption="Web", action="url:planet.net"))
    assert D.field_spec(session, "B").action == "url:https://planet.net"
    with pytest.raises(PdfError):
        D.create_field(session, D.FieldSpec("C", FieldKind.BUTTON, 0, (72, 100, 172, 124), action="url:"))


def test_image_field_shows_the_chosen_picture_after_moves(session):
    D.create_field(session, D.FieldSpec("Photo", FieldKind.BUTTON, 0, (72, 72, 272, 172), action="image"))
    ref = F.get_field(session, "Photo").widgets[0]
    assert D.field_spec(session, "Photo").action == "image" and not B.has_image(session.doc, ref.xref)
    B.set_image(session, 0, ref.xref, _png())
    assert B.has_image(session.doc, ref.xref)
    D.move_widget(session, 0, ref.xref, (100, 300, 300, 400))
    pix = session.doc[0].get_pixmap(dpi=36)
    shown = (pymupdf.Rect(100, 300, 300, 400) * session.doc[0].rotation_matrix).normalize()
    centre = pix.pixel(int(shown.x0 + shown.width / 2) // 2, int(shown.y0 + shown.height / 2) // 2)
    assert centre[0] > 200 and centre[1] < 60  # (still red after the move)


def test_button_becomes_image_field(session):
    D.create_field(session, D.FieldSpec("B", FieldKind.BUTTON, 0, (72, 72, 172, 96), caption="Go", action="print"))
    spec = D.field_spec(session, "B")
    D.update_field(session, "B", replace(spec, action="image", caption=""))
    assert D.field_spec(session, "B").action == "image"
    D.update_field(session, "B", replace(D.field_spec(session, "B"), action="reset", caption="Clear"))
    assert (D.field_spec(session, "B").action, D.field_spec(session, "B").caption) == ("reset", "Clear")
