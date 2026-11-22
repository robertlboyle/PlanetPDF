# PlanetPDF

A free, open-source PDF editor for Windows, the Mac, and Linux, made by Planet Networks for its own
use and shared with everyone.

Read, fill in, sign, comment on, redact, organize, convert, and recognize the text of PDF files, with
menus and keyboard shortcuts that work like Adobe Acrobat's. PlanetPDF never collects anything about
you or how you use it: no telemetry, no analytics, no ads. It goes online only for what you ask
(update checks you can turn off, downloads you start, a timestamp server you set).

Downloads, release notes, and the user manual: **https://software.planet.net/**

## Features

- **View:** single page, two page, and scrolling layouts; rotate the view; zoom tools; reflow; rulers,
  grid, and guides; bookmarks; several documents in tabs or windows.
- **Fill & Sign:** fill in forms; add text, check marks, and signatures; digital signatures with a
  Digital ID file, the Windows certificate store, the Mac Keychain, or a smart card (PAdES).
- **Comment:** highlights, notes with replies, text boxes, callouts, shapes, stamps, and attachments,
  stored as standard PDF annotations.
- **Edit PDF:** move and retype text, move and replace images, add links.
- **Prepare Form:** every field type, with automatic field detection.
- **Redact:** search and pattern redaction, sanitizing, and a verification report that refuses to
  save a copy that fails its checks.
- **Organize pages:** insert, delete, reorder, rotate, split, combine, crop, page labels.
- **Scan & OCR:** scan from WIA, eSCL / AirScan, and ImageCaptureCore scanners; clean up and
  straighten scans; recognize text with Tesseract.
- **Export:** Word, Excel, PowerPoint, RTF, HTML, XML, text, images, PostScript, PDF/A, PDF/X, and PDF/E.
- **Protect:** passwords and certificate encryption.
- **Read Out Loud** with natural offline voices (Kokoro, Piper).
- **Scriptable:** a command line for every function, a local JSON-RPC API, and plugins that run in
  their own process with permissions you grant (see [docs/SCRIPTING.md](docs/SCRIPTING.md)).

## Running from source

PlanetPDF needs **Python 3.14**.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m planetpdf
```

On the Mac and Linux use `python3.14 -m venv .venv` and `.venv/bin/python`. Optional programs PlanetPDF
uses when they're installed: **Tesseract** (text recognition) and **Ghostscript** (PDF/A, PDF/X,
PDF/E, and PostScript). `scripts/download_voices.py` downloads the Read Out Loud voices.

Run the tests with `.venv\Scripts\python.exe -m pytest`.

## Building

- Windows: `scripts\build.ps1` (PyInstaller, a small Rust launcher, and Inno Setup 6).
- Mac: `scripts/build_mac.py`.
- Linux (AppImage, .deb, and .rpm for x86-64 and ARM64): `scripts/build_linux.py --docker`.

## Icons

The icons are [Font Awesome Free](https://fontawesome.com) 6.7.2 (CC BY 4.0). Planet Networks' own
builds use Font Awesome Pro under its company license; those files are not part of this repository,
and PlanetPDF uses the free icons whenever they're missing.

## License

PlanetPDF is free software under the **GNU Affero General Public License, version 3 or later**
([LICENSE](LICENSE)). It includes or uses other people's work under their own licenses (PyMuPDF and
Ghostscript under the AGPL, Qt under the LGPL, and many more); see
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).

There is no warranty and no support, but we try to fix reported bugs quickly and add good
suggestions. Bug reports and feedback: **software@planet.net**.
