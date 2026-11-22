"""Throwaway certificates for signing tests (self-signed signer + a test timestamp authority)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PFX_PASSWORD = "test-pfx-pass"
SIGNER_NAME = "PlanetPDF Test Signer"


@dataclass
class TestCerts:
    pfx: Path
    signer_cert_pem: Path  # to import as a "trusted root" (it's self-signed)
    tsa_cert: object  # asn1crypto x509.Certificate
    tsa_key: object  # asn1crypto keys.PrivateKeyInfo


def _self_signed(common_name: str, *, timestamping: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Planet Networks Test")])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    )
    if timestamping:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    cert = builder.sign(key, hashes.SHA256())
    return key, cert


def make_test_certs(directory: Path) -> TestCerts:
    from asn1crypto import keys as asn1_keys
    from asn1crypto import x509 as asn1_x509

    directory.mkdir(parents=True, exist_ok=True)
    key, cert = _self_signed(SIGNER_NAME)
    pfx = directory / "signer.pfx"
    pfx.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"signer", key, cert, None, serialization.BestAvailableEncryption(PFX_PASSWORD.encode())
        )
    )
    pem = directory / "signer.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    tsa_key, tsa_cert = _self_signed("PlanetPDF Test TSA", timestamping=True)
    return TestCerts(
        pfx=pfx,
        signer_cert_pem=pem,
        tsa_cert=asn1_x509.Certificate.load(tsa_cert.public_bytes(serialization.Encoding.DER)),
        tsa_key=asn1_keys.PrivateKeyInfo.load(
            tsa_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        ),
    )
