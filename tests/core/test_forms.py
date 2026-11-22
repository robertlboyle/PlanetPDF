from __future__ import annotations

from datetime import date

import pymupdf
import pypdf
import pytest

from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.forms import fields as F
from planetpdf.core.forms import xfa
from planetpdf.core.forms.flatten import flatten_forms
from tests.fixtures import generate as g


@pytest.fixture
def form(pdf_copy):
    s = PdfSession.open(pdf_copy("acroform.pdf"))
    yield s
    s.close()


@pytest.fixture
def extra(pdf_copy):
    s = PdfSession.open(pdf_copy("form_extra.pdf"))
    yield s
    s.close()


FILLED = {
    "full_name": "Jane Q. Doe",
    "subscribe": True,
    "plan": "premium",
    "state": "NJ",
    "services": ["Phone"],
    "start_date": "10/01/2026",
}


def _fill(session, values=FILLED):
    for name, value in values.items():
        F.set_field_value(session, name, value)


def _values(session) -> dict:
    return {f.name: f.value for f in F.list_fields(session)}


# --- fill, save, reopen --------------------------------------------------------------------


def test_fill_every_type_save_and_reopen(form):
    _fill(form)
    assert _values(form) == FILLED
    form.save()
    reopened = PdfSession.open(form.path)
    assert _values(reopened) == FILLED
    reopened.close()


def test_values_readable_by_independent_parser(form):
    _fill(form)
    form.save()
    fields = pypdf.PdfReader(form.path).get_fields()
    assert fields["full_name"]["/V"] == "Jane Q. Doe"
    assert fields["subscribe"]["/V"] == "/Yes"
    assert fields["plan"]["/V"] == "/premium"  # a PDF name, not a string
    assert fields["state"]["/V"] == "NJ"
    assert fields["start_date"]["/V"] == "10/01/2026"


def test_radio_switches_between_options(form):
    F.set_field_value(form, "plan", "basic")
    F.set_field_value(form, "plan", "premium")
    states = [form.doc.xref_get_key(w.xref, "AS")[1] for w in F.get_field(form, "plan").widgets]
    assert states == ["/Off", "/premium"]
    F.set_field_value(form, "plan", None)
    assert F.get_field(form, "plan").value is None


def test_clearing_values(form):
    _fill(form)
    for f in F.list_fields(form):
        F.set_field_value(form, f.name, F.empty_value(f))
    assert _values(form) == {"full_name": "", "subscribe": False, "plan": None, "state": "", "services": [], "start_date": ""}
    form.save()
    assert b"Jane Q. Doe" not in pymupdf.open(form.path).tobytes(expand=255)


def test_set_returns_previous_value(form):
    assert F.set_field_value(form, "full_name", "A") == ""
    assert F.set_field_value(form, "full_name", "B") == "A"


def test_invalid_values_rejected(form):
    with pytest.raises(PdfError):
        F.set_field_value(form, "state", "TX")
    with pytest.raises(PdfError):
        F.set_field_value(form, "plan", "gold")
    with pytest.raises(PdfError):
        F.set_field_value(form, "services", ["Radio"])
    with pytest.raises(PdfError):
        F.set_field_value(form, "no_such_field", "x")


def test_appearance_shows_the_value(form):
    F.set_field_value(form, "full_name", "Visible Value")
    assert "Visible Value" in form.doc[0].get_text()


# --- advanced fields --------------------------------------------------------------------------


def test_shared_field_updates_every_widget(extra):
    F.set_field_value(extra, g.SHARED_FIELD, "REF-7")
    extra.save()
    reopened = PdfSession.open(extra.path)
    assert "REF-7" in reopened.doc[0].get_text() and "REF-7" in reopened.doc[1].get_text()
    assert len(F.get_field(reopened, g.SHARED_FIELD).widgets) == 2
    reopened.close()


def test_multiselect_list_and_choice_pairs(extra):
    F.set_field_value(extra, g.MULTI_LIST, ["Fiber", "TV"])
    F.set_field_value(extra, g.PAIR_COMBO, "MW")  # export value, shown as "Midwest"
    extra.save()
    reopened = PdfSession.open(extra.path)
    assert F.get_field(reopened, g.MULTI_LIST).value == ["Fiber", "TV"]
    assert F.get_field(reopened, g.PAIR_COMBO).value == "MW"
    reopened.close()


def test_multiselect_list_draws_its_selection(extra):
    F.set_field_value(extra, g.MULTI_LIST, ["Voice", "Security"])
    extra.save()
    page = extra.doc[0]
    ref = F.get_field(extra, g.MULTI_LIST).widgets[0]
    pix = page.get_pixmap(clip=pymupdf.Rect(ref.rect), dpi=72)
    line = 11 * 1.15

    def highlighted(row: int) -> bool:
        y = int(1 + row * line + line / 2)
        r, gg, b = pix.pixel(pix.width - 6, y)[:3]  # right edge: highlight, not text
        return b > r + 20

    assert [highlighted(i) for i in range(4)] == [False, True, False, True]
    assert pypdf.PdfReader(extra.path).get_fields()[g.MULTI_LIST]["/V"] == ["Voice", "Security"]
    F.set_field_value(extra, g.MULTI_LIST, [])
    assert F.get_field(extra, g.MULTI_LIST).value == []


