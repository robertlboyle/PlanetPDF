"""Find (Ctrl+F), text selection and copy, and following links."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QMenu, QToolBar, QToolButton

from planetpdf.core import links as L
from planetpdf.core.document import PdfError
from planetpdf.core.redaction.marks import to_display
from planetpdf.core.redaction.search import SearchHit, build_pattern, search_one_page
from planetpdf.core.text import Word, line_rects, page_words, selection_text
from planetpdf.ui.viewer.page_view import DrawStyle

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

FIND_COLOR = QColor(255, 170, 0)
SELECTION_COLOR = QColor(30, 110, 255)
MAX_HITS = 5000
# Find searches a slice of pages at a time so the window keeps answering on long documents (user
# 2026-10-23: no progress for Find): at least FIRST_SLICE pages at once (short documents are done
# straight away), then as many as fit in SLICE_SECONDS per turn of the event loop.
FIRST_SLICE = 10
SLICE_SECONDS = 0.04


def _qrect(r) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


class FindBar(QToolBar):
    """Find in the document: all matches highlighted; Enter / F3 next, Shift+Enter previous."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__("Find", window)
        self.setObjectName("findToolbar")
        self.setMovable(False)
        self._window = window
        self.hits: list[SearchHit] = []
        self.current = -1
        self.field = QLineEdit(self)
        self.field.setPlaceholderText("Find in document")
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(260)
        self.field.returnPressed.connect(self._enter)
        self.field.textEdited.connect(lambda _t: self._debounce.start())
        self.case = QCheckBox("Match case", self)
        self.words = QCheckBox("Whole words", self)
        for box in (self.case, self.words):
            box.toggled.connect(lambda _c: self.search())
        self.count = QLabel("", self)
        prev_button = QToolButton(self)
        prev_button.setText("▲")
        prev_button.setToolTip("Previous match (Shift+Enter)")
        prev_button.clicked.connect(lambda: self.step(-1))
        next_button = QToolButton(self)
        next_button.setText("▼")
        next_button.setToolTip("Next match (Enter / F3)")
        next_button.clicked.connect(lambda: self.step(1))
        close = QToolButton(self)
        close.setText("✕")
        close.setToolTip("Close (Esc)")
        close.clicked.connect(self.close_bar)
        self.stop_button = QToolButton(self)
        self.stop_button.setText("Stop")
        self.stop_button.setToolTip("Stop searching (Esc); the matches found so far stay")
        self.stop_button.clicked.connect(self.stop_search)
        self.addWidget(QLabel(" Find: ", self))
        self.addWidget(self.field)
        self.addWidget(prev_button)
        self.addWidget(next_button)
        self.addWidget(self.case)
        self.addWidget(self.words)
        self.addWidget(self.count)
        self.act_stop = self.addWidget(self.stop_button)
        self.act_stop.setVisible(False)
        self.addWidget(close)
        self._debounce = QTimer(self, singleShot=True, interval=350)
        self._debounce.timeout.connect(self.search)
        self._slice = QTimer(self, singleShot=True, interval=0)
        self._slice.timeout.connect(lambda: self._search_slice(first=False))
        self._pending: list[int] = []
        self._pattern = None
        self._search_session = None
        self._total = 0
        self._stopped = False
        self.act_next = QAction("Find Next", window)
        self.act_next.setShortcut(QKeySequence(QKeySequence.StandardKey.FindNext))
        self.act_next.triggered.connect(lambda: self.step(1))
        self.act_prev = QAction("Find Previous", window)
        self.act_prev.setShortcut(QKeySequence(QKeySequence.StandardKey.FindPrevious))
        self.act_prev.triggered.connect(lambda: self.step(-1))
        window.addAction(self.act_next)
        window.addAction(self.act_prev)
        self.hide()

    def open_bar(self) -> None:
        self.show()
        selected = self._window.text_tools.selected_text()
        if selected and "\n" not in selected:
            self.field.setText(selected)
        self.field.setFocus()
        self.field.selectAll()
        if self.field.text():
            self.search()

    def close_bar(self) -> None:
        self._cancel_search()
        self.hide()
        self.hits, self.current = [], -1
        self._window.view.clear_highlights("find")
        self._window.view.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.stop_search() if self.searching else self.close_bar()
            return
        super().keyPressEvent(event)

    @property
    def searching(self) -> bool:
        return bool(self._pending)

    def _enter(self) -> None:
        from PySide6.QtWidgets import QApplication

        backwards = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.searching and not self.hits and self._searched_for == self._query():
            return  # (still looking: Enter again doesn't start over)
        if not self.hits or self._searched_for != self._query():
            self.search()
            if backwards:
                self.step(-1)
            return
        self.step(-1 if backwards else 1)

    def _query(self) -> tuple:
        return (self.field.text(), self.case.isChecked(), self.words.isChecked())

    _searched_for: tuple = ()

    def search(self) -> None:
        """Search the open document; a long one goes on in slices (``_search_slice``), the bar
        counting "Searching page 37 of 120…" with Stop."""
        self._cancel_search()
        session = self._window.session
        self._searched_for = self._query()
        term = self.field.text()
        self.hits, self.current = [], -1
        if session is None or not term.strip():
            self.count.setText("")
            self._window.view.clear_highlights("find")
            return
        try:
            self._pattern = build_pattern(term, regex=False, case_sensitive=self.case.isChecked(),
                                          whole_word=self.words.isChecked())
        except PdfError as exc:
            self.count.setText(str(exc))
            return
        self._search_session, self._total, self._stopped = session, session.page_count, False
        self._pending = list(range(self._total))
        self._search_slice(first=True)

    def _search_slice(self, first: bool) -> None:
        import time

        if self._window.session is not self._search_session:  # (another document: never mind)
            self._cancel_search()
            return
        start, done = time.perf_counter(), 0
        while self._pending and (done < (FIRST_SLICE if first else 1) or time.perf_counter() - start < SLICE_SECONDS):
            self.hits += search_one_page(self._search_session, self._pattern, self._pending.pop(0))
            done += 1
            if len(self.hits) >= MAX_HITS:
                del self.hits[MAX_HITS:]
                self._pending = []
        here = max(self._window.view.current_page, 0)
        if self.current < 0 and any(h.page >= here for h in self.hits):
            # Start from the page being viewed, like other viewers: shown as soon as it's found.
            self.current = next(i for i, h in enumerate(self.hits) if h.page >= here)
        if self._pending:
            if self.current >= 0:
                self._show()
            searched = self._total - len(self._pending)
            found = f"{len(self.hits)} match{'es' if len(self.hits) != 1 else ''}"
            self.count.setStyleSheet("")
            self.count.setText(f" Searching page {searched + 1} of {self._total}… {found} ")
            self.act_stop.setVisible(True)
            self._slice.start()
            return
        self._finish_search()

    def _finish_search(self) -> None:
        self.act_stop.setVisible(False)
        if self.current < 0:
            self.current = 0 if self.hits else -1
        self._show()
        if self._stopped:
            self.count.setText(self.count.text().rstrip() + " (stopped) ")

    def stop_search(self) -> None:
        """Stop a long search; the matches found so far stay."""
        if self.searching:
            self._pending, self._stopped = [], True
            self._slice.stop()
            self._finish_search()

    def _cancel_search(self) -> None:
        self._pending = []
        self._slice.stop()
        self.act_stop.setVisible(False)

    def step(self, delta: int) -> None:
        if not self.hits:
            if self.field.text() and not self.searching:
                self.search()
            return
        self.current = (self.current + delta) % len(self.hits)
        self._show()

    def _show(self) -> None:
        view = self._window.view
        session = self._window.session
        if not self.hits:
            self.count.setText("No matches")
            self.count.setStyleSheet("color: #c0392b;")
            view.clear_highlights("find")
            return
        self.count.setStyleSheet("")
        self.count.setText(f" {self.current + 1} of {len(self.hits)} ")
        rects: dict[int, list[QRectF]] = {}
        current_key = None
        for i, hit in enumerate(self.hits):
            page = session.doc[hit.page]
            for rect in hit.rects:
                rects.setdefault(hit.page, []).append(_qrect(to_display(page, rect)))
                if i == self.current and current_key is None:
                    current_key = (hit.page, len(rects[hit.page]) - 1)
        view.set_highlights("find", rects, FIND_COLOR, current_key)
        hit = self.hits[self.current]
        view.show_rect(hit.page, rects[hit.page][current_key[1]])


