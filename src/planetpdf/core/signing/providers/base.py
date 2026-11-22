from __future__ import annotations

from abc import ABC, abstractmethod


class SigningProvider(ABC):
    """Supplies a pyHanko signer (private key + certificate chain)."""

    @abstractmethod
    def signer(self):
        """Return a ``pyhanko.sign.signers.Signer``."""

    @property
    @abstractmethod
    def subject_name(self) -> str:
        """Human-readable name of the signing certificate's owner."""


class TrustStore(ABC):
    """Supplies the root certificates treated as trusted when validating."""

    @abstractmethod
    def roots(self) -> list:
        """Return ``asn1crypto.x509.Certificate`` objects."""

    def intermediates(self) -> list:
        """Intermediate CA certificates that help build the path to a root (not trusted
        themselves)."""
        return []


class CombinedTrustStore(TrustStore):
    """Several trust stores as one (e.g. the user's imported roots + what Windows trusts)."""

    def __init__(self, *stores: TrustStore) -> None:
        self.stores = [s for s in stores if s is not None]

    def roots(self) -> list:
        return [c for s in self.stores for c in s.roots()]

    def intermediates(self) -> list:
        return [c for s in self.stores for c in s.intermediates()]
