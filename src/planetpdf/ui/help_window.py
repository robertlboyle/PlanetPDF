"""Help ▸ PlanetPDF Help (F1): the user manual (``core/help.py``) in a window of its own:
Contents, Index and Search on the left, the page on the right, Back / Forward / Home, and the
manual as a PDF. The menu and shortcut reference pages are made from the window's own menus, so
they can't fall behind."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.core import help as H

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

LINK = Qt.ItemDataRole.UserRole


def menu_reference(window: MainWindow) -> str:
    """Every menu command, with its shortcut and what it does (its tooltip / status tip)."""
    from PySide6.QtWidgets import QMenu

    from planetpdf.ui.shortcuts import _plain

    parts = ['<h1 id="top">Menu Reference</h1>',
             "<p>Every command on PlanetPDF's menus, menu by menu, with its keyboard shortcut. Commands "
             "a plugin added are marked. (This page is made from the menus themselves.)</p>"]

    def walk(menu: QMenu, path: str, depth: int) -> None:
        rows = []
        for action in menu.actions():
            if action.isSeparator() or not action.isVisible() and action.menu() is None:
                continue
            if action.menu() is not None:
                if rows:
                    parts.append(_table(rows))
                    rows = []
                sub = f"{path} ▸ {_plain(action.menu().title())}"
                parts.append(f"<h3>{html.escape(sub)}</h3>")
                walk(action.menu(), sub, depth + 1)
                continue
            text = _plain(action.text().split("\t")[0])
            if not text:
                continue
            keys = ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts())
            if not keys and "\t" in action.text():
                keys = action.text().split("\t", 1)[1]
            what = action.statusTip() or action.toolTip()
            if what.replace("&", "").replace("…", "").strip() == text.strip():
                what = ""
            if action.objectName().startswith("plugin:"):
                what = (what + " " if what else "") + "(a plugin)"
            if action.property("not_yet"):
                what = "Not available in PlanetPDF yet."
            rows.append((text, keys, what))
        if rows:
            parts.append(_table(rows))

    for top in window.menuBar().actions():
        if top.menu() is not None:
            title = _plain(top.menu().title())
            parts.append(f'<h2 id="{title.lower()}">{html.escape(title)} menu</h2>')
            walk(top.menu(), title, 1)
    return H.prepared("\n".join(parts))


def _table(rows) -> str:
    body = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td><td>{html.escape(c)}</td></tr>"
                   for a, b, c in rows)
    return f'<table class="ref" cellpadding="4"><tr><th>Command</th><th>Shortcut</th><th>What it does</th></tr>' \
           f"{body}</table>"


def shortcut_reference(window: MainWindow) -> str:
    from planetpdf.ui.shortcuts import shortcut_rows

    rows = sorted(shortcut_rows(window), key=lambda r: (r[0], r[1]))
    parts = ['<h1 id="top">Keyboard Shortcuts</h1>',
             "<p>PlanetPDF uses Adobe Acrobat's shortcuts, so they'll feel familiar. (This list is made from the "
             "menus themselves.)</p>"]
    current = None
    group: list = []
    for path, command, keys in rows + [(None, "", "")]:
        top = path.split(" ▸ ")[0] if path else None
        if top != current and group:
            parts.append(f'<h2 id="{current.lower()}">{html.escape(current)}</h2>')
            parts.append(_table([(c, k, p) for p, c, k in group]).replace("What it does", "Menu"))
            group = []
        current = top
        if path:
            group.append((path, command, keys))
    return H.prepared("\n".join(parts))


GENERATED = {"menus.html": menu_reference, "shortcuts.html": shortcut_reference}


class HelpBrowser(QTextBrowser):
    def __init__(self, window: MainWindow, parent=None) -> None:
        super().__init__(parent)
        self.main_window = window
        self.setOpenLinks(True)
        self.setOpenExternalLinks(False)
        self.setSearchPaths([str(H.help_dir())])
        self.anchorClicked.connect(self._external)

    def _external(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)

    def setSource(self, url: QUrl, kind=QTextDocument.ResourceType.UnknownResource) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            return  # (opened in the browser by _external)
        super().setSource(url, kind)

    def loadResource(self, kind: int, url: QUrl):
        name = Path(url.toLocalFile() or url.path()).name
        if kind == QTextDocument.ResourceType.HtmlResource.value or name.endswith(".html"):
            if name in GENERATED:
                return GENERATED[name](self.main_window)
            path = H.help_dir() / name
            if path.is_file():
                return H.prepared(path.read_text(encoding="utf-8"))
            return f"<h1>Not found</h1><p>The help page {html.escape(name)} isn't there.</p>"
        return super().loadResource(kind, url)


class HelpWindow(QWidget):
    _instance: HelpWindow | None = None

    @classmethod
    def show_help(cls, window: MainWindow, link: str = "index.html") -> HelpWindow:
        if cls._instance is None:
            cls._instance = HelpWindow(window)
        cls._instance.open_link(link)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def __init__(self, window: MainWindow) -> None:
        super().__init__(None, Qt.WindowType.Window)
        from planetpdf.ui import fa

        self.setWindowTitle("PlanetPDF Help")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.main_window = window
        self.chapters = H.chapters()

        self.browser = HelpBrowser(window, self)
        self.browser.document().setDefaultStyleSheet(STYLE)
        self.browser.sourceChanged.connect(self._follow)

        # left: Contents / Index / Search
        self.tabs = QTabWidget(self)
        self.contents = QTreeWidget(self)
        self.contents.setHeaderHidden(True)
        for chapter in self.chapters:
            item = QTreeWidgetItem([chapter.title])
            item.setData(0, LINK, chapter.file)
            self.contents.addTopLevelItem(item)
            parent = item
            for s in chapter.sections:
                if s.level == 2:
                    parent = QTreeWidgetItem(item, [s.title])
                    parent.setData(0, LINK, s.link)
                elif s.level == 3:
                    child = QTreeWidgetItem(parent, [s.title])
                    child.setData(0, LINK, s.link)
        self.contents.itemActivated.connect(lambda it: self.open_link(it.data(0, LINK)))
        self.contents.itemClicked.connect(lambda it: self.open_link(it.data(0, LINK)))
        self.tabs.addTab(self.contents, "Contents")

        index_page = QWidget(self)
        self.index_filter = QLineEdit(index_page)
        self.index_filter.setPlaceholderText("Type a word…")
        self.index_list = QListWidget(index_page)
        for term, link in H.index_entries(self.chapters):
            item = QListWidgetItem(term, self.index_list)
            item.setData(LINK, link)
        self.index_filter.textChanged.connect(self._filter_index)
        self.index_filter.returnPressed.connect(self._open_first_index)
        self.index_list.itemActivated.connect(lambda it: self.open_link(it.data(LINK)))
        self.index_list.itemClicked.connect(lambda it: self.open_link(it.data(LINK)))
        column = QVBoxLayout(index_page)
        column.setContentsMargins(4, 4, 4, 4)
        column.addWidget(self.index_filter)
        column.addWidget(self.index_list)
        self.tabs.addTab(index_page, "Index")

        search_page = QWidget(self)
        self.search_box = QLineEdit(search_page)
        self.search_box.setPlaceholderText("Search the manual…")
        self.search_box.setClearButtonEnabled(True)
        self.results = QListWidget(search_page)
        self.results.setWordWrap(True)
        self.search_box.textChanged.connect(self.search)
        self.results.itemActivated.connect(lambda it: self._open_result(it))
        self.results.itemClicked.connect(lambda it: self._open_result(it))
        column = QVBoxLayout(search_page)
        column.setContentsMargins(4, 4, 4, 4)
        column.addWidget(self.search_box)
        column.addWidget(self.results)
        self.tabs.addTab(search_page, "Search")

        # right: the buttons over the page
        def button(icon, tip, slot, shortcut=None):
            b = QToolButton(self)
            b.setIcon(fa.icon(icon))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            if shortcut:
                b.setShortcut(QKeySequence(shortcut))
            return b

        self.back = button("arrow-left", "Back (Alt+Left)", self.browser.backward, "Alt+Left")
        self.forward = button("arrow-right", "Forward (Alt+Right)", self.browser.forward, "Alt+Right")
        home = button("house", "Contents page", lambda: self.open_link("index.html"))
        pdf = button("file-pdf", "The manual as a PDF (to print or keep)", self.open_pdf)
        self.browser.backwardAvailable.connect(self.back.setEnabled)
        self.browser.forwardAvailable.connect(self.forward.setEnabled)
        self.back.setEnabled(False)
        self.forward.setEnabled(False)
        bar = QHBoxLayout()
        for b in (self.back, self.forward, home):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(pdf)
        right = QWidget(self)
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(bar)
        column.addWidget(self.browser)

        splitter = QSplitter(self)
        splitter.addWidget(self.tabs)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)
        self.resize(980, 680)

    # --- going places ---
    def open_link(self, link: str | None) -> None:
        if not link:
            return
        file, _, anchor = link.partition("#")
        url = QUrl.fromLocalFile(str(H.help_dir() / file))
        if anchor:
            url.setFragment(anchor)
        self.browser.setSource(url)

    def current_page(self) -> str:
        return Path(self.browser.source().toLocalFile()).name

    def _follow(self, url: QUrl) -> None:
        """Highlight the chapter in Contents."""
        name = Path(url.toLocalFile()).name
        for n in range(self.contents.topLevelItemCount()):
            item = self.contents.topLevelItem(n)
            if item.data(0, LINK) == name:
                self.contents.setCurrentItem(item)
                break

    def _filter_index(self, text: str) -> None:
        wanted = text.casefold().strip()
        for n in range(self.index_list.count()):
            item = self.index_list.item(n)
            item.setHidden(bool(wanted) and wanted not in item.text().casefold())

    def _open_first_index(self) -> None:
        for n in range(self.index_list.count()):
            item = self.index_list.item(n)
            if not item.isHidden():
                self.index_list.setCurrentItem(item)
                self.open_link(item.data(LINK))
                return

    def search(self, text: str) -> list[H.Hit]:
        self.results.clear()
        hits = H.search(text, self.chapters)
        for hit in hits:
            item = QListWidgetItem(f"{hit.section.title}  ({hit.chapter})\n{hit.snippet}", self.results)
            item.setData(LINK, hit.section.link)
            item.setToolTip(hit.snippet)
        if text.strip() and not hits:
            QListWidgetItem("Nothing found. Try fewer or other words.", self.results)
        return hits

    def _open_result(self, item: QListWidgetItem) -> None:
        self.open_link(item.data(LINK))
        words = self.search_box.text().split()
        if words:
            self.browser.find(words[0])

    def open_pdf(self) -> None:
        from planetpdf.ui import help_pdf

        path = help_pdf.manual_pdf(self.main_window)
        if path is None:
            self.main_window.show_message("PlanetPDF Help", "The PDF of the manual couldn't be made.")
            return
        self.main_window.open_paths([path])
        self.main_window.bring_forward()


STYLE = """
h1 { font-size: 20pt; margin-bottom: 6px; }
h2 { font-size: 15pt; margin-top: 18px; }
h3 { font-size: 12pt; margin-top: 12px; }
p, li { line-height: 130%; }
code { font-family: Consolas, Menlo, monospace; }
table.ref th { text-align: left; }
.note { margin-left: 12px; }
"""
