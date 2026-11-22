"""PKCS#12 (.pfx / .p12) signing keys and file-based trusted roots (all platforms)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.signing.providers.base import SigningProvider, TrustStore

log = logging.getLogger(__name__)
CERT_SUFFIXES = {".pem", ".crt", ".cer", ".der"}


class WrongCertificatePassword(PdfError):
    pass


class Pkcs12Provider(SigningProvider):
    """Loads the key from a .pfx/.p12 file. The password is used once and not kept."""

    def __init__(self, path: Path, password: str) -> None:
        from pyhanko.sign import signers

        self.path = Path(path)
        try:
            data = self.path.read_bytes()
        except OSError as exc:
            raise PdfError(f"The certificate file couldn't be read: {exc}") from exc
        try:
            self._signer = signers.SimpleSigner.load_pkcs12_data(data, other_certs=None, passphrase=password.encode("utf-8"))
        except Exception as exc:  # pyHanko/cryptography raise various errors for bad passwords
            raise WrongCertificatePassword(
                "The certificate couldn't be opened. Check the password (and that the file is a .pfx/.p12)."
            ) from exc
        if self._signer is None:
            raise WrongCertificatePassword("The certificate couldn't be opened. Check the password.")

    def signer(self):
        return self._signer

    @property
    def subject_name(self) -> str:
        cert = self._signer.signing_cert
        try:
            return cert.subject.native.get("common_name") or cert.subject.human_friendly
        except Exception:
            return "Unknown signer"


class FileTrustStore(TrustStore):
    """Trusted roots = certificate files the user imported into ``directory``."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def roots(self) -> list:
        from asn1crypto import pem, x509

        certs = []
        if not self.directory.is_dir():
            return certs
        for path in sorted(self.directory.iterdir()):
            if path.suffix.lower() not in CERT_SUFFIXES:
                continue
            try:
                data = path.read_bytes()
                if pem.detect(data):
                    for _type, _headers, der in pem.unarmor(data, multiple=True):
                        certs.append(x509.Certificate.load(der))
                else:
                    certs.append(x509.Certificate.load(data))
            except Exception as exc:
                log.warning("Skipping unreadable certificate %s: %s", path, exc)
        return certs

    def import_certificate(self, source: Path) -> Path:
        """Copy a certificate file into the store (validated first)."""
        from asn1crypto import pem, x509

        source = Path(source)
        data = source.read_bytes()
        try:
            der = pem.unarmor(data)[2] if pem.detect(data) else data
            cert = x509.Certificate.load(der)
            cert.native  # parse fully
        except Exception as exc:
            raise PdfError(f"{source.name} isn't a certificate file: {exc}") from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / (source.stem + (source.suffix.lower() if source.suffix.lower() in CERT_SUFFIXES else ".cer"))
        shutil.copyfile(source, target)
        return target

    def names(self) -> list[str]:
        out = []
        for cert in self.roots():
            try:
                out.append(cert.subject.human_friendly)
            except Exception:
                out.append("(unnamed certificate)")
        return out
