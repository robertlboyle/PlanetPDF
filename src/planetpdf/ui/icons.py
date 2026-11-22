"""The application icon (Planet Networks logo)."""

from __future__ import annotations

from PySide6.QtGui import QIcon

from planetpdf.core.resources import app_icon_path


def app_icon() -> QIcon:
    return QIcon(str(app_icon_path()))
