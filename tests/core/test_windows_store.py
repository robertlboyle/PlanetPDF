"""The Windows certificate store (backlog item, built 2026-10-02). Signing goes through
CNG with the key inside Windows; the test Digital ID is loaded into a temporary in-memory
store, so nothing is added to the user's real certificate store."""

from __future__ import annotations

import sys

import pymupdf
import pytest

from planetpdf.core.signing.digital import SignOptions, Verdict, sign_pdf, validate_signatures
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore
from tests.fixtures.certs import PFX_PASSWORD, SIGNER_NAME

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows certificate store")


@pytest.fixture
def provider(test_certs):
    from planetpdf.core.signing.providers.windows_store import WindowsStoreProvider

    p = WindowsStoreProvider.from_pfx_in_memory(test_certs.pfx.read_bytes(), PFX_PASSWORD)
    yield p
    p.close()


def test_signs_through_windows_and_validates(provider, pdf_copy, tmp_path, test_certs):
    assert provider.subject_name == SIGNER_NAME and provider.certificate.has_private_key
    target = tmp_path / "signed.pdf"
    sign_pdf(pdf_copy("text.pdf"), target, provider, 0, (72, 600, 300, 680), SignOptions(reason="CNG"))
    trust = FileTrustStore(tmp_path / "trust")
    trust.import_certificate(test_certs.signer_cert_pem)
    [status] = validate_signatures(target, trust)
    assert status.intact and status.verdict is Verdict.VALID, status.summary
    with pymupdf.open(target) as doc:
        assert "Digitally signed by" in doc[0].get_text()


def test_lists_the_users_certificates_and_windows_roots():
    from planetpdf.core.signing.providers.windows_store import WindowsTrustStore, store_certificates

    certificates = store_certificates()  # (read only: whatever this user has)
    assert all(c.thumbprint and c.name for c in certificates)
    assert all(c.has_private_key and not c.expired for c in store_certificates(signing_only=True))
    roots = WindowsTrustStore().roots()
    assert len(roots) > 5  # (every Windows has some trusted roots)

def test_opens_certificate_encrypted_files_with_the_key_in_windows(test_certs, tmp_path):
    """Backlog item (2026-10-03): open Encrypt with Certificate files with a Windows-store
    key. The document key is unwrapped by NCryptDecrypt; the private key never leaves."""
    from planetpdf.core import cert_encryption as CE
    from planetpdf.core.document import PdfError, PdfSession
    from planetpdf.core.signing.providers.windows_store import WindowsStoreProvider

    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "Board minutes", fontname="helv")
    mine = CE.certificate_from_pfx(test_certs.pfx, PFX_PASSWORD)
    data = CE.encrypt_bytes(doc.tobytes(), CE.CertificateProtection((mine,), allow_print=False))
    path = tmp_path / "enc.pdf"
    path.write_bytes(data)

    def providers():
        return [WindowsStoreProvider.from_pfx_in_memory(test_certs.pfx.read_bytes(), PFX_PASSWORD)]

    plain, protection = CE.decrypt_with_windows(data, providers())
    assert "Board minutes" in pymupdf.open(stream=plain, filetype="pdf")[0].get_text()
    assert protection.recipients == (mine,) and not protection.allow_print
    session = PdfSession.open_certificate_encrypted(path, windows_providers=providers())
    try:
        assert "Board minutes" in session.doc[0].get_text()
        assert session.certificate_protection.recipients == (mine,)
    finally:
        session.close()
    # someone else's file: none of "my" certificates can open it
    from tests.fixtures.certs import make_test_certs

    other = make_test_certs(tmp_path / "other")
    theirs = CE.encrypt_bytes(doc.tobytes(), CE.CertificateProtection((CE.certificate_from_pfx(other.pfx, PFX_PASSWORD),)))
    with pytest.raises(CE.NoWindowsCertificate):
        CE.decrypt_with_windows(theirs, providers())
    assert issubclass(CE.NoWindowsCertificate, PdfError)
