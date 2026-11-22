"""The Mac Keychain (2026-10-14): signing with the key inside the Keychain, decrypting, trust.
The test Digital ID goes into a throwaway keychain file made for the test (never the user's
login keychain), imported so any program may use its key without asking."""

from __future__ import annotations

import subprocess
import sys

import pymupdf
import pytest

from planetpdf.core.signing.digital import SignOptions, Verdict, sign_pdf, validate_signatures
from planetpdf.core.signing.providers import key_store
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore
from tests.fixtures.certs import PFX_PASSWORD, SIGNER_NAME

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the Mac Keychain")
KEYCHAIN_PASSWORD = "planetpdf-test"


def _legacy_pfx(pfx_path, out):
    """The test Digital ID re-saved with the encryption macOS' importer reliably reads
    (3DES / SHA-1; cryptography's default AES one may be refused)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    key, cert, extra = pkcs12.load_key_and_certificates(pfx_path.read_bytes(), PFX_PASSWORD.encode())
    encryption = (serialization.PrivateFormat.PKCS12.encryption_builder()
                  .kdf_rounds(2048).key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                  .hmac_hash(hashes.SHA1()).build(PFX_PASSWORD.encode()))
    out.write_bytes(pkcs12.serialize_key_and_certificates(b"signer", key, cert, extra, encryption))
    return out


@pytest.fixture
def keychain(test_certs, tmp_path):
    path = tmp_path / "planetpdf-test.keychain-db"
    run = lambda *args: subprocess.run(["security", *args], check=True, capture_output=True)  # noqa: E731
    run("create-keychain", "-p", KEYCHAIN_PASSWORD, str(path))
    try:
        run("unlock-keychain", "-p", KEYCHAIN_PASSWORD, str(path))
        run("set-keychain-settings", str(path))  # (no automatic locking during the test)
        pfx = _legacy_pfx(test_certs.pfx, tmp_path / "legacy.pfx")
        run("import", str(pfx), "-k", str(path), "-P", PFX_PASSWORD, "-f", "pkcs12", "-A")
        yield (str(path),)
    finally:
        subprocess.run(["security", "delete-keychain", str(path)], capture_output=True)


def test_lists_the_digital_id(keychain):
    from planetpdf.core.signing.providers.mac_keychain import keychain_certificates

    [mine] = keychain_certificates(signing_only=True, keychains=keychain)
    assert mine.name == SIGNER_NAME and mine.has_private_key and mine.store == "keychain"
    assert any(c.thumbprint == mine.thumbprint for c in keychain_certificates(keychains=keychain))


def test_signs_inside_the_keychain_and_validates(keychain, pdf_copy, tmp_path, test_certs):
    from planetpdf.core.signing.providers.mac_keychain import KeychainProvider, keychain_certificates

    [mine] = keychain_certificates(signing_only=True, keychains=keychain)
    provider = KeychainProvider(mine.thumbprint, keychains=keychain)
    try:
        target = tmp_path / "signed.pdf"
        sign_pdf(pdf_copy("text.pdf"), target, provider, 0, (72, 600, 300, 680), SignOptions(reason="Keychain"))
    finally:
        provider.close()
    trust = FileTrustStore(tmp_path / "trust")
    trust.import_certificate(test_certs.signer_cert_pem)
    [status] = validate_signatures(target, trust)
    assert status.intact and status.verdict is Verdict.VALID, status.summary


def test_opens_a_certificate_encrypted_file_with_the_keychain_key(keychain, test_certs):
    from planetpdf.core import cert_encryption as CE
    from planetpdf.core.signing.providers.mac_keychain import KeychainProvider, keychain_certificates

    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "Board minutes", fontname="helv")
    mine = CE.certificate_from_pfx(test_certs.pfx, PFX_PASSWORD)
    data = CE.encrypt_bytes(doc.tobytes(), CE.CertificateProtection((mine,), allow_print=False))
    [cert] = keychain_certificates(signing_only=True, keychains=keychain)
    provider = KeychainProvider(cert.thumbprint, keychains=keychain)
    try:
        plain, protection = CE.decrypt_with(data, provider.envelope_decrypter())
    finally:
        provider.close()
    assert "Board minutes" in pymupdf.open(stream=plain, filetype="pdf")[0].get_text()
    assert protection.recipients == (mine,) and not protection.allow_print


def test_trusts_what_macos_trusts():
    from planetpdf.core.signing.providers.mac_keychain import KeychainTrustStore

    assert len(KeychainTrustStore().roots()) > 20  # (macOS ships well over a hundred)


def test_a_program_signing_certificate_isnt_offered_for_documents(tmp_path):
    """The Mac mini's Keychain holds a Developer ID (signs programs): not a document signer."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    def cert(purpose):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        built = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                 .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
                 .add_extension(x509.ExtendedKeyUsage([purpose]), critical=False).sign(key, hashes.SHA256()))
        return built.public_bytes(serialization.Encoding.DER)

    assert not key_store.can_sign(cert(ExtendedKeyUsageOID.CODE_SIGNING))
    assert key_store.can_sign(cert(ExtendedKeyUsageOID.EMAIL_PROTECTION))
