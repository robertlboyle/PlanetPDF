"""Regenerate the third-party license inventory (user request 2026-10-02).

Run after changing requirements.txt, bundled fonts/icons or the Python version:

    .venv\\Scripts\\python.exe scripts\\update_notices.py

* pip-licenses (dev dependency) gives name / version / license / URL of every runtime
  package in requirements.txt (dev-only packages such as pytest aren't shipped, so
  they aren't listed).
* Every license / notice / copying file each package ships in its metadata is copied in
  full (pip-licenses only takes one, and e.g. cryptography has three).
* Parts that pip-licenses can't see are listed by hand in BUNDLED: the Python runtime,
  Qt inside PySide6, MuPDF inside PyMuPDF, FFmpeg inside OpenCV, Tesseract language
  data, the fonts and the Font Awesome icons.

Writes THIRD-PARTY-NOTICES.txt (project root, and the same file in
src/planetpdf/resources/licenses/ for Help > About and the packaged app) and
src/planetpdf/resources/licenses/components.json (the About screen's table).
tests/test_notices.py fails when these are out of date.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "src" / "planetpdf" / "resources"
LICENSES = RESOURCES / "licenses"
NOTICES_NAME = "THIRD-PARTY-NOTICES.txt"
LICENSE_FILE = re.compile(r"(^|[/\\])(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/\\]*$", re.IGNORECASE)
RULE = "=" * 78
# Which of a package's licenses PlanetPDF uses, where its metadata doesn't make that clear
PACKAGE_NOTES = {
    "pyside6-essentials": "Used under the LGPL-3.0 (the wheel ships only Qt's commercial license wording); "
                          "the LGPL-3.0 and GPL-3.0 texts are listed under Qt.",
    "shiboken6": "Used under the LGPL-3.0; the LGPL-3.0 and GPL-3.0 texts are listed under Qt.",
    "pymupdf": "Used under the GNU AGPL-3.0: PlanetPDF is itself AGPL-3.0-or-later, its complete source at "
               "https://github.com/robertlboyle/PlanetPDF.",
    "espeakng-loader": "Its eSpeak NG library is GPL-3.0 (listed under eSpeak NG).",
}
_MIT_THEWH1TEAGLE = """MIT License

Copyright (c) 2025 github.com/thewh1teagle

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
# Packages whose metadata names no license: (license, its text if the package ships
# none, home page), checked on the project's repository (2026-10-02).
LICENSE_FIXES = {
    "kokoro-onnx": ("MIT", _MIT_THEWH1TEAGLE, "https://github.com/thewh1teagle/kokoro-onnx"),
    "espeakng-loader": ("MIT", _MIT_THEWH1TEAGLE, "https://github.com/thewh1teagle/espeakng-loader"),
}


def runtime_packages() -> list[tuple[str, str]]:
    """(name, pinned version) from requirements.txt, in file order."""
    pins = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()  # (no comments, no "; sys_platform" markers)
        if "==" in line:
            name, version = line.split("==", 1)
            pins.append((name.strip(), version.split()[0]))  # (the hashes follow it: hash_requirements.py)
    return pins


def pip_licenses(names: list[str]) -> dict[str, dict]:
    exe = Path(sys.executable).with_name("pip-licenses.exe" if sys.platform == "win32" else "pip-licenses")
    out = subprocess.run([str(exe), "--format=json", "--with-urls", "--packages", *names],
                         check=True, capture_output=True, text=True, encoding="utf-8").stdout
    return {_key(row["Name"]): row for row in json.loads(out)}


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_files(name: str) -> list[tuple[str, str]]:
    """(file name, full text) of every license-like file the package ships."""
    dist = metadata.distribution(name)
    found = []
    for file in dist.files or []:
        path = str(file)
        if ".dist-info" not in path or not LICENSE_FILE.search(path):
            continue
        try:
            text = Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.append((path.split(".dist-info/", 1)[-1].split(".dist-info\\", 1)[-1], text))
    return sorted(found)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _model_hashes(prefix: str) -> str:
    """The voice files' SHA-256 (PlanetPDF checks them before loading: security review 2026-11-25)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from planetpdf.core.neural_speech import MODEL_SHA256

    return "SHA-256: " + "; ".join(f"{name.split('/', 1)[1]} {digest}" for name, digest in MODEL_SHA256.items()
                                   if name.startswith(prefix)) + "."


GHOSTSCRIPT_VERSION = "10.08.0"  # the copy bundled by scripts/build.ps1 (C:\Program Files\gs\gs<version>)


RUST_MIT = """Copyright (c) The Rust Project Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def _ghostscript_license() -> list:
    path = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "gs" / f"gs{GHOSTSCRIPT_VERSION}" / "doc" / "COPYING"
    return [("COPYING (Ghostscript)", _text(path))] if path.is_file() else []


