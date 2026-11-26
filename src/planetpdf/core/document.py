"""PdfSession: one open PDF, with safe save / save-as.

Save rules (see CLAUDE.md decisions):
* Unsigned document: full rewrite to a temp file beside the target, close, atomic
  replace, reopen. The original is never left half-written, and Windows file locks
  on the open document are avoided.
* Document with digital signatures: incremental save in place so existing signatures
  stay valid. Anything that would force a full rewrite raises
  ``SignaturesWouldBreak`` unless the caller explicitly allows it.
* Encryption is kept on normal saves.
"""

from __future__ import annotations

import gc
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from planetpdf.core.font_embedding import embed_quietly
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside


def _pages(count: int) -> str:
    return f"{count:,} page{'' if count == 1 else 's'}"


class PdfError(Exception):
    """Base class for document errors shown to the user."""


class NotAPdf(PdfError):
    pass


class PasswordRequired(PdfError):
    pass


class WrongPassword(PdfError):
    pass


class SignaturesWouldBreak(PdfError):
    """Raised when a save would invalidate existing digital signatures."""


def _open_document(path: Path, password: str | None) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path)
    except FileNotFoundError as exc:
        raise PdfError(f"{path} does not exist.") from exc
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise NotAPdf(f"{path.name} could not be opened as a PDF: {exc}") from exc
    return _check_opened(doc, path.name, password)


