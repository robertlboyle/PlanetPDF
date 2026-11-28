"""Each sanitize option removes exactly its own secret, verified in the saved bytes."""

from __future__ import annotations

import io

import pymupdf
import pypdf
import pytest

from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionOptions, redact_to_file
from planetpdf.core.redaction.ocg import hidden_ocgs, remove_hidden_layers, tokenize
from planetpdf.core.redaction.sanitize import LABELS, SanitizeOptions, sanitize
from tests.fixtures import generate as g

OPTION_NAMES = list(SanitizeOptions.__dataclass_fields__)


def _only(name: str) -> SanitizeOptions:
    return SanitizeOptions(**{k: k == name for k in OPTION_NAMES})


def _saved_bytes(doc: pymupdf.Document) -> bytes:
    data = doc.tobytes(garbage=4, clean=True, deflate=True)
    expanded = pymupdf.open(stream=data, filetype="pdf").tobytes(expand=255)
    return data + expanded


def _everything(data: bytes) -> str:
    """All text two parsers can extract, plus decoded bytes."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    mu = "\n".join(p.get_text() for p in doc)
    py = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages)
    return mu + py


def test_every_option_has_a_label():
    assert set(LABELS) == set(OPTION_NAMES)


def test_fixture_contains_every_secret(fixture_dir):
    with pymupdf.open(fixture_dir / "sanitize.pdf") as doc:
        data = _saved_bytes(doc)
    for name, secret in g.SANITIZE_SECRETS.items():
        assert g.bytes_contain_text(data, secret) or secret in _everything(data), name


@pytest.mark.parametrize("name", OPTION_NAMES)
def test_each_option_removes_only_its_secret(fixture_dir, name):
    doc = pymupdf.open(fixture_dir / "sanitize.pdf")
    sanitize(doc, _only(name))
    data = _saved_bytes(doc)
    text = _everything(data)
    secret = g.SANITIZE_SECRETS[name]
    assert not g.bytes_contain_text(data, secret) and secret not in text, f"{name} left {secret}"
    for other, other_secret in g.SANITIZE_SECRETS.items():
        if other != name:
            assert g.bytes_contain_text(data, other_secret) or other_secret in text, (
                f"{name} also removed {other}"
            )
    assert g.BODY_TEXT in text


def test_all_options_together(fixture_dir):
    doc = pymupdf.open(fixture_dir / "sanitize.pdf")
    result = sanitize(doc, SanitizeOptions())
    data = _saved_bytes(doc)
    text = _everything(data)
    for secret in g.SANITIZE_SECRETS.values():
        assert not g.bytes_contain_text(data, secret) and secret not in text, secret
    assert g.BODY_TEXT in text
    assert g.VISIBLE_LAYER_TEXT in text  # visible layer content stays, just un-layered
    assert result.removed["hidden_layers"] >= 1
    reopened = pymupdf.open(stream=data, filetype="pdf")
    assert reopened.get_toc() == []
    assert reopened.embfile_count() == 0
    assert reopened.xref_get_key(reopened.pdf_catalog(), "OCProperties")[0] == "null"


def test_redact_with_sanitize_end_to_end(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("sanitize.pdf"))
    area = session.doc[0].search_for(g.BODY_TEXT)[0]
    M.add_mark(session, M.make_spec(0, [tuple(area)], "Area"))
    out = tmp_path / "clean.pdf"
    secrets = list(g.SANITIZE_SECRETS.values())
    result = redact_to_file(session, out, RedactionOptions(), SanitizeOptions(), extra_terms=secrets)
    session.close()
    assert result.report.passed, result.report.as_text()
    assert out.read_bytes().count(b"%%EOF") == 1


def test_form_values_cleared_for_every_field_type(fixture_dir):
    doc = pymupdf.open(fixture_dir / "acroform.pdf")
    page = doc[0]
    for w in page.widgets():
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
            w.field_value = "FILLED-VALUE-9"
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            w.field_value = True
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_COMBOBOX:
            w.field_value = "NJ"
        w.update()
    sanitize(doc, _only("form_values"))
    data = _saved_bytes(doc)
    assert not g.bytes_contain_text(data, "FILLED-VALUE-9")
    reopened = pymupdf.open(stream=data, filetype="pdf")
    rpage = reopened[0]
    for w in rpage.widgets():
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
            assert (w.field_value or "").strip() == ""
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            assert w.field_value in (False, "Off", "")
    assert len(list(rpage.widgets())) == 7  # fields kept, only values cleared


# --- hidden layers in detail -------------------------------------------------------------------


def test_tokenizer_handles_tricky_syntax():
    data = (
        b"q (a (nested) \\) paren) Tj <48656c6c6f> Tj % comment BDC\n"
        b"/OC /MC0 BDC [ (x) 12 ] TJ EMC BI /W 2 /H 1 /BPC 8 /CS /G ID \x00EMC\x01 EI Q"
    )
    ops = [t.value for t in tokenize(data) if t.kind == "op"]
    assert ops == [b"q", b"Tj", b"Tj", b"BDC", b"TJ", b"EMC", b"BI", b"ID", b"Q"]


def test_nested_and_ocmd_hidden_content(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    on = doc.add_ocg("On", on=True)
    off = doc.add_ocg("Off", on=False)
    page.insert_text((72, 72), "SHOWN-TEXT", oc=on)
    page.insert_text((72, 100), "HIDDEN-TEXT", oc=off)
    # An OCMD that is visible only if *all* members are on -> hidden (one is off).
    ocmd = doc.set_ocmd(ocgs=[on, off], policy="AllOn")
    page.insert_text((72, 130), "OCMD-HIDDEN", oc=ocmd)
    path = tmp_path / "layers.pdf"
    doc.save(path)
    doc.close()

    doc = pymupdf.open(path)
    assert hidden_ocgs(doc) == {off}
    assert remove_hidden_layers(doc) == 2
    data = _saved_bytes(doc)
    text = _everything(data)
    assert "SHOWN-TEXT" in text
    for gone in ("HIDDEN-TEXT", "OCMD-HIDDEN"):
        assert gone not in text and not g.bytes_contain_text(data, gone)


def test_hidden_form_xobject_is_removed(tmp_path):
    """Content placed via a form XObject that sits in a hidden layer."""
    src = pymupdf.open()
    src.new_page(width=200, height=50).insert_text((10, 30), "XOBJECT-SECRET")
    doc = pymupdf.open()
    page = doc.new_page()
    off = doc.add_ocg("Off", on=False)
    page.show_pdf_page(pymupdf.Rect(72, 72, 272, 122), src, 0, oc=off)
    page.insert_text((72, 200), "Normal text")
    path = tmp_path / "xobj.pdf"
    doc.save(path)
    doc.close()

    doc = pymupdf.open(path)
    remove_hidden_layers(doc)
    data = _saved_bytes(doc)
    assert "XOBJECT-SECRET" not in _everything(data)
    assert not g.bytes_contain_text(data, "XOBJECT-SECRET")
    assert "Normal text" in _everything(data)


def test_alternate_text_inside_page_content_goes_too():
    """Security review 2026-11-25: /Alt and /ActualText written into the page's content stayed
    after Sanitize removed the tag tree."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Visible words", fontsize=12)
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n/Span <</ActualText (ACTUAL-SECRET) /MCID 0>> BDC EMC"
                      b"\n/Figure <</Alt <414c542d534543524554>>> BDC EMC\n")
    form = doc.get_new_xref()
    doc.update_object(form, "<< /Type /XObject /Subtype /Form /BBox [0 0 10 10] >>")
    doc.update_stream(form, b"/P <</E (EXPANSION-SECRET)>> BDC EMC")
    kind, value = doc.xref_get_key(page.xref, "Resources")
    owner, key = (int(value.split()[0]), "XObject") if kind == "xref" else (page.xref, "Resources/XObject")
    doc.xref_set_key(owner, key, f"<< /Fm9 {form} 0 R >>")
    result = sanitize(doc, _only("structure"))
    data = _saved_bytes(doc)
    for secret in ("ACTUAL-SECRET", "ALT-SECRET", "EXPANSION-SECRET"):
        assert not g.bytes_contain_text(data, secret), secret
    assert result.removed["structure"] >= 3
    assert "Visible words" in pymupdf.open("pdf", doc.tobytes())[0].get_text()


