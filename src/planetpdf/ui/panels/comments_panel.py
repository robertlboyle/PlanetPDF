"""Comments panel, like Acrobat's (user screenshots 2026-10-04): "Comments N" with a search
box and a ⋯ menu (where comments show: in boxes on the page, or here), then a card per
comment grouped by page: its kind, author and time, a ⋯ menu (Edit, Delete, Flatten), its
words and its replies. The selected card gets a blue frame and a reply box with Post.

Clicking a card jumps to its comment in the document (``MarkupController.jump_to``);
clicking a comment on the page selects its card (``select_item``). Double-clicking the
words (the comment's or a reply's) edits them right in the card.

**By keyboard and screen reader** (user 2026-10-25): each card takes the focus (Tab, or Down
from the search box) and is read out whole: kind, author, page, date, words and replies.
Up / Down / Home / End go between cards, Enter or F2 edits the words (Ctrl+Enter saves,
Esc cancels), R goes to the reply box (Ctrl+Enter posts), Delete deletes, and the menu key or
Shift+F10 opens the card's ⋯ menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import markup as K

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

BLUE = "#1473e6"
KIND_ICONS = {
    K.Kind.NOTE: "message-lines", K.Kind.HIGHLIGHT: "highlighter", K.Kind.UNDERLINE: "underline",
    K.Kind.STRIKEOUT: "strikethrough", K.Kind.REPLACE: "text-slash", K.Kind.CARET: "i-cursor",
    K.Kind.TEXTBOX: "input-text", K.Kind.CALLOUT: "message", K.Kind.RECT: "square", K.Kind.ELLIPSE: "circle",
    K.Kind.LINE: "minus", K.Kind.ARROW: "arrow-right-long", K.Kind.INK: "pen", K.Kind.POLYGON: "draw-polygon",
    K.Kind.POLYLINE: "wave-pulse", K.Kind.CLOUD: "cloud", K.Kind.ATTACH: "paperclip", K.Kind.STAMP: "stamp",
    K.Kind.TEXT: "text",
}


def _when(item_or_reply) -> str:
    from planetpdf.ui.markup_controller import _pdf_date

    return _pdf_date(item_or_reply.modified)


class _Words(QWidget):
    """Words that turn into a text box on a double-click (Save / Cancel under it)."""

    saved = Signal(str)

    def __init__(self, text: str, placeholder: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._text = text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(text or placeholder, self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        if not text:
            self.label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.label)
        self.editor: QPlainTextEdit | None = None

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            key, mods = event.key(), event.modifiers()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods & Qt.KeyboardModifier.ControlModifier:
                self.saved.emit(self.editor.toPlainText().strip())
                return True
            if key == Qt.Key.Key_Escape:
                card = self.parentWidget()
                while card is not None and not isinstance(card, CommentCard):
                    card = card.parentWidget()
                self._close()
                if card is not None:
                    card.setFocus()
                return True
        return False

    def edit(self) -> None:
        if self.editor is not None:
            return
        self.label.hide()
        self.editor = QPlainTextEdit(self._text, self)
        self.editor.setMaximumHeight(90)
        self.editor.setAccessibleName("Comment text")
        self.editor.setAccessibleDescription("Ctrl+Enter saves, Esc cancels")
        self.editor.setTabChangesFocus(True)
        self.editor.installEventFilter(self)
        row = QHBoxLayout()
        save, cancel = QPushButton("Save", self), QPushButton("Cancel", self)
        save.setStyleSheet(f"QPushButton {{ background: {BLUE}; color: white; border: none; padding: 3px 12px; }}")
        save.clicked.connect(lambda: self.saved.emit(self.editor.toPlainText().strip()))
        cancel.clicked.connect(self._close)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        self.layout().addWidget(self.editor)
        self.layout().addLayout(row)
        self.editor.show()  # (now: a widget only shown on the next pass can't take the focus)
        self.editor.setFocus()

    def _close(self) -> None:
        # (rebuilt by the panel's next populate; until then just show the words again)
        if self.editor is not None:
            self.editor.hide()
        self.label.show()
        self.editor = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.edit()


class CommentCard(QFrame):
    def __init__(self, panel: CommentsPanel, item: K.MarkupItem) -> None:
        super().__init__(panel.list_widget)
        from planetpdf.ui import fa

        self.panel = panel
        self.item = item
        self.item_id = item.item_id
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        icon = QLabel(self)
        icon.setPixmap(fa.icon(KIND_ICONS.get(item.kind) or "comment").pixmap(14, 14))
        icon.setToolTip(item.type_name)
        top.addWidget(icon)
        who = QLabel(f"<b>{_escape(item.author) or ''}</b> <span style='color:gray'>{_when(item)}</span>", self)
        who.setWordWrap(True)
        top.addWidget(who)
        top.addStretch(1)
        more = self.more = QToolButton(self)
        more.setText("⋯")
        more.setAccessibleName("More for this comment")
        more.setToolTip("Edit, delete, or flatten this comment")
        more.setAutoRaise(True)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        top.addWidget(more)
        layout.addLayout(top)
        self.words = _Words(item.text, f"({item.type_name})", self)
        self.words.saved.connect(lambda text: panel.w.markup.set_text(self.item_id, text))
        layout.addWidget(self.words)
        for reply in item.replies:  # (indented, like Acrobat's)
            box = QWidget(self)
            rl = QVBoxLayout(box)
            rl.setContentsMargins(24, 6, 0, 0)
            rl.setSpacing(2)
            rl.addWidget(QLabel(f"<b>{_escape(reply.author) or 'Reply'}</b> <span style='color:gray'>{_when(reply)}</span>",
                                box))
            words = _Words(reply.text, "", box)
            words.saved.connect(lambda text, rid=reply.item_id: panel.edit_reply(rid, text))
            words.setObjectName(reply.item_id)
            rl.addWidget(words)
            layout.addWidget(box)
        # the reply box: only on the selected card (Acrobat)
        self.reply_box = QFrame(self)
        self.reply_box.setObjectName("replyBox")
        bl = QVBoxLayout(self.reply_box)
        bl.setContentsMargins(6, 6, 6, 6)
        self.reply_text = QPlainTextEdit(self.reply_box)
        self.reply_text.setPlaceholderText("Reply…")
        self.reply_text.setAccessibleName("Reply")
        self.reply_text.setAccessibleDescription("Ctrl+Enter posts the reply")
        self.reply_text.setTabChangesFocus(True)
        self.reply_text.installEventFilter(self)
        self.reply_text.setMaximumHeight(60)
        bl.addWidget(self.reply_text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.post = QPushButton("Post", self.reply_box)
        self.post.setStyleSheet(f"QPushButton {{ background: {BLUE}; color: white; border: none; padding: 3px 16px; }}"
                                "QPushButton:disabled { background: #a9c6ee; }")
        self.post.setEnabled(False)
        self.reply_text.textChanged.connect(lambda: self.post.setEnabled(bool(self.reply_text.toPlainText().strip())))
        self.post.clicked.connect(self._post)
        row.addWidget(self.post)
        bl.addLayout(row)
        layout.addWidget(self.reply_box)
        self.reply_box.setVisible(False)
        more.setMenu(self._menu())  # (after the words: its Edit edits them)
        self.set_selected(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # (keyboard and screen readers)
        self.setAccessibleName(self.spoken())
        self.setAccessibleDescription("Enter edits, R replies, Delete deletes, Shift+F10 for more; "
                                      "Up and Down go to the other comments")

    def spoken(self) -> str:
        """The whole card as a screen reader says it."""
        item = self.item
        text = f"{item.type_name} by {item.author or 'no author'}, page {item.page + 1}"
        when = _when(item)
        text += f", {when}" if when else ""
        text += f": {item.text}" if item.text else ""
        if item.replies:
            count = len(item.replies)
            text += f". {count} {'reply' if count == 1 else 'replies'}: " + "; ".join(
                f"{r.author or 'Reply'}: {r.text}" for r in item.replies)
        return text

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        if watched is self.reply_text and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._post()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.setFocus()
                return True
        return False

    def focusInEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().focusInEvent(event)
        if not self.selected and event.reason() != Qt.FocusReason.MouseFocusReason:  # (a click does it itself)
            self.panel.card_clicked(self.item_id)  # (as a click: selected, and the page goes there)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        key, mods = event.key(), event.modifiers()
        appearance = bool(self.item.kind and self.item.kind.uses_appearance)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End):
            self.panel.focus_card(self.item_id, {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1,
                                                 Qt.Key.Key_Home: "first", Qt.Key.Key_End: "last"}[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2) and not appearance:
            self.words.edit()
        elif key == Qt.Key.Key_R and not mods and self.reply_box.isVisible():
            self.reply_text.setFocus()
        elif key == Qt.Key.Key_Delete:
            self.panel.w.markup.delete(self.item_id)
        elif key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and mods & Qt.KeyboardModifier.ShiftModifier):
            self.more.showMenu()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _menu(self) -> QMenu:
        menu = QMenu(self)
        w = self.panel.w
        item = self.item
        appearance = bool(item.kind and item.kind.uses_appearance)
        if not appearance:
            menu.addAction("Edit", self.words.edit)
        if appearance:
            menu.addAction("Flatten (can't be moved afterwards)", lambda: w.markup.flatten(self.item_id))
        menu.addAction("Delete", lambda: w.markup.delete(self.item_id))
        return menu

    def _post(self) -> None:
        text = self.reply_text.toPlainText().strip()
        if text:
            self.reply_text.clear()
            self.panel.w.markup.post_reply(self.item_id, text)

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self.reply_box.setVisible(on and not (self.item.kind and self.item.kind.uses_appearance))
        self.setStyleSheet(
            f"#card {{ background: palette(base); border: {'2px solid ' + BLUE if on else '1px solid transparent'};"
            " border-radius: 3px; }"
            f" #replyBox {{ border: 1px solid {BLUE}; border-radius: 3px; background: palette(base); }}")

    def mousePressEvent(self, event) -> None:
        self.panel.card_clicked(self.item_id)
        super().mousePressEvent(event)


def _escape(text: str) -> str:
    from html import escape

    return escape(text or "")


class CommentsPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        self._window = window
        self._items: dict[str, K.MarkupItem] = {}
        self._cards: dict[str, CommentCard] = {}
        self._selected: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.title = QLabel("<b>Comments</b>", self)
        top.addWidget(self.title)
        top.addStretch(1)
        self.more = QToolButton(self)
        self.more.setText("⋯")
        self.more.setAutoRaise(True)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setToolTip("Where comments show, and more")
        self.more.setAccessibleName("Comment options")
        self.more.setMenu(self._options_menu())
        top.addWidget(self.more)
        layout.addLayout(top)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search comments")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._rebuild())
        self.search.installEventFilter(self)  # (Down goes to the first comment)
        layout.addWidget(self.search)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # (cards wrap instead)
        self.setMinimumWidth(260)  # (about Acrobat's comments pane)
        self.list_widget = QWidget(self.scroll)
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll, 1)
        self.summary = QLabel("No comments or markup.", self)
        self.summary.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.summary)

    # --- where comments show ------------------------------------------------------------------

    def _options_menu(self) -> QMenu:
        menu = QMenu(self)
        group = QActionGroup(menu)
        self.act_on_page = QAction("Show Comments in Boxes on the Page", menu, checkable=True)
        self.act_in_panel = QAction("Show Comments in This Panel", menu, checkable=True)
        for action in (self.act_on_page, self.act_in_panel):
            group.addAction(action)
            menu.addAction(action)
        in_panel = self.w.settings.comments_in_panel
        self.act_in_panel.setChecked(in_panel)
        self.act_on_page.setChecked(not in_panel)
        self.act_on_page.triggered.connect(lambda: self.w.markup.set_comments_in_panel(False))
        self.act_in_panel.triggered.connect(lambda: self.w.markup.set_comments_in_panel(True))
        return menu

    # --- the cards ------------------------------------------------------------------------------

    def populate(self, items: list[K.MarkupItem], selected: str | None = None) -> None:
        self._items = {i.item_id: i for i in items}
        if selected is not None:
            self._selected = selected
        self._rebuild()

    def _matches(self, item: K.MarkupItem) -> bool:
        query = self.search.text().strip().lower()
        if not query:
            return True
        texts = [item.text, item.author, item.type_name] + [r.text for r in item.replies] + [r.author for r in item.replies]
        return any(query in (t or "").lower() for t in texts)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down:
            if self._cards:
                next(iter(self._cards.values())).setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return False

    def focus_card(self, item_id: str, step) -> None:
        """Moves the keyboard to another card: ``step`` -1 / 1, or "first" / "last"."""
        ids = list(self._cards)
        if not ids:
            return
        if step == "first":
            target = ids[0]
        elif step == "last":
            target = ids[-1]
        else:
            here = ids.index(item_id) if item_id in ids else 0
            target = ids[max(0, min(len(ids) - 1, here + step))]
        self._cards[target].setFocus(Qt.FocusReason.TabFocusReason)

    def _rebuild(self) -> None:
        keyboard = self.window().focusWidget()  # (where the keyboard is, active window or not)
        focused = next((item_id for item_id, card in self._cards.items()
                        if keyboard is not None and (card is keyboard or card.isAncestorOf(keyboard))), None)
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        self._cards = {}
        shown = [i for i in self._items.values() if self._matches(i)]
        pages: dict[int, list[K.MarkupItem]] = {}
        for item in shown:
            pages.setdefault(item.page, []).append(item)
        for page in sorted(pages):
            header = QLabel(f"PAGE {page + 1}  <span style='color:gray'>({len(pages[page])})</span>", self.list_widget)
            header.setStyleSheet("background: palette(alternate-base); padding: 6px; font-size: 8pt;")
            self.list_layout.addWidget(header)
            for item in sorted(pages[page], key=lambda i: (i.rect[1], i.rect[0])):
                card = CommentCard(self, item)
                self._cards[item.item_id] = card
                self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)
        total = len(self._items)
        self.title.setText(f"<b>Comments</b> <span style='color:gray'>{total}</span>")
        self.summary.setVisible(not shown)
        self.summary.setText("No comments or markup." if not total else "No comments match the search.")
        self._show_selection()
        if focused is not None:  # (the keyboard stays on the card that was rebuilt, or the nearest)
            card = self._cards.get(focused) or self._cards.get(self._selected or "")
            if card is None and self._cards:
                card = next(iter(self._cards.values()))
            if card is not None:  # (once the new cards show: a hidden widget can't take the focus)
                QTimer.singleShot(0, lambda c=card: shiboken6.isValid(c) and c.setFocus(Qt.FocusReason.OtherFocusReason))

    def _show_selection(self) -> None:
        for item_id, card in self._cards.items():
            card.set_selected(item_id == self._selected)
        card = self._cards.get(self._selected or "")
        if card is not None:
            self.scroll.ensureWidgetVisible(card, 0, 40)

    def select_item(self, item_id: str | None) -> None:
        """A comment clicked on the page: its card, scrolled to (a reply: its comment's)."""
        parent = next((i.item_id for i in self._items.values() if any(r.item_id == item_id for r in i.replies)), None)
        self._selected = parent or item_id
        self._show_selection()

    def selected_id(self) -> str | None:
        return self._selected

    def card(self, item_id: str) -> CommentCard | None:
        return self._cards.get(item_id)

    def card_clicked(self, item_id: str) -> None:
        if item_id != self._selected:
            self._selected = item_id
            self._show_selection()
        self.w.markup.jump_to(item_id)  # (to the comment in the document)

    def edit_reply(self, reply_id: str, text: str) -> None:
        from planetpdf.ui.commands import ReplyCommand

        if not text:
            self.w.push_command(ReplyCommand(self.w, "Delete Reply", reply_id=reply_id))
        else:
            self.w.push_command(ReplyCommand(self.w, "Edit Reply", reply_id=reply_id, new_text=text))
