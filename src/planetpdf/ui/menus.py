"""The menu bar, laid out like classic Adobe Acrobat (user decision 2026-10-01: start
from Acrobat's exact menus, then adjust).

File · Edit · View · Tools · Help. Commands PlanetPDF doesn't have yet are listed
(grayed out, tooltip "Not available in PlanetPDF yet") so the gaps are visible; they're
collected in ``NOT_YET`` for the to-do list. Icons are Font Awesome (``ui/fa.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from planetpdf.core.export import ExportFormat
from planetpdf.ui import fa

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

NOT_YET_TIP = "Not available in PlanetPDF yet"
NOT_YET: list[str] = []  # filled while the menus are built (menu path ▸ command)


class MenuBuilder:
    def __init__(self, window: MainWindow) -> None:
        self.w = window

    # --- helpers ------------------------------------------------------------------------------

    def menu(self, parent, title: str, icon: str | None = None) -> QMenu:
        menu = QMenu(title, self.w)
        menu.setToolTipsVisible(True)
        if icon:
            menu.setIcon(fa.icon(icon))
        parent.addMenu(menu)
        return menu

    def add(self, menu: QMenu, action: QAction, icon: str | None = None, text: str | None = None) -> QAction:
        if icon and action.icon().isNull():
            action.setIcon(fa.icon(icon))
        if text is not None:
            action.setText(text)
        menu.addAction(action)
        return action

    def alias(self, menu: QMenu, text: str, slot, *, icon: str | None = None, doc: bool = True, shortcut_text: str = "") -> QAction:
        """A menu entry with its own wording for an existing command."""
        action = self.w._action(text + (f"\t{shortcut_text}" if shortcut_text else ""), slot, enabled_with_doc=doc)
        if icon:
            action.setIcon(fa.icon(icon))
        menu.addAction(action)
        return action

    def not_yet(self, menu: QMenu, text: str, icon: str | None = None, shortcut_text: str = "") -> QAction:
        action = QAction(text + (f"\t{shortcut_text}" if shortcut_text else ""), self.w)
        action.setEnabled(False)
        action.setToolTip(NOT_YET_TIP)
        action.setStatusTip(NOT_YET_TIP)
        action.setProperty("not_yet", True)
        if icon:
            action.setIcon(fa.icon(icon))
        menu.addAction(action)
        NOT_YET.append(menu.title().replace("&", "") + " ▸ " + text.replace("&", "").split("\t")[0])
        return action

    def not_yet_menu(self, parent: QMenu, title: str, items: list[str], icon: str | None = None) -> QMenu:
        menu = self.menu(parent, title, icon)
        for item in items:
            self.not_yet(menu, item)
        return menu

    # --- the bar ------------------------------------------------------------------------------

    def build(self) -> None:
        NOT_YET.clear()
        bar = self.w.menuBar()
        self.file_menu(bar)
        self.edit_menu(bar)
        self.view_menu(bar)
        self.tools_menu(bar)
        self.window_menu(bar)
        self.help_menu(bar)

    # --- File -----------------------------------------------------------------------------------

    def create_items(self, menu: QMenu) -> None:
        w = self.w
        self.alias(menu, "PDF from &File…", w.create_pdf_from_file, icon="file-import", doc=False)
        self.add(menu, w.act_scan_create, "scanner-image")
        self.add(menu, w.act_web_create, "globe")
        self.alias(menu, "PDF from &Clipboard", w.create_pdf_from_clipboard, icon="clipboard", doc=False)
        menu.addSeparator()
        self.add(menu, w.act_combine, "object-group", "Co&mbine Files into a Single PDF…")
        self.add(menu, w.act_create_multiple, "files")
        menu.addSeparator()
        self.alias(menu, "Create F&orm…", lambda: w.markup_ui.set_mode("design"), icon="rectangle-list")
        menu.addSeparator()
        self.add(menu, w.act_portfolio, "briefcase")
        menu.addSeparator()
        self.alias(menu, "&Blank Page", w.create_blank_pdf, icon="file", doc=False)

    def export_items(self, menu: QMenu) -> None:
        w = self.w

        def export(fmt):
            return lambda _c=False: w.export_dialog(fmt)

        word = self.menu(menu, "Microsoft &Word")
        self.alias(word, "&Word Document", export(ExportFormat.DOCX), icon="file-word")
        self.alias(word, "Word &97-2003 Document", export(ExportFormat.DOC))
        sheet = self.menu(menu, "&Spreadsheet")
        self.alias(sheet, "Microsoft &Excel Workbook", export(ExportFormat.XLSX), icon="file-excel")
        self.alias(sheet, "&XML Spreadsheet 2003", export(ExportFormat.XML_SPREADSHEET))
        self.alias(menu, "Microsoft PowerPoin&t Presentation", export(ExportFormat.PPTX), icon="file-powerpoint")
        image = self.menu(menu, "&Image")
        self.alias(image, "&JPEG", export(ExportFormat.JPG), icon="file-image")
        self.alias(image, "JPEG&2000", export(ExportFormat.JPX))
        self.alias(image, "&PNG", export(ExportFormat.PNG))
        self.alias(image, "&TIFF", export(ExportFormat.TIFF))
        image.addSeparator()  # PlanetPDF extras
        self.alias(image, "&Multi-page TIFF", export(ExportFormat.TIFF_MULTI))
        self.alias(image, "Image-only P&DF", export(ExportFormat.IMAGE_PDF))
        menu.addSeparator()
        self.alias(menu, "&HTML Web Page", export(ExportFormat.HTML), icon="globe")
        menu.addSeparator()
        self.alias(menu, "&Rich Text Format", export(ExportFormat.RTF))
        menu.addSeparator()
        self.alias(menu, "E&ncapsulated PostScript", export(ExportFormat.EPS))
        self.alias(menu, "&PostScript", export(ExportFormat.PS))
        menu.addSeparator()
        self.alias(menu, "Text (A&ccessible)", export(ExportFormat.TXT_ACCESSIBLE))
        self.alias(menu, "Text (P&lain)", export(ExportFormat.TXT))
        menu.addSeparator()
        self.alias(menu, "X&ML 1.0", export(ExportFormat.XML))

    def file_menu(self, bar) -> None:
        w = self.w
        m = self.menu(bar, "&File")
        self.add(m, w.act_open, "folder-open")
        self.add(m, w.act_reopen_session, "folder-arrow-up")
        self.create_items(self.menu(m, "C&reate", "file-circle-plus"))
        insert = self.menu(m, "&Insert Pages")
        self.alias(insert, "From &File…", w.insert_pages_dialog, icon="file-import")
        self.alias(insert, "From &Clipboard…", w.insert_pages_from_clipboard, icon="clipboard")
        self.add(insert, w.act_scan_insert, "scanner-image")
        self.add(insert, w.act_web_insert, "globe")
        self.alias(insert, "A &Blank Page…", w.insert_blank_page, icon="file")
        m.addSeparator()
        self.add(m, w.act_save, "floppy-disk")
        self.add(m, w.act_save_as, None, "Save &As…")
        other = self.menu(m, "Save as Ot&her")
        self.alias(other, "&Reduced Size PDF…", w.reduce_size_dialog)  # (its own entry: renaming act_reduce renamed Tools ▸ Compress PDF too)
        self.add(other, w.act_certify, None)
        # (no Reader Extended PDF: it needs Adobe's private key; user decision 2026-10-02)
        self.alias(other, "&Optimized PDF…", w.reduce_size_dialog)
        other.addSeparator()
        self.alias(other, "&Archivable PDF (PDF/A)…", lambda: w.save_as_standard("pdfa"))
        self.alias(other, "Press-Ready PDF (PDF/&X)…", lambda: w.save_as_standard("pdfx"))
        self.alias(other, "PDF/&E…", lambda: w.save_as_standard("pdfe"))
        self.export_items(self.menu(m, "Expor&t To"))
        self.alias(m, "Protect Using Password", w.protect_dialog)
        m.addSeparator()  # (no E-sign, 2026-10-01, and no Share File, 2026-10-02: Adobe cloud features)
        self.add(m, w.act_revert, None)
        self.add(m, w.act_close, None, "&Close File")
        m.addSeparator()
        self.add(m, w.act_properties, None, "Propert&ies…")
        m.addSeparator()
        self.add(m, w.act_print, "print")
        self._recent_marker = m.addSeparator()
        self.add(m, w.act_all_recent, None)
        m.addSeparator()
        self.add(m, w.act_quit, None, "E&xit Application")
        m.aboutToShow.connect(lambda: w.fill_recent_files(m, self._recent_marker))

    # --- Edit -------------------------------------------------------------------------------------

    def edit_menu(self, bar) -> None:
        w = self.w
        clip = w.clipboard_tools
        m = self.menu(bar, "&Edit")
        self.add(m, w.act_undo, "arrow-rotate-left")
        self.add(m, w.act_redo, "arrow-rotate-right")
        self.add(m, w.act_history_panel, "clock-rotate-left")  # (PlanetPDF: go back several steps)
        m.addSeparator()
        self.add(m, w.act_cut, "scissors")
        self.add(m, w.act_copy, "copy")
        self.add(m, w.act_paste, "paste")
        # (Acrobat's Edit ▸ More, on the Edit menu itself: user decision 2026-10-01)
        self.alias(m, "&Delete", clip.delete, icon="delete-left")
        self.add(m, w.act_select_all, None)
        self.add(m, w.act_deselect_all, None)
        self.alias(m, "Copy File to Clip&board", clip.copy_file, icon="file-arrow-up")
        self.alias(m, "Ta&ke a Snapshot", clip.take_snapshot, icon="camera")
        m.addSeparator()
        self.add(m, w.edit_ui.act_mode_edit, "pen-to-square", "Ed&it PDF")
        self.alias(m, "A&dd Text", lambda: (w.markup_ui.set_mode("edit"), w.edit.set_tool("text")), icon="i-cursor")
        self.alias(m, "Add I&mage…", lambda: (w.markup_ui.set_mode("edit"), w.edit.add_image()), icon="image")
        m.addSeparator()
        self.add(m, w.act_delete_pages_menu, "trash")
        # (Rotate Clockwise / Counterclockwise straight on the Edit menu: user decision)
        self.add(m, w.act_rotate_cw, "rotate-right", "Rotate C&lockwise")
        self.add(m, w.act_rotate_ccw, "rotate-left", "Rotate Counterclock&wise")
        self.alias(m, "Redact Te&xt && Images", w.start_redacting, icon="eraser")
        m.addSeparator()
        self.add(m, w.act_find, "magnifying-glass")
        self.add(m, w.act_advanced_search, "magnifying-glass-plus")
        self.add(m, w.act_search_tools, None)
        m.addSeparator()
        spelling = self.menu(m, "Chec&k Spelling")
        spelling.addActions([w.act_spelling, w.act_dictionary])
        m.addSeparator()
        protection = self.menu(m, "Protec&tion")
        self.add(protection, w.act_protect, "lock", "Encrypt with &Password…")
        self.add(protection, w.act_encrypt_certificate, "certificate")
        self.add(protection, w.act_unprotect, "lock-open", "&Remove Security")
        m.addSeparator()
        self.add(m, w.act_preferences, "gear")

    # --- View -------------------------------------------------------------------------------------

    def view_menu(self, bar) -> None:
        w = self.w
        m = self.menu(bar, "&View")
        rotate = self.menu(m, "Rotate &View")
        self.add(rotate, w.act_rotate_view_cw, "rotate-right")
        self.add(rotate, w.act_rotate_view_ccw, "rotate-left")
        nav = self.menu(m, "Page &Navigation")
        self.add(nav, w.act_first, "circle-chevron-up")
        self.add(nav, w.act_prev, "circle-arrow-up")
        self.add(nav, w.act_next, "circle-arrow-down")
        self.add(nav, w.act_last, "circle-chevron-down")
        self.add(nav, w.act_go_to_page, None, "&Go To Page…")
        nav.addSeparator()
        self.add(nav, w.act_back, "circle-arrow-left")
        self.add(nav, w.act_forward, "circle-arrow-right")
        m.addSeparator()
        display = self.menu(m, "&Page Display")
        from planetpdf.ui.viewer.page_view import LayoutMode

        # Acrobat's names and order (user screenshot 2026-10-02); Four Page Scrolling is ours.
        # Single / Two Page View show one page / pair at a time (Page Down turns them)
        self.add(display, w.layout_actions[LayoutMode.SINGLE_PAGE], "file")
        self.add(display, w.layout_actions[LayoutMode.SINGLE], "file-lines")
        self.add(display, w.layout_actions[LayoutMode.TWO_PAGE], "book-open")
        self.add(display, w.layout_actions[LayoutMode.TWO_UP], "book-open-cover")
        self.add(display, w.layout_actions[LayoutMode.GRID_2X2], "table-cells-large")
        display.addSeparator()
        self.add(display, w.act_gaps, None)
        self.add(display, w.act_cover, None)
        display.addSeparator()
        self.add(display, w.act_auto_scroll, None)
        zoom = self.menu(m, "&Zoom")
        self.add(zoom, w.act_zoom_to, None)
        self.alias(zoom, "&Marquee Zoom", w.marquee_zoom, icon="magnifying-glass-arrows-rotate")
        self.add(zoom, w.act_dynamic_zoom, "magnifying-glass-arrows-rotate")
        zoom.addSeparator()
        self.add(zoom, w.act_actual, "1")
        self.add(zoom, w.act_fit_page, "file-magnifying-glass", "Zoom to &Page Level")
        self.add(zoom, w.act_fit_width, "arrows-left-right-to-line", "Fit &Width")
        self.add(zoom, w.act_fit_height, None)
        self.add(zoom, w.act_fit_visible, None)
        zoom.addSeparator()
        self.add(zoom, w.act_pan_zoom, "image")
        self.add(zoom, w.act_loupe, "magnifying-glass")
        zoom.addSeparator()
        self.add(zoom, w.act_reflow, None)
        m.addSeparator()
        show = self.menu(m, "&Show/Hide")
        panes = self.menu(show, "&Navigation Panes")
        panes.addAction(w.act_toggle_nav)
        panes.addSeparator()
        for action in (w.act_thumbs, w.act_bookmarks_panel, w.act_attachments_panel, w.act_comments_panel,
                       w.act_signatures_panel, w.act_forms_panel, w.act_redact_panel, w.act_history_panel):
            panes.addAction(action)
        panes.addSeparator()
        panes.addActions(list(w.side_actions.values()))  # which side the Pages panel is on
        self.add(show, w.act_tools_pane, None)
        show.addAction(w.act_show_tabs)
        toolbars = self.menu(show, "Tool&bar Items")
        toolbars.addAction(w.act_toggle_toolbars)
        controls = self.menu(show, "&Page Controls")
        controls.addAction(w.act_page_controls)
        self.add(show, w.act_toggle_menubar, None)
        show.addSeparator()
        labels = self.menu(show, "Button &Labels")
        labels.addActions(list(w.view_options.label_actions.values()))
        rulers = self.menu(show, "&Rulers && Grids")
        rulers.addActions([w.act_rulers, w.act_grid, w.act_snap, w.act_guides])
        rulers.addSeparator()
        rulers.addAction(w.act_clear_guides)
        self.add(show, w.act_cursor, None)
        m.addSeparator()
        theme = self.menu(m, "Display T&heme")  # (user: light / dark, or follow the system)
        theme.addActions(list(w.theme_actions.values()))
        m.addSeparator()
        self.add(m, w.act_read_mode, "book-open-reader")
        self.add(m, w.act_full_screen, "display")
        m.addSeparator()  # (no Tracker: it follows Adobe cloud reviews; user decision 2026-10-02)
        read = self.menu(m, "Rea&d Out Loud")
        read.addAction(w.act_read_activate)
        read.addSeparator()
        read.addActions(w.read_actions)
        self.add(m, w.act_compare, None, "&Compare Files")

    # --- Tools (top level, user decision 2026-10-01) ----------------------------------------------

    def tools_menu(self, bar) -> None:
        """Each tool opens its bar under the main toolbar (user decision 2026-10-01): the
        bar holds the tool's commands, and shows which tool you're in. The open tool is
        checked here."""
        from planetpdf.ui.toolbars import TOOLS

        w = self.w
        m = self.menu(bar, "&Tools")
        self.tool_actions: dict[str, QAction] = {}
        for tool in TOOLS:
            if tool.key == "compare":
                continue
            action = QAction(fa.icon(tool.icon), tool.label.replace("&", "&&"), w)
            action.setCheckable(True)
            action.triggered.connect(lambda _c=False, k=tool.key: w.tool_panes.open(k))
            m.addAction(action)
            self.tool_actions[tool.key] = action
        m.addSeparator()
        self.add(m, w.act_compare, "code-compare")
        self.add(m, w.act_reduce, "file-zipper")
        m.addSeparator()
        self.add(m, w.act_close_tool, "xmark")

        def mark_open_tool() -> None:
            current = w.tool_panes.current
            for key, action in self.tool_actions.items():
                action.setChecked(key == current)
            w.act_close_tool.setEnabled(current is not None)

        m.aboutToShow.connect(mark_open_tool)

    # --- Help ------------------------------------------------------------------------------------

    def window_menu(self, bar) -> None:
        """Window menu: arrange the windows, move the current document to a window of its
        own, close everything, and every open document (user 2026-10-02: tabs in one
        window; a pop-out icon at the far right of each name moves it to a new window)."""
        w = self.w
        m = self.menu(bar, "&Window")
        m.addAction("&Cascade", lambda: w.arrange_windows("cascade"))
        tile = m.addMenu("&Tile")
        tile.addAction("&Horizontally", lambda: w.arrange_windows("horizontal"))
        tile.addAction("&Vertically", lambda: w.arrange_windows("vertical"))
        m.addSeparator()
        self.add(m, w.act_move_to_new_window, "arrow-up-right-from-square")
        m.addAction(w.act_next_tab)
        m.addAction(w.act_previous_tab)
        m.addAction(w.act_next_pane)
        m.addAction(w.act_previous_pane)
        m.addSeparator()
        m.addAction("C&lose All", w.close_all_documents)
        m.addSeparator()
        listed: list = []

        def fill() -> None:  # every open document, grouped by window; the current one ticked
            for action in listed:
                m.removeAction(action)
            listed.clear()
            n = 0
            windows = w.open_windows()
            for k, window in enumerate(windows):
                if k:
                    listed.append(m.addSeparator())
                for doc in window.documents:
                    n += 1
                    row = DocumentMenuRow(m, window, doc, n, current=window is w and doc is w.document,
                                          can_pop_out=len(window.documents) > 1)
                    m.addAction(row)
                    listed.append(row)
            if not n:
                none = m.addAction("(no documents open)")
                none.setEnabled(False)
                listed.append(none)

        m.aboutToShow.connect(fill)
        w.window_menu = m

    def help_menu(self, bar) -> None:
        w = self.w
        m = self.menu(bar, "&Help")
        self.add(m, w.act_help, "book")
        self.add(m, w.act_help_shortcuts, "keyboard")
        self.add(m, w.act_profile_folder, "folder-tree")
        m.addSeparator()
        self.add(m, w.act_check_updates, "cloud-arrow-down")
        self.add(m, w.act_report_problem, "envelope")
        self.add(m, w.act_about, "circle-question")


class DocumentMenuRow(QWidgetAction):
    """A document in the Window menu: its name (click = show it), and at the far right a
    pop-out button that moves it to a window of its own (user 2026-10-02). Right-click
    offers the same."""

    def __init__(self, menu: QMenu, window, doc, number: int, *, current: bool, can_pop_out: bool) -> None:
        super().__init__(menu)
        self.window, self.doc = window, doc
        self.current, self.can_pop_out = current, can_pop_out
        self.setText(f"{number} {doc.name}")

    def show_it(self) -> None:
        self.window.show_document(self.window, self.doc)

    def pop_out(self) -> None:
        self.window.move_document(self.doc)

    def createWidget(self, parent):
        return _DocumentRow(self, parent)


class _DocumentRow(QWidget):
    def __init__(self, action: DocumentMenuRow, parent) -> None:
        super().__init__(parent)
        self._action = action
        self.setAutoFillBackground(True)  # (hover: palettes in enter/leaveEvent, see SignatureMenuAction)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        check = QLabel("✓" if action.current else "", self)
        check.setFixedWidth(16)
        layout.addWidget(check)
        self.label = QLabel(action.text(), self)
        if action.current:
            font = self.label.font()
            font.setBold(True)
            self.label.setFont(font)
        self.label.setToolTip(str(action.doc.session.path))
        layout.addWidget(self.label, 1)
        self.pop = QToolButton(self)
        self.pop.setIcon(fa.icon("arrow-up-right-from-square"))
        self.pop.setIconSize(QSize(14, 14))
        self.pop.setAutoRaise(True)
        self.pop.setToolTip("Move to New Window")
        self.pop.setVisible(action.can_pop_out)  # (alone in its window already)
        self.pop.clicked.connect(lambda: self._run(action.pop_out))
        layout.addWidget(self.pop)
        self._highlight(False)

    def _run(self, what) -> None:
        menu = self._action.parent()
        while isinstance(menu, QMenu):  # close the menu (and its parent menus) first
            menu.close()
            menu = menu.parent() if isinstance(menu.parent(), QMenu) else None
        what()

    def _highlight(self, on: bool) -> None:
        palette = self.parentWidget().palette() if self.parentWidget() else self.palette()
        mine = self.palette()
        mine.setColor(QPalette.ColorRole.Window,
                      palette.color(QPalette.ColorRole.Highlight if on else QPalette.ColorRole.Window))
        self.setPalette(mine)
        text = self.label.palette()
        text.setColor(QPalette.ColorRole.WindowText,
                      palette.color(QPalette.ColorRole.HighlightedText if on else QPalette.ColorRole.WindowText))
        self.label.setPalette(text)

    def enterEvent(self, event) -> None:
        self._highlight(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._highlight(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._run(self._action.show_it)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        popout = menu.addAction(fa.icon("arrow-up-right-from-square"), "Move to New Window",
                                lambda: self._run(self._action.pop_out))
        popout.setEnabled(self._action.can_pop_out)
        menu.addAction("Show", lambda: self._run(self._action.show_it))
        menu.exec(event.globalPos())
