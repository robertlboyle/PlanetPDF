"""Acrobat's Stamp tool (user screenshots 2026-10-01): the Stamp ▾ menu with pictures,
favorites, Show Stamp Names, Paste Clipboard Image as Stamp Tool, Custom Stamps ▸ and
the Stamps Palette.

Stamp ▾ (as in Acrobat): the stamp used last (click to place it again) · your
favorites · Dynamic ▸ / Sign Here ▸ / Standard Business ▸ / Custom ▸ (pictures) ·
Add Current Stamp to Favorites / Remove Current Stamp from Favorites · Show Stamp
Names · Paste Clipboard Image as Stamp Tool · Custom Stamps ▸ · Stamps Palette.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
)

from planetpdf.core.document import PdfError
from planetpdf.core.markup import Kind
from planetpdf.core.stamps import CATEGORIES, RENAMED, StampSpec, render_image_seal
from planetpdf.ui.markup_toolbar import SignatureMenuAction
from planetpdf.ui.viewer.qimage import pdf_preview

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

MENU_PICTURE = (170, 46)
PALETTE_PICTURE = QSize(150, 56)
FAVORITES = "Favorites"
CUSTOM = "Custom"


class StampUi:
    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self._pictures: dict[tuple, object] = {}
        self.palette: StampsPalette | None = None
        self.act_show_names = QAction("Show Stamp Names", window, checkable=True)
        self.act_show_names.setChecked(window.settings.show_stamp_names)
        self.act_show_names.toggled.connect(self._show_names)
        self.act_paste = QAction("Paste Clipboard Image as Stamp Tool", window)
        self.act_paste.setToolTip("Place the picture on the clipboard as a stamp")
        self.act_paste.triggered.connect(self.paste_clipboard_stamp)
        self.act_palette = QAction("Stamps Palette", window)
        self.act_palette.setToolTip("A window with every stamp: click one, then click on the page")
        self.act_palette.triggered.connect(self.show_palette)
        self.act_create_text = QAction("Create Text Stamp…", window)
        self.act_create_text.triggered.connect(self.create_text_stamp)
        self.act_create_image = QAction("Create from Image…", window)
        self.act_create_image.triggered.connect(self.create_image_stamp)
        self.act_manage = QAction("Manage…", window)
        self.act_manage.triggered.connect(window.manage_stamps)
        self.custom_menu = QMenu("Custom Stamps", window)
        self.custom_menu.addActions([self.act_create_text, self.act_create_image, self.act_manage])
        window._doc_actions.append(self.act_paste)

    # --- pictures ---------------------------------------------------------------------------

    def _resolve(self) -> dict:
        s = self.w.settings
        return {"user": s.user_name, "company": s.company_name, "now": datetime.now(), "date_format": s.date_format,
                "time_format": s.time_format}

    def picture(self, spec: StampSpec, width: int, height: int):
        """The stamp as it would be placed now (cached; dynamic ones change by the minute)."""
        r = self._resolve()
        key = (spec, width, height, r["user"], r["company"], r["now"].strftime("%Y%m%d%H%M"), r["date_format"], r["time_format"])
        if key not in self._pictures:
            try:
                self._pictures[key] = pdf_preview(self.w.stamp_library.render(spec, **r), width, height)
            except (PdfError, OSError):
                self._pictures[key] = None
        return self._pictures[key]

    def stamps_in(self, category: str) -> list[StampSpec]:
        library = self.w.stamp_library
        if category == FAVORITES:
            ids = self.w.settings.stamp_favorites
            by_id = {s.stamp_id: s for s in library.all}
            ids = [RENAMED.get(i, i) for i in ids]
            return [by_id[i] for i in ids if i in by_id]
        if category == CUSTOM:
            return [s for s in library.all if not s.builtin]
        return [s for s in library.all if s.builtin and s.category == category]

    def current(self) -> StampSpec | None:
        try:
            return self.w.stamp_library.get(self.w.settings.last_stamp)
        except PdfError:
            return None

    # --- the Stamp ▾ menu ---------------------------------------------------------------------

    def _row(self, menu: QMenu, spec: StampSpec) -> QAction:
        names = self.w.settings.show_stamp_names
        action = SignatureMenuAction(self.picture(spec, *MENU_PICTURE), spec.name if names else "", menu, MENU_PICTURE)
        action.setToolTip(spec.name)
        action.setData(spec.stamp_id)
        action.setEnabled(self.w.session is not None)
        action.triggered.connect(lambda _c=False, sid=spec.stamp_id: self.w.markup.place_stamp(sid))
        menu.addAction(action)
        return action

    def fill_menu(self, menu: QMenu) -> None:
        menu.clear()
        menu.setToolTipsVisible(True)
        current = self.current()
        if current is not None:
            self._row(menu, current)
            menu.addSeparator()
        favorites = self.stamps_in(FAVORITES)
        for spec in favorites:
            self._row(menu, spec)
        if favorites:
            menu.addSeparator()
        for category in (*CATEGORIES, CUSTOM):
            stamps = self.stamps_in(category)
            if not stamps:
                continue
            sub = menu.addMenu(category)
            sub.setToolTipsVisible(True)
            for spec in stamps:
                self._row(sub, spec)
        menu.addSeparator()
        is_favorite = current is not None and current.stamp_id in self.w.settings.stamp_favorites
        add = menu.addAction("Add Current Stamp to Favorites", self.add_favorite)
        add.setEnabled(current is not None and not is_favorite)
        remove = menu.addAction("Remove Current Stamp from Favorites", self.remove_favorite)
        remove.setEnabled(is_favorite)
        menu.addAction(self.act_show_names)
        menu.addSeparator()
        menu.addAction(self.act_paste)
        menu.addMenu(self.custom_menu)
        menu.addAction(self.act_palette)

    def add_favorite(self) -> None:
        current = self.current()
        favorites = self.w.settings.stamp_favorites
        if current is not None and current.stamp_id not in favorites:
            favorites.append(current.stamp_id)
            self._changed()

    def remove_favorite(self) -> None:
        current = self.current()
        favorites = self.w.settings.stamp_favorites
        if current is not None and current.stamp_id in favorites:
            favorites.remove(current.stamp_id)
            self._changed()

    def _show_names(self, on: bool) -> None:
        self.w.settings.show_stamp_names = on
        self._changed()

    def _changed(self) -> None:
        self.w.save_settings_now()
        if self.palette is not None:
            self.palette.populate()

    # --- commands -----------------------------------------------------------------------------

    def paste_clipboard_stamp(self) -> bool:
        """Acrobat's Paste Clipboard Image as Stamp Tool: the copied picture follows the cursor."""
        if self.w.session is None:
            return False
        image = QApplication.clipboard().image()
        if image.isNull():
            self.w.show_message("Paste Clipboard Image as Stamp Tool", "There's no picture on the clipboard. Copy an image first.")
            return False
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        try:
            source = render_image_seal(bytes(data), max_side=min(216.0, max(image.width(), image.height()) * 0.75))
        except PdfError as exc:
            self.w.show_message("Paste Clipboard Image as Stamp Tool", str(exc))
            return False
        self.w.markup.place_source(Kind.SEAL, source, "Clipboard image")
        return True

    def _manager(self):
        from planetpdf.ui.dialogs.stamps import StampManagerDialog

        return StampManagerDialog(self.w)

    def create_text_stamp(self) -> None:
        if self._manager().new_text_stamp() is not None:
            self._changed()

    def create_image_stamp(self) -> None:
        if self._manager().import_seal() is not None:
            self._changed()

    def show_palette(self) -> None:
        if self.palette is None:
            self.palette = StampsPalette(self)
        self.palette.populate()
        self.palette.show()
        self.palette.raise_()