def test_layers_hidden_by_a_visibility_rule_are_removed_not_shown(tmp_path):
    """Security review 2026-11-27, finding 7: an OCMD's /VE rule decides, and was ignored; Sanitize
    dropped the layer settings, and viewers that obey /VE (Firefox, Adobe Reader) then showed the
    words and pictures it had hidden."""
    doc = pymupdf.open()
    page = doc.new_page()
    on = doc.add_ocg("L1 on", on=True)
    off = doc.add_ocg("L2 off", on=False)
    page.insert_text((72, 72), "VISIBLE-TEXT")
    page.insert_text((72, 100), "VE-AND-HIDDEN", oc=doc.set_ocmd(ocgs=[on, off], ve=["and", on, off]))
    page.insert_text((72, 130), "VE-NOT-HIDDEN", oc=doc.set_ocmd(ve=["not", on]))
    page.insert_text((72, 160), "VE-OR-SHOWN", oc=doc.set_ocmd(ve=["or", on, off]))
    page.insert_text((72, 190), "VE-NESTED-HIDDEN", oc=doc.set_ocmd(ve=["and", on, ["not", ["or", on, off]]]))
    path = tmp_path / "ve.pdf"
    doc.save(path)
    doc.close()

    doc = pymupdf.open(path)
    assert remove_hidden_layers(doc) == 3
    data = _saved_bytes(doc)
    for gone in ("VE-AND-HIDDEN", "VE-NOT-HIDDEN", "VE-NESTED-HIDDEN"):
        assert not g.bytes_contain_text(data, gone), gone
    assert "VE-OR-SHOWN" in _everything(data) and "VISIBLE-TEXT" in _everything(data)


