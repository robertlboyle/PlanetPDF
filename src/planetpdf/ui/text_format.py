"""The text format bar (user 2026-10-03: "When adding or editing text, it should show me
the font, weight, italics, underline, bold, etc. and I should be able to alter these
settings"; "the toolbar at the top should also change based on the tool I have selected").

``TextFormatBar``: font list (every `core.fonts` family, each shown in its own font),
size, Bold, Italic, Underline, Strike-out and color. One sits on its own row under the
open tool's bar, shown only while there is text to format; the Edit PDF panel's FORMAT
section is another (``TextFormatBar(rows=2)``).

``TextFormatter`` decides what a change applies to, in this order:

1. placed text being typed (Add Text, or retyping one): the editor shows it at once and
   the text is placed with it;
2. selected placed text (Fill & Sign / Edit PDF): restyled, one undo step;
3. selected lines of page text (Edit PDF): ``EditController.restyle_selected`` (no
   strike-out there);
4. the Add Text tool on, nothing selected: the format of the next text.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QFontDatabase
from PySide6.QtWidgets import QComboBox, QCompleter, QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from planetpdf.core import fonts
from planetpdf.ui import fa
from planetpdf.ui.inline_editor import KEEPS_EDITOR
from planetpdf.ui.item_bar import color_swatch

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

SIZES = [6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72, 96, 144]
STYLE_KEYS = ("bold", "italic", "underline", "strikeout")


# --- fonts for the screen ------------------------------------------------------------------------------


@lru_cache(maxsize=256)
def _app_font_families(path: str) -> tuple[str, ...]:
    """Qt's family names for a font file (loaded once, for this program only), so the
    editor shows exactly the file the PDF gets."""
    font_id = QFontDatabase.addApplicationFont(path)
    return tuple(QFontDatabase.applicationFontFamilies(font_id)) if font_id >= 0 else ()


_DATA_FONTS: dict[int, tuple[str, ...]] = {}


def _data_font_families(data: bytes) -> tuple[str, ...]:
    """The same for a font program that's only in a document (no file)."""
    import zlib

    from PySide6.QtCore import QByteArray

    key = zlib.crc32(data)
    if key not in _DATA_FONTS:
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        _DATA_FONTS[key] = tuple(QFontDatabase.applicationFontFamilies(font_id)) if font_id >= 0 else ()
    return _DATA_FONTS[key]


def screen_families(family: str, bold: bool = False, italic: bool = False, doc=None, text: str = "") -> list[str]:
    """The Qt font families that show ``family`` on screen (``doc``: its own fonts too).
    A document font's missing letters show in its closest font, as they'll be drawn."""
    try:
        face = fonts.face(family, bold, italic, doc=doc, text=text)
    except fonts.FontError:
        face = fonts.face(fonts.DEFAULT_FAMILY, bold, italic)
    if face.editor_families:
        return list(face.editor_families)
    if face.in_document:
        return [*_data_font_families(face.buffer), *fonts.face(face.fallback).editor_families]
    found = list(_app_font_families(str(face.path))) if face.path is not None else []
    return found + [family, "Arial"]


def editor_font(fmt: dict, doc=None, text: str = "") -> QFont:
    """The on-page editor's font for a text format (font, font_size, bold, italic,
    underline, strikeout): scene units are points, so its pixel size is the point size."""
    family = fmt.get("font") or fonts.DEFAULT_FAMILY
    bold, italic = bool(fmt.get("bold")), bool(fmt.get("italic"))
    font = QFont()
    font.setFamilies(screen_families(family, bold, italic, doc, text))
    font.setPixelSize(max(4, round(float(fmt.get("font_size", 12)))))
    font.setBold(bold)
    font.setItalic(italic)
    font.setUnderline(bool(fmt.get("underline")))
    font.setStrikeOut(bool(fmt.get("strikeout")))
    return font


# --- the bar ---------------------------------------------------------------------------------------------


