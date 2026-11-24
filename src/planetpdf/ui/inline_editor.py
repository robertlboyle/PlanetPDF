"""On-page text editing (WYSIWYG): a text box is typed right where it will appear, in
its real font, size and color; notes get a small note pad next to their icon.

Esc cancels, Ctrl+Enter (or clicking elsewhere) finishes. The box grows downwards as
you type, like the final text box.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextOption
from PySide6.QtWidgets import QFrame, QPlainTextEdit

# The PDF text box uses Helvetica. Metric-compatible stand-ins per platform (Arial on
# Windows, Liberation/Nimbus Sans on Linux); the bundled Noto Sans as a last resort.
TEXT_FONT_FAMILIES = ["Helvetica", "Arial", "Liberation Sans", "Nimbus Sans", "Noto Sans"]
BOX_PADDING = 2.0  # points, like MuPDF's free-text inset
KEEPS_EDITOR = "keepsTextEditor"  # widget property: focus going there doesn't finish the typing


class InlineTextEditor(QPlainTextEdit):
    def __init__(
        self,
        text: str,
        *,
        font_size: float,
        color: QColor,
        note: bool,
        on_commit: Callable[[str, float], None],
        on_cancel: Callable[[], None],
        on_resize: Callable[[float], None],
        families: list[str] | None = None,
        bold: bool = False,
        italic: bool = False,
        single_line: bool = False,
        opaque: bool = False,
        wrap: bool = True,
        enter_finishes: bool = False,
        suggest: Callable[[str], list[str]] | None = None,
        forget: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        # Remembered text (user 2026-11-16): the rest of the best match appears selected after
        # what's typed; typing on replaces it, Tab / Right / End keeps it, Up / Down shows the
        # others, Backspace or Esc drops it, Shift+Delete forgets it.
        self._suggest = suggest
        self._forget = forget
        self._choices: list[str] = []
        self._choice = 0
        self._typed = ""
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._on_resize = on_resize
        self._done = False
        self.note = note
        self.single_line = single_line  # editing a line of page text: Enter finishes
        # Add Text: Enter finishes, Shift+Enter starts a new line (user 2026-10-22)
        self.enter_finishes = enter_finishes
        self.wrap = wrap and not single_line  # False: lines are what you type; the box grows sideways
        font = QFont()
        font.setFamilies(families or TEXT_FONT_FAMILIES)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setPixelSize(max(4, round(font_size)))  # scene units are points
        font.setBold(bold)
        font.setItalic(italic)
        self.setFont(font)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(1, 1)  # else the page proxy can't make it as small as one line
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if self.wrap else QTextOption.WrapMode.NoWrap)
        self.document().setDocumentMargin(BOX_PADDING)
        if note:
            self.setStyleSheet(
                "QPlainTextEdit { background: #fff7c2; color: #1a1a1a; border: 1px solid #c9a800; }"
            )
            self.setPlaceholderText("Type your note…")
        elif opaque:
            # Covers the original text while it's being retyped.
            self.setStyleSheet(
                f"QPlainTextEdit {{ background: white; color: {color.name()}; border: 1px dashed #1e5adc; }}"
            )
        else:
            # Transparent: the text appears on the page exactly as it will be drawn.
            self.setStyleSheet(
                f"QPlainTextEdit {{ background: transparent; color: {color.name()}; border: 1px dashed #1e5adc; }}"
            )
        self.viewport().setAutoFillBackground(False)
        self.setPlainText(text)
        self.moveCursor(self.textCursor().MoveOperation.End)
        self.textChanged.connect(self._grow)
        QTimer.singleShot(0, self, self._grow)  # (bound to the editor: never runs once it's gone)

    def show_color(self, color: QColor) -> None:
        """Edit PDF: a color picked while a line is being typed in shows in the box at once."""
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        sheet = self.styleSheet()
        self.setStyleSheet(re.sub(r"color: #[0-9a-fA-F]{6}", f"color: {color.name()}", sheet, count=1))
        cursor = QTextCursor(self.document())  # (a line in several fonts colors each character)
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.mergeCharFormat(fmt)

    def set_text_format(self, font: QFont, color: QColor) -> None:
        """Placed text: the font (family, size in points, bold, italic, underline,
        strike-out) and color chosen in the text format bar, shown while typing."""
        self.setFont(font)
        self.document().setDefaultFont(font)
        sheet = self.styleSheet()
        self.setStyleSheet(re.sub(r"color: #[0-9a-fA-F]{6}", f"color: {color.name()}", sheet, count=1)
                           if "background: transparent" in sheet or "background: white" in sheet else sheet)
        self._grow()

    # --- words in another format (bold / italic / underline / strike-out; user 2026-10-03) -------

    def _char_format(self, bold: bool, italic: bool, underline: bool, strikeout: bool):
        from PySide6.QtGui import QTextCharFormat

        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        fmt.setFontItalic(italic)
        fmt.setFontUnderline(underline)
        fmt.setFontStrikeOut(strikeout)
        return fmt

    def set_runs(self, base: tuple, runs) -> None:
        """Show the text's formats: every character gets its own (the text's, or its run's)."""
        from planetpdf.core import placed_text as PT

        text = self.toPlainText()
        cursor = self.textCursor()
        position = cursor.position()
        formats = PT.char_formats(text, base, runs)
        i = 0
        while i < len(text):
            j = i
            while j < len(text) and formats[j] == formats[i]:
                j += 1
            cursor.setPosition(i)
            cursor.setPosition(j, cursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(self._char_format(*formats[i]))
            i = j
        cursor.setPosition(min(position, len(text)))
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(self._char_format(*(formats[-1] if formats else base)))

    def char_formats(self) -> list[tuple[bool, bool, bool, bool]]:
        """(bold, italic, underline, strikeout) of every character of ``toPlainText()``."""
        out = []
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                f = fragment.charFormat()
                fmt = (f.fontWeight() >= QFont.Weight.DemiBold, f.fontItalic(), f.fontUnderline(), f.fontStrikeOut())
                out += [fmt] * fragment.length()
                it += 1
            block = block.next()
            if block.isValid():
                out.append(out[-1] if out else (False,) * 4)  # (the line break)
        return out

    # --- a line of page text in several fonts (user 2026-10-23) ----------------------------------

    SOURCE = 0x100000 + 1  # QTextFormat.UserProperty + 1: the run (of the line) a character comes from

    def set_sources(self, pieces) -> None:
        """Show ``pieces`` = [(text, run number, QFont, QColor)], each in its own font; every
        character remembers its run (letters typed take on their neighbor's)."""
        from PySide6.QtGui import QTextCharFormat

        self.setPlainText("")
        cursor = self.textCursor()
        for text, source, font, color in pieces:
            fmt = QTextCharFormat()
            fmt.setFont(font)
            fmt.setForeground(color)
            fmt.setProperty(self.SOURCE, source)
            cursor.insertText(text, fmt)
        self.setTextCursor(cursor)

    def char_sources(self) -> list[int]:
        """The run of every character of ``toPlainText()`` (one typed at the very start: the next one's)."""
        out: list[int | None] = []
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                value = fragment.charFormat().property(self.SOURCE)
                out += [value if isinstance(value, int) else None] * fragment.length()
                it += 1
            block = block.next()
        known = next((v for v in out if v is not None), 0)
        filled = []
        for value in out:
            known = value if value is not None else known
            filled.append(known)
        return filled

    def runs(self, base: tuple) -> tuple:
        from planetpdf.core import placed_text as PT

        formats = self.char_formats()
        return PT.make_runs(formats[:len(self.toPlainText().rstrip())], base)

    def selection_format(self) -> dict | None:
        """B / I / U / S of the selected words (on only when every selected letter has
        it), or None with nothing selected."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return None
        formats = self.char_formats()[cursor.selectionStart():cursor.selectionEnd()]
        if not formats:
            return None
        keys = ("bold", "italic", "underline", "strikeout")
        return {k: all(f[n] for f in formats) for n, k in enumerate(keys)}

    def format_selection(self, **changes) -> bool:
        """Bold / italic / underline / strike-out for the selected words only."""
        from PySide6.QtGui import QTextCharFormat

        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        fmt = QTextCharFormat()
        if "bold" in changes:
            fmt.setFontWeight(QFont.Weight.Bold if changes["bold"] else QFont.Weight.Normal)
        if "italic" in changes:
            fmt.setFontItalic(bool(changes["italic"]))
        if "underline" in changes:
            fmt.setFontUnderline(bool(changes["underline"]))
        if "strikeout" in changes:
            fmt.setFontStrikeOut(bool(changes["strikeout"]))
        cursor.mergeCharFormat(fmt)
        self._grow()
        return True

    def format_all(self, **changes) -> None:
        """A format for the whole text: every word takes it, those in another format too."""
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        keep = self.textCursor()
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        if "bold" in changes:
            fmt.setFontWeight(QFont.Weight.Bold if changes["bold"] else QFont.Weight.Normal)
        if "italic" in changes:
            fmt.setFontItalic(bool(changes["italic"]))
        if "underline" in changes:
            fmt.setFontUnderline(bool(changes["underline"]))
        if "strikeout" in changes:
            fmt.setFontStrikeOut(bool(changes["strikeout"]))
        cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)
        self.setTextCursor(keep)

    def content_height(self) -> float:
        layout = self.document().documentLayout()
        return layout.documentSize().height() + 2

    def natural_width(self) -> float:
        """Width the widest line needs (non-wrapping editors grow sideways)."""
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(line) for line in self.toPlainText().split("\n")), default=0)
        block = self.document().begin()  # (bold words are wider: the laid-out lines know)
        while block.isValid():
            lines = block.layout()
            for n in range(lines.lineCount()):
                widest = max(widest, lines.lineAt(n).naturalTextWidth())
            block = block.next()
        return widest + 2 * BOX_PADDING + 8

    def _grow(self) -> None:
        self._on_resize(self.content_height())

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._on_commit(self.toPlainText().rstrip(), self.content_height())

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._on_cancel()

    # --- suggestions from remembered text ----------------------------------------------------------

    @property
    def suggestion(self) -> str:
        """The remembered text shown (selected after what's typed), or ""."""
        if not self._choices:
            return ""
        cursor = self.textCursor()
        text = self.toPlainText()
        if (cursor.selectionStart() != len(self._typed) or cursor.selectionEnd() != len(text)
                or text[:len(self._typed)] != self._typed):
            self._choices = []  # (the cursor went elsewhere: it's ordinary text now)
            return ""
        return self._choices[self._choice]

    def _offer(self, index: int = 0) -> None:
        """Show choice ``index``: what's typed stays as typed, the rest is selected."""
        choice = self._choices[index]
        self._choice = index
        cursor = self.textCursor()
        cursor.setPosition(len(self._typed))
        cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
        cursor.insertText(choice[len(self._typed):])
        cursor.setPosition(len(self._typed))
        cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _suggest_after_typing(self) -> None:
        self._choices = []
        if self._suggest is None:
            return
        cursor = self.textCursor()
        text = self.toPlainText()
        if cursor.hasSelection() or cursor.position() != len(text):
            return  # (only while typing at the end)
        self._typed = text
        self._choices = self._suggest(text)
        if self._choices:
            self._offer(0)

    def _drop_suggestion(self) -> None:
        cursor = self.textCursor()
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        self._choices = []

    def _suggestion_key(self, event) -> bool:
        """Keys that act on a suggestion shown; True when handled."""
        if not self.suggestion:
            return False
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Right, Qt.Key.Key_End) and not mods & Qt.KeyboardModifier.ShiftModifier:
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)
            self._choices = []
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and len(self._choices) > 1:
            step = 1 if key == Qt.Key.Key_Down else -1
            self._offer((self._choice + step) % len(self._choices))
            return True
        if key == Qt.Key.Key_Escape:
            self._drop_suggestion()
            return True
        if key == Qt.Key.Key_Delete and mods & Qt.KeyboardModifier.ShiftModifier and self._forget is not None:
            self._forget(self._choices[self._choice])
            self._drop_suggestion()
            return True
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self._drop_suggestion()  # (Backspace takes away the suggestion, not a typed letter)
            return True
        return False

    def keyPressEvent(self, event) -> None:
        if self._suggestion_key(event):
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if self.enter_finishes and shift:
                self.textCursor().insertBlock()  # (a real new line: Qt's Shift+Enter is a soft line break)
                return
            if self.single_line or self.enter_finishes or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.finish()
                return
        super().keyPressEvent(event)
        typed = event.text()
        if self._suggest is not None and typed and typed.isprintable() \
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self._suggest_after_typing()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.FocusOut and event.reason() in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.TabFocusReason,
        ):
            QTimer.singleShot(0, self._finish_unless_formatting)  # after the click that took focus is handled
        return super().event(event)

    def _finish_unless_formatting(self) -> None:
        """Clicking away finishes, except into the text format bar (its font list, size
        box…): typing goes on there in the new format."""
        from PySide6.QtWidgets import QApplication

        modal = QApplication.activeModalWidget()  # (the color window: Windows' own has no Qt focus)
        if modal is not None and modal.property(KEEPS_EDITOR):
            return
        widget = QApplication.focusWidget() or QApplication.activePopupWidget()
        while widget is not None:
            if widget.property(KEEPS_EDITOR):
                return
            widget = widget.parentWidget()
        self.finish()


def grown_rect(rect: QRectF, content_height: float, min_height: float) -> QRectF:
    return QRectF(rect.left(), rect.top(), rect.width(), max(min_height, content_height))
