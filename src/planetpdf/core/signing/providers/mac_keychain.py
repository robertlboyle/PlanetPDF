"""The Mac Keychain (2026-10-14; the "macOS Keychain" backlog item, now testable on the Mac mini).

The Mac's counterpart of ``windows_store``:

* ``KeychainProvider``: sign with a Digital ID (an identity: certificate + private key) in the
  Keychain, smart cards included (macOS shows their identities through CryptoTokenKit). The key
  never leaves the Keychain: the hash is signed by ``SecKeyCreateSignature``, and macOS asks the
  user itself when the key's access rules want it (a password, a PIN, "Always Allow").
* ``KeychainTrustStore``: the roots macOS trusts, plus the certificates in the Keychain as
  intermediates (Preferences: "Also trust the certificates macOS trusts").
* ``keychain_certificates``: Digital IDs to sign with, or certificates to encrypt for.

Apple's Security framework through ``ctypes`` (no new library); it's loaded when first used, so
importing this anywhere is harmless. ``keychains=`` (paths) limits a search to those keychain
files: the tests use a throwaway keychain, never the user's.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
from functools import cache

from planetpdf.core.document import PdfError
from planetpdf.core.signing.providers.base import SigningProvider, TrustStore
from planetpdf.core.signing.providers.key_store import (
    StoreCertificate, build_envelope_decrypter, build_signer, can_sign, chain_from, describe,
)

NOT_FOUND = -25300  # errSecItemNotFound
STORE = "keychain"
P = ctypes.c_void_p
DIGESTS = {"sha1": "SHA1", "sha224": "SHA224", "sha256": "SHA256", "sha384": "SHA384", "sha512": "SHA512"}


class _Lib:
    def __init__(self) -> None:
        self.cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        self.sec = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Security"))
        cf, sec = self.cf, self.sec
        for name, restype, args in (
            ("CFDictionaryCreateMutable", P, [P, ctypes.c_long, P, P]),
            ("CFDictionarySetValue", None, [P, P, P]),
            ("CFArrayCreate", P, [P, ctypes.POINTER(P), ctypes.c_long, P]),
            ("CFArrayGetCount", ctypes.c_long, [P]),
            ("CFArrayGetValueAtIndex", P, [P, ctypes.c_long]),
            ("CFDataCreate", P, [P, ctypes.c_char_p, ctypes.c_long]),
            ("CFDataGetLength", ctypes.c_long, [P]),
            ("CFDataGetBytePtr", P, [P]),
            ("CFErrorCopyDescription", P, [P]),
            ("CFStringGetCString", ctypes.c_bool, [P, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
            ("CFRelease", None, [P]),
            ("CFRetain", P, [P]),
        ):
            f = getattr(cf, name)
            f.restype, f.argtypes = restype, args
        for name, restype, args in (
            ("SecItemCopyMatching", ctypes.c_int32, [P, ctypes.POINTER(P)]),
            ("SecIdentityCopyCertificate", ctypes.c_int32, [P, ctypes.POINTER(P)]),
            ("SecIdentityCopyPrivateKey", ctypes.c_int32, [P, ctypes.POINTER(P)]),
            ("SecCertificateCopyData", P, [P]),
            ("SecKeyCreateSignature", P, [P, P, P, ctypes.POINTER(P)]),
            ("SecKeyCreateDecryptedData", P, [P, P, P, ctypes.POINTER(P)]),
            ("SecTrustCopyAnchorCertificates", ctypes.c_int32, [ctypes.POINTER(P)]),
            ("SecKeychainOpen", ctypes.c_int32, [ctypes.c_char_p, ctypes.POINTER(P)]),
        ):
            f = getattr(sec, name)
            f.restype, f.argtypes = restype, args

    def const(self, name: str, lib=None) -> int:
        return P.in_dll(lib or self.sec, name).value

    def callbacks(self, name: str) -> int:
        return ctypes.addressof(ctypes.c_char.in_dll(self.cf, name))


@cache
def _lib() -> _Lib:
    try:
        return _Lib()
    except (OSError, AttributeError, TypeError) as exc:
        raise PdfError(f"The Mac Keychain isn't available here ({exc}).") from exc


def _bytes(data: int) -> bytes:
    lib = _lib()
    size = lib.cf.CFDataGetLength(data) if data else 0
    pointer = lib.cf.CFDataGetBytePtr(data) if size > 0 else None
    # (never string_at on a null pointer: it crashes the whole process, no exception to catch)
    return ctypes.string_at(pointer, size) if pointer else b""


def _error_text(error: int) -> str:
    lib = _lib()
    if not error:
        return "no details"
    text = lib.cf.CFErrorCopyDescription(error)
    buffer = ctypes.create_string_buffer(1024)
    lib.cf.CFStringGetCString(text, buffer, len(buffer), 0x08000100)
    lib.cf.CFRelease(text)
    lib.cf.CFRelease(error)
    return buffer.value.decode("utf-8", "replace")


def _search(item_class: str, *, keychains: tuple[str, ...] = (), token: bool = False) -> list[int]:
    """Every item of ``item_class`` (a retained ``SecIdentityRef`` / ``SecCertificateRef`` each:
    release them). ``token``: smart cards' items (CryptoTokenKit) instead."""
    lib = _lib()
    cf = lib.cf
    query = cf.CFDictionaryCreateMutable(None, 0, lib.callbacks("kCFTypeDictionaryKeyCallBacks"),
                                         lib.callbacks("kCFTypeDictionaryValueCallBacks"))
    opened = []
    try:
        cf.CFDictionarySetValue(query, lib.const("kSecClass"), lib.const(item_class))
        cf.CFDictionarySetValue(query, lib.const("kSecReturnRef"), lib.const("kCFBooleanTrue", cf))
        cf.CFDictionarySetValue(query, lib.const("kSecMatchLimit"), lib.const("kSecMatchLimitAll"))
        if token:
            cf.CFDictionarySetValue(query, lib.const("kSecAttrAccessGroup"), lib.const("kSecAttrAccessGroupToken"))
        if keychains:
            for path in keychains:
                keychain = P()
                if lib.sec.SecKeychainOpen(str(path).encode(), ctypes.byref(keychain)) == 0:
                    opened.append(keychain.value)
            array = (P * len(opened))(*opened)
            search = cf.CFArrayCreate(None, array, len(opened), lib.callbacks("kCFTypeArrayCallBacks"))
            cf.CFDictionarySetValue(query, lib.const("kSecMatchSearchList"), search)
            cf.CFRelease(search)
        result = P()
        status = lib.sec.SecItemCopyMatching(query, ctypes.byref(result))
        if status == NOT_FOUND or not result.value:
            return []
        if status != 0:
            raise PdfError(f"The Keychain couldn't be searched (status {status}).")
        items = [cf.CFRetain(cf.CFArrayGetValueAtIndex(result.value, i)) for i in range(cf.CFArrayGetCount(result.value))]
        cf.CFRelease(result.value)
        return items
    finally:
        cf.CFRelease(query)
        for keychain in opened:
            cf.CFRelease(keychain)