class TextFormatBar(QWidget):
    """Font, size, B I U S and color. ``changed`` carries what was changed (font=…,
    font_size=…, bold=…, …, color=(r, g, b))."""

    changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None, *, rows: int = 1) -> None:
        super().__init__(parent)
        self.setProperty(KEEPS_EDITOR, True)  # (choosing a font doesn't end the typing)
        self.setStyleSheet(fa.CHECKED_STYLE)  # (Bold on = white on a dark tile, like the other bars)
        self._updating = False
        self._document: tuple[str, ...] = ()
        self._color = QColor("black")
        self.font_box = QComboBox(self)
        self.font_box.setObjectName("textFont")
        self.font_box.setEditable(True)
        self.font_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_box.setMinimumContentsLength(16)
        self.font_box.setMaxVisibleItems(20)
        self.font_box.setToolTip("Font")
        completer = QCompleter(self.font_box.model(), self.font_box)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.font_box.setCompleter(completer)
        # The list is filled the first time it's needed (_ensure_fonts): reading the fonts
        # for it took 0.15 s and 13 MB of every start (user 2026-10-07, "open faster, less RAM").
        self._filled = False
        self._pending_doc = None
        # Each font shown in its own typeface (Preferences, user 2026-10-07): off = just the
        # names, and no font file is read to draw the list.
        self.previews = True
        self.font_box.activated.connect(lambda i: self._font_chosen(self.font_box.itemText(i)))
        self.font_box.lineEdit().returnPressed.connect(lambda: self._font_chosen(self.font_box.currentText()))

        self.size_box = QComboBox(self)
        self.size_box.setObjectName("textSize")
        self.size_box.setEditable(True)
        self.size_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.size_box.addItems([str(s) for s in SIZES])
        self.size_box.setValidator(QDoubleValidator(4.0, 144.0, 1, self.size_box))
        self.size_box.setMinimumContentsLength(4)
        self.size_box.setToolTip("Font size (points)")
        self.size_box.activated.connect(lambda _i: self._size_chosen(self.size_box.currentText()))
        self.size_box.lineEdit().returnPressed.connect(lambda: self._size_chosen(self.size_box.currentText()))

        self.buttons: dict[str, QToolButton] = {}
        for key, icon, tip in (("bold", "bold", "Bold"), ("italic", "italic", "Italic"),
                               ("underline", "underline", "Underline"), ("strikeout", "strikethrough", "Strike-out")):
            button = QToolButton(self, checkable=True)
            button.setObjectName(f"text_{key}")
            button.setIcon(fa.icon(icon))
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda on, k=key: self._emit({k: on}))
            self.buttons[key] = button
        self.color_button = QToolButton(self)
        self.color_button.setObjectName("textColor")
        self.color_button.setToolTip("Text color")
        self.color_button.setAutoRaise(True)
        self.color_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.color_button.setIcon(color_swatch(self._color))
        self.color_button.clicked.connect(self._pick_color)
        self.pick_color = None  # (window.pick_color: set by the formatter)

        if rows == 1:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(4, 0, 4, 0)
            layout.setSpacing(4)
            for widget in (self.font_box, self.size_box, *self.buttons.values(), self.color_button):
                layout.addWidget(widget)
            layout.addStretch(1)
        else:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.font_box)
            row = QHBoxLayout()
            row.addWidget(self.size_box, 1)
            for widget in (*self.buttons.values(), self.color_button):
                row.addWidget(widget)
            layout.addLayout(row)

    DOCUMENT_SUFFIX = " (in this document)"

    def _fill_fonts(self, document: tuple[str, ...] = (), doc=None) -> None:
        """The font list: the open document's own fonts first ("BettoniVF (in this
        document)"), then the standard, PlanetPDF's and the installed fonts. Each item's
        data is its family name."""
        self._document = document
        self.font_box.clear()
        standard = set(fonts.STANDARD) | set(fonts.BUNDLED)

        def add(text: str, family: str, families) -> None:
            self.font_box.addItem(text, family)
            if not self.previews:
                return
            preview = QFont()
            preview.setFamilies(families())
            preview.setPointSize(10)
            self.font_box.setItemData(self.font_box.count() - 1, preview, Qt.ItemDataRole.FontRole)

        for family in document:
            add(family + self.DOCUMENT_SUFFIX, family, lambda f=family: screen_families(f, doc=doc))
        if document:
            self.font_box.insertSeparator(self.font_box.count())
        for n, family in enumerate(fonts.families()):
            add(family, family, lambda f=family: screen_families(f) if f in standard else [f])
            if n == len(fonts.STANDARD) - 1 or n == len(fonts.STANDARD) + len(fonts.BUNDLED) - 1:
                self.font_box.insertSeparator(self.font_box.count())

    def _ensure_fonts(self) -> None:
        if not self._filled:
            self._filled = True
            self._fill_fonts(self._document, self._pending_doc)
            self._pending_doc = None

    def showEvent(self, event) -> None:
        self._ensure_fonts()
        super().showEvent(event)

    def set_document_fonts(self, document: tuple[str, ...], doc=None) -> None:
        """The open document's own fonts (listed while it's the one being edited)."""
        if not self._filled:  # (remembered for when the list is filled)
            self._document, self._pending_doc = tuple(document), doc
            return
        if tuple(document) != self._document:
            self._refill(tuple(document), doc)

    def set_previews(self, on: bool, doc=None) -> None:
        """Fonts in their own typeface, or just their names (Preferences)."""
        if on != self.previews:
            self.previews = on
            if self._filled:
                self._refill(self._document, doc)

    def _refill(self, document: tuple[str, ...], doc) -> None:
        current = self.font_box.currentData()
        self._updating = True
        try:
            self._fill_fonts(document, doc)
            index = self.font_box.findData(current)
            if index >= 0:
                self.font_box.setCurrentIndex(index)
        finally:
            self._updating = False

    def show_format(self, fmt: dict | None, *, enabled: set[str] | None = None) -> None:
        """Show a format (None: nothing to format, all off). ``enabled``: the keys that
        can change (e.g. no strike-out for page text); default all."""
        self._updating = True
        try:
            keys = {"font", "font_size", "color", *STYLE_KEYS} if enabled is None else enabled
            self.font_box.setEnabled(fmt is not None and "font" in keys)
            self.size_box.setEnabled(fmt is not None and "font_size" in keys)
            self.color_button.setEnabled(fmt is not None and "color" in keys)
            for key, button in self.buttons.items():
                button.setEnabled(fmt is not None and key in keys)
                button.setChecked(bool(fmt and fmt.get(key)))
            if fmt is None:
                return
            self._ensure_fonts()
            family = fmt.get("font") or fonts.DEFAULT_FAMILY
            index = self.font_box.findData(family)
            if index < 0:  # (a font that can't be used here, e.g. a Type 3 font: shown by its name)
                self.font_box.setEditText(family)
            else:
                self.font_box.setCurrentIndex(index)
            size = float(fmt.get("font_size", 12))
            self.size_box.setCurrentText(f"{size:g}")
            self._color = QColor.fromRgbF(*fmt.get("color", (0, 0, 0)))
            self.color_button.setIcon(color_swatch(self._color))
        finally:
            self._updating = False

    def _emit(self, change: dict) -> None:
        if not self._updating:
            self.changed.emit(change)

    def _font_chosen(self, text: str) -> None:
        wanted = text.strip().casefold()
        for n in range(self.font_box.count()):
            family = self.font_box.itemData(n)
            if family and wanted in (self.font_box.itemText(n).casefold(), family.casefold()):
                self._emit({"font": family})
                return

    def _size_chosen(self, text: str) -> None:
        try:
            size = float(text.replace(",", "."))
        except ValueError:
            return
        if 4 <= size <= 144:
            self._emit({"font_size": size})

    def _pick_color(self) -> None:
        color = self.pick_color(self._color, "Text Color") if self.pick_color else None
        if color is not None:
            self._emit({"color": (color.redF(), color.greenF(), color.blueF())})


