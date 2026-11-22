"""Sign with a smart card or USB token through its PKCS#11 driver (all platforms;
backlog Phase D, built 2026-10-02). The token vendor installs the driver, a DLL / .so
(e.g. ``eTPKCS11.dll`` for SafeNet / Thales tokens, ``opensc-pkcs11.dll`` for OpenSC);
the key never leaves the token, which asks for its PIN.

(On Windows most smart cards also work through the Windows certificate store
(``windows_store``), with no driver path to choose: this is for tokens that only come
with a PKCS#11 driver.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.signing.providers.base import SigningProvider


@dataclass(frozen=True)
class TokenInfo:
    slot: int
    label: str
    manufacturer: str


def tokens(module: Path) -> list[TokenInfo]:
    """The tokens plugged in, as the driver ``module`` sees them."""
    import pkcs11

    module = Path(module)
    if not module.is_file():
        raise PdfError(f"The token driver {module} wasn't found.")
    try:
        lib = pkcs11.lib(str(module))
        return [TokenInfo(slot.slot_id, slot.get_token().label.strip(), slot.get_token().manufacturer_id.strip())
                for slot in lib.get_slots(token_present=True)]
    except Exception as exc:
        raise PdfError(f"The token driver couldn't be loaded: {exc}") from exc


class Pkcs11Provider(SigningProvider):
    """The first (or ``cert_label``) certificate and its key on the token in ``slot``."""

    def __init__(self, module: Path, pin: str, *, slot: int | None = None, token_label: str | None = None,
                 cert_label: str | None = None) -> None:
        from pyhanko.config.pkcs11 import TokenCriteria
        from pyhanko.sign import pkcs11 as P

        module = Path(module)
        if not module.is_file():
            raise PdfError(f"The token driver {module} wasn't found.")
        try:
            criteria = TokenCriteria(label=token_label) if token_label and slot is None else None
            self._session = P.open_pkcs11_session(str(module), slot_no=slot, token_criteria=criteria, user_pin=pin)
        except Exception as exc:
            raise PdfError(f"The token couldn't be opened (check that it's plugged in, and the PIN): {exc}") from exc
        try:
            self._signer = P.PKCS11Signer(self._session, cert_label=cert_label, key_label=cert_label)
            cert = self._signer.signing_cert
        except Exception as exc:
            self._session.close()
            raise PdfError(f"No signing certificate was found on the token: {exc}") from exc
        self._name = cert.subject.native.get("common_name") or cert.subject.human_friendly

    def signer(self):
        return self._signer

    @property
    def subject_name(self) -> str:
        return self._name

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
