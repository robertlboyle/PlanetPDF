"""Make the manual as a PDF: ``python scripts/make_manual.py "<folder>/PlanetPDF Manual.pdf"``.

Run by scripts\\build.ps1 for every build (user 2026-10-18: a PDF of the help "in the install
folder … update these files as each new build is built"). The menu and shortcut reference pages
come from a real (offscreen) PlanetPDF window, so they match the build.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "build" / "PlanetPDF Manual.pdf"
    from PySide6.QtWidgets import QApplication

    from planetpdf import __version__
    from planetpdf.core import help as H

    app = QApplication.instance() or QApplication(["make_manual"])
    from planetpdf.ui.help_pdf import generated_pages
    from planetpdf.ui.main_window import MainWindow

    with tempfile.TemporaryDirectory() as profile:
        window = MainWindow(Path(profile))
        try:
            pages = generated_pages(window)
        finally:
            window.close()

    def progress(done, total, stage=""):
        print(f"{stage} {done + 1} of {total}", flush=True)
        return True

    H.write_pdf(target, pages, version=__version__, progress=progress)
    del app
    print(f"Wrote {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