def test_a_visibility_rule_that_cant_be_read_stops_sanitize(tmp_path):
    from planetpdf.core.redaction.ocg import LayerError

    doc = pymupdf.open()
    page = doc.new_page()
    on = doc.add_ocg("L1 on", on=True)
    ocmd = doc.set_ocmd(ve=["and", on, on])
    doc.xref_set_key(ocmd, "VE", f"[/Xor {on} 0 R]")  # (no such operator)
    page.insert_text((72, 100), "UNKNOWN-RULE", oc=ocmd)
    with pytest.raises(LayerError):
        remove_hidden_layers(doc)


def test_alternate_text_in_every_spelling_goes():
    """Security review 2026-11-27, finding 8: /ActualText in a property list written straight into
    /Properties, and under an escaped name (/Act#75alText), were left by Sanitize."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Visible words", fontsize=12)
    indirect = doc.get_new_xref()
    doc.update_object(indirect, "<< /ActualText (ALT-INDIRECT) >>")
    owner, key = page.xref, "Resources"
    kind, value = doc.xref_get_key(page.xref, "Resources")
    if kind == "xref":
        owner, key = int(value.split()[0]), ""
    prefix = f"{key}/" if key else ""
    doc.xref_set_key(owner, f"{prefix}Properties", f"<< /P1 << /ActualText (ALT-DIRECT) >> /P2 {indirect} 0 R >>")
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n/Span /P1 BDC EMC /Span /P2 BDC EMC"
                      b"\n/Span << /Act#75alText (ALT-ESCAPED) >> BDC EMC\n/Span << /ActualText (ALT-INLINE) >> BDC EMC\n")
    sanitize(doc, _only("structure"))
    data = _saved_bytes(doc)
    for secret in ("ALT-DIRECT", "ALT-ESCAPED", "ALT-INLINE", "ALT-INDIRECT"):
        assert not g.bytes_contain_text(data, secret), secret
    assert "Visible words" in pymupdf.open("pdf", doc.tobytes())[0].get_text()


def test_a_hidden_pictures_data_leaves_the_file(tmp_path):
    """Not only undrawn: the picture's data goes too (security review 2026-11-27)."""
    doc = pymupdf.open()
    page = doc.new_page()
    off = doc.add_ocg("Off", on=False)
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
    picture.set_rect(picture.irect, (255, 0, 0))
    page.insert_image(pymupdf.Rect(100, 100, 200, 200), pixmap=picture, oc=off)
    page.insert_text((72, 72), "VISIBLE-TEXT")
    path = tmp_path / "picture.pdf"
    doc.save(path)
    doc.close()
    doc = pymupdf.open(path)
    assert remove_hidden_layers(doc) == 1
    saved = pymupdf.open("pdf", doc.tobytes(garbage=4))
    assert saved[0].get_images() == []
    assert b"/Image" not in saved.tobytes(garbage=4, expand=255)
