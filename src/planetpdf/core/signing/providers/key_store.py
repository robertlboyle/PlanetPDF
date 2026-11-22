"""What signing with a system key store needs on any system (2026-10-14): Windows' certificate
store (``windows_store``) and the Mac Keychain (``mac_keychain``) both keep the private key to
themselves and only sign a hash or unwrap a document key when asked. This module turns such a
provider into pyHanko's signer and envelope decrypter, so both systems share that part.

A provider has: ``certificate`` (a ``StoreCertificate``), ``chain`` (DER, the certificate first),
``sign_hash(digest, digest_algorithm, rsa=…)`` and ``decrypt(data, oaep_hash=…)``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from planetpdf.core.document import PdfError

SIGNING_USAGES = {"digital_signature", "non_repudiation"}


@dataclass(frozen=True)
class StoreCertificate:
    der: bytes
    name: str
    issuer: str
    expires: datetime
    thumbprint: str  # SHA-1, hex: how Windows (and the Keychain) name a certificate
    has_private_key: bool
    store: str  # Windows: "MY" (Personal) / "AddressBook" (Other People); Mac: "keychain"

    @property
    def expired(self) -> bool:
        return self.expires < datetime.now(timezone.utc)

    @property
    def label(self) -> str:
        return f"{self.name} (issued by {self.issuer}, expires {self.expires:%b %d, %Y})"


def describe(der: bytes, has_key: bool, store: str) -> StoreCertificate:
    from asn1crypto import x509

    cert = x509.Certificate.load(der)
    name = cert.subject.native.get("common_name") or cert.subject.human_friendly
    issuer = cert.issuer.native.get("common_name") or cert.issuer.human_friendly
    return StoreCertificate(der, name, issuer, cert["tbs_certificate"]["validity"]["not_after"].native,
                            hashlib.sha1(der).hexdigest(), has_key, store)


def can_sign(der: bytes) -> bool:
    """Meant for signing documents: key usage allows it (or isn't stated), and it isn't only for
    something else (e.g. a Developer ID, which signs programs)."""
    from asn1crypto import x509

    cert = x509.Certificate.load(der)
    usage = cert.key_usage_value
    if usage is not None and not (set(usage.native) & SIGNING_USAGES):
        return False
    purposes = cert.extended_key_usage_value
    if purposes is not None:
        allowed = {"email_protection", "document_signing", "adobe_authentic_documents_trust",
                   "any_extended_key_usage", "client_auth", "1.3.6.1.4.1.311.10.3.12"}  # (MS document signing)
        if not set(purposes.native) & allowed:
            return False
    return True


def build_signer(provider, *, ec_signature: str = "p1363"):
    """A pyHanko signer whose private key stays in the system store (built lazily: pyHanko is
    big). ``ec_signature``: how ``sign_hash`` returns ECDSA signatures, "p1363" (r‖s, Windows'
    CNG) or "der" (the Keychain's X9.62)."""
    from asn1crypto import algos, x509
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    cert = x509.Certificate.load(provider.certificate.der)
    algorithm = cert.public_key.algorithm  # "rsa" / "ec"
    if algorithm not in ("rsa", "ec"):
        raise PdfError(f"Signing with a {algorithm} key isn't supported.")
    mechanism = algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa" if algorithm == "rsa" else "sha256_ecdsa"})
    chain = [x509.Certificate.load(der) for der in provider.chain]

    class StoreSigner(signers.Signer):
        async def async_sign_raw(self, data: bytes, digest_algorithm: str, dry_run: bool = False) -> bytes:
            if dry_run:  # (pyHanko sizes the signature first)
                return bytes(cert.public_key.bit_size // 8 if algorithm == "rsa" else 8 + 2 * (cert.public_key.bit_size // 8 + 1))
            digest = hashlib.new(digest_algorithm, data).digest()
            raw = provider.sign_hash(digest, digest_algorithm, rsa=algorithm == "rsa")
            if algorithm == "rsa" or ec_signature == "der":
                return raw
            return algos.DSASignature.from_p1363(raw).dump()

    return StoreSigner(signing_cert=cert, cert_registry=SimpleCertificateStore.from_certs(chain),
                       signature_mechanism=mechanism)


def build_envelope_decrypter(provider):
    """A pyHanko ``EnvelopeKeyDecrypter`` that opens Encrypt with Certificate files with the
    provider's key (RSA key transport; built lazily)."""
    from asn1crypto import x509
    from pyhanko.pdf_utils.crypt import EnvelopeKeyDecrypter

    cert = x509.Certificate.load(provider.certificate.der)

    class StoreDecrypter(EnvelopeKeyDecrypter):
        @property
        def cert(self):
            return cert

        def decrypt(self, encrypted_key: bytes, algo_params) -> bytes:
            name = algo_params["algorithm"].native
            if name == "rsaes_pkcs1v15":
                return provider.decrypt(encrypted_key)
            if name == "rsaes_oaep":
                params = algo_params["parameters"]
                return provider.decrypt(encrypted_key, oaep_hash=params["hash_algorithm"]["algorithm"].native)
            raise NotImplementedError(f"Opening files encrypted with {name} isn't supported.")

    return StoreDecrypter()


def chain_from(der: bytes, pool: list[bytes]) -> list[bytes]:
    """The certificate, then its issuers as far as ``pool`` (DER certificates) has them."""
    from asn1crypto import x509

    candidates = []
    for item in pool:
        try:
            candidates.append(x509.Certificate.load(item))
        except Exception:
            continue
    chain = [der]
    current = x509.Certificate.load(der)
    for _ in range(8):
        if current.self_signed != "no":
            break
        issuer = next((c for c in candidates if c.subject == current.issuer), None)
        if issuer is None:
            break
        chain.append(issuer.dump())
        current = issuer
    return chain
