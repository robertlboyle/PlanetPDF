from __future__ import annotations

from dataclasses import replace

import pymupdf
import pypdf
import pytest

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.forms import design as D
from planetpdf.core.forms import fields as F
from planetpdf.core.forms.fields import FieldKind


@pytest.fixture
def blank(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(path)
    s = PdfSession.open(path)
    yield s
    s.close()


def _reopen(session):
    session.save()
    path = session.path
    session.close()
    return PdfSession.open(path)


def test_create_every_kind_and_fill(blank):
    specs = [
        D.FieldSpec("name", FieldKind.TEXT, 0, (72, 72, 272, 92), tooltip="Your name", required=True),
        D.FieldSpec("born", FieldKind.DATE, 0, (72, 100, 272, 120), date_format="mm/dd/yyyy"),
        D.FieldSpec("agree", FieldKind.CHECKBOX, 0, (72, 130, 86, 144)),
        D.FieldSpec("size", FieldKind.RADIO, 0, (72, 150, 86, 164), options=("S", "M", "L"),
                    option_rects=((72, 150, 86, 164), (100, 150, 114, 164), (128, 150, 142, 164))),
        D.FieldSpec("state", FieldKind.COMBO, 0, (72, 180, 172, 198), options=("NY", "NJ")),
        D.FieldSpec("extras", FieldKind.LIST, 0, (72, 210, 172, 260), options=("A", "B", "C"), multiselect=True),
        D.FieldSpec("sign_here", FieldKind.SIGNATURE, 1, (72, 600, 272, 650)),
    ]
    for spec in specs:
        D.create_field(blank, spec)
    s = _reopen(blank)
    fields = {f.name: f for f in F.list_fields(s)}
    assert {n: f.kind for n, f in fields.items()} == {sp.name: sp.kind for sp in specs}
    assert fields["name"].required and fields["born"].date_format == "mm/dd/yyyy"
    assert [w.on_state for w in fields["size"].widgets] == ["S", "M", "L"] and fields["size"].value is None
    assert fields["extras"].multiselect
    F.set_field_value(s, "size", "M")
    F.set_field_value(s, "name", "Jane")
    F.set_field_value(s, "extras", ["A", "C"])
    assert F.get_field(s, "size").value == "M"
    s.save()
    reader_fields = pypdf.PdfReader(s.path).get_fields()
    assert set(reader_fields) == {sp.name for sp in specs}  # other readers see the same, no stray kids
    assert reader_fields["size"]["/V"] == "/M"
    s.close()


def _ink(session, rect, pad: float = 2) -> int:
    """How many pixels in this part of page 1 aren't white."""
    pix = session.doc[0].get_pixmap(dpi=144, clip=pymupdf.Rect(rect) + (-pad, -pad, pad, pad))
    samples = pix.samples
    return sum(1 for i in range(0, len(samples), pix.n) if min(samples[i:i + 3]) < 200)


def test_new_fields_are_visible_even_when_empty(blank):
    """User bug 2026-10-02: a new dropdown / radio group made a blank box with nothing to see."""
    specs = [
        D.FieldSpec("t", FieldKind.TEXT, 0, (72, 72, 272, 92)),
        D.FieldSpec("c", FieldKind.COMBO, 0, (72, 120, 272, 140), options=("NY", "NJ")),
        D.FieldSpec("x", FieldKind.CHECKBOX, 0, (72, 160, 86, 174)),
        D.FieldSpec("r", FieldKind.RADIO, 0, (72, 200, 86, 214), options=("A", "B"),
                    option_rects=((72, 200, 86, 214), (100, 200, 114, 214))),
    ]
    for spec in specs:
        D.create_field(blank, spec)
    for spec in specs:
        assert _ink(blank, spec.rect) > 20, spec.name  # (a border at least)
    # radio buttons are drawn round from the start (they're made as square checkboxes first)
    page = blank.doc[0]
    radio = next(w for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON)
    corner = _ink(blank, (radio.rect.x0, radio.rect.y0, radio.rect.x0 + 1, radio.rect.y0 + 1), pad=0)
    assert corner == 0  # (a square's corner would be inked; a circle's isn't)
    assert F.get_field(blank, "r").value is None


def _blue(session, rect) -> int:
    """Pixels of the list-box selection highlight."""
    pix = session.doc[0].get_pixmap(dpi=72, clip=rect)
    s = pix.samples
    return sum(1 for i in range(0, len(s), pix.n) if s[i] < 180 and s[i + 2] > 200 and s[i + 2] - s[i] > 40)


def test_every_field_survives_moves_and_property_changes(blank):
    """User 2026-10-02 ("check the other form objects"): a moved radio button switched itself
    on, list boxes didn't show (or lost) their chosen items."""
    D.create_field(blank, D.FieldSpec("size", FieldKind.RADIO, 0, (72, 150, 86, 164), options=("S", "M", "L"),
                                      option_rects=((72, 150, 86, 164), (100, 150, 114, 164), (128, 150, 142, 164))))
    D.create_field(blank, D.FieldSpec("one", FieldKind.LIST, 0, (72, 200, 200, 250), options=("A", "B", "C")))
    D.create_field(blank, D.FieldSpec("many", FieldKind.LIST, 0, (72, 300, 200, 350), options=("A", "B", "C"),
                                      multiselect=True))
    F.set_field_value(blank, "size", "M")
    F.set_field_value(blank, "one", ["B"])
    F.set_field_value(blank, "many", ["A", "C"])
    assert _blue(blank, (72, 200, 200, 250)) > 50  # (a single-choice list shows its choice too)
    many_before = _blue(blank, (72, 300, 200, 350))
    assert many_before > 50
    for f in F.list_fields(blank):
        for ref in f.widgets:
            r = ref.rect
            D.move_widget(blank, ref.page, ref.xref, (r[0] + 5, r[1], r[2] + 5, r[3]))
    assert F.get_field(blank, "size").value == "M"  # (not the last button moved)
    assert [w.field_value for w in blank.doc[0].widgets() if w.field_name == "size"].count("Off") == 2
    for name in ("one", "many"):
        D.update_field(blank, name, replace(D.field_spec(blank, name), tooltip="Pick"))
    assert _blue(blank, (77, 200, 205, 250)) > 50
    assert _blue(blank, (77, 300, 205, 350)) == pytest.approx(many_before, rel=0.2)
    s = _reopen(blank)
    assert F.get_field(s, "size").value == "M" and F.get_field(s, "many").value == ["A", "C"]
    s.close()


def test_radio_buttons_show_their_text_beside_a_small_circle(blank):
    """User 2026-10-02: the circle grew with the box, the typed text never showed, and the
    name overlapped the button. A paragraph label wraps beside a text-sized circle."""
    para = "I agree to the terms of service and the monthly billing schedule.\r\nSecond line."
    D.create_field(blank, D.FieldSpec("terms", FieldKind.RADIO, 0, (72, 100, 372, 200), options=("Agree", "No"),
                                      option_rects=((72, 100, 372, 200), (72, 210, 372, 226)),
                                      labels=(para, "I do not agree.")))
    page = blank.doc[0]
    text = page.get_text()  # (the labels are in the widgets' appearance, so other viewers show them)
    assert "I agree to the terms of service" in text and "Second line." in text and "I do not agree." in text
    # the circle stays small: nothing inked in the big box's middle, left of the text
    assert _ink(blank, (80, 150, 84, 190), pad=0) == 0
    assert _ink(blank, (72, 100, 86, 116)) > 20  # (the circle, at the first line)
    F.set_field_value(blank, "terms", "Agree")
    before = _ink(blank, (72, 100, 86, 116))
    assert before > 20 and "Second line." in page.get_text()  # (choosing keeps the text)
    spec = D.field_spec(blank, "terms")
    assert spec.labels == (para, "I do not agree.")
    xref = F.get_field(blank, "terms").widgets[0].xref
    D.move_widget(blank, 0, xref, (100, 100, 400, 200))
    assert F.get_field(blank, "terms").value == "Agree" and "Second line." in blank.doc[0].get_text()
    s = _reopen(blank)
    assert [w.label for w in F.get_field(s, "terms").widgets] == [para, "I do not agree."]
    s.close()


def test_button_values_with_spaces_stay_clean_and_visible(blank):
    """User bug 2026-10-02: picking a radio button made it (and its text) invisible: values
    with spaces were escaped again on every read ("Choice#2320B")."""
    D.create_field(blank, D.FieldSpec("plan", FieldKind.RADIO, 0, (72, 100, 272, 116), options=("Choice 1", "Choice B"),
                                      option_rects=((72, 100, 272, 116), (72, 124, 272, 140)),
                                      labels=("Choice 1", "Choice B")))
    assert [w.on_state for w in F.get_field(blank, "plan").widgets] == ["Choice 1", "Choice B"]
    F.set_field_value(blank, "plan", "Choice B")
    s = _reopen(blank)
    f = F.get_field(s, "plan")
    assert f.value == "Choice B" and [w.on_state for w in f.widgets] == ["Choice 1", "Choice B"]
    assert "Choice B" in s.doc[0].get_text()  # (the chosen one is still drawn, with its text)
    assert _ink(s, (72, 124, 86, 140)) > 20
    s.close()


def test_radio_buttons_fit_their_text(blank):
    """User 2026-10-02: size the buttons and their spacing to the text; a bigger text size
    or more text grows them, a narrower group wraps (taller), never bigger than needed."""
    para = "I agree to the terms of service, including the acceptable use policy and the billing schedule."
    spec = D.FieldSpec("terms", FieldKind.RADIO, 0, (72, 100, 392, 300), options=("Agree", "No"),
                       option_rects=((72, 100, 392, 300), (72, 310, 392, 400)), labels=(para, "I do not agree."))
    fitted = D.fit_radio(blank, spec)
    (a, b) = [pymupdf.Rect(r) for r in fitted.option_rects]
    assert a.x0 == 72 and a.y0 == 100 and a.width == 320 and b.width == 320
    assert 20 < a.height < 30 and 10 < b.height < 15  # (two lines, one line: not the 200 pt drawn)
    assert 4 < b.y0 - a.y1 < 8  # (the gap follows the text size)
    bigger = [pymupdf.Rect(r) for r in D.fit_radio(blank, replace(spec, font_size=16)).option_rects]
    assert bigger[0].height > a.height and bigger[1].y0 - bigger[0].y1 > b.y0 - a.y1
    narrow = [pymupdf.Rect(r) for r in D.fit_radio(blank, spec, width=150).option_rects]
    assert narrow[0].width == 150 and narrow[0].height > a.height  # (wrapped onto more lines)
    for r, label in zip(narrow, spec.labels):  # everything fits: the drawing's last line is inside
        assert r.height >= F.radio_fit_height(r.width, label, F.RADIO_TEXT_SIZE) - 0.01
    other = replace(spec, labels=())
    assert D.fit_radio(blank, other) == other  # (another program's group: left as it is)


def test_multi_choice_lists_show_check_boxes(blank):
    D.create_field(blank, D.FieldSpec("many", FieldKind.LIST, 0, (72, 300, 272, 350), options=("A", "B", "C"),
                                      multiselect=True))
    F.set_field_value(blank, "many", ["B"])
    xref = F.get_field(blank, "many").widgets[0].xref
    ap = blank.doc.xref_stream(int(blank.doc.xref_get_key(xref, "AP/N")[1].split()[0]))
    assert ap.count(b"re B") == 3 and F.LIST_HIGHLIGHT.encode() in ap  # (a box per choice; one chosen)


def test_label_wrapping_keeps_line_breaks():
    lines = F.wrap_label("one two three four five six\nseven", width=60, size=10)
    assert lines[-1] == "seven" and len(lines) >= 3 and all(lines)
    assert F.wrap_label("", 50, 10) == [""]


def test_list_box_on_a_rotated_page_reads_upright(blank):
    blank.doc[0].set_rotation(90)
    D.create_field(blank, D.FieldSpec("many", FieldKind.LIST, 0, (72, 300, 272, 350), options=("A", "B"),
                                      multiselect=True))
    F.set_field_value(blank, "many", ["A"])
    xref = F.get_field(blank, "many").widgets[0].xref
    ap = int(blank.doc.xref_get_key(xref, "AP/N")[1].split()[0])
    source = blank.doc.xref_object(ap, compressed=True)
    assert "/Matrix[0 1 -1 0 0 0]" in source.replace(" [", "[") and "/BBox[0 0 50" in source.replace(" [", "[")


def test_validation_and_duplicates(blank):
    with pytest.raises(PdfError):
        D.create_field(blank, D.FieldSpec("a.b", FieldKind.TEXT, 0, (0, 0, 10, 10)))
    with pytest.raises(PdfError):
        D.create_field(blank, D.FieldSpec("c", FieldKind.COMBO, 0, (0, 0, 10, 10)))
    D.create_field(blank, D.FieldSpec("x", FieldKind.TEXT, 0, (72, 72, 172, 92)))
    with pytest.raises(PdfError):
        D.create_field(blank, D.FieldSpec("x", FieldKind.TEXT, 0, (72, 100, 172, 120)))
    assert D.unique_name(blank, "x") == "x1"
    D.create_field(blank, D.FieldSpec("Text1", FieldKind.TEXT, 0, (72, 130, 172, 150)))
    assert D.unique_name(blank, "Text") == "Text2"


def test_delete_and_recreate_round_trip(pdf_copy):
    s = PdfSession.open(pdf_copy("acroform.pdf"))
    before = {f.name: f for f in F.list_fields(s)}
    spec = D.delete_field(s, "plan")
    assert "plan" not in {f.name for f in F.list_fields(s)}
    assert len(D._acro_fields(s.doc)) == len(before) - 1  # the radio parent is gone too
    D.create_field(s, spec)
    again = F.get_field(s, "plan")
    assert [w.rect for w in again.widgets] == [w.rect for w in before["plan"].widgets]
    for name in ("state", "full_name", "start_date"):
        spec = D.delete_field(s, name)
        D.create_field(s, spec)
        f = F.get_field(s, name)
        assert f.kind is before[name].kind and f.options == before[name].options and f.date_format == before[name].date_format
    s.close()


def test_move_and_update_in_place(pdf_copy):
    s = PdfSession.open(pdf_copy("acroform.pdf"))
    f = F.get_field(s, "full_name")
    old = D.move_widget(s, 0, f.widgets[0].xref, (300, 400, 450, 420))
    assert F.get_field(s, "full_name").widgets[0].rect == (300, 400, 450, 420)
    assert old == f.widgets[0].rect
    spec = D.field_spec(s, "full_name")
    D.update_field(s, "full_name", D.replace(spec, name="customer", required=True, tooltip="Customer name"))
    f = F.get_field(s, "customer")
    assert f.required and D.field_spec(s, "customer").tooltip == "Customer name"
    spec = D.field_spec(s, "plan")
    D.update_field(s, "plan", D.replace(spec, name="tier", read_only=True))
    assert F.get_field(s, "tier").read_only and [w.on_state for w in F.get_field(s, "tier").widgets] == ["basic", "premium"]
    D.update_field(s, "state", D.replace(D.field_spec(s, "state"), options=("NY", "NJ", "PA")))
    assert F.get_field(s, "state").options == ["NY", "NJ", "PA"]
    s.close()


def test_field_on_rotated_page_reads_upright(blank):
    blank.doc[0].set_rotation(90)
    page = blank.doc[0]
    rect = tuple(pymupdf.Rect(100, 100, 300, 120) * page.derotation_matrix)
    D.create_field(blank, D.FieldSpec("r", FieldKind.TEXT, 0, rect, value="Hello there", font_size=12))
    kind, value = blank.doc.xref_get_key(F.get_field(blank, "r").widgets[0].xref, "MK/R")
    assert value == "90"

@pytest.mark.parametrize("rotation", [0, 90])
def test_checkbox_shows_its_own_text(tmp_path, rotation):
    """User 2026-10-04: "the checkbox doesn't allow me to edit the text that is shown"."""
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).set_rotation(rotation)
    doc.save(tmp_path / "c.pdf")
    s = PdfSession.open(tmp_path / "c.pdf")
    try:
        spec = D.FieldSpec("Agree", FieldKind.CHECKBOX, 0, (72, 72, 86, 86), labels=("",))
        D.create_field(s, spec)
        new = D.fit_checkbox(s, replace(D.field_spec(s, "Agree"), labels=("I agree to the terms",)))
        D.replace_field(s, "Agree", new)
        back = D.field_spec(s, "Agree")
        assert back.labels == ("I agree to the terms",)
        shown = (pymupdf.Rect(back.rect) * s.doc[0].rotation_matrix).normalize()
        assert shown.width >= D.CHECKBOX_TEXT_WIDTH - 1  # (wide enough for its text now)
        assert "I agree to the terms" in s.doc[0].get_text()  # (the text is in the field: every viewer shows it)
        F.set_field_value(s, "Agree", True)
        assert F.get_field(s, "Agree").value is True
        assert s.doc.xref_get_key(F.get_field(s, "Agree").widgets[0].xref, "AS")[1] != "/Off"
        F.set_field_value(s, "Agree", False)
        assert F.get_field(s, "Agree").value is False
    finally:
        s.close()

