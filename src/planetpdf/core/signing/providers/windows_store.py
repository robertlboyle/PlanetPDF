"""The Windows certificate store (Windows only: imported only when running there; backlog
item, built 2026-10-02).

* ``WindowsStoreProvider``: sign with a certificate from the user's Personal store
  ("MY"). The private key never leaves Windows: the hash is signed through CNG
  (``NCryptSignHash``), so keys that can't be exported (smart cards, TPM, company-issued
  certificates) work, and Windows asks for a PIN itself when the key needs one.
* ``WindowsTrustStore``: the roots Windows trusts ("ROOT"), plus its intermediate CAs.
* ``store_certificates``: certificates to encrypt for (Personal + Other People).

Only ``ctypes`` and the standard library (no pywin32: CLAUDE.md). The pyHanko signer and
decrypter around these keys are shared with the Mac Keychain (``key_store``, 2026-10-14).
"""

from __future__ import annotations

import ctypes
import hashlib
import ssl
import sys
from ctypes import wintypes as W

from planetpdf.core.document import PdfError
from planetpdf.core.signing.providers.base import SigningProvider, TrustStore
from planetpdf.core.signing.providers.key_store import (
    SIGNING_USAGES, StoreCertificate, build_envelope_decrypter, build_signer, chain_from,
)
from planetpdf.core.signing.providers.key_store import describe as _describe

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("The Windows certificate store is only available on Windows.")

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_ncrypt = ctypes.WinDLL("ncrypt", use_last_error=True)


class _CERT_CONTEXT(ctypes.Structure):
    _fields_ = [("dwCertEncodingType", W.DWORD), ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
                ("cbCertEncoded", W.DWORD), ("pCertInfo", ctypes.c_void_p), ("hCertStore", ctypes.c_void_p)]


_PCCERT = ctypes.POINTER(_CERT_CONTEXT)


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", W.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class _PKCS1_PADDING(ctypes.Structure):
    _fields_ = [("pszAlgId", W.LPCWSTR)]