def _package_license(name: str) -> list:
    dist = metadata.distribution(name)
    return [(Path(str(f)).name, _text(Path(dist.locate_file(f)))) for f in dist.files or []
            if "COPYING" in str(f) and ".dist-info" in str(f)]


def bundled() -> list[dict]:
    """What ships inside the app but isn't a Python package of its own."""
    import pymupdf
    from PySide6 import QtCore

    site = Path(metadata.distribution("opencv-python-headless").locate_file(""))
    fonts = RESOURCES / "fonts"
    items = [
        {"name": "Python", "version": platform.python_version(), "license": "PSF-2.0",
         "url": "https://www.python.org", "part_of": "the PlanetPDF runtime",
         "files": [("LICENSE.txt", _text(Path(sys.base_prefix) / "LICENSE.txt"))]},
        {"name": "Qt", "version": QtCore.qVersion(), "license": "LGPL-3.0-only",
         "url": "https://www.qt.io/licensing/open-source-lgpl-obligations", "part_of": "PySide6-Essentials",
         "note": "Qt's libraries are separate DLLs in the program folder (one-folder build) and can be "
                 "replaced with compatible builds; Qt's source code is available from https://download.qt.io.",
         "files": [("LGPL-3.0.txt", _text(LICENSES / "LGPL-3.0.txt")), ("GPL-3.0.txt", _text(LICENSES / "GPL-3.0.txt"))]},
        {"name": "MuPDF", "version": pymupdf.mupdf_version, "license": "AGPL-3.0-or-later",
         "url": "https://mupdf.com", "part_of": "PyMuPDF",
         "note": "Used under the AGPL: PlanetPDF is itself AGPL-3.0-or-later, its complete source at "
                 "https://github.com/robertlboyle/PlanetPDF; the license text "
                 "is listed under PyMuPDF.", "files": []},
        {"name": "FFmpeg", "version": "(bundled build)", "license": "LGPL-2.1-or-later",
         "url": "https://ffmpeg.org", "part_of": "opencv-python-headless",
         "note": "opencv_videoio_ffmpeg*.dll; its license and source offer are in OpenCV's third-party "
                 "license file, listed under opencv-python-headless.", "files": []},
        {"name": "Ghostscript (PDF/A, PDF/X, PDF/E, PostScript)", "version": GHOSTSCRIPT_VERSION,
         "license": "AGPL-3.0-or-later", "url": "https://www.ghostscript.com",
         "part_of": "the installed program (ghostscript folder), run as a separate program",
         "note": "Used under the AGPL: PlanetPDF is itself AGPL-3.0-or-later, its complete source at "
                 "https://github.com/robertlboyle/PlanetPDF; unchanged Artifex "
                 "build; source code at https://github.com/ArtifexSoftware/ghostpdl-downloads/releases.",
         "files": _ghostscript_license()},
        {"name": "PyInstaller bootloader", "version": metadata.version("pyinstaller"),
         "license": "GPL-2.0-or-later WITH Bootloader-exception", "url": "https://pyinstaller.org",
         "part_of": "PlanetPDF.exe (the program's starter)",
         "note": "The bootloader exception allows using it with programs under any license.",
         "files": _package_license("pyinstaller")},
        {"name": "Rust standard library", "version": "stable", "license": "MIT OR Apache-2.0",
         "url": "https://www.rust-lang.org", "part_of": "PlanetPDF.exe in the install folder (the launcher, launcher\\)",
         "note": "Compiled into the launcher; no other Rust libraries are shipped.",
         "files": [("MIT (Rust)", RUST_MIT), ("Apache-2.0.txt", _text(LICENSES / "Apache-2.0.txt"))]},
        {"name": "PortAudio (sound for Read Out Loud)", "version": "19.7.0", "license": "MIT",
         "url": "https://www.portaudio.com", "part_of": "the Linux packages (_internal/libportaudio.so.2)",
         "note": "Built with ALSA only; used when the computer has no PortAudio of its own.",
         "files": [("LICENSE.txt (PortAudio)", _text(LICENSES / "PortAudio-LICENSE.txt"))]},
        {"name": "XCB utility libraries (xcb-util-cursor, -image, -util, -render-util, -icccm, -keysyms)",
         "version": "", "license": "MIT", "url": "https://xcb.freedesktop.org",
         "part_of": "the Linux packages (_internal/libxcb-*.so)",
         "note": "Qt's X11 window support needs them; many desktops don't install them.",
         "files": [("copyright (Debian)", _text(LICENSES / "xcb-util-copyright.txt"))]},
        {"name": "Tesseract OCR language data (tessdata: eng, osd)", "version": "4.x", "license": "Apache-2.0",
         "url": "https://github.com/tesseract-ocr/tessdata", "part_of": "the packaged app (OCR)",
         "files": [("Apache-2.0.txt", _text(LICENSES / "Apache-2.0.txt"))]},
        {"name": "Kokoro-82M voice model (kokoro-v1.0.onnx, voices-v1.0.bin)", "version": "1.0", "license": "Apache-2.0",
         "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0",
         "part_of": "models/kokoro (Read Out Loud)", "note": _model_hashes("kokoro/"),
         "files": [("Apache-2.0.txt", _text(LICENSES / "Apache-2.0.txt"))]},
        {"name": "Piper voices: Kristin (en_US-kristin-medium), Joe (en_US-joe-medium)", "version": "medium",
         "license": "Public domain (Kristin, LibriVox recordings) / CC0-1.0 (Joe)",
         "url": "https://huggingface.co/rhasspy/piper-voices", "part_of": "models/piper (Read Out Loud)",
         "note": "Chosen because their licenses allow business use; several popular Piper voices are non-commercial only. "
                 "From commit 5ec43b186ef915a195d3ddbcfd9916e76c503c3f. " + _model_hashes("piper/"),
         "files": []},
        {"name": "eSpeak NG (pronunciation, inside piper-tts and espeakng-loader)", "version": "", "license": "GPL-3.0-or-later",
         "url": "https://github.com/espeak-ng/espeak-ng", "part_of": "Read Out Loud",
         "note": "Used under the GPL-3.0, compatible with PlanetPDF's own AGPL-3.0-or-later; PlanetPDF's "
                 "complete source is at https://github.com/robertlboyle/PlanetPDF.",
         "files": [("GPL-3.0.txt", _text(LICENSES / "GPL-3.0.txt"))]},
    ]
    # the optional AI Text Reading add-on (its own installer: scripts/build_ai_addon.ps1; 2026-10-21)
    addon = "the optional AI Text Reading add-on (a separate installer; ai-ocr folder)"
    items += [
        {"name": "Unlimited-OCR (AI text reading model, Baidu)", "version": "8249d20", "license": "MIT",
         "url": "https://huggingface.co/baidu/Unlimited-OCR", "part_of": addon,
         "note": "The model's own LICENSE file is installed with it (ai-ocr/model/LICENSE).", "files": []},
        {"name": "PyTorch (with NVIDIA CUDA 12.8)", "version": "2.10.0", "license": "BSD-3-Clause",
         "url": "https://pytorch.org", "part_of": addon,
         "note": "Includes NVIDIA's CUDA runtime libraries, redistributed under NVIDIA's CUDA license "
                 "(https://docs.nvidia.com/cuda/eula/); their license files are installed with PyTorch.",
         "files": []},
        {"name": "Hugging Face Transformers", "version": "4.57.1", "license": "Apache-2.0",
         "url": "https://github.com/huggingface/transformers", "part_of": addon,
         "files": [("Apache-2.0.txt", _text(LICENSES / "Apache-2.0.txt"))]},
        {"name": "Python (the add-on's own, embeddable)", "version": "3.12.10", "license": "PSF-2.0",
         "url": "https://www.python.org", "part_of": addon, "files": []},
    ]
    for family, file in (("Noto Sans", "NotoSans"), ("Noto Serif", "NotoSerif"), ("Dancing Script", "DancingScript"),
                         ("Great Vibes", "GreatVibes"), ("Caveat", "Caveat"), ("Barlow Condensed", "BarlowCondensed")):
        items.append({"name": f"{family} (font)", "version": "", "license": "OFL-1.1",
                      "url": "https://openfontlicense.org", "part_of": "resources/fonts",
                      "files": [(f"OFL-{file}.txt", _text(fonts / f"OFL-{file}.txt"))]})
    items.append({"name": "Font Awesome Free (icons)", "version": "6.7.2",
                  "license": "CC-BY-4.0 (icons); see LICENSE.txt", "url": "https://fontawesome.com/license/free",
                  "part_of": "resources/icons/free",
                  "files": [("LICENSE.txt", _text(RESOURCES / "icons" / "free" / "LICENSE.txt"))]})
    if (RESOURCES / "icons" / "fa" / "LICENSE.txt").is_file():  # (builds made with a Pro license only)
        items.append({"name": "Font Awesome Pro (icons)", "version": "", "license": "Font Awesome Pro commercial license",
                      "url": "https://fontawesome.com/license", "part_of": "resources/icons/fa",
                      "note": "Licensed to Planet Networks.",
                      "files": [("LICENSE.txt", _text(RESOURCES / "icons" / "fa" / "LICENSE.txt"))]})
    assert site.exists()
    return items