# --- what the bar changes ----------------------------------------------------------------------------------


class TextFormatter(QObject):
    """Keeps every text format bar in step with what's being typed or selected, and
    sends changes to it (see the module doc)."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        self.bars: list[TextFormatBar] = []
        self.toolbar = None  # (the row under the tool bar: shown only while it applies)
        self._pending = QTimer(self, singleShot=True, interval=0)
        self._pending.timeout.connect(self.update)
        markup = window.markup
        for signal in (window.view.selectionChanged, markup.toolChanged, markup.modeChanged, markup.editorChanged,
                       markup.textColorChanged, window.edit.toolChanged, window.edit.modeChanged,
                       window.edit.selectionChanged):
            signal.connect(lambda *_a: self._pending.start())

    def add_bar(self, bar: TextFormatBar) -> None:
        bar.previews = self.w.settings.font_previews
        bar.pick_color = lambda initial, title: self.w.pick_color(initial, title)  # (tests replace window.pick_color)
        bar.changed.connect(self.apply)
        self.bars.append(bar)
        self.update()

    def schedule(self) -> None:
        self._pending.start()

    def apply_font_previews(self) -> None:
        """The Preferences setting changed: every bar's list follows it."""
        for bar in self.bars:
            bar.set_previews(self.w.settings.font_previews, self._doc())

    # --- the target ---------------------------------------------------------------------------------

    def _placed_ids(self) -> list[str]:
        from planetpdf.core.markup import Kind

        ids = []
        for overlay in self.w.view.selected_group:
            item_id = overlay[3:] if overlay.startswith("mk:") else overlay
            item = self.w.markup._item(item_id) if self.w.session is not None else None
            if item is not None and item.spec is not None and item.spec.kind in (Kind.TEXT, Kind.TEXTBOX, Kind.CALLOUT):
                ids.append(item_id)
        return ids

    def _page_lines(self) -> list:
        edit = self.w.edit
        if edit.mode != "edit" or self.w.session is None:
            return []
        return edit.selected_objects()[0]

    def target(self) -> tuple[str, object]:
        """("typing" | "placed" | "lines" | "next" | "none", what)."""
        import shiboken6

        markup = self.w.markup
        if markup.typing_format is not None and markup.editor is not None and shiboken6.isValid(markup.editor):
            return "typing", markup.typing_format
        if self.w.session is not None:
            placed = self._placed_ids()
            if placed:
                return "placed", placed
            lines = self._page_lines()
            if lines:
                return "lines", lines
        from planetpdf.core.markup import Kind

        if markup.tool in (Kind.TEXT, Kind.TEXTBOX, Kind.CALLOUT):
            return "next", markup.tool
        if self.w.edit.tool == "text":
            return "next", Kind.TEXT
        return "none", None

    def current_format(self) -> tuple[dict | None, set[str] | None]:
        from planetpdf.core import content_edit as E
        from planetpdf.core.markup import Kind

        kind, what = self.target()
        if kind == "typing":
            fmt = dict(what)
            fmt.update(self.w.markup.selected_words_format() or {})  # (words selected: theirs)
            return fmt, None
        if kind == "placed":
            spec = self.w.markup._item(what[0]).spec
            fmt = {**spec.style.text_format, "font_size": spec.style.font_size, "color": spec.style.color}
            for key in STYLE_KEYS:  # (several: on only when all are)
                fmt[key] = all(getattr(self.w.markup._item(i).spec.style, key) for i in what)
            return fmt, None
        if kind == "lines":
            line = what[0]
            edit = self.w.edit
            fmt = {"font": E.line_font(line), "font_size": round(line.size, 1), "color": line.color,
                   "bold": all(l.bold for l in what), "italic": all(l.italic for l in what),
                   "underline": all(edit.underlined(l) for l in what), "strikeout": False}
            if edit.typing_line():  # (what was picked while typing, not yet written)
                names = {"size": "font_size", "color": "color", "font": "font", "bold": "bold", "italic": "italic"}
                fmt.update({names[k]: v for k, v in edit.pending_format.items() if k in names})
            return fmt, {"font", "font_size", "color", "bold", "italic", "underline"}
        if kind == "next":
            style = self.w.markup.style_for(what)
            return {**style.text_format, "font_size": style.font_size, "color": style.color}, None
        return None, None

    def _doc(self):
        return self.w.session.doc if self.w.session is not None else None

    def update(self) -> None:
        fmt, enabled = self.current_format()
        doc = self._doc()
        # the open document's own fonts at the top of the list (only looked at while the bar shows)
        own = tuple(fonts.document_fonts(doc)) if fmt is not None and doc is not None else None
        for bar in self.bars:
            if own is not None:
                bar.set_document_fonts(own, doc)
            bar.show_format(fmt, enabled=enabled)
        if self.toolbar is not None:
            self.toolbar.setVisible(fmt is not None)

    # --- changes ------------------------------------------------------------------------------------

    def _target_text(self, kind: str, what) -> str:
        if kind == "typing":
            editor = self.w.markup.editor
            return editor.toPlainText() if editor is not None else ""
        if kind == "placed":
            return "".join(self.w.markup._item(i).spec.text for i in what)
        if kind == "lines":
            return "".join(line.text for line in what)
        return ""

    def apply(self, change: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        kind, what = self.target()
        if "font" in change:
            fmt, _enabled = self.current_format()
            try:  # (a font whose license forbids putting it in a PDF: say so now)
                face = fonts.face(change["font"], bool(fmt and fmt.get("bold")), bool(fmt and fmt.get("italic")),
                                  doc=self._doc(), text=self._target_text(kind, what))
            except fonts.FontError as exc:
                QMessageBox.warning(self.w, "Font", str(exc))
                self.update()
                return
            change = self._check_letters(face, kind, what, change)
            if change is None:
                self.update()
                return
        markup = self.w.markup
        if kind == "lines" and self.w.edit.typing_line() and not {"underline", "strikeout"} & set(change):
            # a line being typed in: the edited words get it when the typing ends (user 2026-11-24:
            # restyling the line under the box lost the color: the typing then wrote the old one)
            self.w.edit.format_while_typing(change)
            self.update()
            return
        if kind == "typing":
            if not markup.set_typing_format(**change):  # (not just for the selected words)
                markup.remember_text_format(kind=markup.typing_kind, **change)
        elif kind == "placed":
            markup.restyle_texts(what, **change)
        elif kind == "lines":
            self._restyle_lines(change)
        elif kind == "next":
            markup.remember_text_format(kind=what, **change)
        self.update()

    def _check_letters(self, face, kind: str, what, change: dict) -> dict | None:
        """A font only in this document often lacks letters (its copy holds just the
        letters the document used): say which, and offer the closest font. Added text can
        keep it (the missing letters are drawn in the closest font); page text can't."""
        missing = face.missing(self._target_text(kind, what)) if face.in_document else []
        if not missing:
            return change
        answer = self.ask_missing_letters(face, missing, can_keep=kind != "lines")
        if answer == "closest":
            return {**change, "font": face.fallback}
        return change if answer == "keep" else None

    def ask_missing_letters(self, face, missing: list[str], *, can_keep: bool) -> str | None:
        """"keep", "closest" or None (canceled)."""
        from PySide6.QtWidgets import QMessageBox

        shown = ", ".join(c for c in missing[:20]) + ("…" if len(missing) > 20 else "")
        text = (f"“{face.family}” is only in this document, and only with the letters the document "
                f"uses. It doesn't have: {shown}\n\n")
        box = QMessageBox(QMessageBox.Icon.Question, "Font", "", parent=self.w)
        closest = box.addButton(f"Use {face.fallback}", QMessageBox.ButtonRole.AcceptRole)
        keep = None
        if not can_keep:
            box.setText(text + f"Use {face.fallback}, the closest font, instead?")
        else:
            box.setText(text + f"Those letters can be drawn in {face.fallback}, the closest font, or all the text "
                               f"can use {face.fallback}.")
            keep = box.addButton(f"Keep {face.family}", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep or closest)
        box.exec()
        if box.clickedButton() is closest:
            return "closest"
        if keep is not None and box.clickedButton() is keep:
            return "keep"
        return None

    def _restyle_lines(self, change: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        from planetpdf.core import content_edit as E

        edit = self.w.edit
        if "underline" in change:
            edit.toggle_underline()
        elif "color" in change:
            edit.recolor_selected(tuple(change["color"]))
        elif "font_size" in change:
            edit.restyle_selected(size=float(change["font_size"]))
        elif "font" in change:
            edit.restyle_selected(font=change["font"])
        else:
            styles = {k: v for k, v in change.items() if k in ("bold", "italic")}
            doc = self._doc()
            own = fonts.document_fonts(doc) if doc is not None else {}
            for line in self._page_lines():  # (a document font with no bold / italic weight for these letters)
                family = E.line_font(line)
                if family in own:
                    face = own[family].face(styles.get("bold", line.bold), styles.get("italic", line.italic), line.text)
                    if face.fake_bold or face.fake_italic:
                        which = "bold" if face.fake_bold else "italic"
                        QMessageBox.information(
                            self.w, "Font",
                            f"“{family}” is only in this document, and it has no {which} letters for "
                            f"“{line.text[:60]}”. Choose another font to make it {which}.")
                        return
            edit.restyle_selected(**styles)