def open_pdf_bytes(data: bytes, name: str, password: str | None = None) -> pymupdf.Document:
    """Open an in-memory PDF with the same checks as a file (used for inserting pages)."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise NotAPdf(f"{name} could not be opened as a PDF: {exc}") from exc
    return _check_opened(doc, name, password)


def _check_opened(doc: pymupdf.Document, name: str, password: str | None) -> pymupdf.Document:
    if not doc.is_pdf:
        doc.close()
        raise NotAPdf(f"{name} is not a PDF file.")
    if doc.needs_pass:
        if password is None:
            doc.close()
            raise PasswordRequired(f"{name} is password protected.")
        if not doc.authenticate(password):
            doc.close()
            raise WrongPassword("The password is incorrect.")
    if doc.page_count == 0:  # only meaningful after authentication
        doc.close()
        raise NotAPdf(f"{name} has no pages.")
    return doc


WHOLE_FILE = dict(garbage=4, deflate=True, clean=True)  # (earlier revisions and unused objects dropped)


def write_whole(doc: pymupdf.Document, target: Path, **options) -> None:
    """``doc`` saved as a whole new file at ``target`` (nothing of an earlier revision kept; its
    encryption kept unless ``options`` say otherwise): written to a temporary file beside it, then
    moved into place, so nothing half-written is ever at ``target``."""
    target = Path(target)
    tmp = temp_path_beside(target)
    try:
        doc.save(str(tmp), **{**WHOLE_FILE, "encryption": pymupdf.PDF_ENCRYPT_KEEP, **options})
        replace_with_retry(tmp, target)
    except BaseException:
        remove_quietly(tmp)
        raise


def incremental_bytes(doc: pymupdf.Document) -> bytes:
    """The document as its original bytes plus its changes appended (an incremental update),
    in memory, whether it was opened from a file or from bytes: a signed file's signed revisions
    stay byte for byte, so its signatures stay valid. (PyMuPDF's own ``write`` refuses incremental
    output; MuPDF's writer does it.)"""
    from pymupdf import mupdf

    options = mupdf.PdfWriteOptions()
    options.do_incremental = 1  # (encryption kept: do_encrypt defaults to PDF_ENCRYPT_KEEP)
    buffer = mupdf.fz_new_buffer(1 << 16)
    out = mupdf.FzOutput(buffer)
    mupdf.pdf_write_document(pymupdf._as_pdf_document(doc), out, options)
    out.fz_close_output()
    return bytes(mupdf.fz_buffer_extract(buffer))


def _has_digital_signatures(doc: pymupdf.Document) -> bool:
    """True if any signature field has a value (i.e. has actually been signed)."""
    if not doc.is_form_pdf:
        return False
    for page in doc:
        for widget in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
            kind, _value = doc.xref_get_key(widget.xref, "V")
            if kind != "null":
                return True
    return False


@dataclass(frozen=True)
class Protection:
    """Password protection applied when the document is next saved (AES-256).

    * open_password: needed to open the file at all (empty = anyone can open it).
    * permissions_password: needed to change what's allowed below (empty = the open
      password also unlocks everything).
    """

    open_password: str = ""
    permissions_password: str = ""
    allow_print: bool = True
    allow_copy: bool = True
    allow_edit: bool = True
    allow_comments: bool = True
    allow_forms: bool = True

    def permissions(self) -> int:
        value = pymupdf.PDF_PERM_ACCESSIBILITY
        if self.allow_print:
            value |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
        if self.allow_copy:
            value |= pymupdf.PDF_PERM_COPY
        if self.allow_edit:
            value |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE
        if self.allow_comments:
            value |= pymupdf.PDF_PERM_ANNOTATE
        if self.allow_forms:
            value |= pymupdf.PDF_PERM_FORM
        return value

    def validate(self) -> None:
        if not self.open_password and not self.permissions_password:
            raise PdfError("Enter an open password, a permissions password, or both.")


    def save_args(self) -> dict:
        """PyMuPDF ``save()`` keyword arguments that apply this protection (AES-256)."""
        return {
            "encryption": pymupdf.PDF_ENCRYPT_AES_256,
            "owner_pw": self.permissions_password or self.open_password,
            "user_pw": self.open_password,
            "permissions": self.permissions(),
        }

    @classmethod
    def from_permission_flags(cls, flags: int, open_password: str = "", permissions_password: str = "") -> Protection:
        """The allow_* settings of a PDF's permission bits (``Document.permissions``)."""
        return cls(
            open_password, permissions_password,
            allow_print=bool(flags & pymupdf.PDF_PERM_PRINT),
            allow_copy=bool(flags & pymupdf.PDF_PERM_COPY),
            allow_edit=bool(flags & pymupdf.PDF_PERM_MODIFY),
            allow_comments=bool(flags & pymupdf.PDF_PERM_ANNOTATE),
            allow_forms=bool(flags & pymupdf.PDF_PERM_FORM),
        )


def save_pdf(doc: pymupdf.Document, target: Path, protection=None, **options) -> None:
    """``doc`` written to ``target``: a temporary file beside it, then moved into place. With
    ``protection`` (a password ``Protection`` or a ``cert_encryption.CertificateProtection``) it is
    encrypted *as it is written*, so no unprotected copy is ever in ``target``'s folder, not even
    for a moment (security review 2026-11-25: a watcher copied the unprotected file in the 19 ms
    before it was encrypted in place). New files made from a protected document stay protected
    (backlog item, built 2026-10-02)."""
    from planetpdf.core.cert_encryption import CertificateProtection, encrypt_bytes

    target = Path(target)
    options = {"garbage": 3, "deflate": True, **options}
    tmp = temp_path_beside(target)
    try:
        if protection is None:
            doc.save(str(tmp), **options)
        else:
            protection.validate()
            if isinstance(protection, CertificateProtection):
                plain = doc.tobytes(**{**options, "encryption": pymupdf.PDF_ENCRYPT_NONE})
                tmp.write_bytes(encrypt_bytes(plain, protection))  # (the plain bytes only in memory)
            else:
                doc.save(str(tmp), **{k: v for k, v in options.items() if k != "encryption"},
                         **protection.save_args())
        replace_with_retry(tmp, target)
    finally:
        remove_quietly(tmp)


def protect_pdf_file(source: Path, protection, target: Path | None = None) -> None:
    """Encrypts the unprotected PDF file ``source`` with ``protection`` into ``target`` (default:
    ``source`` itself). ``source`` should be in a private folder (``private_folder``), never beside
    a protected result."""
    source = Path(source)
    with pymupdf.open(stream=source.read_bytes(), filetype="pdf") as doc:  # (from memory: it may be replaced)
        if doc.needs_pass:
            raise PdfError(f"{source.name} is already password protected.")
        save_pdf(doc, target or source, protection)


@contextmanager
def private_folder():
    """A new folder only this user can open (inside the user's own temporary folder; mode 0700 on
    the Mac and Linux), deleted afterwards: the place for unprotected work files, such as a redacted
    copy being checked before it is encrypted."""
    folder = Path(tempfile.mkdtemp(prefix="planetpdf-"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


REMOVE_PROTECTION = "remove"


class DocumentCopy:
    """``PdfSession.work_copy``: a document of its own with the session's path, for work in
    another thread (user 2026-10-23: Split, the comparison report off the window's thread)."""

    def __init__(self, doc: pymupdf.Document, path: Path) -> None:
        self.doc = doc
        self.path = path

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def close(self) -> None:
        if not self.doc.is_closed:
            self.doc.close()


class PdfSession:
    font_report = None  # (the last save: fonts embedded / not on this computer; font_embedding)

    def __init__(self, path: Path, doc: pymupdf.Document, password: str | None) -> None:
        self._path = path
        self._doc = doc
        self._password = password
        self._modified = False
        self._has_signatures = _has_digital_signatures(doc)
        # None = keep the file's current protection; Protection / CertificateProtection =
        # apply; "remove" = none.
        self._pending_protection = None
        # Encrypt with Certificate: the file on disk is certificate-encrypted (MuPDF can't
        # read it); the document is the decrypted copy (``_plain``), and saving encrypts again.
        self._cert_protection = None
        self._plain: bytes | None = None
        self.revision = 0  # bumped whenever the underlying document object is replaced

    @classmethod
    def open(cls, path: Path, password: str | None = None) -> PdfSession:
        path = Path(path).resolve()
        return cls(path, _open_document(path, password), password)

    @classmethod
    def open_certificate_encrypted(cls, path: Path, pfx: Path | None = None, pfx_password: str = "", *,
                                   windows_providers=None) -> PdfSession:
        """A file from Encrypt with Certificate, opened with your Digital ID file (``pfx``),
        or with a certificate in this computer's store, Windows' or the Mac Keychain (no ``pfx``)."""
        from planetpdf.core.cert_encryption import decrypt_with_pfx, decrypt_with_windows

        path = Path(path).resolve()
        if pfx is None:
            plain, protection = decrypt_with_windows(path.read_bytes(), windows_providers)
        else:
            plain, protection = decrypt_with_pfx(path.read_bytes(), pfx, pfx_password)
        session = cls(path, open_pdf_bytes(plain, path.name, None), None)
        session._cert_protection, session._plain = protection, plain
        return session

    @property
    def certificate_protection(self):
        """The certificate encryption the file has (or will have after the next save)."""
        from planetpdf.core.cert_encryption import CertificateProtection

        pending = self._pending_protection
        if isinstance(pending, CertificateProtection):
            return pending
        return None if pending is not None else self._cert_protection

    # --- state -----------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def doc(self) -> pymupdf.Document:
        """The PyMuPDF document. For core modules only; UI code goes through the session."""
        return self._doc

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page_size(self, index: int) -> tuple[float, float]:
        """Page size in points, after page rotation."""
        rect = self._doc[index].rect
        return rect.width, rect.height

    @property
    def is_encrypted(self) -> bool:
        """Opened with a password (or a Digital ID: certificate encryption)."""
        return self._password is not None or self._cert_protection is not None

    @property
    def is_protected(self) -> bool:
        """The file on disk is encrypted (open and/or permissions password, or certificates)."""
        # doc.is_encrypted turns False once opened; the metadata keeps the method.
        return (bool((self._doc.metadata or {}).get("encryption")) or self._password is not None
                or self._cert_protection is not None)

    @property
    def pending_protection(self) -> Protection | str | None:
        return self._pending_protection

    def set_protection(self, protection) -> None:
        """Protection to apply on the next save: a ``Protection``, a
        ``CertificateProtection``, ``REMOVE_PROTECTION``, or None to keep whatever the file has."""
        if protection is not None and protection != REMOVE_PROTECTION:
            protection.validate()
        self._pending_protection = protection
        self._modified = True

    def _encryption_args(self) -> dict:
        pending = self._pending_protection
        if pending is None:
            return {"encryption": pymupdf.PDF_ENCRYPT_KEEP}
        if pending == REMOVE_PROTECTION:
            return {"encryption": pymupdf.PDF_ENCRYPT_NONE}
        return pending.save_args()

    def known_protection(self) -> tuple[Protection | None, bool]:
        """What protects this document, as far as PlanetPDF knows it, for new files made
        from it: (protection, complete). ``complete`` is False when it had to be guessed
        (the file's own permissions password is never known: only the password it was
        opened with). (None, True) = not protected (or protection is being removed)."""
        pending = self._pending_protection
        if isinstance(pending, Protection):
            return pending, True
        if self.certificate_protection is not None:
            return self.certificate_protection, True
        if pending == REMOVE_PROTECTION or not self.is_protected:
            return None, True
        flags = self._doc.permissions
        return Protection.from_permission_flags(flags, self._password or ""), False

    @property
    def has_digital_signatures(self) -> bool:
        return self._has_signatures

    @property
    def is_modified(self) -> bool:
        return self._modified

    def mark_modified(self) -> None:
        self._modified = True

    # --- saving ----------------------------------------------------------------

    def save(self, *, allow_signature_loss: bool = False, progress: Progress | None = None) -> None:
        """``progress`` hears each step ("Step 2 of 3: writing 120 pages…"); a save can't be
        canceled once it has begun (user 2026-10-23: no progress for Save)."""
        if self.certificate_protection is not None or self._cert_protection is not None:
            self._rewrite_to(self._path, progress)  # (always a full rewrite: the copy in memory is decrypted)
            return
        if self._has_signatures:
            if self._pending_protection is not None and not allow_signature_loss:
                raise SignaturesWouldBreak(
                    "Changing the password protection rewrites the file, which invalidates its digital signatures."
                )
            if self._pending_protection is None and self._doc.stream is not None:
                # (put back from a snapshot: undo, or a command that failed; MuPDF only appends to
                # the file it opened, so the changes are appended here: security review 2026-11-25)
                if self._save_appended(progress):
                    return
            elif self._pending_protection is None and self._doc.can_save_incrementally():
                report(progress, 0, 0, "Adding the changes to the file (its signatures are kept)…")
                self._doc.save(
                    str(self._path), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP
                )
                self._modified = False
                return
            if not allow_signature_loss:
                raise SignaturesWouldBreak(
                    "This file can't be saved incrementally, so saving would invalidate "
                    "its digital signatures."
                )
        self._rewrite_to(self._path, progress)

    def save_as(self, target: Path, *, allow_signature_loss: bool = False, progress: Progress | None = None) -> None:
        target = Path(target).resolve()
        if target == self._path:
            self.save(allow_signature_loss=allow_signature_loss, progress=progress)
            return
        if self._has_signatures and not allow_signature_loss:
            if self._modified:
                raise SignaturesWouldBreak(
                    "Saving a changed copy rewrites the file and invalidates its digital "
                    "signatures. Use Save to keep them."
                )
            report(progress, 0, 0, "Copying the file (its signatures are kept)…")
            self._copy_to(target)
            return
        self._rewrite_to(target, progress)

    def recovery_bytes(self) -> bytes:
        """The document as it is now, for the auto-save recovery copy. Keeps the file's
        own encryption, so a password-protected document isn't left lying around
        unprotected (certificate encryption too)."""
        cert = self.certificate_protection or self._cert_protection
        if cert is not None:
            from planetpdf.core.cert_encryption import encrypt_bytes

            return encrypt_bytes(self._doc.tobytes(garbage=1), cert)
        return self._doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_KEEP)

    def _release(self) -> None:
        """Close the document so its file can be replaced. MuPDF keeps the file open until
        the last object referring to the document is freed, and PyMuPDF widgets and
        annotations can sit in reference cycles (e.g. after delete_widget); collect
        them, or Windows refuses the replace with "Access is denied"."""
        self._doc.close()
        gc.collect()

    def _rewrite_to(self, target: Path, progress: Progress | None = None) -> None:
        cert = self.certificate_protection
        if cert is not None or (self._cert_protection is not None and self._pending_protection is None):
            self._rewrite_certificate_encrypted(target, cert or self._cert_protection, progress)
            return
        tmp = temp_path_beside(target)
        pending = self._pending_protection
        # (fonts it only names: from this computer, user 2026-10-03)
        self.font_report = embed_quietly(self._doc, progress, "Step 1 of 3: ")
        try:
            report(progress, 0, 0, f"Step 2 of 3: writing {_pages(self._doc.page_count)}…")
            self._doc.save(str(tmp), garbage=3, deflate=True, **self._encryption_args())
            self._release()
            replace_with_retry(tmp, target)
        except BaseException:
            remove_quietly(tmp)
            if self._doc.is_closed:
                self._reopen(self._path)
            raise
        if pending == REMOVE_PROTECTION:
            self._password = None
        elif isinstance(pending, Protection):
            self._password = pending.open_password or None
        self._pending_protection = None
        self._cert_protection, self._plain = None, None  # (no longer certificate-encrypted)
        report(progress, 0, 0, "Step 3 of 3: opening the saved file…")
        self._reopen(target)

    def _rewrite_certificate_encrypted(self, target: Path, protection, progress: Progress | None = None) -> None:
        """Save: the plain document encrypted for the recipients; the session keeps working
        on the plain copy (MuPDF can't read the encrypted file)."""
        from planetpdf.core.cert_encryption import encrypt_bytes

        self.font_report = embed_quietly(self._doc, progress, "Step 1 of 3: ")
        report(progress, 0, 0, f"Step 2 of 3: writing {_pages(self._doc.page_count)}, encrypted for its recipients…")
        plain = self._doc.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        tmp = temp_path_beside(target)
        try:
            tmp.write_bytes(encrypt_bytes(plain, protection))
            self._release()
            replace_with_retry(tmp, target)
        except BaseException:
            remove_quietly(tmp)
            if self._doc.is_closed:
                self._doc = open_pdf_bytes(self._plain or plain, self._path.name, None)
            raise
        self._pending_protection = None
        self._password = None
        self._cert_protection, self._plain = protection, plain
        report(progress, 0, 0, "Step 3 of 3: opening the saved file…")
        self._reopen(target)

    def adopt_file(self, new_file: Path, password: str | None = None, *, cert=None, plain: bytes | None = None) -> None:
        """Replace this session's file with ``new_file`` (moved into place) and reopen.

        Used when a finished file (e.g. redacted output) should become the document.
        ``password``: the new file's open password (None: it isn't encrypted). ``cert`` /
        ``plain``: it is certificate-encrypted; ``plain`` is its decrypted content.
        """
        self._doc.close()
        try:
            replace_with_retry(new_file, self._path)
        except BaseException:
            self._reopen(self._path)
            raise
        self._password = password or None
        self._pending_protection = None
        self._cert_protection, self._plain = (cert, plain) if cert is not None else (None, None)
        self._reopen(self._path)

    def _copy_to(self, target: Path) -> None:
        """Byte-for-byte copy of the unmodified file (keeps signatures intact)."""
        tmp = temp_path_beside(target)
        try:
            shutil.copyfile(self._path, tmp)
            self._release()
            replace_with_retry(tmp, target)
        except BaseException:
            remove_quietly(tmp)
            if self._doc.is_closed:
                self._reopen(self._path)
            raise
        self._reopen(target)

    def snapshot(self) -> bytes:
        """The whole document as it is now (keeping its encryption), for undo of changes
        page snapshots can't cover (e.g. flattening the form). A signed document's snapshot is its
        file with the changes appended, so putting it back keeps the signatures (and Save can
        still add to the file)."""
        if self._has_signatures:
            try:
                return incremental_bytes(self._doc)
            except Exception:  # noqa: BLE001 (MuPDF's own errors too; a full copy then)
                pass
        return self._doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_KEEP)

    def _save_appended(self, progress: Progress | None) -> bool:
        """Saves a signed document that was put back from a snapshot by appending its changes to
        the file (a temporary file, then replaced). False if the file on disk isn't the start of
        it any more (changed elsewhere), so appending would break the signatures."""
        report(progress, 0, 0, "Adding the changes to the file (its signatures are kept)…")
        data = incremental_bytes(self._doc)
        if not data.startswith(self._path.read_bytes()):
            return False
        tmp = temp_path_beside(self._path)
        try:
            tmp.write_bytes(data)
            self._release()
            replace_with_retry(tmp, self._path)
        except BaseException:
            remove_quietly(tmp)
            if self._doc.is_closed:
                self._doc = open_pdf_bytes(data, self._path.name, self._password)
            raise
        self._reopen(self._path)
        return True

    def signatures_changed(self) -> None:
        """Reads again whether the document is signed (after signatures were removed)."""
        self._has_signatures = _has_digital_signatures(self._doc)

    def open_snapshot(self, data: bytes) -> pymupdf.Document:
        """A separate document object from a ``snapshot()`` (e.g. for work in a background
        thread), unlocked like this one, so writing it keeps the file's own encryption."""
        return open_pdf_bytes(data, self._path.name, self._password)

    def work_copy(self) -> DocumentCopy:
        """A copy of the document as it is now (unsaved changes included) for work in another
        thread: what reads ``.doc``, ``.page_count`` and ``.path`` (``pages.split_document``)
        works on it as on the session. Close it when done."""
        return DocumentCopy(self.open_snapshot(self.snapshot()), self._path)

    def restore_snapshot(self, data: bytes) -> None:
        """Put back a ``snapshot()``: the document object is replaced (same file name)."""
        doc = open_pdf_bytes(data, self._path.name, self._password)
        self._doc.close()
        self._doc = doc
        self._modified = True
        self._has_signatures = _has_digital_signatures(doc)  # (Sanitize may have removed them)
        self.revision += 1

    def _reopen(self, path: Path) -> None:
        if self._cert_protection is not None and self._plain is not None:
            self._doc = open_pdf_bytes(self._plain, Path(path).name, None)  # (the decrypted copy)
        else:
            self._doc = _open_document(path, self._password)
        self._path = path
        self._modified = False
        self._has_signatures = _has_digital_signatures(self._doc)
        self.revision += 1

    def close(self) -> None:
        if not self._doc.is_closed:
            self._doc.close()
