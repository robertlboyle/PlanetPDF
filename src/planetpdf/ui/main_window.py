from __future__ import annotations

import base64
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QGuiApplication,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QUndoCommand,
    QUndoGroup,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import pymupdf

from planetpdf import APP_NAME, FEEDBACK_EMAIL, __version__
from planetpdf.core import ocr
from planetpdf.core import pages as P
from planetpdf.core.markup import Kind
from planetpdf.core.export import ExportCanceled, ExportFormat, ExportOptions, export
from planetpdf.core.document import (
    PasswordRequired,
    PdfError,
    PdfSession,
    SignaturesWouldBreak,
    WrongPassword,
    open_pdf_bytes,
)
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore
from planetpdf.core.signing.visual import SignatureLibrary
from planetpdf.core.stamps import StampLibrary
from planetpdf.core.storage.profile import Profile
from planetpdf.core.storage.settings import Settings, load_settings, save_settings
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import redact_copy
from planetpdf.ui import accessibility, keyboard_nav, plain
from planetpdf.ui.save_path import choose_save_path, plain_file_name, refuse_temporary, safe_start
from planetpdf.ui.commands import (
    OcrTextCommand,
    AddMarksCommand,
    DeletePagesCommand,
    InsertBlankPageCommand,
    InsertPdfPagesCommand,
    MovePagesCommand,
    RemoveMarksCommand,
    RotatePagesCommand,
)
from planetpdf.ui.dialogs.export import FILE_FILTERS, ExportDialog
from planetpdf.ui.dialogs.ocr import OcrDialog
from planetpdf.ui.tasks import TaskCanceled, page_progress_text, run_in_background
from planetpdf.ui.dialogs.rotate import RotateDialog
from planetpdf.ui.dialogs.sanitize import SanitizeDialog
from planetpdf.ui.dialogs.verify_report import VerificationReportDialog
from planetpdf.core.signing.digital import SignOptions, Verdict, sign_pdf, validate_signatures
from planetpdf.ui.dialogs.digital_sign import DigitalSignDialog
from planetpdf.ui.dialogs.preferences import PreferencesDialog
from planetpdf.ui.dialogs.signature_capture import SignatureCaptureDialog
from planetpdf.ui.dialogs.signature_manager import SignatureManagerDialog
from planetpdf.ui.dialogs.stamps import StampManagerDialog
from planetpdf.ui.stamp_ui import StampUi
from planetpdf.ui.organizer import OrganizeGrid
from planetpdf.ui.fonts import register_bundled_fonts
from planetpdf.ui.forms_controller import FormController
from planetpdf.ui.edit_controller import EditController, EditUi
from planetpdf.ui.icons import app_icon
from planetpdf.ui.clipboard_tools import ClipboardTools
from planetpdf.ui import fa
from planetpdf.ui.documents import Document, DocumentTabs, rebind
from planetpdf.ui.recovery import AutoSaver
from planetpdf.ui.shortcuts import apply_acrobat_shortcuts
from planetpdf.ui.markup_controller import MarkupController
from planetpdf.ui.markup_toolbar import MarkupUi
from planetpdf.core.combine import combine_files
from planetpdf.core.document import REMOVE_PROTECTION
from planetpdf.core.optimize import reduce_size
from planetpdf.core.page_marks import add_header_footer, add_watermark
from planetpdf.ui.commands import CropCommand, DocumentSnapshotCommand, PageContentCommand, SetMetadataCommand, SetProtectionCommand
from planetpdf.ui.dialogs.document_tools import (
    CropDialog,
    HeaderFooterDialog,
    PropertiesDialog,
    ProtectDialog,
    ReduceSizeDialog,
    SplitDialog,
    WatermarkDialog,
)
from planetpdf.ui.dialogs.combine import CombineDialog
from planetpdf.ui.panels.bookmarks_panel import BookmarksPanel
from planetpdf.ui.panels.comments_panel import CommentsPanel
from planetpdf.ui.text_tools import FindBar, TextTools
from planetpdf.ui.panels.signatures_panel import SignaturesPanel
from planetpdf.ui.panels.forms_panel import FormsPanel
from planetpdf.ui.panels.redaction_panel import RedactionPanel
from planetpdf.ui.printing import PrintError, print_with_dialog
from planetpdf.ui.viewer.page_view import LayoutMode, PageView, ZoomMode
from planetpdf.ui.viewer.thumbnails import ThumbnailList

PDF_FILTER = "PDF files (*.pdf);;All files (*)"
ZOOM_PRESETS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
# Saved toolbar/panel layouts from builds with a different set of bars are ignored.
WINDOW_STATE_VERSION = 7  # 3: Pages / Bookmarks left the docks (side panel); 4: the left-hand rail; 5: Attachments dock; 6: text format bar; 7: Prepare Form panel
UNDO_STEPS = 100  # undo memory per document (Edit ▸ Undo History shows them all)
# a save of a document this long or big shows its steps (shorter ones are over before it'd be read)
SAVE_PROGRESS_PAGES = 30
SAVE_PROGRESS_BYTES = 15_000_000
FIT_WIDTH_TEXT = "Fit width"
FIT_PAGE_TEXT = "Fit page"