def _fn(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


_open_system_store = _fn(_crypt32, "CertOpenSystemStoreW", ctypes.c_void_p, ctypes.c_void_p, W.LPCWSTR)
_close_store = _fn(_crypt32, "CertCloseStore", W.BOOL, ctypes.c_void_p, W.DWORD)
_enum = _fn(_crypt32, "CertEnumCertificatesInStore", _PCCERT, ctypes.c_void_p, _PCCERT)
_duplicate = _fn(_crypt32, "CertDuplicateCertificateContext", _PCCERT, _PCCERT)
_free_cert = _fn(_crypt32, "CertFreeCertificateContext", W.BOOL, _PCCERT)
_get_property = _fn(_crypt32, "CertGetCertificateContextProperty", W.BOOL, _PCCERT, W.DWORD, ctypes.c_void_p,
                    ctypes.POINTER(W.DWORD))
_acquire_key = _fn(_crypt32, "CryptAcquireCertificatePrivateKey", W.BOOL, _PCCERT, W.DWORD, ctypes.c_void_p,
                   ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(W.DWORD), ctypes.POINTER(W.BOOL))
_pfx_import = _fn(_crypt32, "PFXImportCertStore", ctypes.c_void_p, ctypes.POINTER(_BLOB), W.LPCWSTR, W.DWORD)
_sign_hash = _fn(_ncrypt, "NCryptSignHash", ctypes.c_long, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_char_p,
                 W.DWORD, ctypes.c_char_p, W.DWORD, ctypes.POINTER(W.DWORD), W.DWORD)
_free_object = _fn(_ncrypt, "NCryptFreeObject", ctypes.c_long, ctypes.c_size_t)
_decrypt = _fn(_ncrypt, "NCryptDecrypt", ctypes.c_long, ctypes.c_size_t, ctypes.c_char_p, W.DWORD, ctypes.c_void_p,
               ctypes.c_char_p, W.DWORD, ctypes.POINTER(W.DWORD), W.DWORD)


class _OAEP_PADDING(ctypes.Structure):
    _fields_ = [("pszAlgId", W.LPCWSTR), ("pbLabel", ctypes.c_void_p), ("cbLabel", W.DWORD)]

CERT_KEY_PROV_INFO_PROP_ID = 2
CERT_KEY_CONTEXT_PROP_ID = 5
CERT_NCRYPT_KEY_HANDLE_PROP_ID = 78
CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG = 0x00040000
CERT_NCRYPT_KEY_SPEC = 0xFFFFFFFF
NCRYPT_PAD_PKCS1_FLAG = 0x2
NCRYPT_PAD_OAEP_FLAG = 0x4
PKCS12_NO_PERSIST_KEY = 0x00008000
PKCS12_ALWAYS_CNG_KSP = 0x00000200


def _der(ctx: _PCCERT) -> bytes:
    return ctypes.string_at(ctx.contents.pbCertEncoded, ctx.contents.cbCertEncoded)


def _has_key(ctx: _PCCERT) -> bool:
    """Has a private key, without touching it (no PIN prompt)."""
    for prop in (CERT_KEY_PROV_INFO_PROP_ID, CERT_NCRYPT_KEY_HANDLE_PROP_ID, CERT_KEY_CONTEXT_PROP_ID):
        size = W.DWORD(0)
        if _get_property(ctx, prop, None, ctypes.byref(size)):
            return True
    return False


def _walk(store_handle) -> list[tuple[_PCCERT, bytes, bool]]:
    found = []
    ctx = _enum(store_handle, None)
    while ctx:
        found.append((_duplicate(ctx), _der(ctx), _has_key(ctx)))
        ctx = _enum(store_handle, ctx)  # (frees the previous one)
    return found


def store_certificates(stores=("MY", "AddressBook"), *, signing_only: bool = False) -> list[StoreCertificate]:
    """Certificates in the current user's stores ("MY" = Personal, "AddressBook" = Other
    People). ``signing_only``: those with a private key that may sign and haven't expired."""
    from asn1crypto import x509

    out = []
    for store in stores:
        handle = _open_system_store(None, store)
        if not handle:
            continue
        try:
            for ctx, der, has_key in _walk(handle):
                _free_cert(ctx)
                try:
                    cert = _describe(der, has_key, store)
                except Exception:
                    continue
                if signing_only:
                    usage = x509.Certificate.load(der).key_usage_value
                    if not has_key or cert.expired or (usage is not None and not set(usage.native) & SIGNING_USAGES):
                        continue
                out.append(cert)
        finally:
            _close_store(handle, 0)
    return sorted(out, key=lambda c: (c.name.lower(), c.expires))


class WindowsStoreProvider(SigningProvider):
    """Sign with a Personal-store certificate (found by its thumbprint)."""

    def __init__(self, thumbprint: str, *, _store_handle=None) -> None:
        self._own_store = _store_handle is None
        self._store = _store_handle or _open_system_store(None, "MY")
        if not self._store:
            raise PdfError("The Windows certificate store couldn't be opened.")
        self._ctx = None
        for ctx, der, has_key in _walk(self._store):
            if hashlib.sha1(der).hexdigest() == thumbprint.lower() and self._ctx is None:
                self._ctx, self.certificate = ctx, _describe(der, has_key, "MY")
            else:
                _free_cert(ctx)
        if self._ctx is None:
            raise PdfError("That certificate isn't in your Windows certificate store any more.")
        if not self.certificate.has_private_key:
            raise PdfError("Windows has no private key for that certificate, so it can't sign.")
        self.chain = _issuers(self.certificate.der)
        self._signer = None

    @classmethod
    def from_pfx_in_memory(cls, pfx: bytes, password: str) -> WindowsStoreProvider:
        """A Digital ID loaded into a temporary in-memory store (its key isn't saved
        anywhere: tests, and it signs through exactly the same CNG path)."""
        buffer = (ctypes.c_ubyte * len(pfx)).from_buffer_copy(pfx)
        blob = _BLOB(len(pfx), buffer)
        handle = _pfx_import(ctypes.byref(blob), password, PKCS12_NO_PERSIST_KEY | PKCS12_ALWAYS_CNG_KSP)
        if not handle:
            raise PdfError(f"The Digital ID couldn't be loaded (Windows error {ctypes.get_last_error()}).")
        thumbprint = None
        for ctx, der, has_key in _walk(handle):
            _free_cert(ctx)
            if has_key and thumbprint is None:
                thumbprint = hashlib.sha1(der).hexdigest()
        if thumbprint is None:
            _close_store(handle, 0)
            raise PdfError("That Digital ID has no private key.")
        provider = cls(thumbprint, _store_handle=handle)
        provider._own_store = True
        return provider

    def _with_key(self, use, what: str):
        """Run ``use(key handle)`` with the certificate's CNG private key (Windows asks for
        a PIN itself when the key needs one)."""
        key, spec, free = ctypes.c_size_t(0), W.DWORD(0), W.BOOL(False)
        if not _acquire_key(self._ctx, CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG, None, ctypes.byref(key),
                            ctypes.byref(spec), ctypes.byref(free)):
            error = ctypes.get_last_error()
            # a key that was never saved (an in-memory Digital ID) hangs on the certificate
            # as a property instead (not ours to free)
            size = W.DWORD(ctypes.sizeof(ctypes.c_size_t))
            if not _get_property(self._ctx, CERT_NCRYPT_KEY_HANDLE_PROP_ID, ctypes.byref(key), ctypes.byref(size)):
                raise PdfError(f"Windows wouldn't give access to the {what} key (error {error & 0xFFFFFFFF:#010x}).")
            free.value = False
        try:
            return use(key.value)
        finally:
            if free.value and spec.value == CERT_NCRYPT_KEY_SPEC:
                _free_object(key.value)

    def sign_hash(self, digest: bytes, digest_algorithm: str, *, rsa: bool) -> bytes:
        def sign(key: int) -> bytes:
            padding = _PKCS1_PADDING(digest_algorithm.upper())
            pad_ptr, flags = (ctypes.byref(padding), NCRYPT_PAD_PKCS1_FLAG) if rsa else (None, 0)
            size = W.DWORD(0)
            status = _sign_hash(key, pad_ptr, digest, len(digest), None, 0, ctypes.byref(size), flags)
            if status != 0:
                raise PdfError(f"Signing failed in Windows (status {status & 0xFFFFFFFF:#010x}).")
            out = ctypes.create_string_buffer(size.value)
            status = _sign_hash(key, pad_ptr, digest, len(digest), out, size, ctypes.byref(size), flags)
            if status != 0:
                raise PdfError(f"Signing failed in Windows (status {status & 0xFFFFFFFF:#010x}).")
            return out.raw[: size.value]

        return self._with_key(sign, "signing")

    def decrypt(self, data: bytes, *, oaep_hash: str | None = None) -> bytes:
        """RSA-decrypt ``data`` (a document key wrapped for this certificate: Encrypt with
        Certificate files) with the key in Windows: PKCS#1 v1.5, or OAEP with ``oaep_hash``
        ("sha256"…)."""
        def run(key: int) -> bytes:
            if oaep_hash:
                padding = _OAEP_PADDING(oaep_hash.upper(), None, 0)
                pad_ptr, flags = ctypes.byref(padding), NCRYPT_PAD_OAEP_FLAG
            else:
                pad_ptr, flags = None, NCRYPT_PAD_PKCS1_FLAG
            size = W.DWORD(0)
            status = _decrypt(key, data, len(data), pad_ptr, None, 0, ctypes.byref(size), flags)
            if status != 0:
                raise PdfError(f"Decrypting failed in Windows (status {status & 0xFFFFFFFF:#010x}).")
            out = ctypes.create_string_buffer(size.value)
            status = _decrypt(key, data, len(data), pad_ptr, out, size, ctypes.byref(size), flags)
            if status != 0:
                raise PdfError(f"Decrypting failed in Windows (status {status & 0xFFFFFFFF:#010x}).")
            return out.raw[: size.value]

        return self._with_key(run, "decryption")

    def envelope_decrypter(self):
        """A pyHanko ``EnvelopeKeyDecrypter`` that opens Encrypt with Certificate files with
        this certificate's key in Windows (``key_store``)."""
        return build_envelope_decrypter(self)

    def signer(self):
        if self._signer is None:
            self._signer = build_signer(self, ec_signature="p1363")  # (CNG gives r‖s)
        return self._signer

    @property
    def subject_name(self) -> str:
        return self.certificate.name

    def close(self) -> None:
        if self._ctx:
            _free_cert(self._ctx)
            self._ctx = None
        if self._own_store and self._store:
            _close_store(self._store, 0)
            self._store = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _issuers(der: bytes) -> list[bytes]:
    """The certificate, then its issuers as far as Windows' stores have them."""
    return chain_from(der, [c for c, _enc, _trust in ssl.enum_certificates("CA") + ssl.enum_certificates("ROOT")])


class WindowsTrustStore(TrustStore):
    """The root certificates Windows trusts (Preferences: "Trust what Windows trusts")."""

    def roots(self) -> list:
        from asn1crypto import x509

        out = []
        for der, encoding, _trust in ssl.enum_certificates("ROOT"):
            if encoding != "x509_asn":
                continue
            try:
                out.append(x509.Certificate.load(der))
            except Exception:
                continue
        return out

    def intermediates(self) -> list:
        from asn1crypto import x509

        out = []
        for der, encoding, _trust in ssl.enum_certificates("CA"):
            if encoding == "x509_asn":
                try:
                    out.append(x509.Certificate.load(der))
                except Exception:
                    continue
        return out