class TextTools(QObject):
    """The Select Text tool (drag across words, Ctrl+C), link clicks, page context menu."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.selection: tuple[int, list[Word]] | None = None
        self.links: dict[int, L.Link] = {}
        view = window.view
        view.shapeDrawn.connect(self._on_shape)
        view.linkClicked.connect(self.follow_link)
        view.pageContextMenu.connect(self._context_menu)
        self.act_select = QAction("Select &Text", window, checkable=True)
        self.act_select.setToolTip("Drag across text to select it, then Ctrl+C to copy")
        self.act_select.toggled.connect(self.set_active)
        self.act_copy = QAction("&Copy", window)
        self.act_copy.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        self.act_copy.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.act_copy.triggered.connect(self.copy)
        view.addAction(self.act_copy)
        window.markup.toolChanged.connect(self._markup_tool_changed)
        view.set_pointer_text_selection(DrawStyle(color=SELECTION_COLOR, text_markup="highlight"))

    # --- select text -------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.act_select.isChecked()

    def set_active(self, on: bool) -> None:
        view = self._window.view
        if on:
            self._window.markup.set_tool(None)
            self._window.act_redact_area.setChecked(False)
            view.set_draw_style(DrawStyle(color=SELECTION_COLOR, text_markup="highlight"))
            view.set_draw_tool("text", text_cursor=True)  # (selecting text: the I-beam, not the crosshair)
        elif view.draw_tool == "text" and self._window.markup.tool is None:
            view.set_draw_tool(None)

    def _markup_tool_changed(self, tool) -> None:
        if tool is not None and self.act_select.isChecked():
            self.act_select.setChecked(False)

    def _on_shape(self, draw: str, page_index: int, data) -> None:
        # The Select Text tool, or a drag over text with no tool (Acrobat's Select tool).
        if draw != "text" or self._window.markup.tool is not None:
            return
        if data is None:
            if self.selection is not None:
                self.clear_selection()
            return
        lo, hi = data
        words = page_words(self._window.session, page_index)[lo : hi + 1]
        self.set_selection(page_index, words)

    def set_selection(self, page_index: int, words: list[Word]) -> None:
        view = self._window.view
        self.selection = (page_index, words) if words else None
        if not words:
            view.clear_highlights("selection")
            return
        page = self._window.session.doc[page_index]
        rects = [_qrect(to_display(page, tuple(r))) for r in line_rects(words)]
        view.set_highlights("selection", {page_index: rects}, SELECTION_COLOR)

    def clear_selection(self) -> None:
        self.selection = None
        self._window.view.clear_highlights("selection")

    def selected_text(self) -> str:
        return selection_text(self.selection[1]) if self.selection else ""

    def copy(self) -> bool:
        text = self.selected_text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        self._window.statusBar().showMessage(f"Copied {len(text)} characters.", 3000)
        return True

    def highlight_selection(self) -> None:
        """Turn the current text selection into a highlight annotation."""
        if not self.selection:
            return
        from planetpdf.core import markup as K
        from planetpdf.core.markup import Kind

        page_index, words = self.selection
        points = []
        for r in line_rects(words):
            q = r.quad
            points += [tuple(q.ul), tuple(q.ur), tuple(q.ll), tuple(q.lr)]
        controller = self._window.markup
        spec = K.MarkupSpec(K.new_id(), page_index, Kind.HIGHLIGHT, K.bounds(points), points=tuple(points),
                            style=controller.style_for(Kind.HIGHLIGHT), author=self._window.settings.author())
        controller.add(spec)
        self.clear_selection()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self._window)
        copy = menu.addAction("Copy", self.copy)
        copy.setEnabled(bool(self.selection))
        highlight = menu.addAction("Highlight Selected Text", self.highlight_selection)
        highlight.setEnabled(bool(self.selection))
        bookmark = menu.addAction("Add Bookmark", self._window.bookmark_tool.add)
        bookmark.setToolTip("Bookmark the selected text at its place (or this page)")
        menu.addSeparator()
        menu.addAction("Select Text Tool", lambda: self.act_select.setChecked(True))
        menu.addAction("Find…", self._window.find_bar.open_bar)
        menu.exec(pos)

    # --- links ---------------------------------------------------------------------------------

    def refresh_links(self) -> None:
        session = self._window.session
        self.links = {}
        overlays: dict[int, list[tuple[int, QRectF, str]]] = {}
        if session is not None:
            n = 0
            for index in range(session.page_count):
                page = session.doc[index]
                for link in L.page_links(session, index):
                    self.links[n] = link
                    overlays.setdefault(index, []).append((n, _qrect(to_display(page, link.rect)), link.description))
                    n += 1
        self._window.view.set_link_overlays(overlays)

    def follow_link(self, link_id: int) -> None:
        link = self.links.get(link_id)
        session = self._window.session
        if link is None or session is None:
            return
        view = self._window.view
        if link.kind is L.LinkKind.PAGE and 0 <= link.target_page < session.page_count:
            page = session.doc[link.target_page]
            import pymupdf

            shown = pymupdf.Point(0, link.target_y) * page.rotation_matrix  # as displayed
            view.go_to_point(link.target_page, shown.y if page.rotation in (0, 180) else 0)
        elif link.kind is L.LinkKind.NAMED:
            target = L.resolve_named(link.name, view.current_page, session.page_count)
            if target is not None:
                view.go_to_page(target)
        elif link.kind is L.LinkKind.WEB:
            from planetpdf.ui.outside_links import open_outside_link

            open_outside_link(self._window, link.uri)
        else:
            self._window.show_message("Link", "This link points to another file or program, which PlanetPDF doesn't open for safety.")
