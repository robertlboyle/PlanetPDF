"""Protection ▸ Encrypt with Certificate (Acrobat; backlog item, built 2026-10-02):
public-key ("Adobe.PubSec") encryption with pyHanko. Only the recipients (their private
keys, e.g. a Digital ID .pfx) can open the file; nobody needs to share a password.

MuPDF can't open such files ("unknown encryption handler"), so PlanetPDF decrypts them
with pyHanko first and works on the plain copy in memory; saving encrypts again. A
certificate-encrypted file only names its recipients (issuer + serial number), so when
PlanetPDF encrypts it also keeps the recipients' *public* certificates inside the
document (``/PPDFCertRecipients`` in the catalog, encrypted with everything else): after
opening and editing, saving re-encrypts for the same people. Files encrypted by other
software can only be re-encrypted for the person who opened them (``recipients_known``).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError

RECIPIENTS_KEY = "PPDFCertRecipients"
PUBSEC = b"/Adobe.PubSec"


@dataclass(frozen=True)
class CertificateProtection:
    recipients: tuple[bytes, ...]  # DER certificates of the people who can open the file
    allow_print: bool = True
    allow_copy: bool = True
    allow_edit: bool = True
    allow_comments: bool = True
    allow_forms: bool = True
    recipients_known: bool = True  # False: opened from another program's file (see above)

    def validate(self) -> None:
        if not self.recipients:
            raise PdfError("Add at least one recipient's certificate.")

    @property
    def names(self) -> list[str]:
        return [certificate_name(der) for der in self.recipients]

    def permissions(self):
        from pyhanko.pdf_utils.crypt.permissions import PubKeyPermissions as P

        flags = P.ALLOW_ASSISTIVE_TECHNOLOGY | P.TOLERATE_MISSING_PDF_MAC
        if self.allow_print:
            flags |= P.ALLOW_PRINTING | P.ALLOW_HIGH_QUALITY_PRINTING
        if self.allow_copy:
            flags |= P.ALLOW_CONTENT_EXTRACTION
        if self.allow_edit:
            flags |= P.ALLOW_MODIFICATION_GENERIC | P.ALLOW_REASSEMBLY
        if self.allow_comments:
            flags |= P.ALLOW_ANNOTS_FORM_FILLING
        if self.allow_forms:
            flags |= P.ALLOW_FORM_FILLING
        return flags


def certificate_name(der: bytes) -> str:
    from asn1crypto import x509

    try:
        cert = x509.Certificate.load(der)
        return cert.subject.native.get("common_name") or cert.subject.human_friendly
    except Exception:
        return "(unreadable certificate)"


def load_certificate_file(path: Path) -> list[bytes]:
    """The certificates in a .cer / .crt / .pem / .der file (public: no password)."""
    from asn1crypto import pem, x509

    data = Path(path).read_bytes()
    try:
        ders = [der for _t, _h, der in pem.unarmor(data, multiple=True)] if pem.detect(data) else [data]
        for der in ders:
            x509.Certificate.load(der).native  # parse fully
    except Exception as exc:
        raise PdfError(f"{Path(path).name} isn't a certificate file: {exc}") from exc
    return ders


def certificate_from_pfx(path: Path, password: str) -> bytes:
    """Your own certificate from a Digital ID (.pfx / .p12), to add yourself as a recipient."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

    try:
        _key, cert, _extra = pkcs12.load_key_and_certificates(Path(path).read_bytes(), password.encode("utf-8"))
    except Exception as exc:
        raise PdfError("The Digital ID couldn't be opened. Check the password.") from exc
    if cert is None:
        raise PdfError("That Digital ID has no certificate.")
    return cert.public_bytes(Encoding.DER)


