"""This computer's own certificate store, whichever it is (2026-10-14): Windows' certificate store
(``windows_store``) or the Mac Keychain (``mac_keychain``). The signing dialog, Encrypt with
Certificate, opening certificate-encrypted files and trust use this, so they work the same on
both; elsewhere ``available()`` is False and only Digital ID files are offered.
"""

from __future__ import annotations

import sys


def available(platform: str | None = None) -> bool:
    return (platform or sys.platform) in ("win32", "darwin")


def name(platform: str | None = None) -> str:
    """What the dialogs call it: "Windows" / "Keychain"."""
    return "Keychain" if (platform or sys.platform) == "darwin" else "Windows"


def signing_certificates() -> list:
    """Certificates with a private key, meant for signing, not expired."""
    if sys.platform == "darwin":
        from planetpdf.core.signing.providers.mac_keychain import keychain_certificates

        return keychain_certificates(signing_only=True)
    from planetpdf.core.signing.providers.windows_store import store_certificates

    return store_certificates(("MY",), signing_only=True)


def all_certificates() -> list:
    """Certificates to encrypt for: the user's own and other people's."""
    if sys.platform == "darwin":
        from planetpdf.core.signing.providers.mac_keychain import keychain_certificates

        return keychain_certificates()
    from planetpdf.core.signing.providers.windows_store import store_certificates

    return store_certificates(("MY", "AddressBook"))


def own_certificates() -> list:
    """The user's own certificates (Personal store / the Keychain's identities)."""
    if sys.platform == "darwin":
        return [c for c in all_certificates() if c.has_private_key]
    from planetpdf.core.signing.providers.windows_store import store_certificates

    return store_certificates(("MY",))


def provider(thumbprint: str):
    if sys.platform == "darwin":
        from planetpdf.core.signing.providers.mac_keychain import KeychainProvider

        return KeychainProvider(thumbprint)
    from planetpdf.core.signing.providers.windows_store import WindowsStoreProvider

    return WindowsStoreProvider(thumbprint)


def trust_store():
    if sys.platform == "darwin":
        from planetpdf.core.signing.providers.mac_keychain import KeychainTrustStore

        return KeychainTrustStore()
    from planetpdf.core.signing.providers.windows_store import WindowsTrustStore

    return WindowsTrustStore()
