"""Protect ▸ Advanced Options (user screenshot 2026-10-03): security policies, the security
envelope, and the document's security properties.

* **Security policies** (Acrobat's Manage Security Policies): named, reusable protection
  settings kept in the profile (``security-policies.json``). A password policy keeps
  which things are allowed, never the passwords (they're asked when the policy is
  applied); a certificate policy keeps the recipients' public certificates.
* **Security envelope** (Create Security Envelope): files put into a new PDF as
  attachments, with a cover page listing them, and the PDF protected with a password or
  for chosen people's certificates, to send them safely.
* ``describe``: what protects a document, for Security Properties.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError, Protection

PERMISSIONS = (("allow_print", "Printing"), ("allow_copy", "Copying text and images"),
               ("allow_edit", "Changing the document"), ("allow_comments", "Adding comments"),
               ("allow_forms", "Filling in forms"))
POLICIES_FILE = "security-policies.json"


@dataclass
class SecurityPolicy:
    name: str
    kind: str  # "password" or "certificate"
    description: str = ""
    open_password: bool = True  # (password: an open password is asked for when it's applied)
    permissions: dict = field(default_factory=lambda: {key: True for key, _label in PERMISSIONS})
    recipients: list[str] = field(default_factory=list)  # (certificate: DER, base64)

    def certificate_protection(self):
        from planetpdf.core.cert_encryption import CertificateProtection

        return CertificateProtection(tuple(base64.b64decode(r) for r in self.recipients), **self.permissions)

    def summary(self) -> str:
        denied = [label.lower() for key, label in PERMISSIONS if not self.permissions.get(key, True)]
        who = (f"{len(self.recipients)} recipient{'s' if len(self.recipients) != 1 else ''}" if self.kind == "certificate"
               else ("open password" if self.open_password else "no open password"))
        return f"{who}; " + ("not allowed: " + ", ".join(denied) if denied else "everything allowed")


def load_policies(profile_root: Path) -> list[SecurityPolicy]:
    path = Path(profile_root) / POLICIES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    policies = []
    for entry in data if isinstance(data, list) else []:
        try:
            policies.append(SecurityPolicy(**entry))
        except TypeError:
            continue
    return policies


def save_policies(profile_root: Path, policies: list[SecurityPolicy]) -> None:
    path = Path(profile_root) / POLICIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(p) for p in policies], indent=2), encoding="utf-8")
    tmp.replace(path)


def policy_from_protection(name: str, protection, description: str = "") -> SecurityPolicy:
    """A policy that does what ``protection`` does (passwords left out)."""
    permissions = {key: bool(getattr(protection, key)) for key, _label in PERMISSIONS}
    if isinstance(protection, Protection):
        return SecurityPolicy(name, "password", description, bool(protection.open_password), permissions)
    return SecurityPolicy(name, "certificate", description, False, permissions,
                          [base64.b64encode(r).decode("ascii") for r in protection.recipients])


# --- the security envelope --------------------------------------------------------------------------------


def make_envelope(files: list[Path], protection, *, title: str = "Security Envelope", note: str = "") -> bytes:
    """A PDF holding ``files`` as attachments, with a cover page listing them, protected
    with ``protection`` (a ``Protection`` or a ``CertificateProtection``)."""
    if not files:
        raise PdfError("Choose at least one file to put in the envelope.")
    from planetpdf.core.resources import font_path

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_font(fontname="noto", fontfile=str(font_path("sans")))
    page.insert_text((72, 96), title, fontname="noto", fontsize=24)
    y = 130
    page.insert_text((72, y), f"Created {datetime.now():%B %d, %Y at %I:%M %p}".replace(" 0", " "),
                     fontname="noto", fontsize=10, color=(0.35, 0.35, 0.35))
    y += 30
    if note:
        y = page.insert_textbox(pymupdf.Rect(72, y, 540, y + 120), note, fontname="noto", fontsize=11) and y + 40
    page.insert_text((72, y), "Files in this envelope (open the Attachments panel):", fontname="noto", fontsize=12)
    y += 22
    used: set[str] = set()
    for path in files:
        path = Path(path)
        data = path.read_bytes()
        name = path.name
        n = 2
        while name in used:  # (two files with one name: both kept)
            name = f"{path.stem} ({n}){path.suffix}"
            n += 1
        used.add(name)
        doc.embfile_add(name, data, filename=name, desc=f"{len(data):,} bytes")
        if y < 740:
            page.insert_text((90, y), f"•  {name}   ({_size(len(data))})", fontname="noto", fontsize=11)
            y += 18
    if y >= 740:
        page.insert_text((90, y), f"… {len(files)} files in all", fontname="noto", fontsize=11)
    doc.set_metadata({"title": title, "producer": "PlanetPDF"})
    doc.xref_set_key(doc.pdf_catalog(), "PageMode", "/UseAttachments")
    doc.subset_fonts()
    if isinstance(protection, Protection):
        protection.validate()
        return doc.tobytes(garbage=3, deflate=True, **protection.save_args())
    from planetpdf.core.cert_encryption import encrypt_bytes

    return encrypt_bytes(doc.tobytes(garbage=3, deflate=True), protection)


def _size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    return f"{max(n, 1) / 1024:.0f} KB"


# --- what protects a document -------------------------------------------------------------------------------


def describe(session) -> list[tuple[str, str]]:
    """Security Properties: (what, how) rows for the open document, including protection
    that will be applied on the next save."""
    from planetpdf.core.cert_encryption import CertificateProtection
    from planetpdf.core.document import REMOVE_PROTECTION

    rows: list[tuple[str, str]] = []
    pending = session.pending_protection
    protection, _complete = session.known_protection()
    encryption = (session.doc.metadata or {}).get("encryption") or ""
    if pending == REMOVE_PROTECTION:
        rows.append(("Security method", "No security (protection is removed when you save)"))
        return rows
    if isinstance(protection, CertificateProtection):
        rows.append(("Security method", "Certificate security"))
        rows.append(("Can be opened by", ", ".join(protection.names) or "the people it was encrypted for"))
        rows.append(("Encryption", "AES 256-bit"))
    elif protection is not None:
        rows.append(("Security method", "Password security"))
        rows.append(("Password to open", "Yes" if protection.open_password or session.is_encrypted else "No"))
        rows.append(("Encryption", encryption or "AES 256-bit"))
    else:
        rows.append(("Security method", "No security"))
        return rows
    if pending is not None:
        rows.append(("Note", "These settings are applied when you save."))
    for key, label in PERMISSIONS:
        rows.append((label, "Allowed" if getattr(protection, key, True) else "Not allowed"))
    return rows
