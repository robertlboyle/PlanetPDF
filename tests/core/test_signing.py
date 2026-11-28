from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from planetpdf.core import markup as K
from planetpdf.core.document import PdfError, PdfSession
from planetpdf.core.markup import Kind, MarkupSpec
from planetpdf.core.signing.digital import SignOptions, Verdict, sign_pdf, validate_signatures
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore, Pkcs12Provider, WrongCertificatePassword
from planetpdf.core.signing.visual import (
    SignatureLibrary,
    render_typed,
    signature_source_pdf,
    trim_and_clear_background,
)
from tests.fixtures.certs import PFX_PASSWORD, SIGNER_NAME


# --- visual signatures -------------------------------------------------------------------------


def _alpha_range(png: bytes):
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    return image.getchannel("A").getextrema(), image.size


def test_typed_signature_is_transparent_png():
    png = render_typed("Robert Boyle", "script")
    (lo, hi), size = _alpha_range(png)
    assert lo == 0 and hi == 255  # transparent background, opaque ink
    assert size[0] > size[1]


@pytest.mark.parametrize("font", ["script", "script_formal", "handwriting"])
def test_every_signature_font_renders(font):
    assert render_typed("RB", font)


def test_scanned_signature_background_removed_and_cropped():
    image = Image.new("RGB", (400, 200), "white")
    for x in range(100, 300):
        for y in range(95, 105):
            image.putpixel((x, y), (20, 20, 60))  # a "stroke" of ink
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png = trim_and_clear_background(buf.getvalue())
    (lo, hi), size = _alpha_range(png)
    assert lo == 0 and hi > 200
    assert size[0] < 260 and size[1] < 60  # cropped to the ink


def test_blank_image_rejected():
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    with pytest.raises(PdfError):
        trim_and_clear_background(buf.getvalue())


def test_library_persists_and_defaults(tmp_path):
    lib = SignatureLibrary.load(tmp_path)
    first = lib.add(render_typed("Robert Boyle"), "signature", "Full")
    second = lib.add(render_typed("R. Boyle"), "signature", "Short")
    initials = lib.add(render_typed("RB"), "initials")
    again = SignatureLibrary.load(tmp_path)
    assert again.default("signature").sig_id == second.sig_id  # newest is default
    again.make_default(first.sig_id)
    assert SignatureLibrary.load(tmp_path).default("signature").sig_id == first.sig_id
    assert again.default("initials").sig_id == initials.sig_id
    again.remove(initials.sig_id)
    assert SignatureLibrary.load(tmp_path).default("initials") is None
    assert not (tmp_path / initials.file).exists()


def test_place_signature_on_page(pdf_copy):
    session = PdfSession.open(pdf_copy("acroform.pdf"))
    source = signature_source_pdf(render_typed("Robert Boyle"))
    spec = MarkupSpec(K.new_id(), 0, Kind.SIGNATURE, (300, 600, 460, 640), source=source, author="RB")
    K.add_markup(session, spec)
    session.save()
    [item] = K.list_markup(session)
    assert item.kind is Kind.SIGNATURE
    pix = session.doc[0].get_pixmap(clip=pymupdf.Rect(300, 600, 460, 640), dpi=72)
    samples = pix.samples
    assert sum(1 for i in range(2, len(samples), 3) if samples[i] > 100 and samples[i - 2] < 80) > 30  # blue ink
    session.close()


# --- digital signatures -------------------------------------------------------------------------


@pytest.fixture
def provider(test_certs):
    return Pkcs12Provider(test_certs.pfx, PFX_PASSWORD)


def test_wrong_pfx_password(test_certs):
    with pytest.raises(WrongCertificatePassword):
        Pkcs12Provider(test_certs.pfx, "nope")


def test_provider_names_the_signer(provider):
    assert provider.subject_name == SIGNER_NAME


def _sign(pdf_copy, provider, tmp_path, **options):
    source = pdf_copy("text.pdf")
    target = tmp_path / "signed.pdf"
    sign_pdf(source, target, provider, 0, (72, 600, 300, 680), SignOptions(reason="Approval", location="Office", **options))
    return source, target


