"""Ghostscript, when installed (user decision 2026-10-02; AGPL, fine for internal use):
Save as Other ▸ Archivable PDF (PDF/A-2b), Press-Ready PDF (PDF/X-3), PDF/E-1, and
vector PostScript / EPS. Found like Tesseract: the ``PLANETPDF_GHOSTSCRIPT`` variable,
the PATH, or ``%ProgramFiles%\\gs\\gs*\\bin`` (the newest).

Ghostscript runs as a separate program on a private copy of the document; its "Page N"
lines give the real progress ("Converting page 3 of 10"), and Cancel stops it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import remove_quietly, replace_with_retry, temp_path_beside

STANDARDS = {
    "pdfa": "Archivable PDF (PDF/A-2b)",
    "pdfx": "Press-Ready PDF (PDF/X-3)",
    "pdfe": "PDF/E-1 (engineering)",
}


class GhostscriptCanceled(PdfError):
    pass


def bundled_ghostscript() -> Path | None:
    """The copy installed with PlanetPDF (``ghostscript/bin`` next to the program;
    packaging, 2026-10-04)."""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    # Windows: beside the program; Mac app: PlanetPDF.app/Contents/Resources/ghostscript
    for folder in (exe_dir / "ghostscript" / "bin", exe_dir.parent / "Resources" / "ghostscript" / "bin"):
        for name in ("gswin64c.exe", "gswin32c.exe", "gs"):
            if (folder / name).is_file():
                return folder / name
    return None


def find() -> Path | None:
    configured = os.environ.get("PLANETPDF_GHOSTSCRIPT")
    if configured and Path(configured).is_file():
        return Path(configured)
    bundled = bundled_ghostscript()
    if bundled is not None:
        return bundled
    for name in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(name)
        if found:
            return Path(found)
    if sys.platform == "win32":
        for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))):
            candidates = sorted(Path(base, "gs").glob("gs*/bin/gswin*c.exe"), key=_version, reverse=True)
            if candidates:
                return candidates[0]
    return None


def _version(path: Path) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", path.parent.parent.name)) or (0,)


def _require() -> Path:
    gs = find()
    if gs is None:
        raise PdfError("This needs Ghostscript, which isn't installed on this computer.")
    return gs


def _home(gs: Path) -> Path:
    """The folder holding Ghostscript's ``lib`` and ``iccprofiles``: …\\gs\\gs10.08.0 on Windows
    and in the bundle; ``<prefix>/share/ghostscript[/<version>]`` for Homebrew and Linux packages."""
    prefix = gs.resolve().parent.parent
    share = prefix / "share" / "ghostscript"
    versions = sorted(share.glob("[0-9]*"), key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)),
                      reverse=True)
    for folder in (prefix, share, *versions):
        if (folder / "lib" / "PDFA_def.ps").is_file():
            return folder
    return prefix


def _ps_string(text: str) -> str:
    return "(" + text.replace("\\", "/").replace("(", "\\(").replace(")", "\\)") + ")"


def _run(args: list[str], total: int, progress: Progress | None, stage: str) -> list[str]:
    """Run Ghostscript; its "Page N" lines are the progress. Returns its other messages."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", creationflags=flags)
    messages = []
    report(progress, 0, total, stage)
    for line in process.stdout:
        line = line.rstrip()
        match = re.fullmatch(r"Page (\d+)", line)
        if match:
            if not report(progress, int(match.group(1)) - 1, total, stage):
                process.kill()
                process.wait()
                raise GhostscriptCanceled("Canceled.")
        elif line and not line.startswith(("GPL Ghostscript", "Copyright", "This software", "see the file",
                                           "Processing pages", "Loading ")):
            messages.append(line)
    if process.wait() != 0:
        raise PdfError("Ghostscript couldn't convert the document:\n" + "\n".join(messages[-8:]))
    return messages


