"""Office files are turned into PDFs from a copy that can't reach the network (security review
2026-11-25: a .docx naming a template on a server made Word, driven by PlanetPDF, fetch it and
sign in to that server; Word itself would have opened the downloaded file in Protected View).

- Word, Excel, and PowerPoint files in the Office Open XML formats (.docx, .xlsx, .pptx and their
  kin) are copied with every link to an outside file pointed at a place that doesn't exist on this
  computer: attached templates, linked pictures, linked objects, external data. Hyperlinks stay:
  nothing opens them while converting.
  Each relationship part is read as XML (security review 2026-11-27, finding 5: a pattern missed
  ``TargetMode = "External"``, an entity in the value, and a part written in UTF-16), and any
  relationship that names a place outside the file is pointed nowhere, whatever its TargetMode
  says; a part that can't be read as XML is refused.
- RTF files are cleaned the same way: their attached template (``\\*\\template``), field
  instructions (``\\*\\fldinst``: INCLUDEPICTURE, INCLUDETEXT, LINK…; the field's shown result
  stays), and objects linked to outside files are taken out of the copy.
  An RTF object keeps only the picture of what it showed (``\\result``): an object can make Word
  start the program behind it.
- Data connections that refresh when a workbook opens, and a document's mail merge (its data
  source is queried when it opens), make PlanetPDF refuse the file: they are found by reading
  the parts as XML.
- The older binary formats (.doc and .xls) are turned into the newer ones first by Office's own
  file converters (``wordconv.exe``, ``excelcnv.exe``: they translate the format, without opening
  the file in Word or Excel), then cleaned as above; Word or Excel only ever opens the clean copy.
  Before that, a file that holds a server path or a field that fetches from elsewhere is refused.
  Where the converters aren't there (the Mac, an Office without them), these formats are refused.
- A CSV file becomes a workbook made by PlanetPDF, every cell a value (a CSV's formulas, such as
  WEBSERVICE or DDE, never reach Excel).
- Everything else that can't be cleaned (.ppt, OpenDocument, WordPerfect) is refused, and so is any
  older-format file that came from the internet or an e-mail (Windows' "mark of the web", the
  Mac's quarantine flag): PlanetPDF says how to convert it safely (open it in Office, save it in
  the newer format, convert that). Robert 2026-11-28: "err on the side of not converting vs.
  creating a security issue".

Pure Python (no Qt).
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from planetpdf.core.document import PdfError

OOXML = {".docx", ".docm", ".dotx", ".dotm", ".xlsx", ".xlsm", ".xltx", ".xltm",
         ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm"}
NEWER = {".doc": ".docx", ".rtf": ".docx", ".wpd": ".docx", ".odt": ".docx", ".xls": ".xlsx", ".ods": ".xlsx",
         ".csv": ".xlsx", ".ppt": ".pptx", ".odp": ".pptx"}
REMOVED_TARGET = "file:///C:/PlanetPDF-removed-link" if sys.platform == "win32" else "file:///PlanetPDF-removed-link"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OUTSIDE = re.compile(r"^\s*([a-z][a-z0-9+.-]*:|\\\\|//)", re.IGNORECASE)  # (a scheme, a share, a server)


class OfficeRefused(PdfError):
    """An Office file PlanetPDF won't hand to Office (it could reach the network)."""


CONVERTED = {".doc": ".docx", ".xls": ".xlsx"}  # (by Office's own converters: ``windows_office.convert_old``)
# What a field or a stored path that fetches from elsewhere looks like in an old binary file, in
# its 8-bit or its UTF-16 text. Only ever a reason to refuse (never to let a file through).
_FETCHING = [re.compile(p, re.IGNORECASE) for p in (
    r"\\\\[a-z0-9._$-]+\\",  # (a share: \\server\share)
    r"file:",
    # (a field that fetches: the code after the field mark, 0x13, that begins every field in .doc text)
    r"\x13\s*(includepicture|includetext|ddeauto|dde|import|link|database)\b",
)]