class StampsPalette(QDialog):
    """Acrobat's Stamps Palette: pick a category, click a stamp, click on the page."""

    def __init__(self, ui: StampUi) -> None:
        super().__init__(ui.w, Qt.WindowType.Tool)
        self.ui = ui
        self.setWindowTitle("Stamps")
        self.resize(380, 460)
        self.category = QComboBox(self)
        self.category.setAccessibleName("Category")
        for name in (FAVORITES, *CATEGORIES, CUSTOM):
            self.category.addItem(name)
        self.category.setCurrentText(CATEGORIES[-1])
        self.category.currentIndexChanged.connect(self.populate)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Stamps")
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(PALETTE_PICTURE)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setSpacing(6)
        self.list.itemClicked.connect(self._place)
        layout = QVBoxLayout(self)
        layout.addWidget(self.category)
        layout.addWidget(self.list, 1)

    def populate(self) -> None:
        self.list.clear()
        names = self.ui.w.settings.show_stamp_names
        for spec in self.ui.stamps_in(self.category.currentText()):
            picture = self.ui.picture(spec, PALETTE_PICTURE.width(), PALETTE_PICTURE.height())
            row = QListWidgetItem(QIcon(picture) if picture is not None else QIcon(), spec.name if names else "")
            row.setToolTip(spec.name)
            row.setData(Qt.ItemDataRole.UserRole, spec.stamp_id)
            self.list.addItem(row)

    def _place(self, row: QListWidgetItem) -> None:
        if self.ui.w.session is not None:
            self.ui.w.markup.place_stamp(row.data(Qt.ItemDataRole.UserRole))
