"""``PlanetPDF.exe --self-test REPORT.txt`` (packaging, 2026-10-04): the built program
checks that everything it needs made it into the build: every feature library loads,
the bundled files are there, and the outside programs work for real (OCR on a page,
a PDF/A conversion with the bundled Ghostscript, a spoken sentence made by each voice).
Writes one line per check to REPORT (a windowed program has no console) and exits 1 if
anything failed. Used by scripts/build.ps1 on the build, and by IT on an installed copy.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import time
import traceback
from pathlib import Path

FLAG = "--self-test"

MODULES = [
    "pymupdf", "PySide6.QtWidgets", "PySide6.QtSvg", "PySide6.QtPrintSupport", "pypdf", "PIL.Image",
    "cv2", "numpy", "pyhanko.sign", "pyhanko_certvalidator", "cryptography", "fontTools.subset",
    "openpyxl", "pptx", "docx", "spellchecker", "pkcs11", "onnxruntime", "kokoro_onnx", "piper",
    "sounddevice", "espeakng_loader", "phonemizer", "certifi", "tzdata", "segno", "pdf417gen",
]
WINDOWS_MODULES = ["comtypes.client", "planetpdf.core.windows_office", "planetpdf.core.windows_speech",
                   "planetpdf.core.windows_scanner", "planetpdf.core.signing.providers.windows_store",
                   "planetpdf.core.windows_elevate"]


class _Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = 0

    def check(self, name: str, test, *, optional: bool = False) -> None:
        start = time.perf_counter()
        try:
            note = test()
            self.lines.append(f"OK    {name}{f': {note}' if note else ''} ({time.perf_counter() - start:.1f} s)")
        except Exception as exc:
            if optional:
                self.lines.append(f"SKIP  {name}: {exc}")
                return
            self.failed += 1
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            self.lines.append(f"FAIL  {name}: {detail}")


def _import(name: str):
    return lambda: (importlib.import_module(name), "")[1]


def _resources() -> str:
    from planetpdf.core.resources import app_icon_path, resources_dir

    root = resources_dir()
    needed = [app_icon_path(), root / "fonts", root / "icons" / "free", root / "licenses" / "components.json",
              root / "update" / "update_key.pub", root / "dictionaries"]
    missing = [str(p.relative_to(root)) for p in needed if not p.exists()]
    if missing:
        raise FileNotFoundError("missing " + ", ".join(missing))
    return str(root)


def _pdf2docx() -> str:
    from planetpdf.core.export import _import_pdf2docx

    _import_pdf2docx()
    return ""


def _spelling() -> str:
    from spellchecker import SpellChecker

    checker = SpellChecker()
    if "receive" not in checker or checker.correction("recieve") != "receive":
        raise RuntimeError("the English word list didn't load")
    return ""


def _ocr() -> str:
    import pymupdf

    from planetpdf.core import ocr

    tessdata = ocr.require_tessdata()
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=120)
    page.insert_text((20, 70), "Hello World", fontsize=28)
    pix = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    scan.new_page(width=300, height=120).insert_image(pymupdf.Rect(0, 0, 300, 120), pixmap=pix)
    text = ocr.ocr_page_text(scan[0], tessdata=tessdata)
    if "Hello" not in text:
        raise RuntimeError(f"recognized {text!r}")
    return str(tessdata)


def _ghostscript() -> str:
    import pymupdf

    from planetpdf.core import compliance, ghostscript

    gs = ghostscript.find()
    if gs is None:
        raise FileNotFoundError("Ghostscript wasn't found")
    doc = pymupdf.open()
    doc.new_page(width=300, height=200).insert_text((20, 50), "Archive me", fontsize=14)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a.pdf"
        ghostscript.convert_standard(doc, target, "pdfa")
        with pymupdf.open(target) as made:
            if not compliance.check(made, "pdfa").complete:
                raise RuntimeError("the PDF/A copy doesn't pass Check Compliance")
    return str(gs)


def _voice(engine: str):
    def test() -> str:
        from planetpdf.core import neural_speech as NS

        root = NS.models_dir()
        if root is None:
            raise FileNotFoundError("the voices (models folder) aren't installed")
        voice = NS.load_engine(engine, root)
        samples, rate = voice.make("Hello from PlanetPDF.", voice.default_voice, 1.0)
        if len(samples) < rate // 2:
            raise RuntimeError("no speech was made")
        return f"{len(samples) / rate:.1f} s of speech from {root}"
    return test


def _mac_scanner() -> str:
    """The Mac's scanner helper (ImageCaptureCore) is there and runs."""
    import subprocess

    from planetpdf.core import mac_scanner

    helper = mac_scanner.helper(compile_if_needed=False)
    done = subprocess.run([str(helper), "--version-check"], capture_output=True, text=True, timeout=20)
    if done.returncode != 2 or "usage" not in done.stderr:
        raise RuntimeError(f"the scanner helper didn't start ({done.returncode}: {done.stderr.strip()[:200]})")
    return str(helper)


def _signing() -> str:
    import pyhanko.sign.validation  # noqa: F401

    import planetpdf.core.signing.digital  # noqa: F401
    import planetpdf.core.signing.providers.pkcs11_token  # noqa: F401

    return ""


def run(report_path: Path) -> int:
    from planetpdf import __version__

    report = _Report()
    report.lines.append(f"PlanetPDF {__version__} self-test, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.lines.append(f"Program: {sys.executable}")
    for name in MODULES + (WINDOWS_MODULES if sys.platform == "win32" else []):
        report.check(f"import {name}", _import(name))
    report.check("program files (fonts, icons, licenses, update key, word lists)", _resources)
    report.check("Export to Word (pdf2docx)", _pdf2docx)
    report.check("Check Spelling word list", _spelling)
    report.check("Signing (pyHanko)", _signing)
    report.check("OCR (Tesseract language files)", _ocr)
    report.check("Ghostscript (PDF/A conversion)", _ghostscript)
    if sys.platform == "darwin":
        report.check("Scanner helper (ImageCaptureCore)", _mac_scanner)
    report.check("Read Out Loud: Piper voice", _voice("piper"), optional=True)
    report.check("Read Out Loud: Kokoro voice", _voice("kokoro"), optional=True)
    report.lines.append("RESULT: " + ("all passed" if not report.failed else f"{report.failed} failed"))
    report_path.write_text("\n".join(report.lines) + "\n", encoding="utf-8")
    return 1 if report.failed else 0


def main(argv: list[str]) -> int:
    index = argv.index(FLAG)
    path = Path(argv[index + 1]) if index + 1 < len(argv) else Path(tempfile.gettempdir()) / "planetpdf-self-test.txt"
    return run(path)
