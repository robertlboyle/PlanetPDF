"""Register the bundled fonts with Qt (for previews and the signature typing field)."""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase

from planetpdf.core.resources import FONTS, font_path

_families: dict[str, str] = {}


def register_bundled_fonts() -> dict[str, str]:
    """Font key -> Qt family name. Safe to call repeatedly."""
    if not _families:
        for key in FONTS:
            font_id = QFontDatabase.addApplicationFont(str(font_path(key)))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            _families[key] = families[0] if families else FONTS[key][1]
    return dict(_families)


def family(key: str) -> str:
    return register_bundled_fonts().get(key, "")