@pytest.mark.parametrize("rotation", [0, 90])
def test_labeled_buttons_are_stored_the_standard_way(tmp_path, rotation):
    """User 2026-10-04 (Acrobat screenshot): Acrobat redrew our radio buttons / checkbox from
    the fields' own properties, centered in the whole area, and their text vanished. Now
    the widget is only the circle / box, and the text is page text beside it."""
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).set_rotation(rotation)
    doc.save(tmp_path / "s.pdf")
    s = PdfSession.open(tmp_path / "s.pdf")
    try:
        def shown(r):  # (areas given as displayed, on a turned page too)
            return tuple((pymupdf.Rect(r) * s.doc[0].derotation_matrix).normalize())

        first, second, agree = shown((72, 72, 252, 88)), shown((72, 100, 252, 116)), shown((72, 200, 252, 214))
        radio = D.fit_radio(s, D.FieldSpec("Size", FieldKind.RADIO, 0, first, options=("S", "L"),
                                           option_rects=(first, second), labels=("Small one", "Large one")))
        D.create_field(s, radio)
        D.create_field(s, D.FieldSpec("Agree", FieldKind.CHECKBOX, 0, agree, labels=("I agree",)))
        page = s.doc[0]
        for w in page.widgets():
            r = w.rect
            assert max(r.width, r.height) < 13  # (just the circle / box, the size of the text)
        # (what PlanetPDF works with is still the whole area)
        assert F.get_field(s, "Agree").widgets[0].rect == pytest.approx(agree, abs=0.01)
        # the text is the page's own: still there with every field gone (Acrobat redrawing them)
        bare = pymupdf.open("pdf", s.doc.tobytes())
        bare.xref_set_key(bare[0].xref, "Annots", "[]")  # (no fields left at all)
        text = bare[0].get_text()
        assert "Small one" in text and "Large one" in text and "I agree" in text
        # moved, renamed, deleted: the text follows
        ref = F.get_field(s, "Agree").widgets[0]
        D.move_widget(s, 0, ref.xref, shown((300, 300, 480, 314)))
        D.update_field(s, "Agree", replace(D.field_spec(s, "Agree"), name="Consent"))
        assert s.doc[0].search_for("I agree")[0].x0 == pytest.approx(300 + 13.6, abs=6) or rotation
        D.delete_field(s, "Consent")
        D.delete_field(s, "Size")
        bare = pymupdf.open("pdf", s.doc.tobytes())
        bare.xref_set_key(bare[0].xref, "Annots", "[]")
        assert "I agree" not in bare[0].get_text() and "Small one" not in bare[0].get_text()
    finally:
        s.close()
