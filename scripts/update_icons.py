"""Copy the Font Awesome icons PlanetPDF uses into src/planetpdf/resources/icons/.

Usage:  .venv\\Scripts\\python.exe scripts\\update_icons.py [--free [PACKAGE]] [--pro [PACKAGE]]

Two sets (user 2026-10-24: the code is shared publicly, and "if you have a FontAwesome license,
you can use a different … option with your own files"):

- ``--free``: **Font Awesome Free** (icons CC BY 4.0, free to share) into ``icons/free/``. Every
  copy of PlanetPDF has these, so anyone can build it. The package is the unzipped "free for
  desktop" download from https://fontawesome.com/download or the GitHub release
  (https://github.com/FortAwesome/Font-Awesome/releases, fontawesome-free-<version>-desktop.zip),
  by default Dropbox's Dev\\fontawesome-free. Free has fewer icons and few in the outline
  ("regular") style, so each icon comes from Free's regular style, else its solid style, else
  the stand-in named in ``FREE_STAND_INS``; the file keeps PlanetPDF's name for the icon.
- ``--pro``: **Font Awesome Pro**, for anyone with a Pro license (Planet Networks' builds) into
  ``icons/fa/``, from your own Pro package (unzipped desktop download), by default Dropbox's
  Dev\\fontawesome. PlanetPDF uses an icon from ``icons/fa/`` when it's there, else the free one
  (``ui/fa.py``). Pro icons may not be shared as files: keep ``icons/fa/`` out of any public copy.

With neither flag, both sets are made from the default packages (a missing package is skipped).
The packages stay outside the project (shared with Eudora; found by scripts\\dropbox_dev.py).
Icons are drawn in a mid gray that reads on light and dark themes. Only names that appear in
the UI code are copied (any quoted string that is an icon name), so the bundle stays small.
Each SVG keeps Font Awesome's license comment, and each folder gets the package's LICENSE.txt.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dropbox_dev import dev_path  # noqa: E402

COLOR = "#6b6b6b"
ICONS = ROOT / "src" / "planetpdf" / "resources" / "icons"
UI = ROOT / "src" / "planetpdf" / "ui"

# Pro icons Font Awesome Free doesn't have -> the closest free one (2026-10-24, Free 6.7.2)
FREE_STAND_INS = {
    "arrow-down-left-and-arrow-up-right-to-center": "down-left-and-up-right-to-center",
    "book-open-cover": "book-open",
    "circle-small": "circle",
    "distribute-spacing-horizontal": "grip-lines-vertical",
    "distribute-spacing-vertical": "grip-lines",
    "file-binary": "list-ol",  # (Bates numbering)
    "file-certificate": "certificate",
    "file-check": "file-circle-check",
    "file-lock": "file-shield",
    "file-magnifying-glass": "magnifying-glass",
    "file-slash": "file-circle-xmark",
    "files": "copy",
    "folder-arrow-up": "folder-open",
    "input-text": "font",
    "list-tree": "sitemap",
    "magnifying-glass-arrows-rotate": "magnifying-glass",
    "message-lines": "comment-dots",
    "objects-align-bottom": "arrows-down-to-line",
    "objects-align-center-horizontal": "align-center",
    "objects-align-center-vertical": "grip-lines",
    "objects-align-left": "align-left",
    "objects-align-right": "align-right",
    "objects-align-top": "arrows-up-to-line",
    "rectangle-wide": "square",
    "scanner-image": "file-image",
    "text": "t",  # (Add Text: Pro's is a T too)
    "text-size": "text-height",
    "volume": "volume-high",
    "wave-pulse": "chart-line",
    "waveform-lines": "headphones",
}


def wanted_names() -> set[str]:
    names = set()
    for path in UI.rglob("*.py"):
        names.update(re.findall(r"[\"']([a-z0-9]+(?:-[a-z0-9]+)*)[\"']", path.read_text(encoding="utf-8")))
    return names


def _write(out: Path, icons: dict[str, Path], package: Path) -> None:
    # Replace files in place: removing the folder itself fails while Dropbox holds it open.
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.svg"):
        if old.stem not in icons:
            old.unlink(missing_ok=True)
    for name, source in sorted(icons.items()):
        svg = source.read_text(encoding="utf-8").replace("<svg ", f'<svg fill="{COLOR}" ', 1)
        (out / f"{name}.svg").write_text(svg, encoding="utf-8")
    license_file = package / "LICENSE.txt"
    if license_file.is_file():
        shutil.copyfile(license_file, out / "LICENSE.txt")


def pro(package: Path) -> None:
    source = package / "svgs" / "regular"
    if not source.is_dir():
        sys.exit(f"Font Awesome Pro 'regular' SVGs not found in {source}")
    icons = {name: source / f"{name}.svg" for name in wanted_names() if (source / f"{name}.svg").is_file()}
    _write(ICONS / "fa", icons, package)
    print(f"Pro: {len(icons)} icons copied to {(ICONS / 'fa').relative_to(ROOT)}")


def free(package: Path) -> None:
    styles = [package / "svgs" / "regular", package / "svgs" / "solid"]
    if not all(s.is_dir() for s in styles):
        sys.exit(f"Font Awesome Free 'regular' and 'solid' SVGs not found in {package / 'svgs'}")

    def find(name: str) -> Path | None:
        return next((s / f"{name}.svg" for s in styles if (s / f"{name}.svg").is_file()), None)

    icons, stand_ins = {}, 0
    for name in wanted_names() | set(FREE_STAND_INS):
        source = find(name)
        if source is None and name in FREE_STAND_INS:
            source = find(FREE_STAND_INS[name])
            if source is None:
                sys.exit(f"The stand-in for {name}, {FREE_STAND_INS[name]}, isn't in Font Awesome Free")
            stand_ins += 1
        if source is not None:
            icons[name] = source
    _write(ICONS / "free", icons, package)
    print(f"Free: {len(icons)} icons copied to {(ICONS / 'free').relative_to(ROOT)} ({stand_ins} stand-ins)")
    if (ICONS / "fa").is_dir():  # an icon only Pro has, with no stand-in, would be blank in free builds
        missing = sorted(p.stem for p in (ICONS / "fa").glob("*.svg") if p.stem not in icons)
        if missing:
            print(f"  Not in Free and no stand-in (fine if these aren't icons): {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--free", nargs="?", const="", metavar="PACKAGE", help="Font Awesome Free into icons/free/")
    parser.add_argument("--pro", nargs="?", const="", metavar="PACKAGE", help="your Font Awesome Pro into icons/fa/")
    args = parser.parse_args()
    both = args.free is None and args.pro is None
    if args.free is not None or both:
        package = Path(args.free) if args.free else dev_path("fontawesome-free")
        if args.free is not None or package.is_dir():
            free(package)
    if args.pro is not None or both:
        package = Path(args.pro) if args.pro else dev_path("fontawesome")
        if args.pro is not None or package.is_dir():
            pro(package)


if __name__ == "__main__":
    main()
