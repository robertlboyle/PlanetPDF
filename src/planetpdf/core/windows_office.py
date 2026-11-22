"""Microsoft Office, when installed (Windows; comtypes; ``core/office.py`` picks this or the Mac's): Word 97-2003 export, and Word /
Excel / PowerPoint files to PDF (Create Multiple PDF Files). Nothing here is needed for
anything else: without Office these features say so.

Office runs invisibly; every call opens the file read-only, converts, and closes Office
again. It works from a background thread (each call initializes COM for its thread).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.office import SUFFIXES, WORD_97_2003, OfficeBlocked, app_for  # noqa: F401 (shared)

WORD_PDF = 17  # wdExportFormatPDF
EXCEL_PDF = 0  # xlTypePDF
POWERPOINT_PDF = 32  # ppSaveAsPDF
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3  # msoAutomationSecurityForceDisable: no macros in files opened
PROG_IDS = {"word": "Word.Application", "excel": "Excel.Application", "powerpoint": "PowerPoint.Application"}


def available(app: str) -> bool:
    """Is Word / Excel / PowerPoint installed (registered)?"""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, PROG_IDS[app] + "\\CLSID"))
        return True
    except OSError:
        return False


@contextmanager
def _office(app: str):
    if not available(app):
        raise PdfError(f"This needs Microsoft {app.title()} on this computer.")
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    instance = None
    was_in_use = False
    try:
        instance = comtypes.client.CreateObject(PROG_IDS[app], dynamic=True)
        # PowerPoint (and Word, sometimes) joins the copy the user already has open:
        # never quit that one, it would close their work
        try:
            open_now = {"word": lambda: instance.Documents.Count, "excel": lambda: instance.Workbooks.Count,
                        "powerpoint": lambda: instance.Presentations.Count}[app]()
            was_in_use = open_now > 0
        except Exception:
            was_in_use = True  # (can't tell: leave it running)
        if app != "powerpoint":  # (PowerPoint's DisplayAlerts belongs to the user's session)
            try:
                instance.DisplayAlerts = 0
            except Exception:
                pass
        # Macros never run in a file opened for converting, whatever the Trust Center says
        # (security review 2026-11-21: a received .docm / .xlsm). It holds only while this
        # program drives Office; put back afterwards for a copy the user already had open.
        security_before = instance.AutomationSecurity
        instance.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        if instance.AutomationSecurity != MSO_AUTOMATION_SECURITY_FORCE_DISABLE:
            raise PdfError(f"Microsoft {app.title()} wouldn't turn macros off, so the file wasn't opened.")
        try:
            yield instance
        finally:
            if was_in_use:
                try:
                    instance.AutomationSecurity = security_before
                except Exception:
                    pass
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"Microsoft {app.title()} couldn't convert the file: {exc}") from exc
    finally:
        if instance is not None and not was_in_use:
            try:
                instance.Quit()
            except Exception:
                pass
        instance = None
        comtypes.CoUninitialize()


COMMAND_FAILED = -2146824090  # 0x800A1066: Word's "Command failed"


def word_97_blocked_by_policy() -> bool:
    """Microsoft 365 / Group Policy "File Block" for Word 97-2003 files (seen 2026-10-02:
    Planet Networks' cloud policy sets ``word97files``)."""
    if sys.platform != "win32":
        return False
    import winreg

    for root, key in ((winreg.HKEY_CURRENT_USER, r"Software\Policies\Microsoft\Cloud\Office\16.0\Word\Security\FileBlock"),
                      (winreg.HKEY_CURRENT_USER, r"Software\Policies\Microsoft\Office\16.0\Word\Security\FileBlock"),
                      (winreg.HKEY_LOCAL_MACHINE, r"Software\Policies\Microsoft\Office\16.0\Word\Security\FileBlock")):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _type = winreg.QueryValueEx(handle, "word97files")
                if int(value):
                    return True
        except OSError:
            continue
    return False


def word_save_as(source: Path, target: Path, file_format: int) -> None:
    with _office("word") as word:
        word.Visible = False
        document = word.Documents.Open(str(Path(source).resolve()), False, True)  # (ConfirmConversions, ReadOnly)
        try:
            try:
                document.SaveAs2(str(Path(target).resolve()), file_format)
            except Exception as exc:
                if file_format == WORD_97_2003 and getattr(exc, "args", [None])[0] == COMMAND_FAILED:
                    raise OfficeBlocked(
                        "Microsoft Word on this computer isn't allowed to save Word 97-2003 files"
                        + (" (your organization's Office policy, File Block settings)" if word_97_blocked_by_policy()
                           else " (Word's Trust Center ▸ File Block Settings)")
                        + ". Export to Word (.docx) instead, or ask IT to allow it.") from exc
                raise
        finally:
            document.Close(False)


def to_pdf(source: Path, target: Path) -> None:
    """A Word, Excel or PowerPoint file as PDF (Office's own "Save as PDF")."""
    source, target = Path(source).resolve(), Path(target).resolve()
    app = app_for(source)
    if app is None:
        raise PdfError(f"{source.name} isn't a Word, Excel, or PowerPoint file.")
    with _office(app) as office:
        if app == "word":
            document = office.Documents.Open(str(source), False, True)
            try:
                # (OutputFileName, ExportFormat, OpenAfterExport, OptimizeFor, Range, From, To,
                # Item, IncludeDocProps, KeepIRM, CreateBookmarks = wdExportCreateHeadingBookmarks)
                document.ExportAsFixedFormat(str(target), WORD_PDF, False, 0, 0, 1, 1, 0, True, True, 1)
            finally:
                document.Close(False)
        elif app == "excel":
            book = office.Workbooks.Open(str(source), 0, True)
            try:
                book.ExportAsFixedFormat(EXCEL_PDF, str(target))
            finally:
                book.Close(False)
        else:
            presentation = office.Presentations.Open(str(source), True, False, False)  # (ReadOnly, Untitled, WithWindow)
            try:
                presentation.SaveAs(str(target), POWERPOINT_PDF)
            finally:
                presentation.Close()