def _input_file(doc: pymupdf.Document, pages: Sequence[int] | None, near: Path) -> Path:
    tmp = temp_path_beside(near, suffix=".pdf")
    if pages is None:
        doc.save(str(tmp), garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    else:
        part = pymupdf.open()
        for p in pages:
            part.insert_pdf(doc, from_page=p, to_page=p)
        part.save(str(tmp), garbage=1, deflate=True)
        part.close()
    return tmp


def convert_standard(doc: pymupdf.Document, target: Path, standard: str, progress: Progress | None = None) -> list[str]:
    """Write ``doc`` as PDF/A-2b, PDF/X-3 or PDF/E-1. Returns Ghostscript's notes (what it
    had to change, e.g. transparency flattened). PDF/A and PDF/X can't be encrypted."""
    if standard not in STANDARDS:
        raise PdfError(f"Unknown standard {standard!r}.")
    gs = _require()
    home = _home(gs)
    target = Path(target)
    title = (doc.metadata or {}).get("title") or target.stem
    source = _input_file(doc, None, target)
    definition = temp_path_beside(target, suffix=".ps")
    out = temp_path_beside(target, suffix=".pdf")
    total = doc.page_count
    try:
        args = [str(gs), "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite", "-dEmbedAllFonts=true", "-dSubsetFonts=true",
                # (Ghostscript's sandbox stays on: only its own folder may be read, for the
                # color profiles; a folder needs the "/*")
                f"--permit-file-read={home.as_posix()}/*", f"-sOutputFile={out}"]
        if standard == "pdfa":
            text = (home / "lib" / "PDFA_def.ps").read_text(encoding="latin-1")
            text = text.replace("(srgb.icc)", _ps_string(str(home / "iccprofiles" / "srgb.icc")))
            text = text.replace("/Title (Title)", f"/Title {_ps_string(title)}")
            args += ["-dPDFA=2", "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB"]
        elif standard == "pdfx":
            text = (home / "lib" / "PDFX_def.ps").read_text(encoding="latin-1")
            text = text.replace("(ISO Coated sb.icc)", _ps_string(str(home / "iccprofiles" / "default_cmyk.icc")))
            text = text.replace("/Title (Title)", f"/Title {_ps_string(title)}")
            args += ["-dPDFX=3", "-sColorConversionStrategy=CMYK"]
        else:  # PDF/E-1: ISO 24517-1 is PDF 1.6 with every font embedded and no encryption
            text = f"[ /Title {_ps_string(title)} /DOCINFO pdfmark\n"
            args += ["-dCompatibilityLevel=1.6"]
        definition.write_text(text, encoding="latin-1")
        args += [str(definition), str(source)]
        notes = _run(args, total, progress, "Converting page")
        if standard == "pdfe":
            _mark_pdfe(out)
        report(progress, 0, 0, "Saving…")
        replace_with_retry(out, target)
        return notes
    finally:
        for path in (source, definition, out):
            remove_quietly(path)


def _mark_pdfe(path: Path) -> None:
    """The PDF/E identification in the XMP metadata (pdfe:ISO_PDFEVersion)."""
    doc = pymupdf.open(path)
    try:
        xmp = (
            '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:pdfe="http://www.aiim.org/pdfe/ns/id/">'
            "<pdfe:ISO_PDFEVersion>PDF/E-1</pdfe:ISO_PDFEVersion></rdf:Description></rdf:RDF></x:xmpmeta>"
            '<?xpacket end="w"?>'
        )
        doc.set_xml_metadata(xmp)
        tmp = temp_path_beside(path, suffix=".pdf")
        doc.save(str(tmp), garbage=1, deflate=True)
    finally:
        doc.close()
    replace_with_retry(tmp, path)


def to_postscript(doc: pymupdf.Document, pages: Sequence[int], target: Path, *, eps: bool = False,
                  progress: Progress | None = None) -> Path:
    """Vector PostScript (ps2write) or Encapsulated PostScript (eps2write, one page)."""
    pages = list(pages)
    if eps and len(pages) != 1:
        raise PdfError("Encapsulated PostScript holds one page: choose a single page.")
    gs = _require()
    target = Path(target)
    source = _input_file(doc, pages, target)
    out = temp_path_beside(target, suffix=target.suffix or ".ps")
    try:
        _run([str(gs), "-dBATCH", "-dNOPAUSE", f"-sDEVICE={'eps2write' if eps else 'ps2write'}",
              f"-sOutputFile={out}", str(source)], len(pages), progress, "Exporting page")
        replace_with_retry(out, target)
    finally:
        remove_quietly(source)
        remove_quietly(out)
    return target