def main() -> None:
    pins = runtime_packages()
    summary = pip_licenses([name for name, _v in pins])
    components = []
    for name, version in pins:
        row = summary.get(_key(name))
        if row is None:
            sys.exit(f"pip-licenses found no installed package {name!r}: is .venv up to date?")
        if row["Version"] != version:
            sys.exit(f"{name}: requirements.txt pins {version}, but {row['Version']} is installed")
        known = LICENSE_FIXES.get(_key(name))
        license_name = known[0] if known and row["License"] in ("UNKNOWN", "") else row["License"]
        files = license_files(name) or ([(f"{known[0]}.txt", known[1])] if known else [])
        url = row["URL"] if row["URL"] not in ("UNKNOWN", "") else (known[2] if known else row["URL"])
        components.append({"name": row["Name"], "version": row["Version"], "license": license_name,
                           "url": url, "part_of": "", "kind": "Python package",
                           "note": PACKAGE_NOTES.get(_key(name), ""), "files": files})
    for item in bundled():
        components.append({"kind": "Bundled", "note": "", **item})

    lines = [
        "PlanetPDF - third-party software notices",
        RULE,
        "",
        "PlanetPDF is free software from Planet Networks, under the GNU Affero",
        "General Public License, version 3 or later (AGPL-3.0-or-later); its",
        "complete source is at https://github.com/robertlboyle/PlanetPDF. It includes the",
        "third-party software listed below, each under its own license. Full license",
        "texts follow the summary. Generated by scripts/update_notices.py (pip-licenses",
        "for the Python packages); do not edit by hand.",
        "",
        "Summary",
        "-------",
    ]
    width = max(len(c["name"]) for c in components)
    for c in components:
        lines.append(f"{c['name']:<{width}}  {c['version']:<12}  {c['license']}")
    for c in components:
        lines += ["", "", RULE, f"{c['name']} {c['version']}".rstrip(), RULE,
                  f"License: {c['license']}"]
        if c["url"] and c["url"] != "UNKNOWN":
            lines.append(f"Home page: {c['url']}")
        if c.get("part_of"):
            lines.append(f"Included in: {c['part_of']}")
        if c.get("note"):
            lines.append(c["note"])
        for file_name, text in c["files"]:
            lines += ["", f"----- {file_name} -----", "", text.rstrip()]
        if not c["files"] and not c.get("note"):
            lines += ["", "(The package ships no license file; see the home page.)"]
    notices = "\n".join(lines) + "\n"

    LICENSES.mkdir(parents=True, exist_ok=True)
    for target in (ROOT / NOTICES_NAME, LICENSES / NOTICES_NAME):
        target.write_text(notices, encoding="utf-8", newline="\n")
    table = [{k: c[k] for k in ("name", "version", "license", "url", "kind")} | {"part_of": c.get("part_of", "")}
             for c in components]
    (LICENSES / "components.json").write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n",
                                              encoding="utf-8", newline="\n")
    print(f"{len(components)} components, {len(notices) // 1024} KB of notices")


if __name__ == "__main__":
    main()