def _certificate_der(certificate: int) -> bytes:
    lib = _lib()
    data = lib.sec.SecCertificateCopyData(certificate)
    try:
        return _bytes(data)
    finally:
        lib.cf.CFRelease(data)


def _identity_der(identity: int) -> bytes:
    lib = _lib()
    certificate = P()
    if lib.sec.SecIdentityCopyCertificate(identity, ctypes.byref(certificate)) != 0:
        raise PdfError("A Digital ID in the Keychain has no readable certificate.")
    try:
        return _certificate_der(certificate.value)
    finally:
        lib.cf.CFRelease(certificate.value)


def _identities(keychains: tuple[str, ...] = ()) -> list[tuple[int, bytes]]:
    """(retained identity, its certificate's DER): the Keychain's and, unless limited to given
    keychain files, the smart cards'."""
    found = []
    for identity in _search("kSecClassIdentity", keychains=keychains) + \
            ([] if keychains else _search("kSecClassIdentity", token=True)):
        try:
            found.append((identity, _identity_der(identity)))
        except PdfError:
            _lib().cf.CFRelease(identity)
    return found


def keychain_certificates(*, signing_only: bool = False, keychains: tuple[str, ...] = ()) -> list[StoreCertificate]:
    """``signing_only``: Digital IDs (with their private key) meant for signing documents that
    haven't expired. Otherwise every certificate (to encrypt for), noting which have a key."""
    lib = _lib()
    identities = _identities(keychains)
    with_key = {hashlib.sha1(der).hexdigest() for _identity, der in identities}
    for identity, _der in identities:
        lib.cf.CFRelease(identity)
    if signing_only:
        ders = [der for _identity, der in identities]
    else:
        ders = []
        for certificate in _search("kSecClassCertificate", keychains=keychains):
            try:
                ders.append(_certificate_der(certificate))
            finally:
                lib.cf.CFRelease(certificate)
    out, seen = [], set()
    for der in ders:
        thumbprint = hashlib.sha1(der).hexdigest()
        if thumbprint in seen:
            continue
        seen.add(thumbprint)
        try:
            cert = describe(der, thumbprint in with_key, STORE)
        except Exception:
            continue
        if signing_only and (cert.expired or not can_sign(der)):
            continue
        out.append(cert)
    return sorted(out, key=lambda c: (c.name.lower(), c.expires))


