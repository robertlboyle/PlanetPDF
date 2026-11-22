"""View ▸ Display Theme (user decision 2026-10-01): Light, Dark, or follow the system.

Light and Dark use Qt's Fusion style with an explicit palette, so they look the same on
every machine (the classic Windows style ignores dark mode on Windows 10). Following the
system puts back the style and palette the app started with.

**A contrast theme wins** (Windows' Contrast themes, macOS's Increase contrast; user
2026-10-25: low-vision users): while one is on, PlanetPDF uses the system's own colors
whatever theme is chosen, draws its icons in the theme's text color and marks switched-on
buttons with the system highlight. Preferences ▸ Appearance ▸ Text size makes all text larger.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

_original: dict = {}
TEXT_SIZES = (100, 125, 150, 175, 200)  # percent (Preferences ▸ Appearance ▸ Text size)


def _accessibility_hints():
    """Qt's accessibility hints, held for good: PySide deletes them when the Python wrapper
    of the style hints they came from goes away, and every later call fails."""
    app = QApplication.instance()
    if app is None:
        return None
    if _original.get("hints_app") is not app:
        style_hints = app.styleHints()  # (its wrapper too: the hints go with it)
        _original["hints_app"], _original["style_hints"] = app, style_hints
        _original["hints"] = style_hints.accessibility()
    return _original["hints"]


def high_contrast() -> bool:
    """A system contrast theme is on."""
    hints = _accessibility_hints()
    return hints is not None and hints.contrastPreference() == Qt.ContrastPreference.HighContrast


def apply_text_size(percent: int) -> None:
    """All of PlanetPDF's text at ``percent`` of the system's size."""
    app = QApplication.instance()
    if app is None:
        return
    base = _original.setdefault("font", QFont(app.font()))
    font = QFont(base)
    font.setPointSizeF(base.pointSizeF() * max(50, min(300, percent)) / 100)
    if app.font() != font:
        app.setFont(font)


def _palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        colors = {
            QPalette.ColorRole.Window: "#2b2b2b", QPalette.ColorRole.WindowText: "#e8e8e8",
            QPalette.ColorRole.Base: "#1f1f1f", QPalette.ColorRole.AlternateBase: "#2a2a2a",
            QPalette.ColorRole.Text: "#e8e8e8", QPalette.ColorRole.Button: "#353535",
            QPalette.ColorRole.ButtonText: "#e8e8e8", QPalette.ColorRole.ToolTipBase: "#3a3a3a",
            QPalette.ColorRole.ToolTipText: "#f0f0f0", QPalette.ColorRole.Highlight: "#2f7fd1",
            QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.PlaceholderText: "#9a9a9a",
            QPalette.ColorRole.Link: "#6fb3ff", QPalette.ColorRole.Mid: "#4a4a4a", QPalette.ColorRole.Dark: "#1a1a1a",
        }
        disabled = "#7a7a7a"
    else:
        colors = {
            QPalette.ColorRole.Window: "#f3f3f3", QPalette.ColorRole.WindowText: "#1a1a1a",
            QPalette.ColorRole.Base: "#ffffff", QPalette.ColorRole.AlternateBase: "#f7f7f7",
            QPalette.ColorRole.Text: "#1a1a1a", QPalette.ColorRole.Button: "#f0f0f0",
            QPalette.ColorRole.ButtonText: "#1a1a1a", QPalette.ColorRole.ToolTipBase: "#ffffe1",
            QPalette.ColorRole.ToolTipText: "#1a1a1a", QPalette.ColorRole.Highlight: "#1a73e8",
            QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.PlaceholderText: "#8a8a8a",
            QPalette.ColorRole.Link: "#1a5fd1", QPalette.ColorRole.Mid: "#c8c8c8", QPalette.ColorRole.Dark: "#a0a0a0",
        }
        disabled = "#a0a0a0"
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled))
    return p


def apply_theme(theme: str) -> None:
    """Applies the theme. Changing the style re-polishes every widget there is (1.2 s with
    the main window built, user 2026-10-07: "make it open faster"), so the main window
    applies it before building anything, and a theme that's already in place costs nothing."""
    app = QApplication.instance()
    if app is None:
        return
    if "style" not in _original:  # what the app started with, for "follow the system"
        _original["style"] = app.style().name()
        _original["palette"] = QPalette(app.palette())
        # a contrast theme switched on or off while PlanetPDF runs: follow it at once
        _accessibility_hints().contrastPreferenceChanged.connect(
            lambda _p: apply_theme(_original.get("chosen", "system")))
    _original["chosen"] = theme
    contrast = high_contrast()
    if contrast:
        theme = "system"  # (the system's own colors)
    if _original.get("applied") == (theme, contrast):
        return
    _original["applied"] = (theme, contrast)
    hints = app.styleHints()
    if theme in ("light", "dark"):
        style, palette = "Fusion", _palette(theme == "dark")
        hints.setColorScheme(Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)
    else:  # (a palette with nothing set: the system's, also when its colors change)
        style, palette = _original["style"], QPalette()
        hints.setColorScheme(Qt.ColorScheme.Unknown)
    if app.style().name().lower() != style.lower():
        app.setStyle(QStyleFactory.create(style))
    if app.palette() != palette or theme == "system":
        app.setPalette(palette)
    from planetpdf.ui import fa

    # the icons in a contrast theme's text color (those made from now on: the rest at the next start)
    fa.set_default_color(app.palette().color(QPalette.ColorRole.WindowText).name() if contrast else None)
    # Widgets with their own style sheet keep the old colors until they're re-polished;
    # toolbars' "switched on" tiles change with the theme.
    on_style = fa.checked_style(is_dark(), contrast=contrast)
    for widget in app.allWidgets():
        sheet = widget.styleSheet()
        if sheet.startswith("QToolButton:checked"):
            widget.setStyleSheet(on_style)
        elif sheet:
            widget.setStyleSheet(sheet)


def is_dark() -> bool:
    app = QApplication.instance()
    return app is not None and app.palette().color(QPalette.ColorRole.Window).lightness() < 128
