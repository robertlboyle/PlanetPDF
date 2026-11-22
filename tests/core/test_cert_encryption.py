"""Encrypt with Certificate (backlog item, built 2026-10-02): only the recipients can open
the file; PlanetPDF opens it with a Digital ID and saving keeps it encrypted for them."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import cert_encryption as CE
from planetpdf.core.document import PdfError, PdfSession, REMOVE_PROTECTION
from tests.fixtures.certs import PFX_PASSWORD, make_test_certs


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    return make_test_certs(tmp_path_factory.mktemp("certs"))


@pytest.fixture(scope="module")
def other(tmp_path_factory):
    return make_test_certs(tmp_path_factory.mktemp("other"))  # (someone else's Digital ID)


def _plain(path, text="Quarterly secret"):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), text, fontname="helv")
    doc.save(path)
    return path


def _mine(certs):
    return CE.certificate_from_pfx(certs.pfx, PFX_PASSWORD)


def test_only_a_recipient_can_open_it(certs, other, tmp_path):
    path = _plain(tmp_path / "doc.pdf")
    protection = CE.CertificateProtection((_mine(certs),), allow_copy=False)
    data = CE.encrypt_bytes(path.read_bytes(), protection)
    (tmp_path / "enc.pdf").write_bytes(data)
    assert CE.is_certificate_encrypted(tmp_path / "enc.pdf") and not CE.is_certificate_encrypted(path)
    assert b"Quarterly secret" not in data
    with pytest.raises(Exception):
        pymupdf.open(tmp_path / "enc.pdf")  # (MuPDF can't read it: PlanetPDF decrypts first)
    plain, again = CE.decrypt_with_pfx(data, certs.pfx, PFX_PASSWORD)
    assert again.recipients == protection.recipients and again.recipients_known and not again.allow_copy
    assert "Quarterly secret" in pymupdf.open(stream=plain, filetype="pdf")[0].get_text()
    with pytest.raises(PdfError):
        CE.decrypt_with_pfx(data, other.pfx, PFX_PASSWORD)  # (not a recipient)
    assert CE.load_certificate_file(certs.signer_cert_pem) == [protection.recipients[0]]
    assert protection.names == ["PlanetPDF Test Signer"]


def test_session_edits_and_saves_it_encrypted_for_the_same_recipients(certs, other, tmp_path):
    path = _plain(tmp_path / "doc.pdf")
    protection = CE.CertificateProtection((_mine(certs), CE.certificate_from_pfx(other.pfx, PFX_PASSWORD)))
    path.write_bytes(CE.encrypt_bytes(path.read_bytes(), protection))
    session = PdfSession.open_certificate_encrypted(path, certs.pfx, PFX_PASSWORD)
    try:
        assert session.is_protected and session.is_encrypted and session.certificate_protection == protection
        session.doc[0].insert_text((72, 200), "Added later", fontname="helv")
        session.mark_modified()
        session.save()
        assert CE.is_certificate_encrypted(path)  # still encrypted on disk…
        assert "Added later" in session.doc[0].get_text()  # …and the session keeps working
        recovery = session.recovery_bytes()
        assert CE.PUBSEC in recovery and b"Added later" not in recovery  # (no plain copy left lying around)
    finally:
        session.close()
    # the other recipient can open the saved file too
    plain, _p = CE.decrypt_with_pfx(path.read_bytes(), other.pfx, PFX_PASSWORD)
    assert "Added later" in pymupdf.open(stream=plain, filetype="pdf")[0].get_text()


def test_encrypting_a_document_through_the_session_then_removing_it(certs, tmp_path):
    path = _plain(tmp_path / "doc.pdf")
    session = PdfSession.open(path)
    try:
        session.set_protection(CE.CertificateProtection((_mine(certs),)))
        session.save()
        assert CE.is_certificate_encrypted(path) and "Quarterly secret" in session.doc[0].get_text()
        session.set_protection(REMOVE_PROTECTION)
        session.save()
        assert not CE.is_certificate_encrypted(path) and not session.is_protected
    finally:
        session.close()
    with pymupdf.open(path) as doc:
        assert not doc.needs_pass and "Quarterly secret" in doc[0].get_text()


def test_new_files_from_it_get_the_same_certificates(certs, tmp_path):
    from planetpdf.core.document import protect_pdf_file

    path = _plain(tmp_path / "doc.pdf")
    protection = CE.CertificateProtection((_mine(certs),))
    path.write_bytes(CE.encrypt_bytes(path.read_bytes(), protection))
    session = PdfSession.open_certificate_encrypted(path, certs.pfx, PFX_PASSWORD)
    try:
        known, complete = session.known_protection()
        assert known == protection and complete
        out = tmp_path / "copy.pdf"
        _plain(out, "A copy")
        protect_pdf_file(out, known)
        assert CE.is_certificate_encrypted(out)
    finally:
        session.close()
