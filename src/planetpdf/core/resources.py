"""Bundled resources (fonts, app icon). Located next to the package, or in the PyInstaller bundle."""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent.parent  # .../planetpdf


def resources_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "planetpdf" / "resources"
        if candidate.is_dir():
            return candidate
    return _PACKAGE_DIR / "resources"


# Key -> (file, display name). All SIL Open Font License (license files alongside).
FONTS: dict[str, tuple[str, str]] = {
    "sans": ("NotoSans.ttf", "Noto Sans"),
    "serif": ("NotoSerif.ttf", "Noto Serif"),
    "script": ("DancingScript.ttf", "Dancing Script"),
    "script_formal": ("GreatVibes-Regular.ttf", "Great Vibes"),
    "handwriting": ("Caveat.ttf", "Caveat"),
    # Acrobat-style stamps (condensed bold italic, like Acrobat's Myriad)
    "stamp": ("BarlowCondensed-BoldItalic.ttf", "Barlow Condensed Bold Italic"),
    "stamp_sub": ("BarlowCondensed-SemiBoldItalic.ttf", "Barlow Condensed SemiBold Italic"),
}
SIGNATURE_FONTS = ["script", "script_formal", "handwriting"]
STAMP_FONTS = ["sans", "serif"]


def font_path(key: str) -> Path:
    try:
        name = FONTS[key][0]
    except KeyError:
        raise KeyError(f"Unknown font {key!r}") from None
    return resources_dir() / "fonts" / name


def font_label(key: str) -> str:
    return FONTS[key][1]


def app_icon_path() -> Path:
    """The Planet Networks logo (multi-size .ico: 16-256 px)."""
    return resources_dir() / "icons" / "planetpdf.ico"
