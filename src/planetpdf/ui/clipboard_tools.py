"""Edit menu: Cut, Copy, Paste, Delete, Select All, Deselect All, Copy File to Clipboard,
Take a Snapshot (as in Acrobat).

Copy/Cut work on the selected item (comment, placed text, signature, stamp, picture) or
on selected page text. Paste puts back a copied item (slightly offset), or turns a
picture or text copied in another program into a movable item on the current page.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QObject, QPointF, QUrl
from PySide6.QtGui import QGuiApplication, QImage

from planetpdf.core import markup as K
from planetpdf.core.appearance import source_size
from planetpdf.core.create import pdf_from_image
from planetpdf.core.document import PdfError
from planetpdf.core.markup import Kind
from planetpdf.core.redaction.marks import from_display, to_display
from planetpdf.core.text import page_words

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

ITEM_MIME = "application/x-planetpdf-items"
PASTE_OFFSET = 12.0  # points: a pasted copy lands a little below/right of the original
SNAPSHOT_DPI = 150


def image_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class ClipboardTools(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._copied: list[K.MarkupSpec] = []

    @property
    def clipboard(self):
        return QGuiApplication.clipboard()

    def _item(self) -> K.MarkupItem | None:
        item_id = self._window._selected_markup_id()
        return self._window.markup._item(item_id) if item_id else None

    def _status(self, text: str) -> None:
        self._window.statusBar().showMessage(text, 4000)

    # --- copy / cut / paste ------------------------------------------------------------------

    def copy(self) -> bool:
        item = self._item()
        if item is not None and item.spec is not None:
            self._copied = [item.spec]
            mime = QMimeData()
            mime.setData(ITEM_MIME, QByteArray(item.spec.mark_id.encode()))
            if item.spec.text:
                mime.setText(item.spec.text)
            self.clipboard.setMimeData(mime)
            self._status(f"Copied the {item.type_name.lower()}.")
            return True
        return self._window.text_tools.copy()

    def cut(self) -> bool:
        item = self._item()
        if item is not None and item.spec is not None and self.copy():
            return self._window.markup.delete(item.item_id)
        if self._window.text_tools.selected_text():
            self._window.text_tools.copy()
            self._status("Page text can't be cut; it was copied. (Use Edit PDF to change it.)")
            return False
        return False

    def _ours_on_clipboard(self) -> bool:
        mime = self.clipboard.mimeData()
        return bool(self._copied) and mime is not None and mime.hasFormat(ITEM_MIME)

    def visible_point(self, page_index: int) -> QPointF:
        """Middle of what's visible of the page (displayed coordinates)."""
        view = self._window.view
        item = view._items[page_index]
        center = item.mapFromScene(view.mapToScene(view.viewport().rect().center()))
        bounds = item.boundingRect()
        return QPointF(min(max(center.x(), bounds.left() + 20), bounds.right() - 20),
                       min(max(center.y(), bounds.top() + 20), bounds.bottom() - 20))

    def paste(self) -> bool:
        window = self._window
        session = window.session
        if session is None:
            return False
        page_index = max(0, window.view.current_page)
        if self._ours_on_clipboard():
            for spec in self._copied:
                target = spec.page if spec.page == page_index else page_index
                rect = pymupdf.Rect(spec.rect)
                if spec.page == page_index:
                    rect += (PASTE_OFFSET, PASTE_OFFSET, PASTE_OFFSET, PASTE_OFFSET)
                page_rect = session.doc[target].cropbox
                if not page_rect.contains(rect):  # keep it on the page
                    rect = pymupdf.Rect(page_rect.x0 + 36, page_rect.y0 + 36,
                                        page_rect.x0 + 36 + rect.width, page_rect.y0 + 36 + rect.height)
                copy = K.transformed(replace(spec, mark_id=K.new_id(), page=target), tuple(rect))
                window.markup.add(copy)
            return True
        mime = self.clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = self.clipboard.image()
            if not image.isNull():
                return self.paste_picture(page_index, image_png(image))
        if mime is not None and mime.hasText() and mime.text().strip():
            point = self.visible_point(page_index)
            style = window.markup.style_for(Kind.TEXT)
            window.markup.add(K.placed_text(session, page_index, (point.x(), point.y()), mime.text().strip("\n"), style,
                                            author=window.settings.author()))
            return True
        self._status("Nothing on the clipboard that can be pasted here.")
        return False

    def paste_picture(self, page_index: int, png: bytes) -> bool:
        """A picture becomes a movable, resizable item (flatten it to make it page content)."""
        session = self._window.session
        try:
            source = pdf_from_image(png)
        except PdfError as exc:
            self._status(str(exc))
            return False
        width, height = source_size(source)
        page = session.doc[page_index]
        shown = page.rect
        scale = min(1.0, shown.width * 0.8 / width, shown.height * 0.8 / height)
        width, height = width * scale, height * scale
        center = self.visible_point(page_index)
        x0 = min(max(center.x() - width / 2, 0), shown.width - width)
        y0 = min(max(center.y() - height / 2, 0), shown.height - height)
        rect = from_display(page, (x0, y0, x0 + width, y0 + height))
        spec = K.MarkupSpec(mark_id=K.new_id(), page=page_index, kind=Kind.IMAGE, rect=rect, source=source,
                            text="Image", author=self._window.settings.author())
        self._window.markup.add(spec)
        return True

    # --- selection ----------------------------------------------------------------------------

    def delete(self) -> None:
        self._window._delete_selected_marks()

    def select_all(self) -> bool:
        """All text on the current page (like Acrobat's Select All with the Select tool)."""
        window = self._window
        if window.session is None:
            return False
        page_index = max(0, window.view.current_page)
        words = page_words(window.session, page_index)
        if not words:
            self._status("There's no text on this page (a scan? Use Scan & OCR first).")
            return False
        window.text_tools.set_selection(page_index, words)
        self._status(f"Selected all text on page {page_index + 1}. Ctrl+C copies it.")
        return True

    def deselect_all(self) -> None:
        window = self._window
        window.text_tools.clear_selection()
        window.markup.select("")
        window.view.set_selected_marks(set())

    # --- whole file / snapshot ------------------------------------------------------------------

    def copy_file(self) -> bool:
        session = self._window.session
        if session is None:
            return False
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(session.path))])
        self.clipboard.setMimeData(mime)
        note = " (the saved version; save first to include your changes)" if self._window.is_dirty() else ""
        self._status(f"Copied {session.path.name} to the clipboard{note}.")
        return True

    def take_snapshot(self) -> None:
        self._window.markup.pick_box(self.snapshot_area, "Drag a box around the area to copy as a picture. "
                                     "Esc cancels.", name="snapshot")  # (one and done)

    def snapshot_area(self, page_index: int, rect) -> bytes:
        """Copy a picture of ``rect`` (unrotated) as the page shows it."""
        page = self._window.session.doc[page_index]
        clip = pymupdf.Rect(rect) & page.cropbox
        pix = page.get_pixmap(dpi=SNAPSHOT_DPI, clip=clip, alpha=False, annots=True)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.clipboard.setImage(image)
        shown = to_display(page, tuple(clip))
        self._status(f"Copied a picture of the area ({shown.width / 72:.1f} × {shown.height / 72:.1f} in).")
        return image_png(image)
