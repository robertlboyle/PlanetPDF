"""Draw every icon PlanetPDF bundles on one sheet: Pro beside Free, stand-ins marked.

Usage:  .venv\\Scripts\\python.exe scripts\\icon_sheet.py [OUTPUT.png|OUTPUT.pdf ...]
        (default: docs\\icons-pro-vs-free.png and .pdf; the PDF keeps the icons sharp at any zoom)

Re-run it after ``scripts\\update_icons.py`` changes the icons. Without the Pro icons
(``resources/icons/fa/``: a copy built without a Pro license) only the free column is drawn.
The sheet shows Pro artwork, so like ``icons/fa/`` it stays out of any public copy of the code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # (drawn in memory; nothing on screen)
if sys.platform == "win32":  # (offscreen Qt finds no fonts on Windows without this: blank labels)
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPageSize, QPainter, QPdfWriter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from update_icons import FREE_STAND_INS  # noqa: E402

ICONS = ROOT / "src" / "planetpdf" / "resources" / "icons"
OUT = ROOT / "docs" / "icons-pro-vs-free.png"
COLUMNS, CELL_W, CELL_H, ICON = 4, 330, 44, 28
STAND_IN, INK = "#c05600", "#222222"


def _draw(painter: QPainter, svg: Path, x: float, y: float) -> None:
    renderer = QSvgRenderer(QByteArray(svg.read_bytes()))
    box = renderer.viewBoxF()
    scale = ICON / max(box.width(), box.height(), 1)
    w, h = box.width() * scale, box.height() * scale
    renderer.render(painter, QRectF(x + (ICON - w) / 2, y + (ICON - h) / 2, w, h))


def _paint(painter: QPainter, names: list[str], pro: bool) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    title_font, label_font = QFont("Segoe UI"), QFont("Segoe UI")
    title_font.setPixelSize(15)  # (sizes in drawing units, so the PNG and the PDF match)
    title_font.setBold(True)
    label_font.setPixelSize(12)
    painter.setFont(title_font)
    title = "Pro (left) and Free (right)" if pro else "Font Awesome Free"
    painter.drawText(10, 28, f"PlanetPDF icons: {title}. Orange name = a free stand-in. {len(names)} icons.")
    painter.setFont(label_font)
    for i, name in enumerate(names):
        x, y = 10 + (i % COLUMNS) * CELL_W, 50 + (i // COLUMNS) * CELL_H
        if pro:
            _draw(painter, ICONS / "fa" / f"{name}.svg", x, y)
        _draw(painter, ICONS / "free" / f"{name}.svg", x + (38 if pro else 0), y)
        text_x = x + (80 if pro else 42)
        stand_in = FREE_STAND_INS.get(name)
        painter.setPen(QColor(STAND_IN if stand_in else INK))
        painter.drawText(QRectF(text_x, y, CELL_W - (text_x - x) - 5, ICON), Qt.AlignmentFlag.AlignVCenter,
                         name + (f"  → {stand_in}" if stand_in else ""))


def _write(out: Path, names: list[str], pro: bool) -> None:
    """A PNG, or a PDF (vector icons and text, one page the size of the sheet) for ``.pdf``."""
    width, height = COLUMNS * CELL_W + 20, (len(names) + COLUMNS - 1) // COLUMNS * CELL_H + 60
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".pdf":
        writer = QPdfWriter(str(out))
        writer.setResolution(72)  # (one unit = one point: the same layout as the PNG's pixels)
        writer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Unit.Point, "Icons"))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0))
        writer.setTitle("PlanetPDF icons: Font Awesome Pro and Free")
        painter = QPainter(writer)
        _paint(painter, names, pro)
        painter.end()
    else:
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        _paint(painter, names, pro)
        painter.end()
        if not image.save(str(out)):
            sys.exit(f"Couldn't write {out}")
    print(f"{len(names)} icons drawn to {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


def main() -> None:
    outs = [Path(a) for a in sys.argv[1:]] or [OUT, OUT.with_suffix(".pdf")]
    free = {p.stem for p in (ICONS / "free").glob("*.svg")}
    pro = {p.stem for p in (ICONS / "fa").glob("*.svg")}
    names = sorted(pro & free) if pro else sorted(free)
    _app = QGuiApplication.instance() or QGuiApplication([])
    for out in outs:
        _write(out, names, bool(pro))


if __name__ == "__main__":
    main()