def fetching_text(data: bytes) -> str:
    """The first text in an old binary Office file that names a server, a file, or a field that
    fetches from elsewhere ("" when none): the file is then refused before anything opens it."""
    for text in (data.decode("latin-1"), data.decode("utf-16-le", errors="ignore"),
                 data[1:].decode("utf-16-le", errors="ignore")):
        for pattern in _FETCHING:
            found = pattern.search(text)
            if found:
                return found.group(0).strip()
    return ""


def downloaded(path: Path) -> bool:
    """Whether ``path`` came from the internet or an e-mail, as the operating system marked it."""
    path = Path(path)
    if sys.platform == "win32":
        try:
            with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="replace") as marks:
                found = re.search(r"ZoneId\s*=\s*(\d+)", marks.read())
            return bool(found and int(found.group(1)) >= 3)  # (3 = internet, 4 = restricted)
        except OSError:
            return False
    if sys.platform == "darwin":
        try:
            return subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)],
                                  capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def unlinked_rels(data: bytes | str) -> tuple[bytes, int]:
    """A ``.rels`` part, read as XML (any encoding its declaration names, entities decoded), with
    every relationship that leads outside the file pointed nowhere: ``TargetMode`` External, or a
    target that is an address, a share, or a server, but hyperlinks (nothing opens them while
    converting). Returns (the part, how many were changed). ``OfficeRefused`` when it can't be read."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise OfficeRefused(f"A part of this Office file can't be read as XML ({exc}), so PlanetPDF can't make sure "
                            "it doesn't reach other places. Open it in Office, save it again, and try again.") from exc
    count = 0
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        mode = (element.get("TargetMode") or "").strip().lower()
        target = element.get("Target") or ""
        if mode != "external" and not _OUTSIDE.match(target):
            continue
        if (element.get("Type") or "").strip().lower().endswith("/hyperlink"):
            continue
        element.set("Target", REMOVED_TARGET)
        count += 1
    if not count:
        return data, 0
    ElementTree.register_namespace("", RELATIONSHIPS)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), count


# --- RTF ----------------------------------------------------------------------------------------

_RTF_REMOVED = re.compile(rb"\{\s*(?:\\\*\s*)?\\(template|fldinst|linkval)(?![a-z])")
_RTF_OBJECT = re.compile(rb"\{\s*\\object(?![a-z])")
_RTF_RESULT = re.compile(rb"\{\s*\\result(?![a-z])")
_RTF_BIN = re.compile(rb"\\bin(-?\d+) ?")


def _rtf_group_end(data: bytes, start: int) -> int:
    """Where the group opened at ``start`` ends (after its closing brace)."""
    depth, i, n = 0, start, len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:  # (a backslash: an escaped character, a control word, or binary data)
            binary = _RTF_BIN.match(data, i)
            if binary:
                i = binary.end() + max(int(binary.group(1)), 0)
                continue
            i += 2
            continue
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def unlinked_rtf(data: bytes) -> tuple[bytes, int]:
    """An RTF file without its attached template, its fields' instructions, and objects linked to
    outside files (security review 2026-11-27, finding 5: Word read an RTF's template from a server,
    and stopped on its own question about a picture field). Returns (the file, how many groups went)."""
    out, i, n, count = bytearray(), 0, len(data), 0
    while i < n:
        c = data[i]
        if c == 0x5C:
            binary = _RTF_BIN.match(data, i)
            end = binary.end() + max(int(binary.group(1)), 0) if binary else i + 2
            out += data[i:end]
            i = end
            continue
        if c == 0x7B:
            if _RTF_REMOVED.match(data, i):
                i, count = _rtf_group_end(data, i), count + 1
                continue
            if _RTF_OBJECT.match(data, i):
                # (every object, linked or embedded: only the picture of what it showed stays, from
                # its \result group; an embedded object can be made to start its program on open)
                end = _rtf_group_end(data, i)
                result = _RTF_RESULT.search(data, i + 1, end)
                if result:
                    out += data[result.start():_rtf_group_end(data, result.start())]
                i, count = end, count + 1
                continue
        out.append(c)
        i += 1
    return bytes(out), count


_REFRESHED = re.compile(r"(connections|querytables/[^/]+|pivotcache/[^/]+)\.xml$", re.IGNORECASE)


def _refuses(name: str, data: bytes) -> str:
    """Why a part makes Office reach elsewhere when the file opens ("" = it doesn't): a data
    connection or query table set to refresh on opening, or a document's mail merge (its data
    source is queried when it opens). Read as XML; a part that can't be read is a reason too."""
    lower = name.lower()
    wanted = _REFRESHED.search(lower) or lower.endswith("settings.xml")
    if not wanted:
        return ""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return f"its part {name} can't be read"
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local == "mailMerge":
            return "it is a mail merge document, whose data source Word would ask for"
        refresh = {k.rsplit("}", 1)[-1]: v for k, v in element.attrib.items()}.get("refreshOnLoad", "")
        if refresh.strip().lower() in ("1", "true"):
            return "it has data that refreshes from elsewhere when it opens"
    return ""