class KeychainProvider(SigningProvider):
    """Sign with a Digital ID in the Keychain (found by its certificate's SHA-1 thumbprint)."""

    def __init__(self, thumbprint: str, *, keychains: tuple[str, ...] = ()) -> None:
        lib = _lib()
        self._identity = None
        for identity, der in _identities(keychains):
            if self._identity is None and hashlib.sha1(der).hexdigest() == thumbprint.lower():
                self._identity, self.certificate = identity, describe(der, True, STORE)
            else:
                lib.cf.CFRelease(identity)
        if self._identity is None:
            raise PdfError("That Digital ID isn't in your Keychain any more.")
        pool = [d for d in (c.der for c in keychain_certificates(keychains=keychains))] + _anchor_ders()
        self.chain = chain_from(self.certificate.der, pool)
        self._signer = None

    def _with_key(self, use, what: str) -> bytes:
        lib = _lib()
        key = P()
        if lib.sec.SecIdentityCopyPrivateKey(self._identity, ctypes.byref(key)) != 0 or not key.value:
            raise PdfError(f"The Keychain didn't give PlanetPDF the key for {what}.")
        try:
            return use(key.value)
        finally:
            lib.cf.CFRelease(key.value)

    def _run(self, function: str, key: int, algorithm: str, data: bytes, what: str) -> bytes:
        lib = _lib()
        error = P()
        source = lib.cf.CFDataCreate(None, data, len(data))
        try:
            result = getattr(lib.sec, function)(key, lib.const(algorithm), source, ctypes.byref(error))
        finally:
            lib.cf.CFRelease(source)
        if not result:
            raise PdfError(f"{what} failed in the Keychain ({_error_text(error.value)}).")
        try:
            return _bytes(result)
        finally:
            lib.cf.CFRelease(result)

    def sign_hash(self, digest: bytes, digest_algorithm: str, *, rsa: bool) -> bytes:
        name = DIGESTS.get(digest_algorithm.lower())
        if name is None:
            raise PdfError(f"Signing with {digest_algorithm} isn't supported.")
        algorithm = (f"kSecKeyAlgorithmRSASignatureDigestPKCS1v15{name}" if rsa
                     else f"kSecKeyAlgorithmECDSASignatureDigestX962{name}")
        return self._with_key(lambda key: self._run("SecKeyCreateSignature", key, algorithm, digest, "Signing"),
                              "signing")

    def decrypt(self, data: bytes, *, oaep_hash: str | None = None) -> bytes:
        """Unwrap a document key (Encrypt with Certificate files): PKCS#1 v1.5, or OAEP."""
        algorithm = (f"kSecKeyAlgorithmRSAEncryptionOAEP{DIGESTS[oaep_hash.lower()]}" if oaep_hash
                     else "kSecKeyAlgorithmRSAEncryptionPKCS1")
        return self._with_key(lambda key: self._run("SecKeyCreateDecryptedData", key, algorithm, data, "Decrypting"),
                              "decryption")

    def envelope_decrypter(self):
        return build_envelope_decrypter(self)

    def signer(self):
        if self._signer is None:
            self._signer = build_signer(self, ec_signature="der")  # (the Keychain gives X9.62 DER)
        return self._signer

    @property
    def subject_name(self) -> str:
        return self.certificate.name

    def close(self) -> None:
        if self._identity:
            _lib().cf.CFRelease(self._identity)
            self._identity = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _anchor_ders() -> list[bytes]:
    lib = _lib()
    anchors = P()
    if lib.sec.SecTrustCopyAnchorCertificates(ctypes.byref(anchors)) != 0 or not anchors.value:
        return []
    try:
        return [_certificate_der(lib.cf.CFArrayGetValueAtIndex(anchors.value, i))
                for i in range(lib.cf.CFArrayGetCount(anchors.value))]
    finally:
        lib.cf.CFRelease(anchors.value)


class KeychainTrustStore(TrustStore):
    """The roots macOS trusts, and the Keychain's certificates as intermediates."""

    def __init__(self, keychains: tuple[str, ...] = ()) -> None:
        self._keychains = keychains

    def roots(self) -> list:
        from asn1crypto import x509

        out = []
        for der in _anchor_ders():
            try:
                out.append(x509.Certificate.load(der))
            except Exception:
                continue
        return out

    def intermediates(self) -> list:
        from asn1crypto import x509

        return [x509.Certificate.load(c.der) for c in keychain_certificates(keychains=self._keychains)
                if x509.Certificate.load(c.der).ca]