class MainWindow(QMainWindow):
    # Every document has its own window, as in classic Acrobat (user 2026-10-01: "open
    # multiple files at the same time"). Windows opened from one share its settings and
    # stamp / signature libraries, so recent files and preferences stay in step.
    _windows: list[MainWindow] = []
    _session_snapshot: list[str] | None = None  # File ▸ Exit: the documents open when it started

    def __init__(self, profile_dir: Path, recovery_dir: Path | None = None, *, restore_layout: bool = True,
                 origin: MainWindow | None = None) -> None:
        """``profile_dir``: the user's single (roaming) PlanetPDF profile directory.
        ``recovery_dir``: where auto-save keeps recovery copies (local, not roaming);
        None turns auto-save off (tests). ``restore_layout`` False skips the saved
        toolbar/panel layout (the last start didn't finish: see ui/startup_log.py)."""
        super().__init__()
        self._restore_layout = restore_layout
        self.setWindowIcon(app_icon())
        self.profile = Profile(Path(profile_dir)).ensure()
        from planetpdf.core import markup as _markup

        _markup.set_item_key(self.profile.item_key())  # (which items this user's PlanetPDF made)
        self._config_dir = self.profile.settings_dir
        self._recovery_dir = recovery_dir
        if origin is not None:  # (another window of the same PlanetPDF)
            self.settings: Settings = origin.settings
            self.stamp_library = origin.stamp_library
            self.signature_library = origin.signature_library
            self.text_history = origin.text_history
        else:
            from planetpdf.core.storage.text_history import TextHistory

            self.settings = load_settings(self._config_dir)
            self.stamp_library = StampLibrary.load(self.profile.stamps_dir)
            self.signature_library = SignatureLibrary.load(self.profile.signatures_dir)
            self.text_history = TextHistory.load(self.profile.text_history_path,  # (Add Text's suggestions)
                                                 keep_private=self.settings.remember_private_numbers)
        self.trust_store = FileTrustStore(self.profile.trusted_certs_dir)
        # The theme before any widget exists: applied later, Qt re-styles every widget
        # already built (1.2 s of a 4 s start, user 2026-10-07). set_theme() below then
        # finds it in place and only checks its menu item.
        from planetpdf.ui.theme import apply_text_size, apply_theme

        apply_theme(self.settings.theme if self.settings.theme in ("light", "dark", "system") else "system")
        apply_text_size(self.settings.text_size)  # (larger text, Preferences ▸ Appearance)
        accessibility.install()
        keyboard_nav.install()  # (arrow keys in toolbars, the focus ring)  # every control a screen reader can name, dialogs too
        plain.install()  # (text from documents never shown as rich text: security review 2026-11-25)
        # The open documents, a tab each (ui/documents.py); ``session`` / ``undo_stack``
        # are the current one's. Undo / Redo and Undo History follow the current tab
        # through the undo group.
        self.documents: list[Document] = []
        self.document: Document | None = None
        self.undo_group = QUndoGroup(self)
        self._idle_stack = QUndoStack(self)  # (no document: nothing to undo)
        self.undo_group.addStack(self._idle_stack)
        self.undo_group.setActiveStack(self._idle_stack)
        self.undo_group.cleanChanged.connect(self._update_title)
        self.autosave: AutoSaver | None = None

        self.view = PageView(self)
        self.view.setAccessibleName("Page")
        self.view.set_cache_limit(self.settings.page_cache_mb)  # (Preferences ▸ Memory)
        # The central area: the page view, or the Organize Pages grid while that tool is open
        self.central = QStackedWidget(self)
        self.central.addWidget(self.view)
        self.organizer = OrganizeGrid(self)
        self.organizer.setAccessibleName("Pages")
        self.central.addWidget(self.organizer)
        # (the stack's minimum would be its widest page, a start page, even while hidden:
        # the splitter couldn't make the page area narrower than ~760 px)
        self.central.setMinimumWidth(240)
        self.thumbnails = ThumbnailList(self)
        self.thumbnails.setAccessibleName("Page thumbnails")
        self.bookmarks_panel = BookmarksPanel(self)
        # Pages / Bookmarks fly out from the right edge of the document and collapse
        # again (user 2026-10-01); a splitter, not a dock, so they never stack with the
        # tool panels.
        from planetpdf.ui.side_panel import SidePanel

        self.side_panel = SidePanel(self, self.thumbnails, self.bookmarks_panel)
        self.act_bookmarks_panel = self.side_panel.actions["bookmarks"]
        sides = QActionGroup(self)
        self.side_actions: dict[str, QAction] = {}
        for side, text in (("left", "Pages Panel on the &Left"), ("right", "Pages Panel on the &Right")):
            action = QAction(text, self, checkable=True)
            action.setToolTip(f"Show the Pages / Bookmarks panel on the {side} of the page")
            action.triggered.connect(lambda _c=False, s=side: self.set_side_panel_side(s))
            sides.addAction(action)
            self.side_actions[side] = action
        self.central_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.central_splitter.setObjectName("centralSplitter")
        self.central_splitter.addWidget(self.central)
        self.central_splitter.addWidget(self.side_panel)
        self.view.drag_page_picker = self.thumbnail_page_at  # drag objects over a thumbnail: that page
        self.central_splitter.setStretchFactor(0, 1)
        self.central_splitter.setCollapsible(0, False)
        self.central_splitter.setCollapsible(1, False)
        # the document tabs, above the page area and its panel
        self.tabs = DocumentTabs(self)
        self.tabs.setAccessibleName("Open documents")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)
        self.tabs.tabMoved.connect(self._on_tabs_moved)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)
        self.tabs.tornOff.connect(self._tab_torn_off)
        self.tabs.hide()
        middle = QWidget(self)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)
        middle_layout.addWidget(self.tabs)
        middle_layout.addWidget(self.central_splitter, 1)
        self.setCentralWidget(middle)

        self._create_actions()
        self.redaction_panel = RedactionPanel(self)
        self._redact_dock = QDockWidget("Redaction", self)
        self._redact_dock.setObjectName("redactionDock")
        self._redact_dock.setWidget(self.redaction_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._redact_dock)
        self._redact_dock.hide()
        self.act_redact_panel = self._redact_dock.toggleViewAction()
        self.act_redact_panel.setText("Redaction &Panel")
        self.forms_panel = FormsPanel(self)
        self._forms_dock = QDockWidget("Fill Form", self)
        self._forms_dock.setObjectName("formsDock")
        self._forms_dock.setWidget(self.forms_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._forms_dock)
        self._forms_dock.hide()
        self.act_forms_panel = self._forms_dock.toggleViewAction()
        self.act_forms_panel.setText("Fill Form &Panel")
        self.forms = FormController(self)
        self.view.set_fields_highlighted(self.settings.highlight_fields)
        register_bundled_fonts()
        self.comments_panel = CommentsPanel(self)
        self._comments_dock = self._dock("Comments", "commentsDock", self.comments_panel)
        self.act_comments_panel = self._comments_dock.toggleViewAction()
        self.act_comments_panel.setText("&Comments Panel")
        self.signatures_panel = SignaturesPanel(self)
        self._signatures_dock = self._dock("Signatures", "signaturesDock", self.signatures_panel)
        self.act_signatures_panel = self._signatures_dock.toggleViewAction()
        self.act_signatures_panel.setText("Si&gnatures Panel")
        self.markup = MarkupController(self)
        self.edit = EditController(self)
        self.view.align_targets = self.edit.align_boxes  # (dotted guides: the page's own text too)
        self.edit_ui = EditUi(self)
        from planetpdf.ui.form_panel import FormPanel

        self.form_panel = FormPanel(self)  # Prepare Form: align, center, match size, distribute, fields
        self._form_design_dock = self._dock("Prepare Form", "prepareFormDock", self.form_panel)
        self.form_panel.connect_window()
        from planetpdf.ui.text_format import TextFormatBar, TextFormatter

        # Font, size, B I U S, color for the text being typed or selected (user 2026-10-03):
        # a row of its own under the tool bar, shown only while there's text to format
        # (added to the window with the other bars in _build_toolbars).
        self.text_format = TextFormatter(self)
        self.text_format_bar = QToolBar("Text Format", self)
        self.text_format_bar.setObjectName("textFormatBar")
        self.text_format_bar.setMovable(False)
        self.text_format_widget = TextFormatBar(self.text_format_bar)
        self.text_format_bar.addWidget(self.text_format_widget)
        self.text_format.toolbar = self.text_format_bar
        self.text_format.add_bar(self.text_format_widget)
        from planetpdf.ui.edit_panel import EditPanel

        self.edit_panel = EditPanel(self)  # Acrobat's right-hand panel while editing
        self._edit_dock = self._dock("Edit PDF", "editPdfDock", self.edit_panel)
        # (the panel flies out with Edit PDF: ToolPanes swaps the tool panels)
        self.markup_ui = MarkupUi(self)
        self.stamp_ui = StampUi(self)
        self.act_preferences = self._action("&Preferences…", self.preferences_dialog, "Ctrl+,")
        self.find_bar = FindBar(self)
        self.text_tools = TextTools(self)
        self.act_find = self._action("&Find…", self.find_bar.open_bar, QKeySequence.StandardKey.Find, enabled_with_doc=True)
        d = dict(enabled_with_doc=True)
        self.act_properties = self._action("Document &Properties…", self.properties_dialog, **d)
        self.act_protect = self._action("Protect with &Password…", self.protect_dialog, **d)
        self.act_unprotect = self._action("&Remove Password Protection", self.remove_protection, **d)
        self.act_certify = self._action("&Certified PDF…", lambda: self.digitally_sign(certify=True), **d)
        self.act_certify.setToolTip("Certify the document as its author, choosing which changes stay allowed")
        self.act_encrypt_certificate = self._action("Encrypt with &Certificate…", self.encrypt_with_certificate, **d)
        self.act_encrypt_certificate.setToolTip("Only the people you choose (with their Digital IDs) can open it")
        self.act_header_footer = self._action("&Header && Footer…", lambda: self.header_footer_dialog("header"), **d)
        self.act_page_numbers = self._action("Add Page &Numbers…", lambda: self.header_footer_dialog("page_numbers"), **d)
        self.act_bates = self._action("&Bates Numbering…", lambda: self.header_footer_dialog("bates"), **d)
        self.act_watermark = self._action("&Watermark…", self.watermark_dialog, **d)
        # Update / Remove (Acrobat): only marks PlanetPDF added (core.page_marks tags them)
        self.act_header_update = self._action("&Update…", lambda: self.update_page_marks("header"), **d)
        self.act_header_remove = self._action("&Remove…", lambda: self.remove_page_marks("header"), **d)
        self.act_watermark_update = self._action("&Update…", lambda: self.update_page_marks("watermark"), **d)
        self.act_watermark_remove = self._action("&Remove…", lambda: self.remove_page_marks("watermark"), **d)
        self.act_bates_remove = self._action("&Remove…", lambda: self.remove_page_marks("bates"), **d)
        self.act_background = self._action("&Add…", lambda: self.background_dialog(), **d)  # (not Qt's "checked" as spec)
        self.act_background_update = self._action("&Update…", lambda: self.update_page_marks("background"), **d)
        self.act_background_remove = self._action("&Remove…", lambda: self.remove_page_marks("background"), **d)
        # Edit PDF ▸ Link (Acrobat): drag a box for a new link, or click one to edit it
        self.act_add_link = self._action("Add/Edit Web or Document &Link", self.link_tool, **d)
        self.act_add_link.setToolTip("Drag a box where the link goes, or click an existing link to change it")
        self.act_remove_links = self._action("&Remove Links…", self.remove_links_dialog, **d)
        self.act_page_labels = self._action("Page &Labels…", self.page_labels_dialog, **d)
        self.act_replace_pages = self._action("&Replace Pages…", self.replace_pages_dialog, **d)
        self.act_replace_pages.setToolTip("Replace pages with pages from another PDF")
        self.act_crop = self._action("&Crop Pages…", self.crop_dialog, **d)
        self.act_split = self._action("&Split Document…", self.split_dialog, **d)
        self.act_reduce = self._action("Co&mpress PDF (Reduce File Size)…", self.reduce_size_dialog, **d)
        self.act_combine = self._action("Com&bine Files…", lambda: self.combine_dialog(images_only=False))
        self.act_from_images = self._action("From &Images or Scans…", lambda: self.combine_dialog(images_only=True))
        self._create_acrobat_actions()
        from planetpdf.ui.view_options import ViewOptions

        self.view_options = ViewOptions(self)  # View ▸ gaps, rulers, grid, guides, labels, page controls…
        self._create_menus()
        self._create_toolbar()
        self.view_options.apply_saved()
        self._page_announcer = QTimer(self, singleShot=True, interval=400, timeout=self._announce_page)
        self.view.currentPageChanged.connect(self._on_current_page_changed)
        self.view.zoomChanged.connect(self._on_zoom_changed)
        self.thumbnails.pageActivated.connect(self.view.go_to_page)
        self.thumbnails.moveRequested.connect(self.move_pages)
        self.thumbnails.copyRequested.connect(self.copy_pages)  # (Shift held on dropping)
        self.thumbnails.filesDropped.connect(self._on_files_dropped_on_thumbnails)
        self.thumbnails.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.thumbnails.addActions(
            [
                self.act_rotate_cw,
                self.act_rotate_ccw,
                self.act_rotate,
                self._separator(),
                self.act_insert_blank,
                self.act_insert_file,
                self._separator(),
                self.act_export_pages,
                self._separator(),
                self.act_delete_pages,
            ]
        )
        self.organizer.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.organizer.addActions(self.thumbnails.actions())  # (and Delete works while it has focus)
        self._apply_saved_layout()

        self.view.areaDrawn.connect(self._on_area_drawn)
        self.view.markClicked.connect(self._on_mark_clicked)
        # (Esc only drops the box being drawn: the redaction tool stays on, user 2026-10-17)
        self.redaction_panel.areaToolToggled.connect(self.act_redact_area.setChecked)
        self.redaction_panel.applyRequested.connect(self.apply_redactions)
        self.act_delete_marks = self._action(
            "Remove Selected Marks", self._delete_selected_marks, QKeySequence.StandardKey.Delete
        )
        self.act_delete_marks.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.view.addAction(self.act_delete_marks)
        apply_acrobat_shortcuts(self)
        # Shortcuts keep working with the menu bar hidden (F9), in full screen, and for
        # commands that only sit in a tool bar's drop-down (e.g. Ctrl+[ / Ctrl+]).
        self.addActions([a for a in self.findChildren(QAction)
                         if a.shortcuts() and a.shortcutContext() == Qt.ShortcutContext.WindowShortcut])

        self.setAcceptDrops(True)
        self.resize(1200, 850)
        # The Pages / Bookmarks panel only flies out with a document open (user 2026-10-02);
        # whether it's wanted is remembered for when one opens.
        self._side_wanted = self.settings.show_thumbnails
        self.side_panel.current = self.settings.side_panel if self.settings.side_panel in ("pages", "bookmarks") else "pages"
        self.side_panel.collapse()
        for action in self.side_panel.actions.values():
            action.setEnabled(False)
        page, panel = max(self.width() - self.settings.side_panel_width, 300), self.settings.side_panel_width
        self.central_splitter.setSizes([panel, page] if self.settings.side_panel_side == "left" else [page, panel])
        self._restore_window_state()
        self._update_document_actions()
        self._update_title()
        if recovery_dir is not None:
            self.autosave = AutoSaver(self, Path(recovery_dir))
        MainWindow._windows.append(self)

    # --- construction ----------------------------------------------------------

    def _action(self, text: str, slot, shortcut=None, *, enabled_with_doc: bool = False) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        if enabled_with_doc:
            self._doc_actions.append(action)
        return action

    def _create_actions(self) -> None:
        std = QKeySequence.StandardKey
        self._doc_actions: list[QAction] = []
        self.act_open = self._action("&Open…", self.open_dialog, std.Open)
        self.act_save = self._action("&Save", self.save, std.Save, enabled_with_doc=True)
        self.act_save_as = self._action(
            "Save &As…", self.save_as_dialog, std.SaveAs, enabled_with_doc=True
        )
        self.act_close = self._action("&Close", self.close_document, std.Close, enabled_with_doc=True)
        self.act_quit = self._action("E&xit", self.quit_all, std.Quit)

        self.act_undo = self.undo_group.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(QKeySequence(std.Undo))
        self.act_redo = self.undo_group.createRedoAction(self, "&Redo")
        self.act_redo.setShortcut(QKeySequence(std.Redo))
        # documents in tabs (user 2026-10-02)
        self.act_move_to_new_window = self._action("&Move to New Window", lambda: self.move_document())
        self.act_move_to_new_window.setToolTip("Give the current document a window of its own "
                                               "(to put documents side by side and drag pages between them)")
        self.act_move_to_new_window.setEnabled(False)
        self.act_next_tab = self._action("&Next Document", lambda: self.next_tab(1))
        self.act_previous_tab = self._action("&Previous Document", lambda: self.next_tab(-1))
        for action in (self.act_next_tab, self.act_previous_tab):
            action.setEnabled(False)
        # F6 / Shift+F6 (Acrobat): the keyboard goes to the next / previous part of the window
        self._panes = keyboard_nav.PaneCycler(self)
        self.act_next_pane = self._action("Next &Pane", lambda: self._panes.step(1))
        self.act_previous_pane = self._action("Previous P&ane", lambda: self._panes.step(-1))
        for action in (self.act_next_pane, self.act_previous_pane):
            action.setToolTip("Move the keyboard to the next / previous part of the window: toolbars, page, panels")
        self.act_show_tabs = self._action("Document &Tabs", self._toggle_tabs)
        self.act_show_tabs.setCheckable(True)
        self.act_show_tabs.setChecked(self.settings.show_document_tabs)
        self.act_show_tabs.setToolTip("A tab for each open document above the pages (otherwise switch "
                                      "with the Window menu)")

        self.act_zoom_in = self._action("Zoom &In", self.view.zoom_in, std.ZoomIn, enabled_with_doc=True)
        self.act_zoom_out = self._action(
            "Zoom &Out", self.view.zoom_out, std.ZoomOut, enabled_with_doc=True
        )
        # Qt maps "Ctrl" to Cmd on macOS automatically.
        self.act_actual = self._action(
            "&Actual Size", lambda: self.view.set_zoom(1.0), "Ctrl+0", enabled_with_doc=True
        )
        self.act_fit_width = self._action(
            "Fit &Width", self.view.fit_width, "Ctrl+1", enabled_with_doc=True
        )
        self.act_fit_page = self._action("Fit &Page", self.view.fit_page, "Ctrl+2", enabled_with_doc=True)
        # View ▸ Zoom and View ▸ Rotate View, as in Acrobat (user screenshots 2026-10-02):
        # they change only the view, never the file (reopened, it shows as before)
        d = {"enabled_with_doc": True}
        self.act_fit_height = self._action("Fit &Height", self.view.fit_height, **d)
        self.act_fit_visible = self._action("Fit &Visible", self.view.fit_visible, **d)
        self.act_fit_visible.setToolTip("The printed part of the page fills the width (the margins are left out)")
        self.view.content_bounds = self._content_bounds
        self.act_rotate_view_cw = self._action("Cloc&kwise", lambda: self.view.rotate_view(90), **d)
        self.act_rotate_view_ccw = self._action("Counterclock&wise", lambda: self.view.rotate_view(-90), **d)
        for action in (self.act_rotate_view_cw, self.act_rotate_view_ccw):
            action.setToolTip("Turns how the pages are shown; the file isn't changed")
        self.act_dynamic_zoom = self._action("&Dynamic Zoom", lambda on: self._view_tool("dynamic_zoom", on), **d)
        self.act_dynamic_zoom.setToolTip("Drag up to zoom in, down to zoom out. Esc stops.")
        self.act_loupe = self._action("&Loupe Tool", lambda on: self._view_tool("loupe", on), **d)
        self.act_loupe.setToolTip("Click or drag on the page to see that spot magnified in a small window")
        for action in (self.act_dynamic_zoom, self.act_loupe):
            action.setCheckable(True)
        self.view.viewToolChanged.connect(self._view_tool_changed)
        self.act_pan_zoom = self._action("Pa&n && Zoom", self._show_pan_zoom, **d)
        self.act_reflow = self._action("&Reflow", self._toggle_reflow, **d)
        self.act_reflow.setCheckable(True)
        self.act_thumbs = self.side_panel.actions["pages"]
        self.act_thumbs.setText("Page &Thumbnails")

        self.act_first = self._action(
            "&First Page", lambda: self.view.go_to_page(0), std.MoveToStartOfDocument, enabled_with_doc=True
        )
        self.act_prev = self._action(
            "&Previous Page",
            lambda: self.view.step_page(-1),  # (a page-at-a-time view turns the whole pair)
            "Ctrl+PgUp",
            enabled_with_doc=True,
        )
        self.act_next = self._action(
            "&Next Page",
            lambda: self.view.step_page(1),
            "Ctrl+PgDown",
            enabled_with_doc=True,
        )
        self.act_last = self._action(
            "&Last Page",
            lambda: self.view.go_to_page(self.view.page_count - 1),
            std.MoveToEndOfDocument,
            enabled_with_doc=True,
        )

        self.act_insert_blank = self._action(
            "Insert &Blank Page", self.insert_blank_page, enabled_with_doc=True
        )
        self.act_insert_file = self._action(
            "Insert Pages from &File…", self.insert_pages_dialog, enabled_with_doc=True
        )
        self.act_delete_pages = self._action(
            "&Delete Selected Pages", self.delete_selected_pages, std.Delete, enabled_with_doc=True
        )
        # Delete only acts on pages while the thumbnail list has focus (it will mean
        # "delete selected mark" in the page view later).
        self.act_delete_pages.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.act_rotate_cw = self._action(
            "Rotate &Clockwise 90°", lambda: self.rotate_pages(self.target_pages(), 90), enabled_with_doc=True
        )
        self.act_rotate_ccw = self._action(
            "Rotate Counterclock&wise 90°",
            lambda: self.rotate_pages(self.target_pages(), -90),
            enabled_with_doc=True,
        )
        self.act_rotate = self._action("&Rotate Pages…", self.rotate_dialog, enabled_with_doc=True)
        self.act_export_pages = self._action(
            "E&xtract Pages…", self.export_pages_dialog, enabled_with_doc=True
        )
        self.act_export = self._action(
            "&Export…", lambda: self.export_dialog(), "Ctrl+E", enabled_with_doc=True
        )
        self.act_ocr = self._action(
            "Scan && &OCR (Recognize Text)…", self.ocr_dialog, "Ctrl+Shift+O", enabled_with_doc=True
        )
        self.act_print = self._action("&Print…", self.print_dialog, std.Print, enabled_with_doc=True)

        self._layout_group = QActionGroup(self)
        self.layout_actions: dict[LayoutMode, QAction] = {}
        for mode, text, key in (
            # (Acrobat's names: user 2026-10-02, after trying others. Four Page Scrolling is
            # ours. No shortcuts: Acrobat uses Ctrl+4 for Reflow)
            (LayoutMode.SINGLE_PAGE, "&Single Page View", None),
            (LayoutMode.SINGLE, "Enable Sc&rolling", None),
            (LayoutMode.TWO_PAGE, "Two &Page View", None),
            (LayoutMode.TWO_UP, "&Two Page Scrolling", None),
            (LayoutMode.GRID_2X2, "&Four Page Scrolling (2×2)", None),
        ):
            action = self._action(text, lambda _c=False, m=mode: self.set_layout(m), key)
            action.setCheckable(True)
            self._layout_group.addAction(action)
            self.layout_actions[mode] = action
        # View ▸ Page Display ▸ Automatically Scroll (Acrobat; user 2026-10-02): + / - speed,
        # Up / Down direction, Esc or a click stops; backward when started on the last page
        self.act_auto_scroll = self._action("Automatically Scro&ll", lambda: self.view.toggle_auto_scroll(),
                                            "Shift+Ctrl+H", enabled_with_doc=True)
        self.act_auto_scroll.setCheckable(True)
        self.view.autoScrollChanged.connect(self._auto_scroll_changed)
        self.view.autoScrollSpeedChanged.connect(
            lambda speed: self.statusBar().showMessage(f"Automatically Scroll: speed {speed:g}   (+ faster, - slower)", 3000))
        self.act_cover = self._action("Show Co&ver Page in Two Page View", self._toggle_cover)
        self.act_cover.setToolTip("In Two and Four Page Scrolling, the first page on its own (like a book's cover)")
        self.act_cover.setCheckable(True)

        # Driven by toggled (not triggered) so the menu, toolbar, panel button and Esc all
        # go through the same path.
        self.act_redact_area = self._action(
            "Mark &Area for Redaction", lambda *_a: None, enabled_with_doc=True
        )
        self.act_redact_area.setCheckable(True)
        self.act_redact_area.toggled.connect(self._toggle_area_tool)
        self.act_redact_find = self._action(
            "&Find and Mark…", self._show_redaction_find, enabled_with_doc=True
        )
        self.act_redact_apply = self._action(
            "&Apply Redactions…", self.apply_redactions, enabled_with_doc=True
        )

    def _separator(self) -> QAction:
        action = QAction(self)
        action.setSeparator(True)
        return action

    def _create_acrobat_actions(self) -> None:
        """Commands added so Acrobat users find what they expect (menus and shortcuts
        follow Acrobat's names and grouping; see ui/shortcuts.py)."""
        d = dict(enabled_with_doc=True)
        self.act_go_to_page = self._action("&Go to Page…", self.go_to_page_box, **d)
        self.act_delete_pages_menu = self._action("&Delete Pages", self.delete_selected_pages, **d)
        self.act_full_screen = self._action("&Full Screen Mode", self.toggle_full_screen)
        self.act_full_screen.setCheckable(True)
        self.act_toggle_nav = self._action("&Navigation Panes", self.toggle_navigation_panes)
        self.act_toggle_toolbars = self._action("&Toolbars", self.toggle_toolbars)
        self.act_toggle_menubar = self._action("&Menu Bar", lambda: self.menuBar().setVisible(not self.menuBar().isVisible()))
        self.act_help = self._action(f"{APP_NAME} &Help", lambda: self.show_help())
        self.act_help_shortcuts = self._action("&Keyboard Shortcuts", self.show_shortcuts)
        self.act_profile_folder = self._action(
            "Open &Profile Folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile.root)))
        )
        self.act_about = self._action(f"&About {APP_NAME}", self.show_about)
        self.act_check_updates = self._action("Check for &Updates…", self.check_for_updates)
        self.act_report_problem = self._action("&Report a Problem…", self.report_problem)
        self.act_compare = self._action("&Compare Files…", lambda: self.compare_documents(), **d)
        # (its own on / off state, passed on explicitly: PySide sometimes called the check
        # box's setChecked with nothing from the Fill & Sign bar's More menu)
        self.act_highlight_fields = self._action(
            "&Highlight Fields", lambda: self.forms_panel.highlight.setChecked(self.act_highlight_fields.isChecked()))
        self.act_highlight_fields.setCheckable(True)
        self.act_highlight_fields.setChecked(self.settings.highlight_fields)
        self.forms_panel.highlight.toggled.connect(self.act_highlight_fields.setChecked)
        self.act_clear_form = self._action("&Clear Form", lambda: self.forms.clear_form(), **d)
        self.act_flatten_form = self._action("&Flatten Form…", lambda: self.forms.flatten(), **d)
        self.act_add_placed_text = self._action("Add &Text", self.add_text_tool, **d)
        self.act_add_placed_text.setToolTip("Click on the page and type; move it or change its size until you flatten the page")
        self.act_text_color = self._action("Text &Color…", lambda: self.markup.pick_text_color(self._selected_markup_id()), **d)
        self.act_text_color.setToolTip("Color of the selected text, or of the next text you add")
        self.act_text_larger = self._action("Larger Text", lambda: self.markup.change_text_size(self._selected_markup_id(), 1), **d)
        self.act_text_smaller = self._action("Smaller Text", lambda: self.markup.change_text_size(self._selected_markup_id(), -1), **d)
        self.act_flatten_page = self._action(
            "Flatten &Page", lambda: self.markup.flatten_page([self.view.current_page]), **d)
        self.act_flatten_all = self._action(
            "Flatten &All Pages", lambda: self.markup.flatten_page(list(range(self.session.page_count))), **d)
        for action in (self.act_flatten_page, self.act_flatten_all):
            action.setToolTip("Placed text, signatures, and stamps become part of the page (can't be moved afterwards)")
        # Page Navigation menu entries show the page-view keys (Home, arrows, End) as
        # Acrobat does; the keys themselves are the page-view-only actions below.
        for action, text in ((self.act_first, "&First Page\tHome"), (self.act_prev, "Pr&evious Page\tLeft Arrow"),
                             (self.act_next, "&Next Page\tRight Arrow"), (self.act_last, "&Last Page\tEnd")):
            action.setText(text)
            action.setShortcuts([])
        self.clipboard_tools = ClipboardTools(self)
        clip = self.clipboard_tools
        self.text_tools.act_copy.setShortcuts([])  # Edit ▸ Copy (below) copies text too
        for name, text, slot in (("act_cut", "Cu&t", clip.cut), ("act_copy", "&Copy", clip.copy),
                                 ("act_paste", "&Paste", clip.paste), ("act_select_all", "Se&lect All", clip.select_all),
                                 ("act_deselect_all", "D&eselect All", clip.deselect_all)):
            action = self._action(text, slot, **d)
            # Only while the page view has focus: lists and text boxes keep their own Ctrl+C/V/X/A.
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.view.addAction(action)
            setattr(self, name, action)
        self.act_advanced_search = self._action("Ad&vanced Search", self.find_bar.open_bar, **d)
        self.act_back = self._action("Previous &View", self.view.go_back, **d)
        self.act_forward = self._action("Next Vie&w", self.view.go_forward, **d)
        self.view.historyChanged.connect(self._update_history_actions)
        self.act_zoom_to = self._action("&Zoom To…", self.zoom_to_box, **d)
        self.act_read_mode = self._action("Rea&d Mode", self.toggle_read_mode, **d)
        self.act_read_mode.setCheckable(True)
        self.act_reopen_session = self._action("Reopen P&DFs from last session", self.reopen_last_session)
        self.act_revert = self._action("Re&vert", self.revert, **d)
        self.act_all_recent = self._action("View All Recent &Files…", self.show_all_recent)
        self.act_close_tool = self._action("C&lose Tool", lambda: self.tool_panes.close())
        # Undo History: every step since the file was opened; click one to go back to it.
        from PySide6.QtWidgets import QUndoView

        self.undo_view = QUndoView(self.undo_group, self)
        self.undo_view.setAccessibleName("Undo history")
        self.undo_view.setEmptyLabel("(as opened or last saved)")
        self._history_dock = self._dock("Undo History", "historyDock", self.undo_view)
        self.act_history_panel = self._history_dock.toggleViewAction()
        self.act_history_panel.setText("Undo &History")
        # Phase C: batch work, Enhance, and the Attachments panel (portfolios)
        from planetpdf.ui.batch_ui import AttachmentsPanel, BatchUi

        self.batch = BatchUi(self)
        self.attachments_panel = AttachmentsPanel(self)
        self._attachments_dock = self._dock("Attachments", "attachmentsDock", self.attachments_panel)
        self.act_attachments_panel = self._attachments_dock.toggleViewAction()
        self.act_attachments_panel.setText("&Attachments")
        self._attachments_dock.visibilityChanged.connect(lambda on: on and self.attachments_panel.refresh())
        self.undo_group.indexChanged.connect(lambda _i: self.attachments_panel.refresh())  # (cheap: names only)
        self.act_create_multiple = self._action("Create M&ultiple PDF Files…", lambda: self.batch.create_multiple())
        self.act_portfolio = self._action("PDF &Portfolio…", lambda: self.batch.portfolio())
        self.act_ocr_multiple = self._action("In &Multiple Files…", lambda: self.batch.ocr_multiple())
        self.act_enhance_scan = self._action("&Scanned Document…", lambda: self.batch.enhance("scan"), **d)
        self.act_enhance_camera = self._action("&Camera Image…", lambda: self.batch.enhance("camera"), **d)
        # Optimize Scans (user 2026-10-03: a tool of its own for cleaning up scans)
        from planetpdf.ui.scan_ui import ScanTools

        self.scan_tools = ScanTools(self)
        from planetpdf.ui.bookmark_tool import BookmarkTool

        self.bookmark_tool = BookmarkTool(self)  # (user 2026-10-04: bookmark selected text; find sections)
        from planetpdf.ui.security_ui import SecurityUi

        self.security_ui = SecurityUi(self)  # (Protect ▸ Advanced Options)
        self.act_optimize_scans = self._action("&Optimize Scans…", self.scan_tools.optimize_dialog, **d)
        self.act_optimize_scans.setToolTip("Straighten, clean, black and white, page direction, bleed-through, "
                                           "blank pages: every scan clean-up step, with a preview")
        # Phase D: scanner, web page, spelling, Read Out Loud
        from planetpdf.ui.phase_d_ui import ExtrasUi, read_out_loud_available

        self.extras = ExtrasUi(self)
        self.act_scan_create = self._action("PDF from &Scanner…", self.extras.create_from_scanner)
        self.act_scan_insert = self._action("From &Scanner…", self.extras.insert_from_scanner, **d)
        self.act_web_create = self._action("PDF from &Web Page…", lambda: self.extras.create_from_web())
        self.act_web_insert = self._action("From &Web Page…", lambda: self.extras.insert_from_web(), **d)
        # (Windows: its own scanners; Mac and Linux: network scanners over AirScan, 2026-10-14)
        self.act_spelling = self._action("In Comments, Fields && Editable Text…", lambda: self.extras.check_spelling(), **d)
        self.act_dictionary = self._action("Edit &Dictionary…", self.extras.edit_dictionary)
        self.act_correct_ocr = self._action("&Correct Recognized Text…", lambda: self.extras.correct_ocr(), **d)
        self.act_correct_ocr.setToolTip("Words the text recognition (OCR) may have misread, next to the scan, "
                                        "to check and retype")
        speech = read_out_loud_available()
        self.act_read_activate = self._action("Activate Read Out &Loud", lambda: None, **d)
        self.act_read_activate.setCheckable(True)
        # (enabled by Activate Read Out Loud, not by having a document: _read_aloud_toggled)
        self.act_read_page = self._action("Read This &Page Only", self.extras.read_page)
        self.act_read_end = self._action("Read to &End of Document", self.extras.read_to_end)
        self.act_read_pause = self._action("&Pause / Resume", self.extras.pause_or_resume)
        self.act_read_stop = self._action("&Stop", self.extras.stop_reading)
        self.read_actions = [self.act_read_page, self.act_read_end, self.act_read_pause, self.act_read_stop]
        self.act_read_activate.toggled.connect(self._read_aloud_toggled)
        self.act_read_activate.setVisible(speech)
        self._read_aloud_toggled(False)
        self.act_history_panel.setToolTip("Every change since the file was opened: click one to go back to it")
        self.act_toggle_nav.setText("Show/Hide &Navigation Pane")
        self.act_toggle_toolbars.setText("&Main Toolbar")
        self.act_toggle_toolbars.setCheckable(True)
        self.act_toggle_toolbars.setChecked(True)
        self.act_toggle_menubar.setCheckable(True)
        self.act_toggle_menubar.setChecked(True)
        self._theme_group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for key, text in (("system", "&Follow System Setting"), ("light", "&Light"), ("dark", "&Dark")):
            action = self._action(text, lambda _c=False, k=key: self.set_theme(k))
            action.setCheckable(True)
            self._theme_group.addAction(action)
            self.theme_actions[key] = action
        self.set_theme(self.settings.theme if self.settings.theme in self.theme_actions else "system")
        # Page-view-only keys (see shortcuts.VIEW_ONLY): arrows nudge the selected item
        # (Shift = 10 pt), otherwise turn pages / scroll; Home/End = first/last page.
        for name, slot in (
            ("act_view_prev", lambda: self._arrow(-1, 0)),
            ("act_view_next", lambda: self._arrow(1, 0)),
            ("act_view_up", lambda: self._arrow(0, -1)),
            ("act_view_down", lambda: self._arrow(0, 1)),
            ("act_view_first", lambda: self.view.go_to_page(0)),
            ("act_view_last", lambda: self.view.go_to_page(self.view.page_count - 1)),
        ):
            action = self._action(name, slot, **d)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.view.addAction(action)
            setattr(self, name, action)

    def _create_menus(self) -> None:
        """Classic Acrobat menus (``ui/menus.py``)."""
        from planetpdf.ui import fa
        from planetpdf.ui.menus import MenuBuilder

        MenuBuilder(self).build()
        from planetpdf.ui.plugins_ui import PluginMenus

        self.plugin_menus = PluginMenus(self)  # plugins' items, at the end of the menus they name
        self.plugin_menus.install()
        # Acrobat's small X at the right end of the menu bar: close the document (user
        # 2026-10-02). The same action as File ▸ Close (Ctrl+W), so it's grayed without one.
        # (not setDefaultAction: the button would copy the action's text and lose its X
        # whenever the menus change the action; it follows its enabled state instead)
        self.close_doc_button = QToolButton(self.menuBar())
        self.close_doc_button.setIcon(fa.icon("xmark"))
        self.close_doc_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.close_doc_button.setToolTip("Close this document (Ctrl+W)")
        self.close_doc_button.setAutoRaise(True)
        self.close_doc_button.clicked.connect(self.act_close.trigger)
        self.close_doc_button.setEnabled(self.act_close.isEnabled())
        self.act_close.changed.connect(lambda: self.close_doc_button.setEnabled(self.act_close.isEnabled()))
        self.menuBar().setCornerWidget(self.close_doc_button, Qt.Corner.TopRightCorner)

    def _menu_actions(self) -> list[QAction]:
        out: list[QAction] = []

        def walk(menu) -> None:
            for action in menu.actions():
                if action.menu() is not None:
                    walk(action.menu())
                elif not action.isSeparator() and action not in out:
                    out.append(action)

        for top in self.menuBar().actions():
            if top.menu() is not None:
                walk(top.menu())
        return out

    # --- placed text, nudging --------------------------------------------------------

    def pick_color(self, initial: QColor, title: str) -> QColor | None:
        """The one place colors are picked (tests replace it). Text being typed stays open while the
        window is up (user 2026-11-24: picking a color ended the typing, and the color was lost)."""
        from planetpdf.ui.inline_editor import KEEPS_EDITOR

        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle(title)
        dialog.setProperty(KEEPS_EDITOR, True)
        try:
            if dialog.exec() != QColorDialog.DialogCode.Accepted:
                return None
            color = dialog.selectedColor()
        finally:
            dialog.deleteLater()
        return color if color.isValid() else None

    def _update_text_color_swatch(self) -> None:
        from planetpdf.ui.item_bar import color_swatch

        from PySide6.QtGui import QIcon, QPainter, QPixmap

        color = QColor.fromRgbF(*self.markup.style_for(Kind.TEXT).color)
        # Fill & Sign (Acrobat, screenshot 2026-10-03): a filled dot in the color
        dot = QPixmap(24, 24)
        dot.fill(Qt.GlobalColor.transparent)
        painter = QPainter(dot)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#6b6b6b") if color.lightness() > 230 else Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(3, 3, 18, 18)
        painter.end()
        self.act_text_color.setIcon(QIcon(dot))
        self.act_text_color.setData(color.name())

    def add_text_tool(self) -> None:
        """Fill & Sign "Add Text" (also Edit PDF's): I-beam, click, type."""
        if self.edit.mode == "edit":
            self.edit.set_tool("text")
            return
        if self.edit.mode is not None:  # Prepare Form: text goes on the page in Fill & Sign
            self.markup_ui.set_mode("form")
        self.markup.set_tool(Kind.TEXT)

    def _selected_markup_id(self) -> str | None:
        """Our markup id of the selection (Edit PDF shows ours as "mk:<id>")."""
        selected = self.view.selected_markup
        if selected and selected.startswith("mk:"):
            return selected[3:]
        return selected if self.edit.mode is None else None

    NUDGE_SMALL, NUDGE_BIG = 1.0, 10.0  # points (Shift = big), like Acrobat

    def _auto_scroll_changed(self, on: bool) -> None:
        self.act_auto_scroll.setChecked(on)
        if on:
            self.view.setFocus()
            self.statusBar().showMessage(
                "Automatically scrolling: + / - change the speed, Up / Down the direction; Esc stops.", 8000)
        else:
            self.statusBar().clearMessage()

    def _arrow(self, dx: int, dy: int) -> None:
        if dy and self.view.auto_scrolling:  # Up / Down while scrolling by itself: its direction
            self.view.set_auto_direction(dy)
            return
        """Arrow keys in the page view: nudge the selected item, else turn the page /
        scroll (Left/Right, Up/Down)."""
        big = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = self.NUDGE_BIG if big else self.NUDGE_SMALL
        group = self.view.selected_group
        if len(group) > 1 and self.edit.mode == "edit":  # several objects: move them all
            self.edit.move_group(group, dx * step, dy * step)
            return
        selected = self.view.selected_markup
        located = self.view.markup_rect(selected) if selected else None
        if located is not None:
            _page, shown = located
            handler = self.edit if self.edit.mode is not None else self.markup
            if handler.nudge(selected, shown, dx * step, dy * step):
                return
        if dx:
            self.view.step_page(dx)
        else:
            bar = self.view.verticalScrollBar()
            action = bar.SliderAction.SliderPageStepAdd if big else bar.SliderAction.SliderSingleStepAdd
            if dy < 0:
                action = bar.SliderAction.SliderPageStepSub if big else bar.SliderAction.SliderSingleStepSub
            bar.triggerAction(action)

    # --- Acrobat-style view commands ------------------------------------------------

    def go_to_page_box(self) -> None:
        """Go to Page (Ctrl+Shift+N): straight into the page number box."""
        self.page_spin.setFocus()
        self.page_spin.selectAll()

    def toggle_navigation_panes(self) -> None:
        self.side_panel.toggle()

    def thumbnail_page_at(self, global_pos: QPoint) -> int | None:
        """While objects are dragged: the page whose thumbnail (Pages panel) is under the
        mouse, highlighted; hovering near the list's top / bottom edge scrolls it."""
        panel, thumbs = self.side_panel, self.thumbnails
        if not panel.is_open or panel.current != "pages":
            return None
        viewport = thumbs.viewport()
        local = viewport.mapFromGlobal(global_pos)
        if not viewport.rect().contains(local):
            return None
        bar = thumbs.verticalScrollBar()
        if local.y() < 24:
            bar.setValue(bar.value() - 12)
        elif local.y() > viewport.height() - 24:
            bar.setValue(bar.value() + 12)
        index = thumbs.indexAt(local)
        if not index.isValid():
            return None
        thumbs.setCurrentRow(index.row())  # (shows which page it will land on)
        return index.row()

    def _toolbars(self) -> list[QToolBar]:
        return [tb for tb in self.findChildren(QToolBar) if tb.parent() is self]

    def toggle_toolbars(self) -> None:
        """F8: show/hide the main toolbar (mode toolbars follow their mode)."""
        main = self.findChild(QToolBar, "mainToolbar")
        main.setVisible(not main.isVisible())

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self._leave_full_screen()
            return
        self._full_screen_state = self.saveState()
        self._full_screen_menubar = self.menuBar().isVisible()
        self.menuBar().hide()
        for toolbar in self._toolbars():
            toolbar.hide()
        for dock in self.findChildren(QDockWidget):
            dock.hide()
        self.statusBar().hide()
        self.act_full_screen.setChecked(True)
        self.showFullScreen()

    def _leave_full_screen(self) -> None:
        if not self.isFullScreen():
            return
        self.showNormal()
        self.restoreState(getattr(self, "_full_screen_state", QByteArray()))
        self.menuBar().setVisible(getattr(self, "_full_screen_menubar", True))
        self.statusBar().show()
        self.act_full_screen.setChecked(False)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():  # like Acrobat
            self._leave_full_screen()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_help(self, link: str = "index.html") -> None:
        """Help ▸ PlanetPDF Help (F1): the manual (``ui/help_window.py``)."""
        from planetpdf.ui.help_window import HelpWindow

        HelpWindow.show_help(self, link if isinstance(link, str) else "index.html")

    def show_shortcuts(self) -> None:
        from planetpdf.ui.shortcuts import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def show_about(self) -> None:
        """Help ▸ About: the version and the third-party components with their licenses."""
        from planetpdf.ui.dialogs.about import AboutDialog

        AboutDialog(self).exec()

    def report_problem(self) -> None:
        """Help ▸ Report a Problem: a new email to Planet Networks in the user's own mail
        program, with the version and system filled in. Nothing is sent by PlanetPDF: the
        user reads, changes, and sends (or discards) the email."""
        if not QDesktopServices.openUrl(report_problem_url()):
            QMessageBox.information(
                self, "Report a Problem",
                f"No email program is set up on this computer. Send your report to {FEEDBACK_EMAIL}, "
                f"and include the version: {APP_NAME} {__version__}.")

    def _create_toolbar(self) -> None:
        """Acrobat's layout (``ui/toolbars.py``): the main toolbar, the tool strip on the
        right, and one tool bar (the open tool's commands) under the main toolbar."""
        from planetpdf.ui.toolbars import ToolPanes, build_main_toolbar

        tb = self.addToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        self.page_spin = QSpinBox(self)
        self.page_spin.setAccessibleName("Page number")
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_spin.setFixedWidth(46)
        self.page_spin.valueChanged.connect(lambda v: self.view.go_to_page(v - 1))
        self.page_count_label = QLabel(" / 0 ", self)
        self.zoom_combo = QComboBox(self)
        self.zoom_combo.setAccessibleName("Zoom")
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_combo.addItems([FIT_WIDTH_TEXT, FIT_PAGE_TEXT] + [f"{round(z * 100)}%" for z in ZOOM_PRESETS])
        self.zoom_combo.setMinimumContentsLength(8)
        self.zoom_combo.textActivated.connect(self._on_zoom_text)
        # (typed and Enter: Qt sends textActivated only for a text in the list; user 2026-11-21:
        # "entering a percentage and clicking enter does nothing")
        self.zoom_combo.lineEdit().returnPressed.connect(lambda: self._on_zoom_text(self.zoom_combo.currentText()))
        build_main_toolbar(self, tb)
        self.markup.textColorChanged.connect(self._update_text_color_swatch)
        self._update_text_color_swatch()
        self._doc_widgets: list[QWidget] = [self.page_spin, self.zoom_combo]
        # The open tool's bar gets its own row under the main toolbar (one shows at a time).
        self.tool_panes = ToolPanes(self)
        self.addToolBarBreak()
        for bar in self.tool_panes.bars.values():
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
            bar.setVisible(False)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.tool_panes.strip)
        self.addToolBarBreak()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.text_format_bar)
        self.text_format_bar.setVisible(False)
        self.addToolBarBreak()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.find_bar)
        self.find_bar.setVisible(False)
        # The left-hand rail: Pages / Bookmarks when the panel is on the left (Acrobat's
        # navigation pane). Created here, before a saved layout is restored, and only
        # shown or hidden afterwards (never moved: see CLAUDE.md, Qt gotchas).
        from planetpdf.ui import fa

        self.nav_rail = QToolBar("Navigation", self)
        self.nav_rail.setObjectName("navRail")
        self.nav_rail.setMovable(False)
        self.nav_rail.setOrientation(Qt.Orientation.Vertical)
        self.nav_rail.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.nav_rail.setStyleSheet(fa.CHECKED_STYLE)
        for action in self.side_panel.actions.values():
            self.nav_rail.addAction(action)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.nav_rail)
        self.set_side_panel_side(self.settings.side_panel_side)

    def set_side_panel_side(self, side: str) -> None:
        """Pages / Bookmarks panel on the "left" (rail of buttons on the left edge) or the
        "right" (its buttons at the top of the tool strip). View ▸ Show/Hide ▸ Navigation
        Panes."""
        from planetpdf.ui import fa

        side = "right" if side == "right" else "left"
        self.settings.side_panel_side = side
        left = side == "left"
        total = sum(self.central_splitter.sizes()) or self.width()
        panel = self.side_panel.width() if self.side_panel.is_open else self.settings.side_panel_width
        self.central_splitter.insertWidget(0 if left else 1, self.side_panel)
        page = max(total - panel, 300)
        self.central_splitter.setSizes([panel, page] if left else [page, panel])
        self.central_splitter.setStretchFactor(0, 0 if left else 1)
        self.central_splitter.setStretchFactor(1, 1 if left else 0)
        self.nav_rail.setVisible(left)
        for item in self.tool_panes.strip_side_items:
            item.setVisible(not left)
        self.side_panel.collapse_button.setIcon(fa.icon("angles-left" if left else "angles-right"))
        for name, action in getattr(self, "side_actions", {}).items():
            action.setChecked(name == side)

    def select_tool(self) -> None:
        """The arrow: no tool (click items; drag over text to select it)."""
        self.view.set_hand_tool(False)
        self.view.set_view_tool(None)
        self.markup.set_tool(None)
        if self.edit.mode is not None:
            self.edit.set_tool(None)
        self.act_redact_area.setChecked(False)
        self.text_tools.act_select.setChecked(False)
        self.act_select_tool.setChecked(True)

    def hand_tool(self, on: bool) -> None:
        if on:
            self.markup.set_tool(None)
            if self.edit.mode is not None:
                self.edit.set_tool(None)
            self.act_redact_area.setChecked(False)
            self.text_tools.act_select.setChecked(False)
        self.view.set_hand_tool(on)

    # --- documents -------------------------------------------------------------

    def open_dialog(self) -> None:
        """File ▸ Open: one or several PDFs (Ctrl/Shift in the file dialog)."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", self.settings.last_directory, PDF_FILTER)
        if paths:
            self.open_paths([Path(p) for p in paths])

    # --- several documents: tabs; windows only for popped-out ones -------------------------

    @classmethod
    def open_windows(cls) -> list[MainWindow]:
        import shiboken6

        cls._windows = [w for w in cls._windows if shiboken6.isValid(w)]
        return [w for w in cls._windows if w.isVisible()]

    @classmethod
    def all_documents(cls) -> list[tuple[MainWindow, Document]]:
        return [(w, d) for w in cls.open_windows() for d in w.documents]

    def find_document(self, path: Path) -> tuple[MainWindow, Document] | None:
        """Where this file is open already (any window)."""
        wanted = Path(path).resolve()
        for window, doc in self.all_documents():
            if Path(doc.session.path).resolve() == wanted:
                return window, doc
        return None

    def window_for(self, path: Path) -> MainWindow | None:
        """The window that has this file open."""
        found = self.find_document(path)
        return found[0] if found else None

    def show_document(self, window: MainWindow, doc: Document) -> None:
        """Bring a document forward: its window in front, on its tab."""
        window._activate(doc)
        window.bring_forward()

    def open_paths(self, paths: list[Path]) -> list[MainWindow]:
        """Open each file in a tab of this window (user 2026-10-02: one copy of the program,
        not a window per file). A file that is already open is brought forward."""
        from planetpdf.ui import printouts

        opened = []
        for path in paths:
            path = Path(path)
            if printouts.is_printout(path):  # (from the PlanetPDF printer: saved where it belongs first)
                saved = printouts.receive(self, path)
                if saved is None or not saved[1]:  # (canceled, or "Open PDF after saving" unticked)
                    continue
                path = saved[0]
            found = self.find_document(path)
            if found is not None:
                self.show_document(*found)
                opened.append(found[0])
                continue
            if self.open_file(path):
                opened.append(self)
        if opened:
            opened[-1].bring_forward()
        return opened

    def new_window(self) -> MainWindow:
        """Another empty window, cascaded from this one."""
        window = MainWindow(self.profile.root, self._recovery_dir, origin=self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.resize(self.size())
        window.move(self.pos() + QPoint(30, 30))
        if self.isMaximized():
            window.showMaximized()
        else:
            window.show()
        window.ensure_on_screen()
        return window

    _last_active: MainWindow | None = None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if sys.platform == "win32" and not getattr(self, "_taskbar_identity", False):
            # the taskbar's icon for this window (Windows 11 showed a generic one: windows_shell)
            self._taskbar_identity = True
            from planetpdf.ui import windows_shell

            windows_shell.window_identity(int(self.winId()), **windows_shell.relaunch_info())

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            MainWindow._last_active = self  # (files from a later start open here)
        super().changeEvent(event)

    @classmethod
    def last_active_window(cls) -> MainWindow | None:
        windows = cls.open_windows()
        if cls._last_active in windows:
            return cls._last_active
        return windows[0] if windows else None

    def bring_forward(self, force: bool = False) -> None:
        """``force``: in front even when another program is in use (a printout arriving: the
        printer's service, not the user, started it; Windows otherwise only flashes the button)."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        # (only on the real display: offscreen tests must never tap a real Alt key)
        if force and sys.platform == "win32" and QGuiApplication.platformName() == "windows":
            from planetpdf.ui import windows_shell

            windows_shell.force_foreground(int(self.winId()))

    def quit_all(self) -> bool:
        """File ▸ Exit: close every window (each asks about unsaved changes; canceling
        one stops it). Reopen PDFs from last session then opens them all again."""
        windows = self.open_windows()
        MainWindow._session_snapshot = [str(d.session.path) for _w, d in self.all_documents()]
        try:
            for window in [w for w in windows if w is not self] + [self]:
                if not window.close():
                    return False
        finally:
            MainWindow._session_snapshot = None
        return True

    def arrange_windows(self, how: str) -> None:
        """Window ▸ Cascade / Tile Horizontally / Tile Vertically."""
        windows = self.open_windows()
        if not windows:
            return
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        for n, window in enumerate(windows):
            window.showNormal()
            if how == "cascade":
                offset = 30 * n
                window.setGeometry(area.x() + offset, area.y() + offset, int(area.width() * 0.7), int(area.height() * 0.75))
            elif how == "horizontal":  # one above the other
                h = area.height() // len(windows)
                window.setGeometry(area.x(), area.y() + n * h, area.width(), h)
            else:  # side by side
                w = area.width() // len(windows)
                window.setGeometry(area.x() + n * w, area.y(), w, area.height())
            window.raise_()

    def close_all_documents(self) -> bool:
        """Window ▸ Close All: every document in every window (each asks about unsaved
        changes; Cancel stops)."""
        for window in [w for w in self.open_windows() if w is not self]:
            if not window.close():
                return False
        while self.documents:
            if not self.close_document():
                return False
        return True

    def maybe_save_all(self) -> bool:
        """Closing the window: ask about each document with unsaved changes (shown first,
        so the question is about the file in front)."""
        for doc in list(self.documents):
            self._activate(doc)
            if not self.maybe_save():
                return False
        return True

    def open_file(self, path: Path, index: int | None = None) -> bool:
        """Open a file in a new tab of this window (after the current tab, or at
        ``index``)."""
        path = Path(path)
        if self._open_elsewhere(path):
            return False
        from planetpdf.core.cert_encryption import is_certificate_encrypted

        if is_certificate_encrypted(Path(path)):
            session = self.open_certificate_encrypted(Path(path))
            if session is None:
                return False
            self._add_document(session, index)
            self._opened(session)
            if not session.certificate_protection.recipients_known:
                self.show_message(
                    "Certificate-Encrypted File",
                    "This file was encrypted by another program. If you save changes, PlanetPDF can only "
                    "keep it encrypted for you. To add the other people back, use Protection ▸ Encrypt with "
                    "Certificate before saving.")
            return True
        password: str | None = None
        while True:
            try:
                session = PdfSession.open(path, password)
                break
            except (PasswordRequired, WrongPassword) as exc:
                prompt = f"{exc}\n\nEnter the password for {path.name}:"
                password, ok = QInputDialog.getText(
                    self, "Password Required", prompt, QLineEdit.EchoMode.Password
                )
                if not ok:
                    return False
            except PdfError as exc:
                QMessageBox.critical(self, "Can't Open File", str(exc))
                return False
        self._add_document(session, index)
        self._opened(session)
        return True

    def _opened(self, session: PdfSession) -> None:
        """Remember an opened file; one in a temporary folder (a zip opened in Explorer, an e-mail
        attachment) is said to be so, and its folder isn't offered for saving later (user
        2026-11-24: such a file was deleted with the changes made to it)."""
        from planetpdf.core.temp_folders import temporary_reason

        self.settings.add_recent_file(session.path)
        reason = temporary_reason(session.path)
        if reason is None:
            self.settings.last_directory = str(session.path.parent)
            return
        self.statusBar().showMessage(
            f"{session.path.name} is in {reason}, which can be emptied without warning. "
            "Saving will ask you where to keep it.", 15000)

    def _open_elsewhere(self, path: Path) -> bool:
        """The file is open in another PlanetPDF already (user 2026-10-22: two copies of one
        file, one closed without asking while the other held the changes): show it there
        instead, or say so. True = don't open it here."""
        from planetpdf.ui import file_locks

        pid = file_locks.holder(path)
        if pid is None:
            return False
        if not file_locks.show_in_holder(path, pid):
            self.show_message(
                "File Already Open",
                f"“{path.name}” is already open in another PlanetPDF window.\n\n"
                "Switch to that window to work on it: two copies of one file could lose changes.")
        return True

    def open_certificate_encrypted(self, path: Path) -> PdfSession | None:
        """Windows: first with the certificates in your Windows certificate store (their keys
        stay there; Windows asks for a PIN itself). Otherwise, or when none of them fits,
        ask for the Digital ID file until the file opens (or the user gives up)."""
        from planetpdf.ui.dialogs.cert_encryption import OpenWithDigitalIdDialog

        from planetpdf.core.signing.providers import system_store

        error = ""
        if system_store.available():  # (Windows' certificate store, the Mac Keychain)
            from planetpdf.core.cert_encryption import NoWindowsCertificate

            try:
                return PdfSession.open_certificate_encrypted(path)
            except NoWindowsCertificate:
                pass  # (not for any certificate in Windows: a Digital ID file, then)
            except (PdfError, OSError, ValueError) as exc:
                error = str(exc)
        while True:
            dialog = OpenWithDigitalIdDialog(self, path, error)
            if dialog.exec() != OpenWithDigitalIdDialog.DialogCode.Accepted:
                return None
            pfx, password = dialog.pfx.text().strip(), dialog.password.text()
            dialog.password.clear()
            try:
                session = PdfSession.open_certificate_encrypted(path, Path(pfx), password)
            except (PdfError, OSError, ValueError) as exc:
                error = str(exc)
                continue
            self.settings.last_certificate = pfx
            return session

    def encrypt_with_certificate(self) -> None:
        """Protection ▸ Encrypt with Certificate: who can open it (applied on save)."""
        from planetpdf.ui.dialogs.cert_encryption import EncryptCertificateDialog

        dialog = EncryptCertificateDialog(self, self.session.certificate_protection)
        if dialog.exec() != EncryptCertificateDialog.DialogCode.Accepted:
            return
        self.push_command(SetProtectionCommand(self, dialog.protection(), "Encrypt with Certificate"))
        self.statusBar().showMessage("The file will be encrypted for "
                                     + ", ".join(dialog.protection().names) + " when you save.", 8000)

    def close_document(self) -> bool:
        """File ▸ Close (Ctrl+W): the current tab; the window too when it was the last
        one and other windows are open."""
        if self.document is None:
            if len(self.open_windows()) > 1:
                self.close()
            return True
        if not self.maybe_save():
            return False
        self._remove_document(self.document)
        if not self.documents and len(self.open_windows()) > 1:
            self.close()
        return True

    # --- the documents of this window (tabs) ---------------------------------------------

    @property
    def session(self) -> PdfSession | None:
        return self.document.session if self.document is not None else None

    @property
    def undo_stack(self) -> QUndoStack:
        return self.document.undo_stack if self.document is not None else self._idle_stack

    @property
    def _signature_change_accepted(self) -> bool:
        return self.document is not None and self.document.signature_change_accepted

    @_signature_change_accepted.setter
    def _signature_change_accepted(self, value: bool) -> None:
        if self.document is not None:
            self.document.signature_change_accepted = value

    def _add_document(self, session: PdfSession, index: int | None = None) -> Document:
        """A newly opened file: a tab of its own (after the current one), shown at once."""
        stack = QUndoStack(self)
        stack.setUndoLimit(UNDO_STEPS)  # (user: "10-20 steps would be really nice")
        doc = Document(session, stack)
        self._insert_document(doc, index)
        self._activate(doc, first=True)
        return doc

    def _insert_document(self, doc: Document, index: int | None) -> None:
        if index is None:
            index = self.documents.index(self.document) + 1 if self.document in self.documents else len(self.documents)
        index = max(0, min(index, len(self.documents)))
        self.documents.insert(index, doc)
        self.undo_group.addStack(doc.undo_stack)
        self.tabs.blockSignals(True)
        self.tabs.insertTab(index, doc.name)
        self.tabs.setTabData(index, doc)
        self.tabs.setTabToolTip(index, str(doc.session.path))
        self.tabs.blockSignals(False)
        self._update_tabs()

    def _take_document(self, doc: Document) -> None:
        """Take a document out of this window (closed, or moved to another window) and
        show its neighbor (or nothing)."""
        if doc is self.document:
            self._leave_document()
        index = self.documents.index(doc)
        self.documents.remove(doc)
        self.undo_group.removeStack(doc.undo_stack)
        self.tabs.blockSignals(True)
        self.tabs.removeTab(self.tabs.index_of(doc))
        self.tabs.blockSignals(False)
        if doc is self.document:
            if not self.documents:  # the last one: remember the panel for the next document
                self._side_wanted = self.side_panel.is_open
                if self.side_panel.is_open:
                    self.settings.side_panel_width = self.side_panel.width()
            self.document = None
            if self.documents:
                self._activate(self.documents[min(index, len(self.documents) - 1)])
            else:  # nothing left to show
                self.undo_group.setActiveStack(self._idle_stack)
                self._show_document()
        self._update_tabs()

    def _remove_document(self, doc: Document) -> None:
        """Close one document (no questions: ``maybe_save`` was asked before)."""
        self._take_document(doc)
        if self.autosave is not None:
            self.autosave.discard(doc)  # it was saved or its changes dropped
        doc.session.close()
        doc.undo_stack.deleteLater()

    def _leave_document(self) -> None:
        """The current document is about to give way to another one: finish what's being
        typed, put half-used tools away and remember where its view was."""
        doc = self.document
        if doc is None:
            return
        self.markup.finish_editing()
        self.edit.finish_editing()
        self.forms.commit()
        doc.view_state = self.view.view_state()

    def _activate(self, doc: Document | None, *, first: bool = False) -> None:
        """Show this document (a click on its tab, the Window menu, …). ``first``: it was
        just opened (opens upright, panels that apply to it fly out, signatures checked)."""
        if doc is self.document and not first:
            return
        if self.document is not None and doc is not self.document and self.document in self.documents:
            self._leave_document()
        if self.document is None and doc is not None and self._side_wanted and not self.side_panel.is_open:
            self.side_panel.show_panel(self.side_panel.current)  # (wanted, as with the last document)
        self.document = doc
        self.undo_group.setActiveStack(doc.undo_stack if doc is not None else self._idle_stack)
        if doc is not None:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self.tabs.index_of(doc))
            self.tabs.blockSignals(False)
        self._show_document(first=first)

    def _on_tab_changed(self, index: int) -> None:
        doc = self.tabs.document(index)
        if doc is not None:
            self._activate(doc)

    def _on_tab_close(self, index: int) -> None:
        doc = self.tabs.document(index)
        if doc is None:
            return
        if doc is not self.document:
            self._activate(doc)  # (so the question about unsaved changes shows the right file)
        self.close_document()

    def _on_tabs_moved(self, _from: int, _to: int) -> None:
        self.documents = [self.tabs.document(n) for n in range(self.tabs.count())]

    def _update_tabs(self) -> None:
        from planetpdf.ui import file_locks

        file_locks.sync([doc.session.path for _w, doc in self.all_documents()])  # (open here only)
        for n, doc in enumerate(self.documents):
            index = self.tabs.index_of(doc)
            if index >= 0:
                self.tabs.setTabText(index, doc.name + (" *" if doc.is_dirty() else ""))
                self.tabs.setTabToolTip(index, str(doc.session.path))
        self.tabs.setVisible(bool(self.documents) and self.settings.show_document_tabs)
        if hasattr(self, "act_move_to_new_window"):
            self.act_move_to_new_window.setEnabled(len(self.documents) > 1)
            self.act_next_tab.setEnabled(len(self.documents) > 1)
            self.act_previous_tab.setEnabled(len(self.documents) > 1)

    def _toggle_tabs(self, on: bool) -> None:
        self.settings.show_document_tabs = on
        for window in self.open_windows():
            window.act_show_tabs.setChecked(on)
            window._update_tabs()

    def next_tab(self, step: int = 1) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab (Acrobat): the next / previous document."""
        if len(self.documents) > 1 and self.document in self.documents:
            index = (self.documents.index(self.document) + step) % len(self.documents)
            self._activate(self.documents[index])

    def _tab_menu(self, pos: QPoint) -> None:
        """Right-click a tab: Move to New Window, Close, Close Other Tabs."""
        doc = self.tabs.document(self.tabs.tabAt(pos))
        if doc is not None:
            self.tab_menu(doc).exec(self.tabs.mapToGlobal(pos))

    def tab_menu(self, doc: Document) -> QMenu:
        menu = QMenu(self)
        popout = menu.addAction(fa.icon("arrow-up-right-from-square"), "Move to New Window",
                                lambda: self.move_document(doc))
        popout.setEnabled(len(self.documents) > 1)
        menu.addSeparator()
        menu.addAction("Close", lambda: self._on_tab_close(self.tabs.index_of(doc)))
        others = menu.addAction("Close Other Tabs", lambda: self.close_other_documents(doc))
        others.setEnabled(len(self.documents) > 1)
        return menu

    def close_other_documents(self, keep: Document) -> bool:
        for doc in [d for d in self.documents if d is not keep]:
            self._activate(doc)
            if not self.close_document():
                return False
        return True

    def move_document(self, doc: Document | None = None, target: MainWindow | None = None, *,
                      index: int | None = None, at: QPoint | None = None) -> MainWindow | None:
        """Window ▸ Move to New Window, a tab's right-click menu, a tab dragged out of the
        window or onto another one: the document goes there with its unsaved changes and
        its undo history. ``target`` None = a new window (at ``at``, if given)."""
        doc = doc or self.document
        if doc is None or doc not in self.documents or target is self:
            return None
        if target is None and len(self.documents) == 1:
            return None  # (it's in a window of its own already)
        self._activate(doc)
        self._take_document(doc)  # (its recovery copy goes along: it's kept on the document)
        if target is None:
            target = self.new_window()
            if at is not None:
                target.move(at - QPoint(target.width() // 3, 20))
                target.ensure_on_screen()
        doc.undo_stack.setParent(target)
        rebind(doc.undo_stack, target)
        target._insert_document(doc, index if index is not None else len(target.documents))
        target._activate(doc)
        target.bring_forward()
        if not self.documents and len(self.open_windows()) > 1:
            self.close()  # (its last tab was dragged into another window)
        return target

    def _tab_torn_off(self, index: int, global_pos: QPoint) -> None:
        """A tab was dragged out of the tab bar and let go: onto another PlanetPDF window
        moves it there (where its tabs are, at that place), anywhere else a new window."""
        doc = self.tabs.document(index)
        if doc is None:
            return
        target = self.window_at(global_pos)
        if target is self:
            return
        if target is not None:
            spot = target.tabs.tabAt(target.tabs.mapFromGlobal(global_pos)) if target.tabs.isVisible() else -1
            self.move_document(doc, target, index=spot if spot >= 0 else None)
        else:
            self.move_document(doc, at=global_pos)

    @classmethod
    def window_at(cls, global_pos: QPoint) -> MainWindow | None:
        """The PlanetPDF window under the mouse (the one in front)."""
        widget = QApplication.widgetAt(global_pos)
        window = widget.window() if widget is not None else None
        if isinstance(window, MainWindow):
            return window
        for window in reversed(cls.open_windows()):  # (offscreen, or the widget lookup missed)
            if window.frameGeometry().contains(global_pos):
                return window
        return None

    def _show_document(self, *, first: bool = False) -> None:
        """Show the current document (or none) in every part of the window."""
        doc = self.document
        session = self.session
        if session is None:
            self.side_panel.collapse()  # nothing to show in it
        for action in self.side_panel.actions.values():
            action.setEnabled(session is not None)
        # (a file opens upright: Rotate View isn't saved; switching back keeps its view)
        self.view.set_session(session, upright=first, state=None if first or doc is None else doc.view_state)
        for page_list in self.page_lists():
            page_list.set_session(session)
        for window in (getattr(self, "_loupe", None), getattr(self, "_pan_zoom", None)):
            if window is not None:
                window.close()
        if hasattr(self, "act_read_activate"):
            self._read_aloud_toggled(self.act_read_activate.isChecked())
        if hasattr(self, "attachments_panel"):  # (a portfolio opens on its list of files)
            from planetpdf.core.batch import attachments, is_portfolio

            self.attachments_panel.refresh()
            if first and session is not None and (is_portfolio(session.doc) or attachments(session.doc)):
                self._attachments_dock.show()
                self._attachments_dock.raise_()
        if self.act_reflow.isChecked():
            if session is None:
                self.act_reflow.trigger()  # (back to the page view)
            else:
                self.reflow_view().refresh()
        if hasattr(self, "tool_panes"):  # e.g. Prepare Form's start page gives way to the new file
            self.show_center(self.tool_panes.current)
        count = session.page_count if session else 0
        current = max(0, self.view.current_page) if session else 0
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, count))
        self.page_spin.setValue(current + 1)
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f" / {count} ")
        self.side_panel.update_page(current, count)
        if session is not None:
            self.thumbnails.set_current_page(current)
        self._update_document_actions()
        self._update_title()
        if session is None:
            self.act_redact_area.setChecked(False)
        self._refresh_marks()
        self.forms.document_opened()
        self.markup.set_tool(None)
        self.markup.refresh()
        self.text_tools.act_select.setChecked(False)
        self.text_tools.clear_selection()
        self.find_bar.close_bar() if self.find_bar.isVisible() else None
        self._refresh_navigation()
        self._update_tabs()
        self._name_page_view()
        if session is not None and session.path is not None:  # (for screen readers)
            pages = f"{count} page{'s' if count != 1 else ''}"
            accessibility.announce(self.view, f"{session.path.name}, {pages}, page {current + 1}")
        if not first:  # (switching tabs: the signatures were checked when it opened)
            self.signatures_panel.show_statuses(doc.signature_statuses or [] if doc is not None else [])
            return
        if session is not None and self.bookmarks_panel.bookmarks:  # a file with bookmarks opens on them
            self.side_panel.show_panel("bookmarks")
        if session is not None and self.forms.fields:
            self.show_tool_panel(self._forms_dock, unless_tool_has_one=True)
        if session is not None and session.has_digital_signatures:
            doc.signature_statuses = self.check_signatures()
        else:
            self.signatures_panel.show_statuses([])
        if session is not None and M.list_marks(session):
            self.show_tool_panel(self._redact_dock, unless_tool_has_one=True)
            self.statusBar().showMessage(
                "This document has unapplied redaction marks. Review them in the Redaction panel.", 10000
            )
        elif session is not None and session.has_digital_signatures:
            self.statusBar().showMessage(
                "This document is digitally signed. Edits are saved after the signed version, which stays in the "
                "file as it was.",
                10000,
            )

    def is_dirty(self) -> bool:
        # All UI edits go through the undo stack (see ui/commands.py), so its clean
        # state is the saved state; undoing back to the save point means "not dirty".
        return self.session is not None and not self.undo_stack.isClean()

    # --- pages ------------------------------------------------------------------

    def pages_changed(self, mapping: list[int | None], focus: int, select: list[int] | None = None) -> None:
        """Called by page commands after the page structure changed."""
        count = self.session.page_count
        focus = max(0, min(focus, count - 1))
        self.view.apply_page_mapping(mapping, focus)
        for page_list in self.page_lists():
            page_list.apply_page_mapping(mapping, select or [focus])
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(count)
        self.page_spin.setValue(focus + 1)
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f" / {count} ")
        self.side_panel.update_page(focus, count)
        self._refresh_marks()  # marks move with their pages
        self.forms.refresh()  # so do form fields
        self.markup.refresh()  # and markup
        self.text_tools.clear_selection()
        self._refresh_navigation()

    # --- helpers used by controllers ------------------------------------------------

    def push_command(self, command: QUndoCommand) -> bool:
        """Pushes ``command`` (False: a signed document's question was answered Cancel)."""
        return self._push(command)

    def check_for_updates(self) -> None:
        """Help ▸ Check for Updates (user 2026-10-04; ui/updates.py)."""
        from planetpdf.ui import updates

        updates.manager(self).check_now(self)

    def show_message(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def ask(self, title: str, text: str) -> bool:
        answer = QMessageBox.warning(
            self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def ask_three(self, title: str, text: str) -> bool | None:
        """Yes -> True, No -> False, Cancel -> None."""
        answer = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return None
        return answer == QMessageBox.StandardButton.Yes

    def replace_files(self, paths, title: str) -> bool:
        """Files a command writes besides the one chosen in a Save dialog (split parts, a picture per
        page): when some are already there, asks before replacing them (security review 2026-11-27:
        they were written over without a word). True: go on."""
        taken = [p for p in paths if Path(p).exists()]
        if not taken:
            return True
        names = "\n".join(f"  {Path(p).name}" for p in taken[:6]) + ("\n  …" if len(taken) > 6 else "")
        return self.ask(title, f"{len(taken)} of the files to be written are already in {Path(taken[0]).parent}:\n"
                               f"{names}\n\nReplace them?")

    def ask_output_protection(self, what: str):
        """A new PDF made from a password-protected document (redacted copy, exported or
        split pages, a smaller copy…): protect it too? Returns the ``Protection`` to apply,
        None for no protection, or False when the user canceled (backlog item, 2026-10-02).
        Passwords set this session are reused as they are; otherwise the protect dialog
        opens with what's known (the open password, the permissions)."""
        from planetpdf.core.cert_encryption import CertificateProtection

        protection, complete = self.session.known_protection()
        if protection is None:
            return None
        if isinstance(protection, CertificateProtection):
            question = (f"{self.session.path.name} is encrypted for certain people "
                        f"({', '.join(protection.names)}).\n\nEncrypt the {what} for them too?")
        else:
            question = (f"{self.session.path.name} is password protected.\n\nProtect the {what} too?"
                        + ("" if complete else "\n\nYou'll be asked for the passwords to use (the permissions "
                                                "password isn't known from the original)."))
        answer = self.ask_three("Protection", question)
        if answer is None:
            return False
        if not answer:
            return None
        if complete:
            return protection
        dialog = ProtectDialog(self)
        dialog.prefill(protection)
        if dialog.exec() != ProtectDialog.DialogCode.Accepted:
            return False
        return dialog.protection()

    def report_error(self, what: str, exc: BaseException) -> None:
        """An unexpected error: tell the user (nothing was changed) and log the details
        (profile/logs/planetpdf.log) so they can be sent in."""
        from planetpdf.ui.errors import log_exception

        log_file = log_exception(self.profile.root, what, exc)
        self.statusBar().showMessage(f"{what} failed: {exc}", 10000)
        QMessageBox.warning(
            self, what,
            f"{what} didn't work, and nothing was changed:\n\n{type(exc).__name__}: {exc}\n\n"
            f"The details are in {log_file} — please send that file in.",
        )

    def document_replaced(self) -> None:
        """The session's document object was swapped for a snapshot (undo/redo of a
        DocumentSnapshotCommand): rebuild everything from it, keeping the undo history and
        where the view was (user bug 2026-10-02: every move of an object on page 2 jumped
        the view back to page 1)."""
        self.view.set_session(self.session, state=self.view.view_state())
        for page_list in self.page_lists():
            page_list.set_session(self.session)
        self._refresh_marks()
        self.forms.refresh()
        self.markup.refresh()
        self._refresh_navigation()
        self._update_title()

    def commit_point(self, message: str) -> None:
        """A non-undoable change was made to the document (flatten, etc.): clear undo
        history, mark the document changed, and redraw everything."""
        self.undo_stack.clear()
        self.undo_stack.resetClean()
        self.view.set_session(self.session, state=self.view.view_state())  # (the view stays where it was)
        for page_list in self.page_lists():
            page_list.set_session(self.session)
        self._refresh_marks()
        self.forms.refresh()
        self.markup.refresh()
        self._refresh_navigation()
        self._update_title()
        self.statusBar().showMessage(message, 8000)

    # --- redaction ----------------------------------------------------------------

    def add_marks(self, specs: list[M.MarkSpec]) -> None:
        if self.session is not None and specs:
            self.show_tool_panel(self._redact_dock)
            self.undo_stack.push(AddMarksCommand(self, specs))

    def remove_marks(self, mark_ids: list[str]) -> None:
        if self.session is not None and mark_ids:
            self.undo_stack.push(RemoveMarksCommand(self, mark_ids))

    def marks_changed(self, pages: set[int], select: set[str] | None = None) -> None:
        """Called by mark commands: re-render affected pages and refresh the lists."""
        for page in pages:
            self.view.refresh_page(page)
            for page_list in self.page_lists():
                page_list.refresh_page(page)
        self._refresh_marks(select or set())

    def _refresh_marks(self, select: set[str] | None = None) -> None:
        overlays: dict[int, list[tuple[str, list[QRectF]]]] = {}
        if self.session is not None:
            for mark in M.list_marks(self.session):
                page = self.session.doc[mark.page]
                rects = []
                for r in mark.rects:
                    shown = M.to_display(page, r)
                    rects.append(QRectF(shown.x0, shown.y0, shown.width, shown.height))
                overlays.setdefault(mark.page, []).append((mark.mark_id, rects))
        self.view.set_mark_overlays(overlays)
        if select is not None:
            self.view.set_selected_marks(select)
        self.redaction_panel.refresh(select=self.view.selected_marks)

    def _on_area_drawn(self, page_index: int, rect: QRectF) -> None:
        if self.session is None:
            return
        page = self.session.doc[page_index]
        unrotated = M.from_display(page, (rect.left(), rect.top(), rect.right(), rect.bottom()))
        try:
            spec = M.make_spec(page_index, [unrotated], "Area")
        except PdfError:
            return  # too small
        self.add_marks([spec])

    def redact_pages(self, text: str | None = None) -> bool:
        """Redact ▸ Redact Pages: mark whole pages (as displayed) for redaction."""
        if self.session is None:
            return False
        from planetpdf.core.pagerange import PageRangeError, format_page_ranges, parse_page_ranges

        if text is None:
            text, ok = QInputDialog.getText(
                self, "Redact Pages", "Pages to redact completely (e.g. 1, 3-5):",
                text=format_page_ranges(self.target_pages()),
            )
            if not ok:
                return False
        try:
            pages = parse_page_ranges(text, self.session.page_count)
        except PageRangeError as exc:
            self.show_message("Redact Pages", str(exc))
            return False
        specs = []
        for index in pages:
            page = self.session.doc[index]
            shown = page.rect
            specs.append(M.make_spec(index, [M.from_display(page, (shown.x0, shown.y0, shown.x1, shown.y1))], "Page"))
        if specs:
            self.add_marks(specs)
        return bool(specs)

    def sanitize_document(self, options=None) -> bool:
        """Sanitize Document / Remove Hidden Information: on this document, undoable
        (Save keeps it). Redaction output is sanitized separately, on its copy."""
        if self.session is None:
            return False
        from planetpdf.core.redaction.ocg import LayerError
        from planetpdf.core.redaction.sanitize import sanitize

        signed = self.session.has_digital_signatures
        choice = "here"
        if options is None:
            dialog = SanitizeDialog(0, 0, signed, self, sanitize_only=True)
            if dialog.exec() != SanitizeDialog.DialogCode.Accepted:
                return False
            options, choice = dialog.options(), dialog.choice
        if not options.any():
            return False
        if signed and choice == "copy":
            return self.save_sanitized_copy(options)
        result = {}

        def apply(session) -> None:
            # (a signed file: its signatures go too, so Save writes a whole new file rather than
            # adding to the signed one, which would keep all of this: security review 2026-11-25)
            result["removed"] = sanitize(session.doc, options, remove_signatures=signed).removed
            session.signatures_changed()

        try:
            command = DocumentSnapshotCommand.applied(self, apply, "Sanitize Document")
        except LayerError as exc:
            self.show_message("Sanitize Document", f"Nothing was changed. {exc}\n\nPlanetPDF stopped rather than "
                              "show what the hidden layers hold. To remove everything else, untick Hidden layers.")
            return False
        except Exception as exc:
            self.report_error("Sanitize Document", exc)
            return False
        self.undo_stack.push(command)  # (the signatures question was answered in the dialog)
        from planetpdf.core.redaction.sanitize import REMOVED_LABELS

        removed = [f"{REMOVED_LABELS.get(k, (k,))[0]}: {v}" for k, v in result.get("removed", {}).items() if v]
        self.statusBar().showMessage(
            "Sanitized. " + ("; ".join(removed) if removed else "Nothing hidden was found.") + " Undo puts it back.", 10000
        )
        return True

    def save_sanitized_copy(self, options) -> bool:
        """A signed document's sanitized copy, as a whole new file without the signatures (the
        signed file stays as it is). Protected like the other new files made from a protected
        document: asked first (security review 2026-11-27, finding 6: a certificate-encrypted file's
        copy was written unencrypted, without a word)."""
        from planetpdf.core.document import WHOLE_FILE, save_pdf, write_whole
        from planetpdf.core.redaction.ocg import LayerError
        from planetpdf.core.redaction.sanitize import REMOVED_LABELS, sanitize

        session = self.session
        target = choose_save_path(self, "Save Sanitized Copy As", session.path.with_name(f"{session.path.stem}_sanitized.pdf"))
        if target is None:
            return False
        if target.resolve() == session.path.resolve():
            self.show_message("Save Sanitized Copy", "Choose another name: the signed file stays as it is.")
            return False
        protection = self.ask_output_protection("sanitized copy")
        if protection is False:
            return False
        copy = session.work_copy()
        try:
            removed = sanitize(copy.doc, options, remove_signatures=True).removed
            if protection is None:
                write_whole(copy.doc, target, encryption=pymupdf.PDF_ENCRYPT_NONE)
            else:  # (encrypted as it is written)
                save_pdf(copy.doc, target, protection, **WHOLE_FILE)
        except LayerError as exc:
            self.show_message("Save Sanitized Copy", f"No copy was saved. {exc}\n\nPlanetPDF stopped rather than "
                              "show what the hidden layers hold. To remove everything else, untick Hidden layers.")
            return False
        except Exception as exc:
            self.report_error("Save Sanitized Copy", exc)
            return False
        finally:
            copy.close()
        self.settings.last_directory = str(target.parent)
        done = [f"{REMOVED_LABELS.get(k, (k,))[0]}: {v}" for k, v in removed.items() if v]
        self.statusBar().showMessage(f"Saved a sanitized copy, {target.name}. " + "; ".join(done), 10000)
        return True

    def _on_mark_clicked(self, mark_id: str, additive: bool) -> None:
        selected = self.view.selected_marks
        if not mark_id:
            selected = set()
        elif additive:
            selected ^= {mark_id}
        else:
            selected = {mark_id}
        self.view.set_selected_marks(selected)
        self.redaction_panel.select_marks(selected)

    def _delete_selected_marks(self) -> None:
        """Delete key in the page view: selected markup (or text, image or field in Edit /
        Prepare Form) first, else redaction marks."""
        if self.view.selected_markup and self.edit.mode is not None:
            self.edit.delete()
            return
        if self.view.selected_markup and self.markup.delete():
            return
        ids = sorted(self.view.selected_marks)
        if ids:
            self.remove_marks(ids)

    # --- markup, stamps, signatures --------------------------------------------------------

    def _dock(self, title: str, name: str, widget: QWidget) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        return dock

    def show_comments_panel(self) -> None:
        self.show_tool_panel(self._comments_dock)

    def tool_panel_docks(self) -> list[QDockWidget]:
        """The panels that belong to one tool (redaction, comments, form, signatures, Edit
        PDF). Only one shows at a time: they swap as the tool / mode changes (user
        2026-10-01: "don't keep tiling them"). Pages, bookmarks and undo history are
        not tool panels and stay as you leave them."""
        return [self._redact_dock, self._comments_dock, self._forms_dock, self._signatures_dock, self._edit_dock,
                self._form_design_dock]

    def show_tool_panel(self, dock: QDockWidget | None, *, unless_tool_has_one: bool = False) -> None:
        """Fly out ``dock`` and close the other tool panels (None: close them all).
        ``unless_tool_has_one``: leave things alone while the open tool has its own panel
        (e.g. opening a signed file while in Comment keeps the comments panel)."""
        panes = getattr(self, "tool_panes", None)
        if unless_tool_has_one and panes is not None and panes.panel_for(panes.current) is not None:
            return
        for other in self.tool_panel_docks():
            if other is not dock:
                other.hide()
        if dock is not None:
            dock.show()
            dock.raise_()

    def _refresh_navigation(self) -> None:
        """Links and bookmarks (after opening, page changes, saves)."""
        self.text_tools.refresh_links()
        self.bookmarks_panel.refresh()

    # --- Document menu ---------------------------------------------------------------------

    def content_changed(self, pages: set[int]) -> None:
        """Page content was rewritten (headers, watermarks, content edits): redraw and
        forget anything cached about the text."""
        for page in pages:
            self.view.refresh_page(page)
            for page_list in self.page_lists():
                page_list.refresh_page(page)
        self.text_tools.clear_selection()
        self.markup.refresh()
        self._refresh_marks()
        if self.find_bar.isVisible() and self.find_bar.field.text():
            self.find_bar.search()

    def fields_designed(self, pages: set[int]) -> None:
        """Called by Prepare Form commands: fields were created, changed or deleted."""
        for page in pages:
            if 0 <= page < self.session.page_count:
                self.view.refresh_page(page)
                for page_list in self.page_lists():
                    page_list.refresh_page(page)
        self.forms.refresh()
        self.edit.refresh()

    def protection_changed(self) -> None:
        pending = self.session.pending_protection
        if pending is None:
            text = "Password protection unchanged."
        elif pending == REMOVE_PROTECTION:
            text = "Password protection will be removed when you save."
        else:
            text = "Password protection will be applied when you save."
        self.statusBar().showMessage(text, 8000)

    def protect_dialog(self) -> None:
        dialog = ProtectDialog(self)
        if dialog.exec() == ProtectDialog.DialogCode.Accepted:
            self.push_command(SetProtectionCommand(self, dialog.protection(), "Add Password Protection"))

    def remove_protection(self) -> None:
        if self.session is None:
            return
        if not self.session.is_protected and self.session.pending_protection is None:
            self.show_message("Remove Password Protection", "This document isn't password protected.")
            return
        self.push_command(SetProtectionCommand(self, REMOVE_PROTECTION, "Remove Password Protection"))

    def header_footer_dialog(self, preset: str) -> None:
        dialog = HeaderFooterDialog(self.session.page_count, self.selected_pages(), preset, self)
        if dialog.exec() != HeaderFooterDialog.DialogCode.Accepted:
            return
        pages, spec = dialog.pages(), dialog.spec()
        if not spec.texts:
            self.show_message(dialog.windowTitle(), "Enter some text for at least one position.")
            return
        label = {"page_numbers": "Add Page Numbers", "bates": "Add Bates Numbers"}.get(preset, "Add Header & Footer")
        self.push_command(PageContentCommand(self, pages, lambda s: add_header_footer(s, pages, spec), label))

    def watermark_dialog(self) -> None:
        dialog = WatermarkDialog(self.session.page_count, self.selected_pages(), self)
        if dialog.exec() != WatermarkDialog.DialogCode.Accepted:
            return
        pages, spec = dialog.pages(), dialog.spec()
        try:
            from planetpdf.core.page_marks import _watermark_source

            _watermark_source(spec)  # validate before it becomes an undo step
        except PdfError as exc:
            self.show_message("Watermark", str(exc))
            return
        self.push_command(PageContentCommand(self, pages, lambda s: add_watermark(s, pages, spec), "Add Watermark"))

    # --- links (Edit PDF ▸ Link) -------------------------------------------------------

    def link_tool(self) -> None:
        """Edit PDF ▸ Link: stays on for the next link until another tool (user 2026-10-17)."""
        if self.edit.mode is not None and self.edit.tool is not None:
            self.edit.set_tool(None)
        self.markup.pick_box(self._link_at_box, "Drag a box where the link goes, or click a link to change it. "
                             "Choose the arrow or another tool to stop.", keep=True, name="link")

    def _link_at_box(self, page_index: int, rect) -> None:
        """A box was drawn (or a click: a small box around it): edit the link under its
        middle, or make a new one for the box."""
        from planetpdf.core import links as L
        from planetpdf.ui.commands import LinksCommand
        from planetpdf.ui.dialogs.link import DELETE, LinkDialog

        box = pymupdf.Rect(rect)
        existing = L.link_at(self.session, page_index, ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))
        dialog = LinkDialog(self.session.page_count, existing, self)
        result = dialog.exec()
        if result == DELETE and existing is not None:
            self.push_command(LinksCommand(self, [(page_index, existing)], [], "Delete Link"))
        elif result == LinkDialog.DialogCode.Accepted:
            area = pymupdf.Rect(existing["from"]) if existing else box
            new = L.link_dict(area, **dialog.target())
            removed = [(page_index, existing)] if existing else []
            self.push_command(LinksCommand(self, removed, [(page_index, new)], "Edit Link" if existing else "Add Link"))

    def remove_links_dialog(self) -> None:
        from planetpdf.core import links as L
        from planetpdf.ui.commands import LinksCommand
        from planetpdf.ui.dialogs.link import RemoveLinksDialog

        dialog = RemoveLinksDialog(self.session.page_count, self.selected_pages(), self)
        if dialog.exec() != RemoveLinksDialog.DialogCode.Accepted:
            return
        found = L.links_on(self.session, dialog.pages())
        if not found:
            self.show_message("Remove Links", "There are no links on those pages.")
            return
        self.push_command(LinksCommand(self, found, [], f"Remove {len(found)} Link{'s' if len(found) != 1 else ''}"))

    def links_changed(self) -> None:
        self.text_tools.refresh_links()

    def choose_attachment(self) -> str:
        """Comment ▸ Attach file: which file (tests replace this)."""
        path, _ = QFileDialog.getOpenFileName(self, "Attach File", self.settings.last_directory, "All files (*)")
        return path

    def save_attachment(self, name: str, data: bytes) -> Path | None:
        """Double-click an attached file: save a copy of it (never opened: it could run code)."""
        # (the name is the document's: only its last part, never a folder or a server share it
        # names; security review 2026-11-25)
        start = Path(self.settings.last_directory or Path.home()) / plain_file_name(name)
        path = choose_save_path(self, "Save Attachment", start, "All files (*)", suffix=None)
        if not path:
            return None
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            QMessageBox.critical(self, "Save Attachment", f"The file could not be saved:\n\n{exc}")
            return None
        from planetpdf.core.zone_mark import carry_mark

        # (the PDF's own "came from the internet" mark goes with it, so the system warns before the
        # file is opened, as for the PDF; security review 2026-11-27)
        marked = carry_mark(self.session.path if self.session is not None else None, Path(path))
        self.statusBar().showMessage(f"Saved {Path(path).name}" + (
            ", marked as from the internet, like the PDF it came from." if marked else "."), 8000)
        return Path(path)

    # --- Organize ▸ Replace, ▸ More ▸ Page Labels ------------------------------------------

    def page_labels_dialog(self) -> None:
        from planetpdf.core.page_labels import LabelRule, get_rules, set_rules
        from planetpdf.ui.commands import PageLabelsCommand
        from planetpdf.ui.dialogs.page_tools import PageLabelsDialog

        old = get_rules(self.session)
        dialog = PageLabelsDialog(self.session.page_count, old, self)
        if dialog.exec() != PageLabelsDialog.DialogCode.Accepted:
            return
        new = dialog.rules()
        if new and not any(r.start == 0 for r in new):
            new.append(LabelRule(0))  # (pages before the first range: 1, 2, 3)
        try:  # validate before it becomes an undo step
            set_rules(self.session, new)
            set_rules(self.session, old)
        except PdfError as exc:
            self.show_message("Page Labels", str(exc))
            return
        if new != old:
            self.push_command(PageLabelsCommand(self, old, new))

    def replace_pages_dialog(self) -> None:
        from planetpdf.core.pagerange import PageRangeError, parse_page_ranges
        from planetpdf.ui.dialogs.page_tools import ReplacePagesDialog

        dialog = ReplacePagesDialog(self.session.page_count, self.selected_pages(), self.settings.last_directory, self)
        if dialog.exec() != ReplacePagesDialog.DialogCode.Accepted or dialog.path is None:
            return
        try:
            originals = dialog.original.pages()
        except PageRangeError as exc:
            self.show_message("Replace Pages", str(exc))
            return
        self.replace_pages(originals, dialog.path, dialog.replacement.text())

    def replace_pages(self, originals: list[int], path: Path, their_pages: str = "all") -> bool:
        """``originals`` (0-based) go; the chosen pages of ``path`` come in where the first
        of them was. One undo step (insert first, so even replacing every page works)."""
        from planetpdf.core.pagerange import PageRangeError, parse_page_ranges

        other = self.open_other_pdf(path, "Replace Pages")
        if other is None:
            return False
        try:
            chosen = parse_page_ranges(their_pages or "all", other.page_count)
            out = pymupdf.open()
            for p in chosen:  # (in the order typed)
                out.insert_pdf(other, from_page=p, to_page=p)
            data = out.tobytes(garbage=1, deflate=True)
            out.close()
        except (PageRangeError, PdfError) as exc:
            self.show_message("Replace Pages", f"{path.name}: {exc}")
            return False
        finally:
            other.close()
        originals = sorted(set(originals))
        at = originals[0]
        self.undo_stack.beginMacro(f"Replace {len(originals)} Page{'s' if len(originals) != 1 else ''}")
        try:
            self._push(InsertPdfPagesCommand(self, data, at, path.name, None))
            self._push(DeletePagesCommand(self, [p + len(chosen) for p in originals]))
        finally:
            self.undo_stack.endMacro()
        return True

    def background_dialog(self, spec=None, pages=None) -> None:
        """Edit PDF ▸ More ▸ Background ▸ Add (a color or picture under the pages)."""
        from planetpdf.core.page_marks import _background_source, add_background
        from planetpdf.ui.dialogs.document_tools import BackgroundDialog

        dialog = BackgroundDialog(self.session.page_count, pages or self.selected_pages(), self)
        if spec is not None:
            dialog.load(spec)
        if dialog.exec() != BackgroundDialog.DialogCode.Accepted:
            return
        pages, spec = dialog.pages(), dialog.spec()
        try:
            _background_source(spec, 100, 100)  # validate before it becomes an undo step
        except PdfError as exc:
            self.show_message("Background", str(exc))
            return
        self.push_command(PageContentCommand(self, pages, lambda s: add_background(s, pages, spec), "Add Background"))

    MARK_NAMES = {"header": "header and footer", "bates": "Bates numbers", "watermark": "watermark",
                  "background": "background"}
    MARK_TITLES = {"header": "Header & Footer", "bates": "Bates Numbers", "watermark": "Watermark",
                   "background": "Background"}

    def _no_marks_found(self, kind: str) -> None:
        self.show_message(
            "Nothing to " + ("remove" if kind == "bates" else "change"),
            f"No {self.MARK_NAMES[kind]} added by PlanetPDF was found in this document.\n\n"
            "Ones added by other software (or by an earlier PlanetPDF) are part of the page "
            "content and can't be told apart from it.")

    def remove_page_marks(self, kind: str) -> int:
        """… ▸ Remove: takes PlanetPDF's header/footer, Bates numbers, watermark or
        background off every page (one undo step)."""
        from planetpdf.core import page_marks as PM

        pages = PM.pages_with(self.session, kind)
        if not pages:
            self._no_marks_found(kind)
            return 0
        self.push_command(PageContentCommand(self, pages, lambda s: PM.remove_marks(s, kind, pages),
                                             f"Remove {self.MARK_TITLES[kind]}"))
        self.statusBar().showMessage(f"Removed the {self.MARK_NAMES[kind]} from {len(pages)} page(s).", 6000)
        return len(pages)

    def update_page_marks(self, kind: str) -> None:
        """… ▸ Update: the Add dialog filled in with the settings used; OK replaces them
        (removed from the pages that had them, added to the pages chosen now)."""
        from planetpdf.core import page_marks as PM
        from planetpdf.ui.dialogs.document_tools import BackgroundDialog

        saved = PM.saved_spec(self.session, kind)
        if saved is None:
            self._no_marks_found(kind)
            return
        spec, old_pages = saved
        if kind == "header":
            dialog = HeaderFooterDialog(self.session.page_count, old_pages, "header", self)
            add = PM.add_header_footer
        elif kind == "watermark":
            dialog = WatermarkDialog(self.session.page_count, old_pages, self)
            add = PM.add_watermark
        else:
            dialog = BackgroundDialog(self.session.page_count, old_pages, self)
            add = PM.add_background
        dialog.setWindowTitle(f"Update {self.MARK_TITLES[kind]}")
        dialog.load(spec)
        dialog.buttons.button(dialog.buttons.StandardButton.Ok).setText("Update")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        new_pages, new_spec = dialog.pages(), dialog.spec()
        if kind == "header" and not new_spec.texts:
            self.show_message(dialog.windowTitle(), "Enter some text for at least one position.")
            return

        def apply(s):
            PM.remove_marks(s, kind, old_pages)
            add(s, new_pages, new_spec)

        touched = sorted(set(old_pages) | set(new_pages))
        self.push_command(PageContentCommand(self, touched, apply, f"Update {self.MARK_TITLES[kind]}"))

    def crop_dialog(self) -> None:
        dialog = CropDialog(self.session.page_count, self.selected_pages(), self)
        result = dialog.exec()
        if result == CropDialog.DRAW:
            self.markup.pick_box(
                lambda page, rect: self.push_command(CropCommand(self, [page], rect=rect)),
                "Drag a box around the part of the page to keep. Esc cancels.",
            )
            return
        if result != CropDialog.DialogCode.Accepted:
            return
        margins, pages = dialog.values(), dialog.pages()
        if not any(margins):
            return
        try:
            for index in pages:  # validate first: commands must not fail in redo()
                shown = self.session.doc[index].rect
                if shown.width - margins[0] - margins[2] < 36 or shown.height - margins[1] - margins[3] < 36:
                    raise PdfError(f"Those margins leave almost nothing of page {index + 1}.")
        except PdfError as exc:
            self.show_message("Crop Pages", str(exc))
            return
        self.push_command(CropCommand(self, pages, margins=margins))

    def crop_by_drawing(self) -> None:
        """Scan & OCR's Crop Pages button (Acrobat: "Click and select area"): drag a box
        and that page is cropped to it at once (one undo step; Crop Pages… for many pages)."""
        if self.session is None:
            return
        self.markup.pick_box(
            lambda page, rect: self.push_command(CropCommand(self, [page], rect=rect)),
            "Crop Pages: drag a box around the part of the page to keep. Esc cancels.",
            name="crop",  # (one and done)
        )

    def split_dialog(self) -> None:
        from planetpdf.core.pagerange import PageRangeError, parse_page_ranges

        has_marks = any(entry[0] == 1 for entry in self.session.doc.get_toc(simple=True))
        dialog = SplitDialog(self.session.page_count, has_marks, self)
        if dialog.exec() != SplitDialog.DialogCode.Accepted:
            return
        try:
            if dialog.every.isChecked():
                groups = [("", g) for g in P.split_groups_every(self.session.page_count, dialog.every_n.value())]
            elif dialog.ranges.isChecked():
                parts = [p for p in dialog.ranges_edit.text().split(";") if p.strip()]
                groups = [("", parse_page_ranges(p, self.session.page_count)) for p in parts]
                if not groups:
                    raise PdfError("Enter the page groups, e.g. 1-3; 4-10.")
            else:
                groups = P.split_groups_by_bookmarks(self.session)
        except (PdfError, PageRangeError) as exc:
            self.show_message("Split Document", str(exc))
            return
        start = self.session.path.parent
        while True:
            folder = QFileDialog.getExistingDirectory(self, "Save the Parts In", str(safe_start(start / "x").parent))
            if not folder or not refuse_temporary(self, Path(folder) / "part.pdf"):
                break
        if not folder:
            return
        self.split_to(groups, Path(folder))

    def split_to(self, groups, folder: Path) -> list[Path]:
        from planetpdf.ui.tasks import run_in_background

        if not self.replace_files(P.split_names(groups, folder, self.session.path.stem), "Split Document"):
            return []
        written: list[Path] = []
        protection = self.ask_output_protection("new files")
        if protection is False:
            return []
        copy = self.session.work_copy()  # (unsaved changes included)

        def work(progress):  # (each file encrypted as it's written: security review 2026-11-25)
            written[:] = P.split_document(copy, groups, folder, copy.path.stem, progress, protection)
            return written

        try:
            # "Writing file x of y", in the background on its own copy (user 2026-10-23)
            run_in_background(self, "Split Document", f"Splitting {copy.path.name}", work)
        except TaskCanceled:
            self.statusBar().showMessage(f"Split canceled after {len(written)} of {len(groups)} files, in {folder}.", 8000)
            return written
        except (PdfError, OSError) as exc:
            QMessageBox.critical(self, "Split Document", str(exc))
            return []
        finally:
            copy.close()
        self.statusBar().showMessage(f"Split into {len(written)} files in {folder}.", 8000)
        return written

    def reduce_size_dialog(self, *, in_place: bool = True) -> None:
        """Compress PDF (the tool, File ▸ Compress PDF, Save as Other ▸ Optimized PDF)."""
        from planetpdf.core.optimize import scanned_images

        if self.session is None:
            return
        size = self.session.path.stat().st_size if self.session.path.exists() else 0
        scanned = len(set(scanned_images(self.session.doc).values()))
        signed = self.session.has_digital_signatures
        dialog = ReduceSizeDialog(size, self, pages=self.session.page_count, scanned=scanned,
                                  in_place=in_place and not signed)
        if dialog.exec() != ReduceSizeDialog.DialogCode.Accepted:
            return
        if dialog.in_place.isChecked():
            self.compress_document(dialog.options(), size)
            return
        default = self.session.path.with_name(f"{self.session.path.stem}_smaller.pdf")
        path = choose_save_path(self, "Save Smaller Copy As", default)
        if path:
            self.reduce_size_to(path, dialog.options())

    def compress_document(self, options, before: int = 0) -> bool:
        """Compress this document in place: done on a copy in the background, then
        swapped in as one undo step ("Compress PDF"); Save writes it."""
        from planetpdf.core.optimize import compressed_bytes
        from planetpdf.ui.commands import DocumentSnapshotCommand

        if not self._confirm_signature_change():  # (a signed document: its signatures would break)
            return False
        session = self.session
        snapshot = session.snapshot()
        before = before or len(snapshot)

        def work(progress):
            return compressed_bytes(session.open_snapshot(snapshot), options, progress)

        try:
            data, done = run_in_background(self, "Compress PDF", f"Compressing {session.path.name}", work,
                                           cancelable=False)  # (MuPDF's picture rewrite can't stop part way)
        except Exception as exc:
            QMessageBox.critical(self, "Compress PDF", str(exc))
            return False
        if len(data) >= before:
            self.show_message("Compress PDF", "This document is already compact: compressing it wouldn't make it "
                                              "smaller, so nothing was changed.")
            return False
        try:
            command = DocumentSnapshotCommand.applied(self, lambda s: s.restore_snapshot(data), "Compress PDF")
        except Exception as exc:
            self.report_error("Compress PDF", exc)
            return False
        self.push_command(command)  # (its redo shows the new document)
        saved = 100.0 * (before - len(data)) / before if before else 0
        scans = []
        if done.black_white:
            scans.append(f"{done.black_white} scanned page{'s' if done.black_white != 1 else ''} in black and white")
        if done.gray:
            scans.append(f"{done.gray} in gray")
        self.show_message(
            "Compress PDF",
            f"Before: {before / 1_048_576:.2f} MB\nAfter (once saved): about {len(data) / 1_048_576:.2f} MB "
            f"({saved:.0f}% smaller)" + (f"\n\n{', '.join(scans).capitalize()}." if scans else "")
            + "\n\nSave to keep it; Undo puts the document back as it was.")
        return True

    def space_usage_dialog(self) -> None:
        from planetpdf.core.optimize import space_usage
        from planetpdf.ui.dialogs.document_tools import SpaceUsageDialog

        if self.session is None:
            return
        size = self.session.path.stat().st_size if self.session.path.exists() else 0
        SpaceUsageDialog(space_usage(self.session.doc), size, self).exec()

    def reduce_size_to(self, target: Path, options):
        if target.resolve() == self.session.path:
            self.show_message("Reduce File Size", "Choose a new file name; the smaller copy can't replace the open document.")
            return None
        protection = self.ask_output_protection("smaller copy")
        if protection is False:
            return None
        snapshot = self.session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        before = self.session.path.stat().st_size if self.session.path.exists() else len(snapshot)

        def work(progress):  # (encrypted as it's written: security review 2026-11-25)
            return reduce_size(pymupdf.open(stream=snapshot, filetype="pdf"), target, options, before, progress,
                               protection)

        try:
            result = run_in_background(
                self, "Reduce File Size", f"Making a smaller copy: {target.name}", work,
                cancelable=False,  # (MuPDF's image rewrite can't be stopped part way)
            )
        except Exception as exc:
            QMessageBox.critical(self, "Reduce File Size", str(exc))
            return None
        self.show_message(
            "Reduce File Size",
            f"Saved {target.name}\n\nBefore: {result.before / 1_048_576:.2f} MB\nAfter: {result.after / 1_048_576:.2f} MB"
            + (f"  ({result.saved_percent:.0f}% smaller)" if result.saved_percent > 0 else "  (this file was already compact)"),
        )
        return result

    def properties_dialog(self) -> None:
        from planetpdf.core import properties as PR

        old = PR.get_metadata(self.session)
        dialog = PropertiesDialog(old, PR.facts(self.session), self)
        if dialog.exec() == PropertiesDialog.DialogCode.Accepted and dialog.values() != old:
            self.push_command(SetMetadataCommand(self, old, dialog.values()))

    def combine_dialog(self, *, images_only: bool) -> None:
        dialog = CombineDialog(self, images_only=images_only, ocr_available=ocr.find_tessdata() is not None,
                               start_dir=self.settings.last_directory)
        if dialog.exec() != CombineDialog.DialogCode.Accepted:
            return
        files, options = dialog.files(), dialog.options()
        self.remember_source_folder(files[0])
        default = self.new_pdf_folder(files[0]) / ("Scans.pdf" if images_only else "Combined.pdf")
        target = choose_save_path(self, "Save New PDF As", default)
        if not target:
            return
        if self.combine_files(files, target, options) and dialog.ocr.isChecked():
            self.run_ocr(list(range(self.session.page_count)), self.settings.ocr_language, self.settings.ocr_dpi)

    def combine_files(self, files: list[Path], target: Path, options) -> bool:
        """Combine into ``target`` (asking for passwords as needed) and open the result. When any of
        the files is protected, asks whether to protect the combined PDF too, as for other new files
        made from protected ones (security review 2026-11-27: it was written unprotected unasked)."""
        options.passwords = dict(options.passwords or {})
        protection = self.ask_combined_protection(files)
        if protection is False:
            return False
        while True:
            try:
                run_in_background(
                    self, "Combine Files", f"Creating {target.name}",
                    lambda progress: combine_files(files, target, options, progress, protection),
                    describe=lambda d, t: f"Creating {target.name}\nAdding file {min(d + 1, t)} of {t}",
                )
                break
            except PasswordRequired as exc:
                name = str(exc).split(" is password")[0]
                password, ok = QInputDialog.getText(self, "Password Required", f"{exc}\n\nPassword:", QLineEdit.EchoMode.Password)
                if not ok:
                    return False
                options.passwords[name] = password
            except (TaskCanceled, PdfError, OSError) as exc:
                if not isinstance(exc, TaskCanceled):
                    QMessageBox.critical(self, "Combine Files", str(exc))
                return False
        self.settings.last_directory = str(target.parent)
        return self.open_file(target)

    def ask_combined_protection(self, files: list[Path]):
        """Protection for a PDF combined from ``files``: None when none is protected or the answer is
        No, False on Cancel."""
        protected = []
        for path in files:
            if Path(path).suffix.lower() != ".pdf":
                continue
            try:
                with pymupdf.open(path) as doc:
                    if doc.is_encrypted:
                        protected.append(Path(path).name)
            except Exception:  # noqa: BLE001 (not readable: combining says why)
                continue
        if not protected:
            return None
        which = protected[0] if len(protected) == 1 else f"{protected[0]} and {len(protected) - 1} more"
        answer = self.ask_three("Protection", f"{which} {'is' if len(protected) == 1 else 'are'} password "
                                              "protected.\n\nProtect the combined PDF too?")
        if answer is None:
            return False
        if not answer:
            return None
        dialog = ProtectDialog(self)
        if dialog.exec() != ProtectDialog.DialogCode.Accepted:
            return False
        return dialog.protection()

    def create_signature(self, kind: str) -> bool:
        """Capture a new signature/initials and save it to the profile."""
        dialog = SignatureCaptureDialog(kind, self.settings.user_name, self)
        if dialog.exec() != SignatureCaptureDialog.DialogCode.Accepted or dialog.png() is None:
            return False
        self.signature_library.add(dialog.png(), kind, dialog.label())
        return True

    def manage_signatures(self) -> None:
        SignatureManagerDialog(self).exec()

    def manage_stamps(self) -> None:
        StampManagerDialog(self).exec()

    def preferences_dialog(self) -> None:
        PreferencesDialog(self).exec()

    def save_settings_now(self) -> None:
        try:
            save_settings(self.settings, self._config_dir)
        except OSError as exc:
            self.statusBar().showMessage(f"Preferences couldn't be saved: {exc}", 8000)

    def check_signatures(self) -> list:
        if self.session is None:
            self.signatures_panel.show_statuses([])
            return []
        try:
            path, trust = self.session.path, self.validation_trust()
            if self.settings.check_revocation:  # (online: in the background, with a window saying so)
                from planetpdf.ui.tasks import run_in_background

                statuses = run_in_background(
                    self, "Digital Signatures", "Checking signatures",
                    lambda _progress: validate_signatures(path, trust, check_revocation=True), cancelable=False,
                    describe=lambda d, t: "Checking the signatures, and asking online whether the signers' "
                                          "certificates were revoked…")
            else:
                statuses = validate_signatures(path, trust)
        except PdfError as exc:
            self.signatures_panel.show_statuses([], str(exc))
            self.show_tool_panel(self._signatures_dock, unless_tool_has_one=True)
            return []
        self.signatures_panel.show_statuses(statuses)
        if statuses:
            self.show_tool_panel(self._signatures_dock, unless_tool_has_one=True)
            worst = max(statuses, key=lambda st: list(Verdict).index(st.verdict))
            self.statusBar().showMessage(f"Digitally signed: {worst.summary}", 12000)
        return statuses

    def _read_aloud_toggled(self, on: bool) -> None:
        """Read Out Loud's commands work once it's activated (Acrobat); off stops reading."""
        for action in self.read_actions:
            action.setEnabled(on and self.session is not None)
        if on and self.session is not None:
            self.extras.prepare_speaker()  # (the voice loads meanwhile; once per run)
        if not on:
            self.extras.stop_reading()

    def save_as_standard(self, standard: str, target: Path | None = None, *, tell: bool = True) -> Path | None:
        """Save as Other ▸ Archivable PDF (PDF/A-2b) / Press-Ready (PDF/X-3) / PDF/E-1, by
        Ghostscript on a copy (the open document doesn't change). What Ghostscript doesn't
        fix (scripts, media, comments that don't print, trim boxes) is fixed on the copy
        first (``compliance.fix_copy``); ``last_conversion`` = (those changes, Ghostscript's
        notes). ``tell=False``: no message at the end (Check Compliance shows it itself)."""
        from planetpdf.core import compliance, ghostscript

        name = ghostscript.STANDARDS[standard]
        if ghostscript.find() is None:
            self.show_message(name, "This needs Ghostscript, which isn't installed on this computer.\n\n"
                                    "Ask IT to install it (Ghostscript from ghostscript.com), then try again.")
            return None
        if target is None:
            suffix = {"pdfa": "_pdfa", "pdfx": "_pdfx", "pdfe": "_pdfe"}[standard]
            suggested = self.session.path.with_name(f"{self.session.path.stem}{suffix}.pdf")
            target = choose_save_path(self, f"Save as {name}", suggested)
            if not target:
                return None
        if target.resolve() == self.session.path:
            self.show_message(name, "Choose a new file name: the converted copy can't replace the open document.")
            return None
        if self.session.is_protected and not self.ask(
                name, f"{self.session.path.name} is protected, but {name} files can't be encrypted: the new file will "
                      "open without a password and have none of its limits.\n\nSave it anyway?"):
            return None  # (it was dropped without a word: security review 2026-11-27)
        snapshot = self.session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)

        def work(progress):
            doc = pymupdf.open(stream=snapshot, filetype="pdf")
            try:
                fixed = compliance.fix_copy(doc, standard)
                return fixed, ghostscript.convert_standard(doc, target, standard, progress)
            finally:
                doc.close()

        try:
            fixed, notes = run_in_background(self, name, f"Saving as {name}: {target.name}", work)
        except TaskCanceled:
            self.statusBar().showMessage("Canceled.", 4000)
            return None
        except PdfError as exc:
            QMessageBox.critical(self, name, str(exc))
            return None
        self.settings.last_directory = str(target.parent)
        changed = [n.strip() for n in notes if n.strip() and not n.strip().startswith("Page ")]
        self.last_conversion = (fixed, changed)
        if not tell:
            return target
        text = f"Saved {target.name} as {name}."
        if standard in ("pdfa", "pdfx"):
            text += " It can't be password protected (the standard doesn't allow it)."
        if fixed or changed:
            text += "\n\nChanged to meet the standard:\n" + "\n".join(f"• {n}" for n in (fixed + changed)[:12])
        self.show_message(name, text)
        return target

    def check_compliance_dialog(self, profile: str = "pdfa") -> None:
        """PDF Standards ▸ Check Compliance (ui/dialogs/compliance.py): checks at once on opening."""
        if self.session is None:
            return
        from planetpdf.ui.dialogs.compliance import ComplianceDialog

        dialog = ComplianceDialog(self, profile)
        dialog.run()
        dialog.exec()

    def validation_trust(self):
        """Roots trusted when checking signatures: the certificates imported in Preferences,
        plus (on Windows, unless switched off there) the roots Windows trusts."""
        import sys

        from planetpdf.core.signing.providers.base import CombinedTrustStore

        stores = [self.trust_store]
        from planetpdf.core.signing.providers import system_store

        if system_store.available() and self.settings.trust_windows_roots:  # (what Windows / macOS trusts)
            stores.append(system_store.trust_store())
        return CombinedTrustStore(*stores)

    def digitally_sign(self, certify: bool = False) -> None:
        """Sign (or, Save as Other ▸ Certified PDF, certify) the saved document."""
        if self.session is None:
            return
        title = "Certify Document" if certify else "Digitally Sign"
        if self.session.is_encrypted:
            self.show_message(title, "Password-protected PDFs can't be signed yet. Save an unprotected copy first.")
            return
        if certify and self.session.has_digital_signatures:
            self.show_message(title, "This document is already signed. A certification must be the first signature.")
            return
        if self.is_dirty():
            if not self.ask("Save First", "The document has unsaved changes. They must be saved before signing.\n\nSave now?"):
                return
            if not self.save():
                return
        dialog = DigitalSignDialog(self, certify=certify)
        if dialog.exec() != DigitalSignDialog.DialogCode.Accepted or dialog.provider is None:
            return
        provider, show_image = dialog.provider, dialog.show_image.isChecked()
        level = dialog.level.currentData() if certify else ""
        if certify and not dialog.visible.isChecked():  # (an invisible certification: no box)
            self.finish_digital_sign(provider, 0, None, show_image, certify=level)
            return
        self.markup.pick_box(
            lambda page, rect: self.finish_digital_sign(provider, page, rect, show_image, certify=level),
            "Drag a box where the signature should appear. Esc cancels.",
        )

    def finish_digital_sign(self, provider, page: int, rect, show_image: bool, target: Path | None = None,
                            certify: str = "") -> bool:
        source = self.session.path
        if target is None:
            suggested = source.with_name(f"{source.stem}_{'certified' if certify else 'signed'}.pdf")
            target = choose_save_path(self, "Save Certified Document As" if certify else "Save Signed Document As",
                                      suggested)
            if not target:
                return False
        s = self.settings
        image = None
        if show_image and self.signature_library.default("signature") is not None:
            image = self.signature_library.png(self.signature_library.default("signature"))
        options = SignOptions(reason=s.sign_reason, location=s.sign_location, timestamp_url=s.timestamp_url,
                              signature_image=image, certify=certify)
        in_place = target.resolve() == source
        tab = self.documents.index(self.document)
        if in_place:
            self._remove_document(self.document)  # the file is replaced; Windows won't allow that while it's open
        try:
            run_in_background(
                self,
                "Digital Signature",
                "Signing",
                lambda _progress: sign_pdf(source, target, provider, page, rect, options),
                cancelable=False,
                describe=lambda d, t: "Signing the document…" + ("\nContacting the timestamp server…" if s.timestamp_url else ""),
            )
        except Exception as exc:
            if in_place:
                self.open_file(source, tab)
            QMessageBox.critical(self, "Signing Failed", str(exc))
            return False
        self.open_file(target, tab if in_place else None)
        self.statusBar().showMessage(f"{'Certified' if certify else 'Signed'} and saved as {target.name}.", 8000)
        return True

    def _toggle_area_tool(self, checked: bool) -> None:
        self.view.set_area_tool(checked)
        self.redaction_panel.area_button.blockSignals(True)
        self.redaction_panel.area_button.setChecked(checked)
        self.redaction_panel.area_button.blockSignals(False)
        if checked:
            self.show_tool_panel(self._redact_dock)
            self.view.setFocus()
            self.statusBar().showMessage("Drag a box over anything to redact. Choose the arrow or another tool "
                                         "to stop.", 6000)

    def _show_redaction_find(self) -> None:
        self.show_tool_panel(self._redact_dock)
        self.redaction_panel.search_edit.setFocus()
        self.redaction_panel.search_edit.selectAll()

    def apply_redactions(self) -> None:
        if self.session is None:
            return
        marks = M.list_marks(self.session)
        if not marks:
            QMessageBox.information(self, "Apply Redactions", "There are no redaction marks yet.")
            return
        dialog = SanitizeDialog(
            len(marks), len({m.page for m in marks}), self.session.has_digital_signatures, self
        )
        if dialog.exec() != SanitizeDialog.DialogCode.Accepted:
            return
        sanitize_options = dialog.options()

        suggested = self.session.path.with_name(f"{self.session.path.stem}_redacted.pdf")
        # (offered in Documents when the source is in a temporary folder; security review 2026-11-25)
        target = choose_save_path(self, "Save Redacted Copy As", suggested)
        if not target:
            return
        overwrite = target.resolve() == self.session.path
        if overwrite:
            answer = QMessageBox.warning(
                self,
                "Overwrite the Original?",
                f"You chose the original file, {target.name}.\n\nThe unredacted original will be "
                "replaced and can't be recovered. Undo history will be cleared.\n\n"
                "Overwrite the original anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        protection = self.ask_output_protection("redacted copy")
        if protection is False:
            return
        self.redact_to(target, sanitize_options, protection=protection)

    def redact_to(self, target: Path, sanitize_options, *, protection=None) -> bool:
        """Run the redaction and show the verification report. ``protection``: encrypt
        the result (it's checked first)."""
        overwrite = Path(target).resolve() == self.session.path
        self.redaction_panel.save_options()
        options = self.redaction_panel.options()
        self.statusBar().showMessage(f"Making a working copy of the {self.session.page_count} pages…")
        data = self.session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)  # (unsaved changes included)

        def work(progress):
            def counted(done: int, total: int, stage: str = "") -> bool:
                if stage.startswith("Writing the new file"):
                    progress.commit()  # (from here on the new file exists: no Cancel)
                return progress(done, total, stage) if stage else progress(done, total)

            return redact_copy(data, target, options, sanitize_options, progress=counted, protection=protection)

        from planetpdf.core.redaction.apply import RedactionCanceled, finish_redaction
        from planetpdf.ui.tasks import run_in_background

        try:
            # (in the background, on its own copy: the window keeps drawing; user 2026-10-23)
            finished = run_in_background(self, "Apply Redactions", f"Redacting to {Path(target).name}", work)
            if not finished.result.report.passed:
                # Failed checks: nothing is saved or replaced unless the user insists (never the
                # original; user 2026-11-21, a security review: "redaction fails open")
                held = VerificationReportDialog(finished.result, can_open=False, parent=self, held=True,
                                                can_keep=not overwrite)
                self._show_report(held)
                self.last_redaction = finished.result
                if not held.keep_requested:
                    finished.discard()
                    self.statusBar().showMessage("The redaction failed its checks, so nothing was saved.", 10000)
                    return False
                result = finish_redaction(self.session, finished)
                self.settings.last_directory = str(result.output.parent)
                self.statusBar().showMessage(f"Saved {result.output.name}, which FAILED its checks.", 10000)
                return False
            shown = False
            if overwrite and finished.result.report.warnings:
                # The original can't be got back: the warnings first (security review 2026-11-27)
                asked = VerificationReportDialog(finished.result, can_open=False, parent=self, held=True,
                                                 replacing=True)
                self._show_report(asked)
                self.last_redaction = finished.result
                if not asked.keep_requested:
                    finished.discard()
                    self.statusBar().showMessage("The original wasn't replaced; nothing was saved.", 10000)
                    return False
                shown = True
            result = finish_redaction(self.session, finished)
        except (TaskCanceled, RedactionCanceled):
            self.statusBar().showMessage("Redaction canceled; nothing was written.", 5000)
            return False
        except (PdfError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Redaction Failed", f"The redacted file could not be created:\n\n{exc}")
            return False
        self.statusBar().showMessage(result.report.summary(), 10000)
        self.settings.last_directory = str(result.output.parent)
        if overwrite:
            # The session now holds the redacted file: rebuild everything from it.
            self._signature_change_accepted = False
            self.undo_stack.clear()
            self.view.set_session(self.session)
            for page_list in self.page_lists():
                page_list.set_session(self.session)
            self.pages_changed(list(range(self.session.page_count)), 0)
            self._update_title()
        self.last_redaction = result
        if shown:
            return result.report.passed
        dialog = VerificationReportDialog(result, can_open=not overwrite, parent=self)
        self._show_report(dialog)
        if dialog.open_requested:
            self.open_file(result.output)
        return result.report.passed

    def _show_report(self, dialog: VerificationReportDialog) -> None:
        dialog.exec()

    def page_lists(self) -> tuple:
        """The sidebar thumbnails and the Organize Pages grid (both mirror the document)."""
        return (self.thumbnails, self.organizer)

    def selected_pages(self) -> list[int]:
        """Pages selected in the Organize Pages grid while it shows, else in the sidebar."""
        if self.central.currentWidget() is self.organizer:
            return self.organizer.selected_pages()
        return self.thumbnails.selected_pages()

    def show_organizer(self, on: bool) -> None:
        self.show_center("organize" if on else None)

    def center_for(self, tool: str | None) -> QWidget:
        """What the middle of the window shows for this open tool (Acrobat): the Organize
        grid, a start page, or the page view."""
        if not hasattr(self, "start_pages"):
            from planetpdf.ui import start_pages as SP

            self.start_pages = {"create": SP.create_pdf_page(self), "export": SP.ExportPage(self),
                                "prepare_form": SP.prepare_form_page(self)}
            for page in self.start_pages.values():
                self.central.addWidget(page)
        if tool == "organize" and self.session is not None:
            return self.organizer
        if tool in ("create", "export"):
            return self.start_pages[tool]
        if tool == "prepare_form" and (self.session is None or (
                not self.forms.fields and not getattr(self.document, "form_started", False))):
            return self.start_pages[tool]  # (no fields yet: Start, with field detection; Acrobat)
        return self.view

    def start_form(self, *, detect: bool, signatures: bool) -> int:
        """Prepare Form ▸ Start: edit this document's fields; ``detect``: first turn its
        blanks, boxes and lines into fields (one undo step). Returns fields made."""
        if self.session is None:
            return 0
        self.document.form_started = True
        made = 0
        if detect and not self.forms.fields:
            made = self.detect_form_fields(signatures=signatures)
        self.tool_panes.open("prepare_form")
        self.show_center("prepare_form")
        return made

    def detect_form_fields(self, *, signatures: bool = False, pages: list[int] | None = None) -> int:
        """Form field auto detection (Acrobat's Prepare Form): fields where the pages have
        blanks to fill in (one undo step)."""
        from planetpdf.core.form_detect import add_detected_fields, detect_fields
        from planetpdf.ui.commands import DocumentSnapshotCommand
        from planetpdf.ui.tasks import TaskCanceled, run_with_progress

        session = self.session
        pages = list(range(session.page_count)) if pages is None else pages
        try:
            found = run_with_progress(self, "Prepare Form", "Looking for form fields",
                                      lambda progress: detect_fields(session.doc, pages, signatures=signatures,
                                                                     progress=progress))
        except TaskCanceled:
            return 0
        if not found:
            self.statusBar().showMessage("No blanks or boxes to fill were found: add fields with the tools above.",
                                         8000)
            return 0
        try:
            command = DocumentSnapshotCommand.applied(self, lambda s: add_detected_fields(s, found),
                                                      "Detect Form Fields")
        except Exception as exc:
            self.report_error("Detect Form Fields", exc)
            return 0
        self.push_command(command)
        self.forms.refresh()
        self.statusBar().showMessage(f"Found {len(found)} form field{'s' if len(found) != 1 else ''}: check their "
                                     "names and places, then try them with Preview.", 10000)
        return len(found)

    def show_center(self, tool: str | None) -> None:
        widget = self.center_for(tool)
        if widget is self.central.currentWidget():
            return
        if self.act_reflow.isChecked():  # (another tool opened: Reflow ends)
            self.act_reflow.setChecked(False)
        if widget is self.organizer:
            self.organizer.select_pages(self.thumbnails.selected_pages() or [max(self.view.current_page, 0)])
        if hasattr(widget, "refresh"):
            widget.refresh()
        self.central.setCurrentWidget(widget)
        widget.setFocus()

    def target_pages(self) -> list[int]:
        """Pages selected in the sidebar (or Organize grid), or the current page if none."""
        selected = self.selected_pages()
        if selected:
            return selected
        return [self.view.current_page] if self.view.current_page >= 0 else []

    def _insert_position(self) -> int:
        """New pages go after the last targeted page."""
        pages = self.target_pages()
        return pages[-1] + 1 if pages else (self.session.page_count if self.session else 0)

    def _push(self, command: QUndoCommand) -> bool:
        check = getattr(command, "check", None)
        if check is not None:
            check()  # (a command that would fail in redo raises here, with nothing pushed or changed)
        if not self._confirm_signature_change():
            # A command already done (``applied``) is taken back, so the document isn't left
            # changed with nothing to undo (security review 2026-11-25).
            if getattr(command, "_first", False) or getattr(command, "_after", None) is not None:
                command.undo()
            return False
        self.undo_stack.push(command)
        return True

    def _confirm_signature_change(self) -> bool:
        if self.session is None:
            return False
        if not self.session.has_digital_signatures or self._signature_change_accepted:
            return True
        answer = QMessageBox.warning(
            self,
            "Signed Document",
            "This document is digitally signed. Changes are saved after its signed version, which stays in the "
            "file as it was: what you delete or change can still be read there. Adding, removing, or moving pages "
            "invalidates its signatures.\n\nTo keep nothing of the signed version, use Protect ▸ Remove Hidden "
            "Information ▸ Remove Signatures and Sanitize instead.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        self._signature_change_accepted = answer == QMessageBox.StandardButton.Yes
        return self._signature_change_accepted

    def move_pages(self, pages: list[int], dest: int) -> None:
        if self.session is None or not pages:
            return
        order = P.plan_move(self.session.page_count, pages, dest)
        if order == list(range(self.session.page_count)):
            return  # dropped where it already was
        self._push(MovePagesCommand(self, order, pages))

    def receive_pages(self, data: bytes, dest: int, name: str, count: int) -> bool:
        """Pages dragged here from another document's window (user 2026-10-02): copies
        go in at ``dest``, one undo step in this document; this window comes forward."""
        if self.session is None:
            return False
        command = InsertPdfPagesCommand(self, data, max(0, min(dest, self.session.page_count)), name, None)
        command.setText(f"Copy {count} Page{'s' if count != 1 else ''} from {name}")
        self._push(command)
        self.bring_forward()
        return True

    def copy_pages(self, pages: list[int], dest: int) -> None:
        """Shift+drag in Organize Pages or the Pages panel: copies of these pages (with
        their comments) inserted at ``dest``, one undo step."""
        if self.session is None or not pages:
            return
        try:
            data = P.pages_as_pdf(self.session, pages)
        except PdfError as exc:
            self.show_message("Copy Pages", str(exc))
            return
        command = InsertPdfPagesCommand(self, data, dest, "copy", None)
        command.setText(f"Copy {len(pages)} Page{'s' if len(pages) != 1 else ''}")
        self._push(command)

    def delete_selected_pages(self) -> None:
        if self.session is None:
            return
        pages = self.target_pages()
        if not pages:
            return
        if len(pages) >= self.session.page_count:
            QMessageBox.information(
                self, "Can't Delete", "A PDF must keep at least one page. Select fewer pages."
            )
            return
        self._push(DeletePagesCommand(self, pages))

    def insert_blank_page(self) -> None:
        if self.session is not None:
            self._push(InsertBlankPageCommand(self, self._insert_position()))

    def insert_pages_dialog(self) -> None:
        if self.session is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert Pages from File", self.settings.last_directory, PDF_FILTER
        )
        if path:
            self.insert_pages_from_file(Path(path), self._insert_position())

    def open_other_pdf(self, path: Path, title: str) -> pymupdf.Document | None:
        """Open another PDF (not as the document), asking for its password if needed."""
        try:
            data = path.read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, title, f"{path.name} could not be read:\n\n{exc}")
            return None
        password: str | None = None
        while True:
            try:
                return open_pdf_bytes(data, path.name, password)
            except (PasswordRequired, WrongPassword) as exc:
                password, ok = QInputDialog.getText(
                    self, "Password Required", f"{exc}\n\nEnter the password for {path.name}:", QLineEdit.EchoMode.Password
                )
                if not ok:
                    return None
            except PdfError as exc:
                QMessageBox.critical(self, title, str(exc))
                return None

    def compare_documents(self, other_path: Path | None = None, *, current_is_new: bool = True):
        """Compare the open document with another PDF (by default the other one is the
        older version) and show the changes."""
        if self.session is None:
            return None
        if other_path is None:
            name, _ = QFileDialog.getOpenFileName(self, "Compare With", self.settings.last_directory, PDF_FILTER)
            if not name:
                return None
            other_path = Path(name)
        other = self.open_other_pdf(other_path, "Compare Documents")
        if other is None:
            return None
        from planetpdf.core import compare as C
        from planetpdf.ui.dialogs.compare import CompareDialog

        old, new = (other, self.session.doc) if current_is_new else (self.session.doc, other)
        try:
            result = run_in_background(self, "Compare Documents", "Comparing documents", lambda progress: C.compare(old, new, progress))
        except TaskCanceled:
            other.close()
            return None
        if getattr(self, "_compare_dialog", None) is not None:
            self._compare_dialog.close()
        dialog = CompareDialog(self, other, other_path, result, current_is_new)
        self._compare_dialog = dialog
        dialog.finished.connect(lambda _r: setattr(self, "_compare_dialog", None))
        dialog.show()
        return dialog

    def insert_pages_from_file(self, path: Path, index: int) -> bool:
        if self.session is None:
            return False
        try:
            data = path.read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, "Can't Insert Pages", f"{path.name} could not be read:\n\n{exc}")
            return False
        password: str | None = None
        while True:  # validate (and unlock) before pushing: commands must not fail in redo()
            try:
                open_pdf_bytes(data, path.name, password).close()
                break
            except (PasswordRequired, WrongPassword) as exc:
                password, ok = QInputDialog.getText(
                    self,
                    "Password Required",
                    f"{exc}\n\nEnter the password for {path.name}:",
                    QLineEdit.EchoMode.Password,
                )
                if not ok:
                    return False
            except PdfError as exc:
                QMessageBox.critical(self, "Can't Insert Pages", str(exc))
                return False
        self._push(InsertPdfPagesCommand(self, data, index, path.name, password))
        return True

    def rotate_pages(self, pages: list[int], degrees: int) -> None:
        if self.session is not None and pages and degrees % 360:
            self._push(RotatePagesCommand(self, pages, degrees))

    def rotate_dialog(self) -> None:
        if self.session is None:
            return
        dialog = RotateDialog(self.session.page_count, self.selected_pages(), self)
        if dialog.exec() == RotateDialog.DialogCode.Accepted:
            self.rotate_pages(dialog.pages(), dialog.degrees())

    def export_pages_dialog(self) -> None:
        self.export_dialog(ExportFormat.PDF)

    def export_dialog(self, fmt: ExportFormat | None = None) -> None:
        if self.session is None:
            return
        s = self.settings
        try:
            fmt = fmt or ExportFormat(s.export_format)
        except ValueError:
            fmt = ExportFormat.PDF
        saved = ExportOptions(s.export_dpi, s.export_jpeg_quality, s.export_compress, s.export_ocr, s.ocr_language,
                              ai_reading=s.export_ai_reading)
        dialog = ExportDialog(
            self.session.page_count,
            self.selected_pages(),
            self.session.is_encrypted,
            ocr.find_tessdata() is not None,
            saved,
            fmt,
            self,
        )
        if dialog.exec() != ExportDialog.DialogCode.Accepted:
            return
        fmt, pages, options = dialog.fmt(), dialog.pages(), dialog.options()
        options.ocr_language = s.ocr_language
        s.export_format, s.export_dpi, s.export_jpeg_quality = fmt.value, options.dpi, options.jpeg_quality
        s.export_compress, s.export_ocr = options.compress_images, options.ocr_missing_text
        s.export_ai_reading = options.ai_reading

        suffix = {ExportFormat.PDF: "_pages", ExportFormat.IMAGE_PDF: "_images"}.get(fmt, "")
        suggested = self.session.path.with_name(f"{self.session.path.stem}{suffix}{fmt.extension}")
        target = choose_save_path(self, "Export", suggested, FILE_FILTERS[fmt], suffix=None)
        if not target:
            return
        if target.suffix.lower() not in (fmt.extension, ".jpeg", ".tiff"):
            target = target.with_name(target.name + fmt.extension)
        self.export_to(fmt, pages, target, options)

    def export_to(self, fmt: ExportFormat, pages: list[int], target: Path, options: ExportOptions) -> list[Path]:
        """Run an export (in the background for anything but plain PDF)."""
        if fmt is ExportFormat.PDF:
            return [target] if self.export_pages(pages, target) else []
        from planetpdf.core.export import output_files

        files = output_files(target, pages, fmt)
        if len(files) > 1 and not self.replace_files(files[1:] if files[0] == target else files, "Export"):
            return []  # (the file named was asked about by the Save dialog; a picture per page wasn't)
        protection = self.ask_output_protection("image-only PDF") if fmt is ExportFormat.IMAGE_PDF else None
        if protection is False:
            return []
        snapshot = self.session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        notes: list[str] = []

        def work(progress):
            doc = pymupdf.open(stream=snapshot, filetype="pdf")
            try:
                if protection is not None:  # (encrypted as it's written: security review 2026-11-25)
                    from planetpdf.core.export import export_image_pdf

                    return [export_image_pdf(doc, pages, target, options, progress, protection)]
                return export(doc, pages, target, fmt, options, progress, notes)
            finally:
                doc.close()

        # Word: core reports stages (Copying / OCR / Reading / Writing page x of y), shown
        # under the label; the other formats count the pages exported
        label = f"Converting to Word: {target.name}" if fmt is ExportFormat.DOCX else f"Exporting to {target.name}"
        try:
            files = run_in_background(
                self, "Export", label, work,
                describe=page_progress_text(label, pages, verb="Exporting page"),
            )
        except (TaskCanceled, ExportCanceled):
            self.statusBar().showMessage("Export canceled.", 4000)
            return []
        except Exception as exc:  # conversion libraries raise all sorts of errors
            QMessageBox.critical(self, "Export Failed", f"The export could not be completed:\n\n{exc}")
            return []
        self.settings.last_directory = str(target.parent)
        what = f"{len(files)} files" if len(files) > 1 else files[0].name
        self.statusBar().showMessage(f"Exported {len(pages)} page(s) to {what}", 6000)
        if notes:  # (Word: parts pdf2docx couldn't convert; shown here, not in the console)
            listed = "\n".join(f"• {note}" for note in notes[:12]) + ("\n• …" if len(notes) > 12 else "")
            self.show_message("Export", f"{files[0].name} was created, but some content couldn't be converted "
                                        f"and was left out:\n\n{listed}")
        return files

    # --- OCR ------------------------------------------------------------------------

    def ocr_dialog(self) -> None:
        if self.session is None:
            return
        languages = ocr.available_languages()
        if not languages:
            QMessageBox.warning(
                self,
                "Recognize Text (OCR)",
                "Text recognition needs Tesseract OCR, which wasn't found on this computer.\n\n"
                "Install Tesseract OCR (or set TESSDATA_PREFIX to its tessdata folder) and try again.",
            )
            return
        doc = self.session.doc
        missing = [i for i in range(self.session.page_count) if ocr.page_needs_ocr(doc, i)]
        dialog = OcrDialog(
            self.session.page_count,
            missing,
            self.selected_pages(),
            languages,
            self.settings.ocr_language,
            self.settings.ocr_dpi,
            self,
        )
        if dialog.exec() != OcrDialog.DialogCode.Accepted:
            return
        self.settings.ocr_language = dialog.language_code()
        self.settings.ocr_dpi = dialog.dpi_value()
        # crooked or specked scans: offer to straighten and clean them first (user 2026-10-03)
        if self.scan_tools.suggest_cleanup(dialog.pages()) is None:
            return
        self.run_ocr(dialog.pages(), dialog.language_code(), dialog.dpi_value())

    def run_ocr(self, pages: list[int], language: str, dpi: int) -> int:
        """OCR ``pages`` in the background, then add the text as one undo step."""
        pages = [p for p in pages if not ocr.page_has_ocr(self.session.doc, p)]
        if not pages:
            self.statusBar().showMessage("Those pages already have recognized text.", 5000)
            return 0
        snapshot = self.session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        try:
            words = run_in_background(
                self,
                "Recognize Text (OCR)",
                "OCR in progress",
                lambda progress: ocr.recognize_pages(snapshot, pages, language=language, dpi=dpi, progress=progress),
                describe=page_progress_text("OCR in progress", pages, verb="Recognizing text on page"),
            )
        except TaskCanceled:
            self.statusBar().showMessage("Text recognition canceled; nothing was changed.", 5000)
            return 0
        except PdfError as exc:
            QMessageBox.critical(self, "Recognize Text", str(exc))
            return 0
        found = {p: w for p, w in words.items() if w}
        if not found:
            self.statusBar().showMessage("No text was recognized on those pages.", 6000)
            return 0
        self.undo_stack.push(OcrTextCommand(self, found))
        total = sum(len(w) for w in found.values())
        self.statusBar().showMessage(f"Recognized {total} words on {len(found)} page(s).", 6000)
        return total

    def text_layer_changed(self, pages: set[int]) -> None:
        """OCR text added/removed: the pages look the same, but search results change."""
        self.redaction_panel.refresh(select=self.view.selected_marks)

    def export_pages(self, pages: list[int], target: Path) -> bool:
        protection = self.ask_output_protection("exported pages")
        if protection is False:
            return False
        try:
            P.export_pages(self.session, pages, target, protection)  # (encrypted as it's written)
        except (PdfError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export Failed", f"The pages could not be exported:\n\n{exc}")
            return False
        self.settings.last_directory = str(target.parent)
        count = len(pages)
        self.statusBar().showMessage(
            f"Exported {count} page{'s' if count != 1 else ''} to {target.name}", 6000
        )
        return True

    def print_error_box(self, exc: PrintError) -> QMessageBox:
        """The printer message with an "Open Printer Settings" button that opens the system's
        printer settings (Windows Settings / macOS System Settings)."""
        box = QMessageBox(QMessageBox.Icon.Warning, "Printer Unavailable", str(exc), QMessageBox.StandardButton.Ok,
                          self)
        open_settings = box.addButton("Open Printer Settings", QMessageBox.ButtonRole.ActionRole)
        open_settings.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(exc.settings_url)))
        box.setDefaultButton(QMessageBox.StandardButton.Ok)
        return box

    def print_dialog(self) -> None:
        if self.session is None:
            return
        try:
            used = print_with_dialog(
                self,
                self.session,
                max(self.view.current_page, 0),
                self.selected_pages(),
                self.settings.last_printer,
            )
        except PrintError as exc:
            if exc.settings_url:
                self.print_error_box(exc).exec()
            else:
                QMessageBox.warning(self, "Printer Unavailable", str(exc))
            return
        except Exception as exc:  # (a driver or the print system failing: never a crash)
            from planetpdf.ui.errors import log_exception

            log_file = log_exception(self.profile.root, "Print", exc)
            QMessageBox.warning(
                self, "Printing Didn't Work",
                "The printer couldn't be used, so nothing was printed.\n\nCheck that it is turned on and "
                f"connected, or choose another printer.\n\n(Details: {type(exc).__name__}: {exc}; "
                f"saved in {log_file})",
            )
            return
        if used:
            self.settings.last_printer = used
            self.statusBar().showMessage(f"Sent to {used}", 5000)

    # --- view layout -------------------------------------------------------------

    def set_layout(self, mode: LayoutMode) -> None:
        self.layout_actions[mode].setChecked(True)
        self.act_cover.setEnabled(mode.columns == 2)
        self.view.set_layout(mode)
        self.settings.view_layout = mode.value

    def _toggle_cover(self, checked: bool) -> None:
        self.settings.cover_page_separate = checked
        self.view.set_layout(self.view.layout_mode, cover_separate=checked)

    def _apply_saved_layout(self) -> None:
        try:
            mode = LayoutMode(self.settings.view_layout)
        except ValueError:
            mode = LayoutMode.SINGLE
        self.act_cover.setChecked(self.settings.cover_page_separate)
        self.view.set_layout(mode, cover_separate=self.settings.cover_page_separate)
        self.layout_actions[mode].setChecked(True)
        self.act_cover.setEnabled(mode.columns == 2)

    def _on_files_dropped_on_thumbnails(self, paths: list[Path], index: int) -> None:
        # Insert in the order dropped; each file goes after the previous one.
        for path in paths:
            before = self.session.page_count if self.session else 0
            if not self.insert_pages_from_file(path, index):
                break
            index += self.session.page_count - before

    def maybe_save(self) -> bool:
        """Ask about unsaved changes. Returns False if the user canceled."""
        if self.session is not None:
            self.markup.finish_editing()  # text still being typed on the page counts
            self.edit.finish_editing()
            self.forms.commit()
        if not self.is_dirty():
            return True
        answer = QMessageBox.warning(
            self,
            "Unsaved Changes",
            f"Save changes to {self.session.path.name} before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def save(self) -> bool:
        if self.session is None:
            return False
        from planetpdf.core.temp_folders import temporary_reason

        reason = temporary_reason(self.session.path)
        if reason is not None:  # (user 2026-11-24: never save where it will be deleted)
            answer = QMessageBox.warning(
                self, "Choose Where to Keep This File",
                f"{self.session.path.name} is in {reason}. Windows or the program that put it there "
                "deletes that folder's files without asking, and your changes with them.\n\n"
                "Choose a folder to save it in, such as Documents.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer != QMessageBox.StandardButton.Save:
                return False
            return self.save_as_dialog()
        return self._run_save(lambda allow, progress: self.session.save(allow_signature_loss=allow, progress=progress))

    def save_as_dialog(self) -> bool:
        if self.session is None:
            return False
        target = choose_save_path(self, "Save As", self.session.path)  # (never in a temporary folder)
        return self.save_as(target) if target else False

    def refuse_temporary(self, target: Path) -> bool:
        """True (after saying so) when ``target`` is in a folder that gets emptied by itself."""
        return refuse_temporary(self, target)

    def save_as(self, target: Path) -> bool:
        if self.session is None:
            return False
        if self.refuse_temporary(Path(target)):
            return False
        ok = self._run_save(lambda allow, progress: self.session.save_as(target, allow_signature_loss=allow,
                                                                         progress=progress))
        if ok:
            self.settings.last_directory = str(target.parent)
            self.settings.add_recent_file(self.session.path)
        return ok

    def _run_save(self, do_save) -> bool:
        self.forms.commit()  # a field being typed into counts
        self.markup.finish_editing()  # so does a text box being typed on the page
        self.edit.finish_editing()
        if not self.forms.before_save():  # hybrid XFA question
            return False
        try:
            try:
                self._saving(do_save, False)
            except SignaturesWouldBreak as exc:
                answer = QMessageBox.warning(
                    self,
                    "Signatures Will Be Invalidated",
                    f"{exc}\n\nSave anyway?",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Save:
                    return False
                self._saving(do_save, True)
        except (PdfError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Save Failed", f"The file could not be saved:\n\n{exc}")
            return False
        self.undo_stack.setClean()
        if self.autosave is not None:
            self.autosave.discard()
        self._signature_change_accepted = False
        self._update_title()
        self.forms.refresh()  # saving renumbers PDF objects; field keys include them
        self.markup.refresh()  # (and ids of other software's annotations)
        self._refresh_navigation()
        pending = len(M.list_marks(self.session))
        if pending:
            self.statusBar().showMessage(
                f"Saved {self.session.path.name} with {pending} UNAPPLIED redaction mark(s). "
                "The content under them has not been removed; use Apply Redactions.",
                12000,
            )
        else:
            report = self.session.font_report
            fonts_note = report.message() if report is not None else ""
            self.statusBar().showMessage(f"Saved {self.session.path.name}. {fonts_note}".strip(),
                                         10000 if fonts_note else 4000)
        self.session.font_report = None
        return True

    def _saving(self, do_save, allow_signature_loss: bool) -> None:
        """``do_save(allow, progress)``; a long save shows its steps (user 2026-10-23: no progress
        for Save): "Step 2 of 3: writing 120 pages…". Not cancelable once begun."""
        session = self.session
        try:
            size = session.path.stat().st_size
        except OSError:
            size = 0
        if session.page_count < SAVE_PROGRESS_PAGES and size < SAVE_PROGRESS_BYTES:
            do_save(allow_signature_loss, None)
            return
        from planetpdf.ui.tasks import run_with_progress

        run_with_progress(self, "Saving", f"Saving {session.path.name}",
                          lambda progress: do_save(allow_signature_loss, progress), cancelable=False)

    # --- UI state --------------------------------------------------------------

    def _update_document_actions(self) -> None:
        has_doc = self.session is not None
        for action in self._doc_actions:
            action.setEnabled(has_doc)
        for widget in self._doc_widgets:
            widget.setEnabled(has_doc)
        if hasattr(self, "act_back"):
            self._update_history_actions()  # (only when there's somewhere to go back to)

    def _update_title(self, *_args) -> None:
        if hasattr(self, "tabs"):
            self._update_tabs()  # (the "*" on a tab with unsaved changes)
        if self.session is None:
            self.setWindowTitle(APP_NAME)
            self.setWindowModified(False)
            return
        self.setWindowTitle(f"{self.session.path.name}[*] — {APP_NAME}")
        self.setWindowFilePath(str(self.session.path))
        self.setWindowModified(self.is_dirty())

    def _on_current_page_changed(self, index: int) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self.thumbnails.set_current_page(index)
        self.side_panel.update_page(index, self.session.page_count if self.session else 0)
        self._name_page_view()
        self._page_announcer.start()  # (said once scrolling settles, not for every page passed)

    def statusBar(self):  # noqa: N802 (Qt)
        """The status bar (made on first use, as Qt does); its messages are spoken to screen readers."""
        return accessibility.speak_status(super().statusBar())

    def _name_page_view(self) -> None:
        """What a screen reader says for the page area: the page, and how to read its text."""
        if self.session is None:
            self.view.setAccessibleName("No document open")
            self.view.setAccessibleDescription("Ctrl+O opens a PDF file")
            return
        self.view.setAccessibleName(f"Page {self.view.current_page + 1} of {self.session.page_count}")
        self.view.setAccessibleDescription("Ctrl+4 shows this page's text to read; Page Down and Page Up turn pages")

    def _announce_page(self) -> None:
        shown = self.central.currentWidget()  # (the page, or Reflow's text)
        if self.session is not None and shown is not None and shown.isVisible():
            accessibility.announce(shown, self.view.accessibleName())

    def _on_zoom_changed(self, zoom: float) -> None:
        mode = self.view.zoom_mode
        if mode is ZoomMode.FIT_WIDTH:
            text = FIT_WIDTH_TEXT
        elif mode is ZoomMode.FIT_PAGE:
            text = FIT_PAGE_TEXT
        else:
            text = f"{round(zoom * 100)}%"
        self.zoom_combo.setEditText(text)

    def _on_zoom_text(self, text: str) -> None:
        if text == FIT_WIDTH_TEXT:
            self.view.fit_width()
            return
        if text == FIT_PAGE_TEXT:
            self.view.fit_page()
            return
        try:
            value = float(text.strip().rstrip("%"))
        except ValueError:
            self._on_zoom_changed(self.view.zoom)
            return
        self.view.set_zoom(value / 100)

    # --- File menu (Acrobat) ---------------------------------------------------------

    RECENT_IN_MENU = 5

    @staticmethod
    def short_path(path: Path, limit: int = 48) -> str:
        """Like Acrobat: C:\\...\\Corporate structu...h EINs for SAM.pdf"""
        text = str(path)
        if len(text) <= limit:
            return text
        sep = "\\" if "\\" in text else "/"
        drive = path.anchor.rstrip("\\/")
        name = path.name
        room = max(limit - len(drive) - 4, 12)
        if len(name) > room:
            half = (room - 3) // 2
            name = name[:half] + "..." + name[-half:]
        return f"{drive}...{sep}{name}"

    def fill_recent_files(self, menu, before: QAction) -> None:
        """The numbered recent files on the File menu (rebuilt each time it opens)."""
        for action in getattr(self, "_recent_actions", []):
            menu.removeAction(action)
            action.deleteLater()
        self._recent_actions = []
        for n, path in enumerate([Path(p) for p in self.settings.recent_files][: self.RECENT_IN_MENU], 1):
            action = QAction(f"&{n} {self.short_path(path).replace('&', '&&')}", menu)  # (a '&' in a name isn't a shortcut)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _c=False, p=path: self.open_paths([p]))
            menu.insertAction(before, action)
            self._recent_actions.append(action)
        self.act_all_recent.setEnabled(bool(self.settings.recent_files))
        self.act_reopen_session.setEnabled(any(Path(p).is_file() for p in self.settings.last_session))

    def show_all_recent(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QVBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Recent Files")
        dialog.resize(640, 360)
        files = QListWidget(dialog)
        for p in self.settings.recent_files:
            files.addItem(p)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Close, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        files.itemDoubleClicked.connect(lambda _i: dialog.accept())
        layout = QVBoxLayout(dialog)
        layout.addWidget(files)
        layout.addWidget(buttons)
        if files.count():
            files.setCurrentRow(0)
        if dialog.exec() == QDialog.DialogCode.Accepted and files.currentItem() is not None:
            self.open_paths([Path(files.currentItem().text())])

    def reopen_last_session(self) -> bool:
        """The PDFs that were open when PlanetPDF last closed (each in its window)."""
        paths = [Path(p) for p in self.settings.last_session if Path(p).is_file()]
        if not paths:
            self.statusBar().showMessage("No PDF from the last session to reopen.", 4000)
            return False
        return bool(self.open_paths(paths))

    def revert(self) -> bool:
        """Throw away the changes since the last save and reload the file."""
        if self.session is None:
            return False
        if self.is_dirty() and not self.ask("Revert", f"Discard all changes to {self.session.path.name} since it was last saved?"):
            return False
        path, tab = self.session.path, self.documents.index(self.document)
        self._remove_document(self.document)
        return self.open_file(path, tab)  # (in the same tab place)

    def new_pdf_folder(self, source: Path | None = None) -> Path:
        """Where a new PDF is offered: beside the file it was made from (user 2026-11-24: "If I open
        a Word doc in a path, it should default to saving the file in the same path"), else the
        folder used last; never a temporary one."""
        from planetpdf.core.temp_folders import lasting_folder

        return lasting_folder(source.parent if source is not None else None, self.settings.last_directory)

    def remember_source_folder(self, source: Path) -> None:
        """The next Open / Create dialog starts in the folder this file was chosen from."""
        from planetpdf.core.temp_folders import is_temporary

        if not is_temporary(source.parent):
            self.settings.last_directory = str(source.parent)

    def _save_new_pdf(self, data: bytes, default_name: str, source: Path | None = None) -> bool:
        """Ask where to save a newly created PDF, write it and open it."""
        default = self.new_pdf_folder(source) / default_name
        target = choose_save_path(self, "Save New PDF As", default)
        if not target:
            return False
        try:
            target.write_bytes(data)
        except OSError as exc:
            QMessageBox.critical(self, "Create PDF", f"The new PDF couldn't be saved:\n\n{exc}")
            return False
        self.settings.last_directory = str(target.parent)
        return self.open_file(target)

    def create_pdf_from_file(self, path: Path | None = None) -> bool:
        from planetpdf.core.create import FILE_FILTER, pdf_from_file

        if not isinstance(path, (str, Path)):  # (from the menu, Qt passes "checked" = False here)
            name, _ = QFileDialog.getOpenFileName(self, "Create PDF from File", self.settings.last_directory, FILE_FILTER)
            if not name:
                return False
            path = Path(name)
        self.remember_source_folder(Path(path))
        try:
            data = self._office_pdf(Path(path)) if self._is_office(Path(path)) else pdf_from_file(path)
        except TaskCanceled:
            return False
        except PdfError as exc:
            QMessageBox.warning(self, "Create PDF", str(exc))
            return False
        return self._save_new_pdf(data, Path(path).stem + ".pdf", source=Path(path))

    @staticmethod
    def _is_office(path: Path) -> bool:
        from planetpdf.core import office

        return office.app_for(path) is not None

    def _office_pdf(self, path: Path) -> bytes:
        """A Word, Excel or PowerPoint file through Office itself (as Create Multiple PDF Files does;
        user report 2026-10-18: one Office file was refused here)."""
        import tempfile

        from planetpdf.core import office
        from planetpdf.ui.tasks import run_in_background

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / (path.stem + ".pdf")

            def work(progress):
                progress(0, 0, f"Converting {path.name} with Microsoft {office.app_for(path).title()}…")
                office.to_pdf(path, target)
                return target.read_bytes()

            return run_in_background(self, "Create PDF", f"Converting {path.name}", work, cancelable=False)

    def clipboard_pdf(self) -> bytes | None:
        """The clipboard's picture or text as a PDF (None if it has neither)."""
        from planetpdf.core.create import pdf_from_image, pdf_from_text
        from planetpdf.ui.clipboard_tools import image_png

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        try:
            if mime is not None and mime.hasImage() and not clipboard.image().isNull():
                return pdf_from_image(image_png(clipboard.image()))
            if mime is not None and mime.hasText() and mime.text().strip():
                return pdf_from_text(mime.text())
        except PdfError as exc:
            self.show_message("Clipboard", str(exc))
            return None
        self.show_message("Clipboard", "The clipboard has no picture or text to make a PDF from.")
        return None

    def create_pdf_from_clipboard(self) -> bool:
        data = self.clipboard_pdf()
        return data is not None and self._save_new_pdf(data, "Clipboard.pdf")

    def create_blank_pdf(self) -> bool:
        from planetpdf.core.create import blank_pdf

        return self._save_new_pdf(blank_pdf(), "Untitled.pdf")

    def insert_pages_from_clipboard(self) -> bool:
        if self.session is None:
            return False
        data = self.clipboard_pdf()
        if data is None:
            return False
        self._push(InsertPdfPagesCommand(self, data, self._insert_position(), "Clipboard", None))
        return True

    def start_redacting(self) -> None:
        """Edit ▸ Redact Text & Images: the redaction box tool and its panel."""
        self.show_tool_panel(self._redact_dock)
        self.act_redact_area.setChecked(True)

    def zoom_to_box(self) -> None:
        """Zoom To… (Ctrl+Y): straight into the zoom box."""
        self.zoom_combo.setFocus()
        self.zoom_combo.lineEdit().selectAll()

    def _content_bounds(self, page: int):
        """Fit Visible: where the page has anything drawn (cached per page content)."""
        from PySide6.QtCore import QRectF

        from planetpdf.core.render import content_bounds

        if self.session is None:
            return None
        key = (page, self.undo_stack.index(), id(self.session))
        cache = self.__dict__.setdefault("_bounds_cache", {})
        if key not in cache:
            box = content_bounds(self.session, page)
            if len(cache) > 50:
                cache.clear()
            cache[key] = None if box is None else QRectF(box[0], box[1], box[2] - box[0], box[3] - box[1])
        return cache[key]

    def _view_tool(self, name: str, on: bool) -> None:
        """Dynamic Zoom / Loupe Tool: tools that only change the view (Esc puts them away)."""
        if on:
            self.markup.set_tool(None)
            if self.edit.mode is not None:
                self.edit.set_tool(None)
            self.act_redact_area.setChecked(False)
            self.text_tools.act_select.setChecked(False)
            self.view.set_view_tool(name)
            self.view.setFocus()
            if name == "loupe":
                self.loupe_window().start()
            self.statusBar().showMessage(
                "Dynamic Zoom: drag up to zoom in, down to zoom out. Esc stops." if name == "dynamic_zoom"
                else "Loupe Tool: click or drag on the page to choose what the Loupe shows. Esc stops.", 8000)
        elif self.view.view_tool == name:
            self.view.set_view_tool(None)

    def _view_tool_changed(self, name: str) -> None:
        self.act_dynamic_zoom.setChecked(name == "dynamic_zoom")
        self.act_loupe.setChecked(name == "loupe")
        if name:
            self.act_select_tool.setChecked(False)
            self.act_hand_tool.setChecked(False)
        else:
            if not self.view.hand_tool:
                self.act_select_tool.setChecked(True)
            self.statusBar().clearMessage()
            if getattr(self, "_loupe", None) is not None and self._loupe.isVisible():
                self._loupe.close()

    def loupe_window(self):
        if getattr(self, "_loupe", None) is None:
            from planetpdf.ui.viewer.zoom_tools import LoupeWindow

            self._loupe = LoupeWindow(self)
            self.undo_group.indexChanged.connect(lambda _i: self._loupe.isVisible() and self._loupe.refresh())
        return self._loupe

    def pan_zoom_window(self):
        if getattr(self, "_pan_zoom", None) is None:
            from planetpdf.ui.viewer.zoom_tools import PanZoomWindow

            self._pan_zoom = PanZoomWindow(self)
        return self._pan_zoom

    def _show_pan_zoom(self) -> None:
        self.pan_zoom_window().start()

    def reflow_view(self):
        if getattr(self, "_reflow", None) is None:
            from planetpdf.ui.viewer.zoom_tools import ReflowView

            self._reflow = ReflowView(self)
            self._reflow.pageStep.connect(self.view.step_page)
            self.central.addWidget(self._reflow)
            self.view.currentPageChanged.connect(lambda _p: self._reflow.isVisible() and self._reflow.refresh())
            self.undo_group.indexChanged.connect(lambda _i: self._reflow.isVisible() and self._reflow.refresh())
        return self._reflow

    def _toggle_reflow(self, on: bool) -> None:
        """Ctrl+4: the page's text rewrapped to the window (again: back to the page)."""
        reflow = self.reflow_view()
        if on and self.central.currentWidget() is self.view:
            reflow.refresh()
            self.central.setCurrentWidget(reflow)
            reflow.setFocus()
        elif not on and self.central.currentWidget() is reflow:
            self.central.setCurrentWidget(self.view)
            self.view.setFocus()
        elif on:  # (another tool's page is in the middle: Reflow is for the pages)
            self.act_reflow.setChecked(False)

    def marquee_zoom(self) -> None:
        self.markup.pick_box(self._zoom_to_area, "Drag a box around the area to zoom in on. Esc cancels.",
                             name="zoom")  # (one and done)

    def _zoom_to_area(self, page_index: int, rect) -> None:
        from planetpdf.core.redaction.marks import to_display

        shown = to_display(self.session.doc[page_index], rect)
        self.view.zoom_to_rect(page_index, QRectF(shown.x0, shown.y0, shown.width, shown.height))

    def set_theme(self, theme: str) -> None:
        """View ▸ Display Theme: light, dark, or whatever the system uses (Qt's own colors)."""
        from planetpdf.ui.theme import apply_theme

        apply_theme(theme)
        self.settings.theme = theme
        self.theme_actions[theme].setChecked(True)

    def toggle_read_mode(self) -> None:
        """Read Mode (Ctrl+H): just the menu bar and the pages."""
        if getattr(self, "_read_mode_state", None) is not None:
            self.restoreState(self._read_mode_state)
            self._read_mode_state = None
            self.act_read_mode.setChecked(False)
            return
        self._read_mode_state = self.saveState()
        for toolbar in self._toolbars():
            toolbar.hide()
        for dock in self.findChildren(QDockWidget):
            dock.hide()
        self.act_read_mode.setChecked(True)

    def _update_history_actions(self) -> None:
        has_doc = self.session is not None
        self.act_back.setEnabled(has_doc and self.view.can_go_back())
        self.act_forward.setEnabled(has_doc and self.view.can_go_forward())

    # --- window events -----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.toLocalFile().lower().endswith(".pdf")]
        if paths:  # several files dropped: a tab each
            self.open_paths(paths)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.maybe_save_all():
            event.ignore()
            return
        # File ▸ Reopen PDFs from last session
        # (every document open now, with this one; or those open when File ▸ Exit began)
        if MainWindow._session_snapshot is not None:
            self.settings.last_session = list(MainWindow._session_snapshot)
        else:
            self.settings.last_session = [str(d.session.path) for _w, d in self.all_documents()]
        for doc in list(self.documents):
            self._remove_document(doc)
        self.redaction_panel.save_options()
        self.settings.show_thumbnails = self._side_wanted  # (the panel is closed now: no document)
        self.settings.side_panel = self.side_panel.current
        if self.side_panel.is_open:
            self.settings.side_panel_width = self.side_panel.width()
        self.settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.window_state = base64.b64encode(bytes(self.saveState(WINDOW_STATE_VERSION))).decode("ascii")
        try:
            save_settings(self.settings, self._config_dir)
        except OSError:
            pass  # never block closing on a settings write failure
        if self in MainWindow._windows:
            MainWindow._windows.remove(self)
        event.accept()

    def ensure_on_screen(self) -> str | None:
        """The profile roams between computers, so a saved position can be on a monitor
        this one doesn't have (the window opens out of sight). If too little of the
        window is on any screen, center it on the primary screen. Returns that screen's
        name when the window was moved."""
        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        if not screens or primary is None:
            return None
        frame = self.frameGeometry()
        visible = max((frame.intersected(s.availableGeometry()) for s in screens),
                      key=lambda r: r.width() * r.height())
        title_bar_reachable = any(s.availableGeometry().contains(frame.center().x(), frame.top() + 10) for s in screens)
        if visible.width() >= 200 and visible.height() >= 100 and title_bar_reachable:
            return None
        area = primary.availableGeometry()
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        width, height = min(self.width(), area.width() - 40), min(self.height(), area.height() - 60)
        self.resize(max(width, 600), max(height, 400))
        frame = self.frameGeometry()
        frame.moveCenter(area.center())
        self.move(frame.topLeft())
        return primary.name()

    def _restore_window_state(self) -> None:
        # Toolbars are laid out first and never rearranged after a restore: moving them
        # about after restoreState() crashed Qt when the window was shown (user report
        # 2026-10-01). Saved layouts carry WINDOW_STATE_VERSION, so one from a build with
        # different toolbars or panels is ignored instead of being patched up.
        self._layout_toolbars()
        try:
            if self.settings.window_geometry:
                self.restoreGeometry(QByteArray(base64.b64decode(self.settings.window_geometry)))
            if self.settings.window_state and self._restore_layout:
                self.restoreState(QByteArray(base64.b64decode(self.settings.window_state)), WINDOW_STATE_VERSION)
        except ValueError:
            pass
        self.tool_panes.strip.setVisible(True)
        self.tool_panes._show(self.tool_panes.current)  # no tool bar left open from last time

    def _layout_toolbars(self) -> None:
        """Main toolbar on row 1; the open tool's bar always on its own row under it (so
        it's obvious which tool you're in); then the find bar; the tool strip on the
        right. Done after restoring the window, which may come from an older layout."""
        main = self.findChild(QToolBar, "mainToolbar")
        bars = list(self.tool_panes.bars.values())
        rows = [main, *bars, self.text_format_bar, self.find_bar]
        shown = {id(tb): not tb.isHidden() for tb in rows}  # (the window may not be on screen yet)
        # (No removeToolBarBreak(): on some saved layouts it corrupts Qt's toolbar layout and
        # the window crashes the moment it's shown (user report 2026-10-01: "the UI never
        # appears"). Removing every bar also drops the rows, breaks included.)
        for tb in [*rows, self.tool_panes.strip]:
            self.removeToolBar(tb)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, main)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        for bar in bars:
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.text_format_bar)  # (its own row, under the tool's bar)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.find_bar)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.tool_panes.strip)
        main.setVisible(shown[id(main)])
        self.act_toggle_toolbars.setChecked(shown[id(main)])
        self.tool_panes.strip.setVisible(True)
        self.find_bar.setVisible(shown[id(self.find_bar)])
        self.tool_panes._show(self.tool_panes.current)
        self.text_format.update()  # (shown only while there's text to format)


def report_problem_url() -> QUrl:
    """The mailto address Help ▸ Report a Problem opens: subject and a short body with the
    version and system (what the user sees in their email before sending)."""
    import platform

    from PySide6.QtCore import QUrlQuery

    system = f"{platform.system()} {platform.release()} ({platform.machine()})"
    body = "\n".join([
        "What happened:", "", "",
        "What I expected:", "", "",
        "Steps to make it happen again:", "", "",
        f"{APP_NAME} {__version__}, {system}", "",
    ])
    url = QUrl(f"mailto:{FEEDBACK_EMAIL}")
    query = QUrlQuery()
    query.addQueryItem("subject", f"{APP_NAME} {__version__}: ")
    query.addQueryItem("body", body)
    url.setQuery(query.query(QUrl.ComponentFormattingOption.FullyEncoded))
    return url