def _unlinked_copy(source: Path, target: Path) -> int:
    changed = 0
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
            for info in original.infolist():
                data = original.read(info)
                if info.filename.lower().endswith(".rels"):
                    cleaned, count = unlinked_rels(data)
                    if count:
                        data, changed = cleaned, changed + count
                why = _refuses(info.filename, data)
                if why:
                    raise OfficeRefused(f"{source.name} wasn't converted: {why}. Open it in Office yourself if you "
                                        "trust it, and save it as a PDF there.")
                copy.writestr(info, data)
    except zipfile.BadZipFile as exc:
        raise PdfError(f"{source.name} isn't a readable Office file ({exc}).") from exc
    except OfficeRefused:
        Path(target).unlink(missing_ok=True)
        raise
    return changed


def _csv_workbook(source: Path, target: Path) -> None:
    """A CSV as a workbook PlanetPDF writes itself: numbers as numbers, everything else as text,
    never a formula (=WEBSERVICE(…) in a CSV makes Excel fetch from the address when it opens it)."""
    import csv

    from openpyxl import Workbook

    raw = source.read_bytes()
    text = raw.decode("utf-8-sig") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8", errors="replace")
    book = Workbook()
    sheet = book.active
    for row in csv.reader(text.splitlines()):
        cells = []
        for value in row:
            try:
                cells.append(float(value))
            except ValueError:
                cells.append(value)
        sheet.append(cells)
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # (never a formula, whatever it starts with)
    book.save(target)


def _refuse_old(source: Path, why: str) -> OfficeRefused:
    newer = NEWER.get(source.suffix.lower(), "a newer format")
    return OfficeRefused(f"{source.name} wasn't converted: {why}. Open it in Office yourself (a file from elsewhere "
                         f"opens in Protected View), save it as {newer}, and make the PDF from that copy.")


def safe_copy(source: Path, folder: Path) -> Path:
    """The copy of ``source`` in ``folder`` (a private one) that Office is given."""
    source = Path(source)
    target = Path(folder) / source.name
    suffix = source.suffix.lower()
    if suffix in OOXML:
        _unlinked_copy(source, target)
        return target
    if downloaded(source):
        raise _refuse_old(source, "it came from the internet or an e-mail, and in this older format PlanetPDF "
                                  "can't take out its links to other places before Office opens it")
    if suffix == ".rtf":
        target.write_bytes(unlinked_rtf(source.read_bytes())[0])
        return target
    if suffix == ".csv":
        target = target.with_suffix(".xlsx")
        _csv_workbook(source, target)
        return target
    if suffix in CONVERTED:
        found = fetching_text(source.read_bytes())
        if found:
            raise _refuse_old(source, f"it names something outside the file (\u201c{found[:60]}\u201d), and in this "
                                      "older format PlanetPDF can't take that out first")
        if sys.platform != "win32":
            raise _refuse_old(source, "on this computer PlanetPDF can't turn this older format into the newer one "
                                      "safely first")
        from planetpdf.core import windows_office

        middle = Path(folder) / f"converted{CONVERTED[suffix]}"
        windows_office.convert_old(source, middle)  # (OfficeRefused when it can't)
        target = target.with_suffix(CONVERTED[suffix])
        _unlinked_copy(middle, target)
        middle.unlink(missing_ok=True)
        return target
    raise _refuse_old(source, "PlanetPDF can't make sure a file in this format doesn't reach other places when "
                              "Office opens it")