def test_flags(extra):
    fields = {f.name: f for f in F.list_fields(extra)}
    assert fields[g.READ_ONLY_FIELD].read_only and fields[g.READ_ONLY_FIELD].value == g.READ_ONLY_VALUE
    assert fields[g.REQUIRED_FIELD].required
    assert fields[g.MULTI_LIST].multiselect


def test_tab_order_follows_rows_and_pages(extra):
    stops = [name for name, _ in F.tab_stops(extra)]
    assert stops[:4] == g.TAB_ROW_ORDER  # despite scrambled creation order
    assert g.READ_ONLY_FIELD not in stops
    assert stops[-2:] == ["page2_first", g.SHARED_FIELD]  # page 2 comes after page 1
    assert stops.count(g.SHARED_FIELD) == 2


def test_tab_order_without_tabs_entry_uses_annotation_order(form):
    assert [n for n, _ in F.tab_stops(form)] == ["full_name", "subscribe", "plan", "plan", "state", "services", "start_date"]


# --- dates -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [("mm/dd/yyyy", "03/07/2026"), ("m/d/yy", "3/7/26"), ("yyyy-mm-dd", "2026-03-07"), ("d mmm yyyy", "7 Mar 2026"), ("mmmm d, yyyy", "March 7, 2026")],
)
def test_date_formatting_round_trip(fmt, expected):
    value = date(2026, 3, 7)
    assert F.format_date(value, fmt) == expected
    assert F.parse_date(expected, fmt) == value


def test_date_parse_rejects_nonsense():
    assert F.parse_date("not a date", "mm/dd/yyyy") is None
    assert F.parse_date("", "mm/dd/yyyy") is None


def test_date_field_detected(form):
    f = F.get_field(form, "start_date")
    assert f.kind is F.FieldKind.DATE and f.date_format == g.FORM_DATE_FORMAT


# --- flatten -----------------------------------------------------------------------------------


def test_flatten_keeps_values_as_page_content(form):
    _fill(form)
    count = flatten_forms(form)
    assert count == 7
    form.save()
    with pymupdf.open(form.path) as doc:
        page = doc[0]
        assert list(page.widgets()) == []
        assert not doc.is_form_pdf
        text = page.get_text()
    assert "Jane Q. Doe" in text and "NJ" in text and "10/01/2026" in text
    assert "Jane Q. Doe" in pypdf.PdfReader(form.path).pages[0].extract_text()


def test_flatten_leaves_other_annotations(form):
    form.doc[0].add_text_annot((400, 600), "a comment")
    flatten_forms(form)
    assert [a.type[1] for a in form.doc[0].annots()] == ["Text"]


def test_flatten_without_fields_is_noop(pdf_copy):
    s = PdfSession.open(pdf_copy("text.pdf"))
    assert flatten_forms(s) == 0
    assert not s.is_modified
    s.close()


# --- XFA -----------------------------------------------------------------------------------------


def test_xfa_detection(fixture_dir):
    for name, kind in [("acroform.pdf", xfa.XfaKind.NONE), ("xfa_hybrid.pdf", xfa.XfaKind.HYBRID), ("xfa_pure.pdf", xfa.XfaKind.PURE)]:
        with pymupdf.open(fixture_dir / name) as doc:
            assert xfa.xfa_kind(doc) is kind, name


def test_hybrid_datasets_updated_by_name(pdf_copy):
    s = PdfSession.open(pdf_copy("xfa_hybrid.pdf"))
    result = xfa.update_datasets(s.doc, {"full_name": "Jane", "state": "NJ", "not_in_xfa": "x"})
    assert result.matched == ["full_name", "state"] and result.unmatched == ["not_in_xfa"]
    s.save()
    with pymupdf.open(s.path) as doc:
        datasets = next(
            doc.xref_stream(int(x)) for n, x in xfa._PACKET.findall(doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")[1]) if n == "datasets"
        )
    assert b"<full_name>Jane</full_name>" in datasets and b"<state>NJ</state>" in datasets
    assert g.XFA_DATA_NS.encode() in datasets  # namespace preserved
    s.close()


def test_remove_xfa_makes_plain_acroform(pdf_copy):
    s = PdfSession.open(pdf_copy("xfa_hybrid.pdf"))
    xfa.remove_xfa(s.doc)
    assert xfa.xfa_kind(s.doc) is xfa.XfaKind.NONE
    assert len(F.list_fields(s)) == 6
    s.close()


def test_xfa_path_matching():
    assert xfa._xfa_path("form1[0].#subform[0].Name[0]") == ["form1", "Name"]
    assert xfa._xfa_path("simple") == ["simple"]