def encrypt_bytes(plain: bytes, protection: CertificateProtection) -> bytes:
    """The PDF ``plain`` encrypted for the recipients (AES-256), their certificates kept
    inside (see the module doc)."""
    from asn1crypto import x509
    from pyhanko.pdf_utils.crypt.pubkey import RecipientEncryptionPolicy
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    protection.validate()
    doc = pymupdf.open(stream=plain, filetype="pdf")
    try:
        array = "[" + " ".join(f"<{der.hex()}>" for der in protection.recipients) + "]"
        doc.xref_set_key(doc.pdf_catalog(), RECIPIENTS_KEY, array)
        marked = doc.tobytes(garbage=1, deflate=True)
    finally:
        doc.close()
    writer = copy_into_new_writer(PdfFileReader(io.BytesIO(marked), strict=False))
    writer.encrypt_pubkey(
        [x509.Certificate.load(der) for der in protection.recipients],
        keylen_bytes=32,  # AES-256
        perms=protection.permissions(),
        pdf_mac=False,  # (the PDF 2.0 MAC: older readers can't open files that have it)
        # personal certificates are often "digital signature" only: accept them (Acrobat does)
        policy=RecipientEncryptionPolicy(ignore_key_usage=True),
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def is_certificate_encrypted(path: Path) -> bool:
    try:
        return PUBSEC in Path(path).read_bytes()
    except OSError:
        return False


def decrypt_with_pfx(data: bytes, pfx: Path, password: str) -> tuple[bytes, CertificateProtection]:
    """Open a certificate-encrypted PDF with your Digital ID: (the plain PDF, the
    protection to save it with again)."""
    from pyhanko.pdf_utils.crypt import SimpleEnvelopeKeyDecrypter

    decrypter = SimpleEnvelopeKeyDecrypter.load_pkcs12(str(pfx), password.encode("utf-8"))
    if decrypter is None:
        raise PdfError("The Digital ID couldn't be opened. Check the password.")
    result = decrypt_with(data, decrypter)
    if result is None:
        raise PdfError("This file wasn't encrypted for that Digital ID.")
    return result


class NoWindowsCertificate(PdfError):
    """None of the certificates in this computer's store (Windows' certificate store or the Mac
    Keychain, with their private key) can open the file. (The name is from when only Windows had one.)"""


def decrypt_with_windows(data: bytes, providers=None) -> tuple[bytes, CertificateProtection]:
    """Open a certificate-encrypted PDF with a certificate in this computer's own store: Windows'
    certificate store (backlog item, built 2026-10-03) or the Mac Keychain (2026-10-14; the name
    is from before). The key stays there: only the document key is unwrapped
    (``provider.decrypt``), and the system asks for a PIN or password itself when the key needs
    one. ``providers``: tests (an in-memory store, a throwaway keychain)."""
    from planetpdf.core.signing.providers import system_store

    where = system_store.name()
    if providers is None:
        providers = []
        for cert in system_store.own_certificates():
            if cert.has_private_key and not cert.expired:
                try:
                    providers.append(system_store.provider(cert.thumbprint))
                except PdfError:
                    continue
    errors = []
    try:
        for provider in providers:
            try:
                result = decrypt_with(data, provider.envelope_decrypter())
            except PdfError as exc:  # (its key couldn't be used: a canceled PIN, a removed card)
                errors.append(f"{provider.subject_name}: {exc}")
                continue
            if result is not None:
                return result
    finally:
        for provider in providers:
            provider.close()
    if errors:
        raise PdfError(f"This file is encrypted for a certificate in your {where}, but it couldn't be used:\n"
                       + "\n".join(errors))
    raise NoWindowsCertificate(f"None of your certificates in your {where} can open this file.")


def decrypt_with(data: bytes, decrypter) -> tuple[bytes, CertificateProtection] | None:
    """Open a certificate-encrypted PDF with a pyHanko ``EnvelopeKeyDecrypter``: (the
    plain PDF, the protection to save it with again), or None when the file wasn't
    encrypted for its certificate."""
    from pyhanko.pdf_utils.crypt.api import AuthStatus
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    reader = PdfFileReader(io.BytesIO(data), strict=False)
    try:
        result = reader.decrypt_pubkey(decrypter)
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"This file couldn't be decrypted with that Digital ID: {exc}") from exc
    if result.status == AuthStatus.FAILED:
        return None
    out = io.BytesIO()
    copy_into_new_writer(reader).write(out)
    plain = out.getvalue()
    flags = result.permission_flags
    from pyhanko.pdf_utils.crypt.permissions import PubKeyPermissions as P

    allowed = {
        "allow_print": bool(flags & P.ALLOW_PRINTING), "allow_copy": bool(flags & P.ALLOW_CONTENT_EXTRACTION),
        "allow_edit": bool(flags & P.ALLOW_MODIFICATION_GENERIC),
        "allow_comments": bool(flags & P.ALLOW_ANNOTS_FORM_FILLING), "allow_forms": bool(flags & P.ALLOW_FORM_FILLING),
    }
    doc = pymupdf.open(stream=plain, filetype="pdf")
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), RECIPIENTS_KEY)
        recipients = tuple(bytes.fromhex(h) for h in _hex_strings(value)) if kind == "array" else ()
    finally:
        doc.close()
    if recipients:
        return plain, CertificateProtection(recipients, **allowed)
    own = decrypter.cert.dump()  # (another program's file: only the opener is known)
    return plain, CertificateProtection((own,), **allowed, recipients_known=False)


def _hex_strings(array: str) -> list[str]:
    import re

    return re.findall(r"<([0-9A-Fa-f\s]+)>", array)
