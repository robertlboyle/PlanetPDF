# PyInstaller build of PlanetPDF (packaging, 2026-10-04). Run through scripts/build.ps1.
#
# One-folder builds only, never one-file (LGPL: Qt and FFmpeg stay replaceable DLLs).
# The program (becomes versions\<version>\ in the install folder). The launcher (PlanetPDF.exe at
# the top of the install folder, starting the current version) is native since 2026-10-07:
# launcher\ (Rust), built by build.ps1.
# Ghostscript, the Read Out Loud voices and Tesseract's language files are added by
# build.ps1 / the installer, not here (except tessdata, which is small and per version).
#
# On a Mac (2026-10-10; scripts/build_mac.sh) the same build becomes PlanetPDF.app (Apple
# Silicon), which says it opens PDF files (Finder's Open With); build_mac.sh adds Ghostscript.
#
# On Linux (2026-10-26; scripts/build_linux.py, in Docker or on a Linux computer) the one-folder
# program becomes the AppImage, .deb and .rpm (x86-64 or ARM64, whichever computer builds it).

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
ICON = str(SRC / "planetpdf" / "resources" / "icons" / "planetpdf.ico")
MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"
DEFAULT_TESSDATA = ("/opt/homebrew/share/tessdata" if MAC else
                    r"C:\Program Files\Tesseract-OCR\tessdata" if WINDOWS else "/usr/share/tesseract-ocr/5/tessdata")
TESSDATA = Path(os.environ.get("PLANETPDF_TESSDATA", DEFAULT_TESSDATA))
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "planetpdf" / "__init__.py").read_text()).group(1)

datas = [(str(SRC / "planetpdf" / "resources"), "planetpdf/resources")]
datas += [(str(TESSDATA / f"{lang}.traineddata"), "tessdata") for lang in ("eng", "osd")]
binaries = []
hiddenimports = [
    # loaded by name or inside functions
    "planetpdf.core.compliance", "planetpdf.core.signing.providers.pkcs11_token",
    "planetpdf.core.update_helper", "planetpdf.self_test", "planetpdf.ui.printer_probe",
]
if MAC:
    hiddenimports += ["planetpdf.ui.macos_shell", "planetpdf.ui.default_app"]
elif WINDOWS:
    hiddenimports += ["planetpdf.core.windows_office", "planetpdf.core.windows_speech",
                      "planetpdf.core.windows_scanner", "planetpdf.core.windows_fonts",
                      "planetpdf.core.windows_elevate", "planetpdf.core.signing.providers.windows_store",
                      "comtypes.client", "comtypes.stream"]
# packages that bring data files or load parts dynamically
for package in ("pyhanko", "pyhanko_certvalidator", "kokoro_onnx", "piper", "espeakng_loader", "phonemizer",
                "spellchecker", "pptx", "docx", "pdf2docx", "language_tags", "segments", "csvw", "pkcs11",
                "onnxruntime", "sounddevice", "_sounddevice_data"):
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("certifi") + collect_data_files("tzdata")
binaries += collect_dynamic_libs("pymupdf")

QT_UNUSED = [f"PySide6.{m}" for m in (
    "QtQml", "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtSql", "QtOpenGL", "QtOpenGLWidgets", "QtTest",
    "QtDesigner", "QtUiTools", "QtHelp", "QtXml", "QtConcurrent", "QtDBus", "QtPdf", "QtPdfWidgets",
    "QtMultimedia", "QtWebEngineCore", "QtWebEngineWidgets", "Qt3DCore", "QtCharts", "QtDataVisualization")]
EXCLUDES = QT_UNUSED + ["tkinter", "pytest", "IPython", "matplotlib", "PyQt5", "PyQt6", "PySide2"]

a = Analysis(
    [str(SRC / "planetpdf" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    # first: the update's installing step cleans its environment before any library loads
    # (security list 2026-11-26, item 10)
    runtime_hooks=[str(ROOT / "packaging" / "rthook_clean_environment.py")] + (
        [str(ROOT / "packaging" / "linux" / f"rthook_{name}.py") for name in ("environment", "portaudio")]
        if sys.platform == "linux" else []),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PlanetPDF",
    icon=ICON,
    version=str(ROOT / "build" / "version_info.txt") if WINDOWS else None,
    console=False,
    upx=False,
    target_arch="arm64" if MAC else None,
)
# The command line (2026-10-18): on Windows a windowed program has no console, so the same program is
# also built as a console one, planetpdf-cli.exe, which PlanetPDF.com in the install folder runs.
# (The Mac's and Linux's program prints to the terminal it was started from as it is.)
console_exes = [] if not WINDOWS else [EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="planetpdf-cli",
    icon=ICON,
    version=str(ROOT / "build" / "version_info.txt"),
    console=True,
    upx=False,
)]
coll = COLLECT(exe, *console_exes, a.binaries, a.datas, strip=False, upx=False, name="PlanetPDF")
if MAC:
    app = BUNDLE(
        coll,
        name="PlanetPDF.app",
        icon=ICON,  # (PyInstaller makes the .icns from the .ico with Pillow)
        bundle_identifier="net.planet.planetpdf",
        version=VERSION,
        info_plist={
            "CFBundleName": "PlanetPDF",
            "CFBundleDisplayName": "PlanetPDF",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": os.environ.get("PLANETPDF_MIN_MACOS", "15.0"),  # (build_mac.py)
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
            # PDF from Scanner finds network scanners with Bonjour and talks to them (AirScan / eSCL):
            # macOS asks the user once (Local Network privacy) with this reason
            "NSLocalNetworkUsageDescription": "PlanetPDF looks for scanners on your network so you can scan "
                                              "straight into a PDF.",
            "NSBonjourServices": ["_uscan._tcp", "_uscans._tcp"],
            # Office for Mac is driven with AppleScript: macOS asks once per Office app, with this
            "NSAppleEventsUsageDescription": "PlanetPDF uses Microsoft Word, Excel and PowerPoint to turn "
                                             "Office files into PDFs and to save Word 97-2003 files.",
            # Finder's Open With; "Alternate" until the user makes it the default (first start)
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF Document",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.adobe.pdf"],
            }],
        },
    )