def test_sign_and_validate_untrusted(pdf_copy, provider, tmp_path):
    source, target = _sign(pdf_copy, provider, tmp_path)
    assert target.read_bytes().startswith(source.read_bytes())  # incremental: original bytes kept
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "no-trust"))
    assert status.intact and not status.trusted
    assert status.signer == SIGNER_NAME
    assert status.verdict is Verdict.VALID_UNTRUSTED
    with pymupdf.open(target) as doc:
        widgets = [w for w in doc[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE]
        assert len(widgets) == 1
        assert "Digitally signed by" in doc[0].get_text()  # visible appearance box


def test_sign_and_validate_trusted(pdf_copy, provider, tmp_path, test_certs):
    _source, target = _sign(pdf_copy, provider, tmp_path)
    trust = FileTrustStore(tmp_path / "trust")
    trust.import_certificate(test_certs.signer_cert_pem)
    assert trust.names() and SIGNER_NAME in trust.names()[0]
    [status] = validate_signatures(target, trust)
    assert status.trusted and status.verdict is Verdict.VALID, status.summary


def test_sign_with_timestamp(pdf_copy, provider, tmp_path, test_certs):
    from pyhanko.sign.timestamps import DummyTimeStamper

    tsa = DummyTimeStamper(tsa_cert=test_certs.tsa_cert, tsa_key=test_certs.tsa_key)
    _source, target = _sign(pdf_copy, provider, tmp_path, timestamper=tsa)
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert not status.timestamped and "isn't proven" in status.summary  # (a timestamp server not trusted)
    trust = FileTrustStore(tmp_path / "trusted")
    pem = tmp_path / "tsa.pem"
    from asn1crypto import pem as asn1_pem

    pem.write_bytes(asn1_pem.armor("CERTIFICATE", test_certs.tsa_cert.dump()))
    trust.import_certificate(pem)
    [status] = validate_signatures(target, trust)
    assert status.timestamped  # (its server trusted: the time is proven)


def test_tampering_is_detected(pdf_copy, provider, tmp_path):
    _source, target = _sign(pdf_copy, provider, tmp_path)
    data = bytearray(target.read_bytes())
    at = data.find(b"Customer record")
    if at < 0:  # content is compressed: flip a byte inside the first stream instead
        at = data.find(b"stream") + 20
    data[at] ^= 0x01
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(bytes(data))
    [status] = validate_signatures(tampered, FileTrustStore(tmp_path / "t"))
    assert status.verdict is Verdict.INVALID


def test_edits_after_signing_are_reported(pdf_copy, provider, tmp_path):
    _source, target = _sign(pdf_copy, provider, tmp_path)
    session = PdfSession.open(target)
    assert session.has_digital_signatures
    session.doc[0].add_text_annot((400, 100), "added later")
    session.mark_modified()
    session.save()  # incremental, so the signature survives
    session.close()
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert status.intact
    assert status.changes_after in ("annotations", "other changes")
    assert "changed" in status.summary or "Later changes" in status.summary


@pytest.mark.parametrize("level", ["none", "forms", "comments"])
def test_certified_pdf_reports_its_level(pdf_copy, provider, tmp_path, level):
    """Save as Other ▸ Certified PDF (2026-10-02): a certification signature with the
    changes it allows; it can be invisible."""
    source = pdf_copy("text.pdf")
    target = tmp_path / "certified.pdf"
    sign_pdf(source, target, provider, 0, None, SignOptions(certify=level))
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert status.intact and status.certified == level and status.summary.startswith("Certified (")
    with pymupdf.open(target) as doc:
        page = doc[0]
        [widget] = [w for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE]
        assert widget.rect.is_empty or widget.rect.width < 1  # (invisible)


def test_comments_after_a_no_changes_certification_break_it(pdf_copy, provider, tmp_path):
    source = pdf_copy("text.pdf")
    target = tmp_path / "certified.pdf"
    sign_pdf(source, target, provider, 0, (72, 600, 300, 680), SignOptions(certify="none"))
    session = PdfSession.open(target)
    session.doc[0].add_text_annot((400, 100), "a comment")
    session.mark_modified()
    session.save()
    session.close()
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert status.verdict is Verdict.MODIFIED, status.summary
    with pytest.raises(PdfError):  # (a certification must come first)
        sign_pdf(target, tmp_path / "again.pdf", provider, 0, None, SignOptions(certify="forms"))


def test_sign_twice(pdf_copy, provider, tmp_path):
    _source, target = _sign(pdf_copy, provider, tmp_path)
    sign_pdf(target, target, provider, 0, (320, 600, 540, 680), SignOptions())
    statuses = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert [s.field_name for s in statuses] == ["Signature1", "Signature2"]
    assert all(s.intact for s in statuses)


def test_signature_box_uses_pdf_coordinates(pdf_copy, provider, tmp_path):
    _source, target = _sign(pdf_copy, provider, tmp_path)
    with pymupdf.open(target) as doc:
        widget = next(w for w in doc[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE)
        assert tuple(widget.rect) == pytest.approx((72, 600, 300, 680), abs=0.5)


def test_sign_with_visual_signature_image(pdf_copy, provider, tmp_path):
    _source, target = _sign(pdf_copy, provider, tmp_path, signature_image=render_typed("Robert Boyle"))
    with pymupdf.open(target) as doc:
        widget = next(w for w in doc[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE)
        pix = doc[0].get_pixmap(clip=widget.rect, dpi=72)
        s = pix.samples
        assert sum(1 for i in range(2, len(s), 3) if s[i] > 100 and s[i - 2] < 80) > 10  # blue signature ink shown


def test_file_trust_store_rejects_non_certificates(tmp_path):
    bogus = tmp_path / "bogus.cer"
    bogus.write_text("not a cert")
    with pytest.raises(PdfError):
        FileTrustStore(tmp_path / "t").import_certificate(bogus)


def test_a_new_title_after_signing_is_not_called_unchanged(pdf_copy, provider, tmp_path):
    """Security review 2026-11-27: with a title changed and saved after signing, the signature was
    "Not changed since signing"."""
    _source, target = _sign(pdf_copy, provider, tmp_path)
    session = PdfSession.open(target)
    session.doc.set_metadata({**session.doc.metadata, "title": "A new title"})
    session.mark_modified()
    session.save()
    session.close()
    [status] = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    assert status.intact and not status.covers_whole_file
    assert "not changed since signing" not in status.summary.lower()
    assert "Saved again after signing" in status.summary or "changed" in status.summary


def test_a_signature_removed_by_a_later_change_is_still_reported(pdf_copy, provider, tmp_path):
    """Security review 2026-11-27: after the signed page was deleted and the file saved, no signature
    was listed at all, though the signed version is still in the file."""
    from planetpdf.core.signing.digital import REMOVED_SUMMARY

    _source, target = _sign(pdf_copy, provider, tmp_path)
    session = PdfSession.open(target)
    session.doc.delete_page(0)
    session.mark_modified()
    session.save()
    session.close()
    statuses = validate_signatures(target, FileTrustStore(tmp_path / "t"))
    if statuses and statuses[0].field_name != "(removed)":  # (the field outlived its page: reported as changed)
        assert statuses[0].verdict is not Verdict.VALID
    else:
        assert [s.summary for s in statuses] == [REMOVED_SUMMARY] and statuses[0].verdict is Verdict.MODIFIED
