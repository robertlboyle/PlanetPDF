"""Toolbars laid out like classic Acrobat (user screenshots 2026-10-01):

* the main toolbar: Open, Save, Print, Find | page up/down, "3 / 3" | Select, Hand |
  zoom out/in, zoom box, Page Display ▾;
* the tool strip down the right side: one icon per tool (Create PDF, Edit PDF, …);
* the tool bar under the main toolbar for the open tool: its name on the left, its
  commands, Close on the right. Closing a tool goes back to plain viewing / form filling.

Tools that are a working mode (Comment, Edit PDF, Prepare Form, Fill & Sign) switch the
mode; the others just show their commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from planetpdf.core.export import ExportFormat
from planetpdf.core.forms.fields import FieldKind
from planetpdf.core.markup import Kind
from planetpdf.ui import fa

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

NOT_YET_TIP = "Not available in PlanetPDF yet"


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    icon: str
    mode: str  # "form" (view / fill), "markup", "edit" or "design"


TOOLS = [
    Tool("create", "Create PDF", "file-circle-plus", "form"),
    Tool("edit", "Edit PDF", "pen-to-square", "edit"),
    Tool("export", "Export PDF", "file-export", "form"),
    Tool("organize", "Organize Pages", "layer-group", "form"),
    Tool("compress", "Compress PDF", "file-zipper", "form"),  # (user 2026-10-03: shrink huge scanned files)
    Tool("bookmark", "Bookmarks", "bookmark", "form"),  # (user 2026-10-04: bookmark selected text / sections)
    Tool("comment", "Comment", "comment", "markup"),
    # (user: a stamp is not a watermark: it sits on top of everything under it)
    Tool("stamp", "Stamp", "stamp", "form"),
    Tool("fill_sign", "Fill & Sign", "signature", "form"),
    Tool("prepare_form", "Prepare Form", "list-check", "design"),
    Tool("protect", "Protect", "shield-halved", "form"),
    Tool("redact", "Redact", "eraser", "form"),
    # (user 2026-10-03: the scanner is Scan & OCR's picture, the wand Optimize Scans')
    Tool("ocr", "Scan & OCR", "scanner-image", "form"),
    Tool("scans", "Optimize Scans", "wand-magic-sparkles", "form"),  # (user 2026-10-03: scan clean-up, its own tool)
    Tool("compare", "Compare Files", "code-compare", "form"),
    # (user screenshot 2026-10-04: Acrobat's PDF Standards tool)
    Tool("standards", "PDF Standards", "file-certificate", "form"),
]
TOOL_BY_KEY = {t.key: t for t in TOOLS}
MODE_TOOL = {"markup": "comment", "edit": "edit", "design": "prepare_form"}
RECENT_TOOLS = 6  # tool bars built at start: the tools used last (the others when first opened)
ACCENT = "#c2185b"  # the colored edge on the tool's name, like Acrobat
# each tool's own color: the edge on its bar's name, and its icon on the tool strip (from the
# user's Acrobat screenshots, 2026-10-03; Create / Edit / Export / Compress / Compare as in
# Acrobat's tool icons)
ACCENTS = {"comment": "#f2c40f", "fill_sign": "#9c4fd6", "stamp": "#9c4fd6", "organize": "#3fae49",
           "prepare_form": "#c93fd4", "protect": "#6c63e8", "redact": "#e0218a", "ocr": "#3fae49",
           "create": "#e5342b", "edit": "#e0218a", "export": "#d6334f", "compress": "#e5342b",
           "scans": "#3fae49", "compare": "#e5342b", "bookmark": "#2d7be5",
           "standards": "#e5342b"}


def accent(key: str) -> str:
    return ACCENTS.get(key, ACCENT)


def tool_label(key: str, text: str, parent: QWidget) -> QLabel:
    """The tool's name on the left of its bar, with the tool's colored edge."""
    label = QLabel(text, parent)  # (a label shows "&" as is)
    label.setStyleSheet(f"QLabel {{ border-left: 4px solid {accent(key)}; padding: 2px 14px 2px 8px; "
                        "font-weight: 600; color: palette(window-text); }")
    return label


COMMENT_ICONS = {
    None: "arrow-pointer", Kind.RECT: "square", Kind.ELLIPSE: "circle", Kind.LINE: "minus", Kind.ARROW: "arrow-right-long",
    Kind.INK: "pen", Kind.TEXTBOX: "input-text", Kind.NOTE: "message-lines", Kind.HIGHLIGHT: "highlighter",
    Kind.UNDERLINE: "underline", Kind.STRIKEOUT: "strikethrough",
    Kind.CARET: "i-cursor", Kind.CALLOUT: "message", Kind.POLYGON: "draw-polygon", Kind.POLYLINE: "wave-pulse",
    Kind.REPLACE: "text-slash", Kind.INK_ERASER: "eraser", Kind.CLOUD: "cloud", Kind.ATTACH: "paperclip",
}
FIELD_ICONS = {
    FieldKind.TEXT: "i-cursor", FieldKind.DATE: "calendar-days", FieldKind.CHECKBOX: "square-check",
    FieldKind.RADIO: "circle-dot", FieldKind.COMBO: "square-caret-down", FieldKind.LIST: "list",
    FieldKind.SIGNATURE: "file-signature", FieldKind.BUTTON: "rectangle-wide", "image_field": "image",
    "barcode_field": "qrcode",
}
SYMBOL_BUTTONS = [("cross", "xmark", "Cross"), ("check", "check", "Check mark"), ("circle", "circle", "Circle"),
                  ("line", "minus", "Line"), ("dot", "circle-small", "Dot")]


def _button(bar: QToolBar, action: QAction, *, text: bool = True, icon: str | None = None) -> QToolButton:
    """A toolbar button that shows its text beside the icon (or just the icon)."""
    if icon and action.icon().isNull():
        action.setIcon(fa.icon(icon))
    button = QToolButton(bar)
    button.setDefaultAction(action)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setAutoRaise(True)
    bar.addWidget(button)
    return button


def _menu_button(bar: QToolBar, text: str, icon: str, menu: QMenu) -> QToolButton:
    button = QToolButton(bar)
    button.setText(text)
    button.setIcon(fa.icon(icon))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setMenu(menu)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setAutoRaise(True)
    bar.addWidget(button)
    return button


def _not_yet(menu: QMenu, text: str) -> QAction:
    action = menu.addAction(text)
    action.setEnabled(False)
    action.setToolTip(NOT_YET_TIP)
    return action


def _spacer(parent: QWidget) -> QWidget:
    spacer = QWidget(parent)
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer


def _gap(parent: QWidget, width: int = 8) -> QWidget:
    """A fixed gap. The buttons start right after the tool's name and stay put: buttons that
    appear or grow (a tool's properties) add to the right instead of re-centering the row
    (user 2026-10-26: "always be locked to the left … the tools to the left of whatever changes
    shouldn't move around")."""
    gap = QWidget(parent)
    gap.setFixedWidth(width)
    return gap


# --- the main toolbar ------------------------------------------------------------------------


def build_main_toolbar(window: MainWindow, tb: QToolBar) -> None:
    w = window
    tb.setIconSize(tb.iconSize())  # (default 24 px icons)
    tb.setStyleSheet(fa.CHECKED_STYLE)  # Select / Hand: white on a dark tile when on
    for action, icon in ((w.act_open, "folder-open"), (w.act_save, "floppy-disk"), (w.act_print, "print"),
                         (w.act_find, "magnifying-glass")):
        _button(tb, action, text=False, icon=icon)
    tb.addSeparator()
    _button(tb, w.act_prev, text=False, icon="circle-arrow-up")
    _button(tb, w.act_next, text=False, icon="circle-arrow-down")
    tb.addWidget(w.page_spin)
    tb.addWidget(w.page_count_label)
    tb.addSeparator()
    group = QActionGroup(w)
    group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
    w.act_select_tool = QAction(fa.icon("arrow-pointer"), "Select", w, checkable=True)
    w.act_select_tool.setToolTip("Select: click items, drag over text to select it")
    w.act_select_tool.triggered.connect(w.select_tool)
    w.act_hand_tool = QAction(fa.icon("hand"), "Hand", w, checkable=True)
    w.act_hand_tool.setToolTip("Hand: drag to move around the page")
    w.act_hand_tool.toggled.connect(w.hand_tool)
    w.view.handToolChanged.connect(lambda on: (w.act_hand_tool.setChecked(on), w.act_select_tool.setChecked(not on)))
    for action in (w.act_select_tool, w.act_hand_tool):
        group.addAction(action)
        _button(tb, action, text=False)
    w.act_select_tool.setChecked(True)
    tb.addSeparator()
    _button(tb, w.act_zoom_out, text=False, icon="circle-minus")
    _button(tb, w.act_zoom_in, text=False, icon="circle-plus")
    tb.addWidget(w.zoom_combo)
    display = QMenu("Page Display", w)
    display.addActions([w.act_fit_page, w.act_fit_width, w.act_actual])
    display.addSeparator()
    display.addActions(list(w.layout_actions.values()))
    display.addAction(w.act_cover)
    button = QToolButton(tb)
    button.setIcon(fa.icon("file-lines"))
    button.setToolTip("Page Display and Zoom")
    button.setMenu(display)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setAutoRaise(True)
    tb.addWidget(button)
    tb.addSeparator()
    # Acrobat's quick tools (screenshot): sticky note, highlight, sign ▾, delete, rotate
    # (the cloud / share buttons are skipped)
    quick = [
        ("note-sticky", "Add a sticky note", lambda: _comment_tool(w, Kind.NOTE)),
        ("highlighter", "Highlight text", lambda: _comment_tool(w, Kind.HIGHLIGHT)),
    ]
    w.quick_actions = {}
    for icon, tip, slot in quick:
        action = QAction(fa.icon(icon), tip, w)
        action.triggered.connect(slot)
        w._doc_actions.append(action)
        w.quick_actions[icon] = action
        _button(tb, action, text=False)
    # Rotate View, not the pages (user 2026-10-02: signing, deleting and turning pages are
    # Fill & Sign / Edit PDF / Organize Pages work, not the main toolbar's)
    for icon, tip, action in (
        ("rotate-left", "Rotate the view counterclockwise (the file isn't changed)", w.act_rotate_view_ccw),
        ("rotate-right", "Rotate the view clockwise (the file isn't changed)", w.act_rotate_view_cw),
    ):
        button = QAction(fa.icon(icon), tip, w)
        button.triggered.connect(action.trigger)
        w._doc_actions.append(button)
        w.quick_actions[icon] = button
        _button(tb, button, text=False)
    _read_aloud_buttons(w, tb)


def _read_aloud_buttons(w, tb: QToolBar) -> None:
    """View ▸ Read Out Loud on the toolbar, shown only while it's activated (user
    2026-10-02): Read This Page (a page with sound), Read to End (a reader with a book),
    and the standard media buttons: Pause, which turns into Play while paused, and Stop."""
    w.read_toolbar_actions = [tb.addSeparator()]
    for action, icon, tip in (
        (w.act_read_page, "file-audio", "Read this page aloud (Shift+Ctrl+V)"),
        (w.act_read_end, "book-open-reader", "Read aloud to the end of the document (Shift+Ctrl+B)"),
        (w.act_read_pause, "pause", "Pause reading (Shift+Ctrl+C)"),
        (w.act_read_stop, "stop", "Stop reading (Shift+Ctrl+E)"),
    ):
        action.setIcon(fa.icon(icon))
        action.setToolTip(tip)
        button = QToolButton(tb)
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        w.read_toolbar_actions.append(tb.addWidget(button))
    # the voice (male / female…) and the volume, right there (user 2026-10-02)
    voices = QMenu("Voice", w)

    def fill_voices() -> None:
        voices.clear()
        try:
            engine, choices, current = w.extras.voice_choices()
        except Exception as exc:  # (a voice that won't load: said in the menu, not a crash)
            voices.addAction(f"Voices unavailable: {exc}").setEnabled(False)
            return
        group = QActionGroup(voices)
        for code, name in choices:
            action = voices.addAction(name, lambda e=engine, c=code: w.extras.choose_voice(e, c))
            action.setCheckable(True)
            action.setChecked(code == current)
            group.addAction(action)
        if not choices:
            voices.addAction("No voices found").setEnabled(False)
        voices.addSeparator()
        voices.addAction("More Read Out Loud Settings…", w.act_preferences.trigger)

    voices.aboutToShow.connect(fill_voices)
    voice_button = QToolButton(tb)
    voice_button.setIcon(fa.icon("waveform-lines"))
    voice_button.setText("Voice")
    voice_button.setToolTip("Choose the reading voice (male or female)")
    voice_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    voice_button.setMenu(voices)
    voice_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    voice_button.setAutoRaise(True)
    w.read_voice_button = voice_button
    w.read_toolbar_actions.append(tb.addWidget(voice_button))
    volume_icon = QLabel(tb)
    volume_icon.setPixmap(fa.icon("volume").pixmap(16, 16))
    volume_icon.setToolTip("Reading volume")
    w.read_toolbar_actions.append(tb.addWidget(volume_icon))
    slider = QSlider(Qt.Orientation.Horizontal, tb)
    slider.setRange(0, 100)
    slider.setValue(w.settings.read_aloud_volume)
    slider.setFixedWidth(90)
    slider.setToolTip("Reading volume")
    slider.valueChanged.connect(lambda v: (w.extras.set_volume(v), slider.setToolTip(f"Reading volume: {v}%")))
    w.read_volume_slider = slider
    w.read_toolbar_actions.append(tb.addWidget(slider))
    show_reading_state(w, "stopped")  # (Play until something is being read)
    for item in w.read_toolbar_actions:
        item.setVisible(w.act_read_activate.isChecked())
    w.act_read_activate.toggled.connect(lambda on: [item.setVisible(on) for item in w.read_toolbar_actions])


def show_reading_state(w, state: str) -> None:
    """Standard media buttons: Pause while reading ("playing"), Play otherwise: "paused"
    (resumes) or "stopped" (starts reading from this page; user 2026-10-04: after Stop the
    button stayed Pause and there was no way to restart without the menu)."""
    w.act_read_pause.setIcon(fa.icon("pause" if state == "playing" else "play"))
    w.act_read_pause.setToolTip({"playing": "Pause reading (Shift+Ctrl+C)",
                                 "paused": "Resume reading (Shift+Ctrl+C)"}.get(
        state, "Read aloud from this page to the end (Shift+Ctrl+C)"))


def _comment_tool(w: MainWindow, kind: Kind) -> None:
    """A Comment tool from the main toolbar: opens Comment and picks the tool (Acrobat)."""
    w.tool_panes.open("comment")
    w.markup.set_tool(kind)


# --- tools: the right-hand strip and the tool bars --------------------------------------------


class StripReorder(QObject):
    """The tool strip's icons can be dragged up and down to another place (user 2026-10-04):
    while dragging, a horizontal blue bar shows where it will go, between the two nearest
    icons; the order is saved (``settings.tool_order``) and used every time. A plain click
    still opens the tool; Esc cancels a drag; right-click on the strip: Reset Tool Order."""

    BAR = "#1473e6"

    def __init__(self, panes: ToolPanes) -> None:
        super().__init__(panes.strip)
        from PySide6.QtWidgets import QFrame

        self.panes = panes
        self.strip = panes.strip
        self._press = None  # (button, key, global start point)
        self.dragging: str | None = None
        self.bar = QFrame(self.strip)
        self.bar.setStyleSheet(f"background: {self.BAR}; border-radius: 1px;")
        self.bar.setFixedHeight(3)
        self.bar.hide()
        for key in self.order():
            self._watch(key)
        self.strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(self._context_menu)

    def order(self) -> list[str]:
        keys = {a: k for k, a in self.panes.strip_actions.items()}
        return [keys[a] for a in self.strip.actions() if a in keys]

    def _watch(self, key: str) -> None:
        button = self.strip.widgetForAction(self.panes.strip_actions[key])
        if button is not None:
            button.setProperty("toolKey", key)
            button.installEventFilter(self)

    def button(self, key: str):
        return self.strip.widgetForAction(self.panes.strip_actions[key])

    # --- the drag -----------------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtWidgets import QApplication

        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press = (obj, obj.property("toolKey"), event.globalPosition().toPoint())
        elif kind == QEvent.Type.MouseMove and self._press is not None:
            point = event.globalPosition().toPoint()
            if self.dragging is None and (point - self._press[2]).manhattanLength() >= QApplication.startDragDistance():
                self.start(self._press[1])
            if self.dragging is not None:
                self.move_to(self.strip.mapFromGlobal(point).y())
                return True
        elif kind == QEvent.Type.MouseButtonRelease and self._press is not None:
            self._press = None
            if self.dragging is not None:
                self.drop()
                return True  # (a drop, not a click: the tool doesn't open)
        elif kind == QEvent.Type.KeyPress and self.dragging is not None and event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        return False

    def start(self, key: str) -> None:
        from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

        self.dragging = key
        button = self.button(key)
        button.setDown(False)
        effect = QGraphicsOpacityEffect(button)
        effect.setOpacity(0.35)
        button.setGraphicsEffect(effect)
        QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
        self._index = None

    def _others(self) -> list:
        return [self.button(k) for k in self.order() if k != self.dragging]

    def move_to(self, y: float) -> int:
        """The gap nearest ``y`` (the strip's coordinates): the bar goes there."""
        others = [b for b in self._others() if b is not None and b.isVisible()]
        index = sum(1 for b in others if b.geometry().center().y() < y)
        if not others:
            return 0
        if index == 0:
            at = others[0].geometry().top() - 2
        elif index == len(others):
            at = others[-1].geometry().bottom() + 1
        else:
            at = (others[index - 1].geometry().bottom() + others[index].geometry().top()) // 2 - 1
        width = self.strip.width() - 6
        self.bar.setGeometry(3, max(0, at), width, 3)
        self.bar.show()
        self.bar.raise_()
        self._index = index
        return index

    def _finish(self) -> str | None:
        from PySide6.QtWidgets import QApplication

        key, self.dragging = self.dragging, None
        self.bar.hide()
        QApplication.restoreOverrideCursor()
        if key is not None and self.button(key) is not None:
            self.button(key).setGraphicsEffect(None)
        return key

    def cancel(self) -> None:
        self._press = None
        self._finish()

    def drop(self) -> None:
        index = self._index
        key = self._finish()
        if key is not None and index is not None:
            self.move(key, index)

    def move(self, key: str, index: int) -> None:
        """``key`` to place ``index`` among the other tools; the order is saved."""
        keys = [k for k in self.order() if k != key]
        index = max(0, min(index, len(keys)))
        if self.order() == keys[:index] + [key] + keys[index:]:
            return
        action = self.panes.strip_actions[key]
        self.strip.removeAction(action)
        self.strip.insertAction(self.panes.strip_actions[keys[index]] if index < len(keys) else None, action)
        self._watch(key)  # (a new button for it)
        self.save()

    def save(self) -> None:
        self.panes.w.settings.tool_order = self.order()

    def _context_menu(self, pos) -> None:
        menu = self.menu()
        menu.exec(self.strip.mapToGlobal(pos))

    def menu(self) -> QMenu:
        menu = QMenu(self.strip)
        menu.addAction("Reset Tool Order", self.reset)
        return menu

    def reset(self) -> None:
        for n, tool in enumerate(TOOLS):
            self.move(tool.key, n)
        self.panes.w.settings.tool_order = []


class ToolPanes:
    def _tool_order(self) -> list[Tool]:
        """TOOLS in the user's saved order; tools the order doesn't know (new ones) at the end."""
        saved = [k for k in self.w.settings.tool_order if k in TOOL_BY_KEY]
        return [TOOL_BY_KEY[k] for k in dict.fromkeys(saved)] + [t for t in TOOLS if t.key not in saved]

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.current: str | None = None
        self._shown: str | None = None  # the tool whose bar shows
        self.form_side_mode: str | None = None  # Prepare Form ▸ Preview ("form") / Edit text & images ("edit")
        self.bars: LazyBars  # (made in _register_bars)
        self.strip = QToolBar("Tools", window)
        self.strip.setObjectName("toolStrip")
        self.strip.setMovable(False)
        self.strip.setOrientation(Qt.Orientation.Vertical)
        self.strip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.strip.setStyleSheet(fa.CHECKED_STYLE)  # the open tool: white on a dark tile
        self._strip_group = QActionGroup(window)
        self._strip_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.strip_actions: dict[str, QAction] = {}
        # Pages / Bookmarks first (when the panel is on the right): they fly the side panel
        # out and collapse it again. Buttons, not the actions themselves, so they can be
        # hidden here while the same actions show on the left-hand rail.
        self.strip_side_items: list[QAction] = []
        for action in window.side_panel.actions.values():
            button = QToolButton(self.strip)
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            self.strip_side_items.append(self.strip.addWidget(button))
        self.strip_side_items.append(self.strip.addSeparator())
        for tool in self._tool_order():  # (in the order the user dragged them to: user 2026-10-04)
            # in the tool's own color (Acrobat); the open tool: white on a dark tile
            action = QAction(fa.icon(tool.icon, accent(tool.key)), tool.label, window, checkable=tool.key != "compare")
            action.setToolTip(tool.label)
            action.triggered.connect(lambda checked=False, k=tool.key: self._strip_clicked(k, checked))
            if action.isCheckable():
                self._strip_group.addAction(action)
            self.strip.addAction(action)
            self.strip_actions[tool.key] = action
        self.reorder = StripReorder(self)  # drag an icon up or down to move it
        self._building = True
        self._register_bars()
        self._building = False
        window.markup.modeChanged.connect(lambda _on: self._mode_changed())
        window.edit.modeChanged.connect(lambda _m: self._mode_changed())
        # the tool that's on shows on its button (user 2026-10-17)
        window.markup.toolChanged.connect(self.sync_tool_buttons)
        window.edit.toolChanged.connect(self.sync_tool_buttons)
        window.edit.modeChanged.connect(self.sync_tool_buttons)

    # --- opening / closing ---------------------------------------------------------------------

    def _strip_clicked(self, key: str, checked: bool) -> None:
        if key == "compare":
            self.w.compare_documents()
            return
        if checked:
            self.open(key)
        else:
            self.close()

    @property
    def previewing(self) -> bool:
        return self.form_side_mode == "form"

    def _side_actions(self) -> dict:
        eui = self.w.edit_ui
        return {"form": eui.act_preview_form, "edit": eui.act_edit_form_content}

    def form_side(self, mode: str | None) -> None:
        """Prepare Form keeps its bar while working in another mode (Acrobat): "form" =
        Preview (fill in the form as people will), "edit" = Edit text & images (Edit PDF on
        the page, with its FORMAT panel); None = back to editing fields."""
        if mode == self.form_side_mode:
            return
        if self.current != "prepare_form":
            mode = None
        self.form_side_mode = mode
        for key, action in self._side_actions().items():
            action.setChecked(key == mode)
        if self.current != "prepare_form":
            return
        self.w.markup_ui.set_mode(mode or "design")
        self.w.show_tool_panel(self.w._edit_dock if mode == "edit" else self.panel_for("prepare_form"))
        self.w.statusBar().showMessage(
            {"form": "Preview: fill in the form to try it. Click Edit to go on editing fields.",
             "edit": "Edit text & images: change the page itself. Pick a field tool (or the arrow) to go on with "
                     "fields."}.get(mode, ""), 6000)

    def preview_form(self, on: bool) -> None:
        if on or self.form_side_mode == "form":
            self.form_side("form" if on else None)

    def edit_form_content(self, on: bool) -> None:
        if on or self.form_side_mode == "edit":
            self.form_side("edit" if on else None)

    def _end_preview(self) -> None:
        if self.form_side_mode is not None:
            self.form_side_mode = None
            for action in self._side_actions().values():
                action.setChecked(False)

    def open(self, key: str) -> None:
        self._end_preview()
        tool = TOOL_BY_KEY[key]
        self.current = key
        mui = self.w.markup_ui
        if mui.mode != tool.mode:
            mui.set_mode(tool.mode)  # (calls _mode_changed, which keeps this tool)
        self._show(key)

    def close(self) -> None:
        self._end_preview()
        self.current = None
        self._show(None)
        if self.w.markup_ui.mode != "form":
            self.w.markup_ui.set_mode("form")
        self.w.select_tool()

    def panel_for(self, key: str | None):
        """The side panel that flies out with this tool (None: the tool has none)."""
        w = self.w
        if key == "redact":
            return w._redact_dock
        if key == "comment":
            return w._comments_dock
        if key == "edit":
            return w._edit_dock
        if key == "fill_sign" and w.session is not None and w.forms.fields:
            return w._forms_dock
        if key == "prepare_form" and w.session is not None and w.center_for("prepare_form") is w.view:
            return w._form_design_dock  # (not over the Start page)
        return None

    def _show(self, key: str | None) -> None:
        if "stamp" in (key, self._shown) and key != self._shown and not self._building:
            self.w.markup.refresh()  # stamps are selectable only while the Stamp tool is open
        self._shown = key
        self.w.show_center(key)  # Organize grid / start page / page view in the middle
        # one tool panel at a time: the new tool's flies out, the others close
        self.w.show_tool_panel(self.panel_for(key))
        self.w.bookmark_tool.set_on(key == "bookmark")
        if key in self.bars:
            self.ensure(key)  # (built the first time it's opened)
            if not self._building:
                self._note_used(key)
                self.sync_tool_buttons()  # (a bar just built shows the tool that's on)
        for name, bar in self.bars.items():
            bar.setVisible(name == key)
        for name, action in self.strip_actions.items():
            if action.isCheckable():
                action.setChecked(name == key)

    def _mode_changed(self) -> None:
        """The mode changed some other way (menu, shortcut): show the matching tool."""
        if self._building:
            return
        mode = self.w.markup_ui.mode
        current = TOOL_BY_KEY.get(self.current) if self.current else None
        if current is not None and current.mode == mode:
            return
        if self.current == "prepare_form" and self.form_side_mode is not None and mode == self.form_side_mode:
            return  # (Preview / Edit text & images, with the Prepare Form bar kept)
        self._end_preview()
        if mode in MODE_TOOL:
            self.current = MODE_TOOL[mode]
        elif current is not None and current.mode != "form":
            self.current = None  # left Comment / Edit PDF / Prepare Form
        self._show(self.current)

    # --- the bars -----------------------------------------------------------------------------
    #
    # Built when first opened (user 2026-10-07: "rarely used tools don't take time or RAM"),
    # except the tools used last (``settings.recent_tools``, RECENT_TOOLS of them), built at
    # start so the usual ones open at once. Every bar exists from the start, empty, and is in
    # the window's layout like before: only its buttons wait (adding tool bars to the window
    # after the saved layout is restored once crashed the window: see CLAUDE.md).

    def _register_bars(self) -> None:
        w, mui, eui = self.w, self.w.markup_ui, self.w.edit_ui
        self.bars = LazyBars(self.ensure)
        self._built: set[str] = set()
        self._shared_actions()
        for tool in TOOLS:
            if tool.key == "compare":
                continue
            existing = {"edit": eui.edit_toolbar, "comment": mui.toolbar, "prepare_form": eui.design_toolbar}.get(tool.key)
            bar = existing if existing is not None else QToolBar(tool.label, w)
            bar.setObjectName(f"tool_{tool.key}")
            bar.setMovable(False)
            bar.setStyleSheet(fa.CHECKED_STYLE)
            bar.setVisible(False)
            dict.__setitem__(self.bars, tool.key, bar)
        for key in self.w.settings.recent_tools[:RECENT_TOOLS]:
            if key in self.bars:
                self.ensure(key)

    def ensure(self, key: str) -> None:
        """Builds the tool's bar if it isn't yet."""
        if key in self._built or key not in self.bars:
            return
        self._built.add(key)
        bar = dict.__getitem__(self.bars, key)
        getattr(self, f"_build_{key}")(bar)
        self.w._update_document_actions()  # (buttons made now start enabled or not, like the rest)

    def _note_used(self, key: str) -> None:
        recent = [key, *(k for k in self.w.settings.recent_tools if k != key)]
        self.w.settings.recent_tools = recent[:RECENT_TOOLS]

    def _shared_actions(self) -> None:
        """What the menus share with the bars, made at start whether a bar is built or not:
        commands, and the icons of the Comment and Prepare Form tools (menus show them too)."""
        w, mui, eui = self.w, self.w.markup_ui, self.w.edit_ui
        w.act_sanitize = QAction(fa.icon("broom"), "Sanitize Document", w)
        w.act_sanitize.setToolTip("Remove hidden information: metadata, attachments, scripts, hidden layers, comments…")
        w.act_sanitize.triggered.connect(lambda: w.sanitize_document())
        w._doc_actions.append(w.act_sanitize)
        w.act_redact_pages = QAction("Redact Pages…", w)
        w.act_redact_pages.setToolTip("Mark whole pages for redaction")
        w.act_redact_pages.triggered.connect(lambda: w.redact_pages())
        w._doc_actions.append(w.act_redact_pages)
        for kind, action in mui.tool_actions.items():
            action.setIcon(fa.icon(COMMENT_ICONS.get(kind)))
        mui.act_text_comment.setIcon(fa.icon("text"))
        for action in [*mui.not_yet_actions, mui.act_collapse_drawing, mui.act_expand_drawing]:
            action.setIcon(fa.icon(action.data()))
        for kind, item in mui.drawing_menu_actions.items():
            item.setIcon(mui.tool_actions[kind].icon())
        mui.update_drawing_button()
        mui.stamp_button.setIcon(fa.icon("stamp"))
        mui.width_button.setIcon(fa.icon("bars-staggered"))
        for kind, action in eui.field_actions.items():
            action.setIcon(fa.icon(FIELD_ICONS.get(kind)))
        eui.act_select_field.setIcon(fa.icon("arrow-pointer"))
        eui.act_detect_fields.setIcon(fa.icon("wand-magic-sparkles"))
        eui.act_edit_form_content.setIcon(fa.icon("file-pen"))
        eui.act_form_help.setIcon(fa.icon("circle-question"))
        eui.act_add_text.setIcon(fa.icon("text"))

    def _start(self, key: str, bar: QToolBar, *, clear: bool = False) -> QToolBar:
        """A tool bar's start: the tool's name on the left, the buttons right after it."""
        if clear:
            bar.clear()
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.addWidget(tool_label(key, TOOL_BY_KEY[key].label, bar))
        bar.addWidget(_gap(bar))  # (locked to the left: nothing moves when buttons change)
        return bar

    def _finish(self, bar: QToolBar, before_close: QWidget | None = None) -> None:
        bar.addWidget(_spacer(bar))
        if before_close is not None:  # (e.g. Redact's Apply, beside Close)
            bar.addWidget(before_close)
        close = QPushButton("Close", bar)
        close.setToolTip("Close this tool")
        close.setStyleSheet("QPushButton { border: 1px solid #8a8a8a; border-radius: 12px; padding: 3px 16px; color: palette(button-text); }")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        bar.close_button = close

    def _build_edit(self, bar: QToolBar) -> None:
        # Edit PDF (screenshot): Select, Add Text, Add Image, Link ▾, Crop page, Header & Footer ▾, Watermark ▾, More ▾
        # (user 2026-10-18: "the edit bar is missing an arrow pointer tool so I can select something I already
        # edited and make changes to it": the first button, Acrobat's "Edit", is now the arrow it really is)
        w, eui = self.w, self.w.edit_ui
        self._start("edit", bar, clear=True)
        w.act_edit_objects = QAction(fa.icon("arrow-pointer"), "Select", w, checkable=True)
        w.act_edit_objects.setToolTip("Select what's on the page to change it: click a line of text (click it again to "
                                      "retype it), text you added, a picture, or a shape to move, resize, or change it")
        w.act_edit_objects.triggered.connect(lambda: w.edit.set_tool(None))
        _button(bar, w.act_edit_objects)
        _button(bar, eui.act_add_text, icon="text")
        _button(bar, eui.act_add_image, icon="image")
        eui.act_erase.setIcon(fa.icon("eraser"))
        erase = QToolButton(bar)  # click: the eraser; ▾: its size
        erase.setDefaultAction(eui.act_erase)
        erase.setMenu(eui.eraser_menu)
        erase.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        erase.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        erase.setAutoRaise(True)
        erase.setMinimumWidth(erase.sizeHint().width() + 14)  # (room for the label when switched on)
        bar.addWidget(erase)
        # the Eraser's width, shown while it's on (user 2026-10-18: "there should be a width property that
        # comes up when it is selected"); the same size as Eraser ▾ and the + / - keys
        size = QSpinBox(bar)
        size.setRange(2, 64)
        size.setSuffix(" pt")
        size.setPrefix("Width: ")
        size.setValue(int(w.edit.eraser_size))
        size.setToolTip("The eraser's width (the + and - keys change it too)")
        size.valueChanged.connect(lambda value: w.edit.set_eraser_size(value))

        def follow(value: float) -> None:
            size.blockSignals(True)
            size.setValue(int(round(value)))
            size.blockSignals(False)

        w.edit.eraserSizeChanged.connect(follow)
        eui.eraser_width = bar.addWidget(size)
        eui.eraser_width.setVisible(w.edit.tool == "erase")
        w.edit.toolChanged.connect(lambda tool: eui.eraser_width.setVisible(tool == "erase"))
        link = QMenu("Link", w)
        link.addActions([w.act_add_link, w.act_remove_links])
        self.link_button = _menu_button(bar, "Link", "link", link)
        self.link_button.setCheckable(True)  # (on while the Link tool is: sync_tool_buttons)
        link.aboutToHide.connect(lambda: QTimer.singleShot(0, self.sync_tool_buttons))
        _button(bar, w.act_crop, icon="crop-simple").setText("Crop page")
        header = QMenu("Header & Footer", w)
        header.addAction("Add…", w.act_header_footer.trigger)
        header.addActions([w.act_header_update, w.act_header_remove])
        _menu_button(bar, "Header && Footer", "heading", header)
        watermark = QMenu("Watermark", w)
        watermark.addAction("Add…", w.act_watermark.trigger)
        watermark.addActions([w.act_watermark_update, w.act_watermark_remove])
        _menu_button(bar, "Watermark", "droplet", watermark)
        more = QMenu("More", w)
        more.addActions([w.act_page_numbers, w.act_bates])
        background = more.addMenu("Background")
        background.addActions([w.act_background, w.act_background_update, w.act_background_remove])
        _menu_button(bar, "More", "rectangle-list", more)
        self._finish(bar)

    def _build_fill_sign(self, bar: QToolBar) -> None:
        # Fill & Sign (screenshot): Add Text, ✗ ✓ ○ — •, color | Sign Yourself ▾
        w, mui = self.w, self.w.markup_ui
        self._start("fill_sign", bar)
        _button(bar, w.act_add_placed_text, icon="text")
        bar.addSeparator()
        for name, _icon, _tip in SYMBOL_BUTTONS:
            button = _button(bar, self.symbol_action(name), text=False)
            if name == "circle":  # ▾: outline or solid, and the line width (user 2026-10-26)
                button.setMenu(self.circle_menu())
                button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        _button(bar, w.act_text_color, text=False)
        bar.addSeparator()
        sign = _menu_button(bar, "Sign Yourself", "signature", mui.choose_menu)
        sign.setToolTip("Place one of your saved signatures or initials, or create one")
        more = QMenu("More", w)
        more.addActions([w.act_text_smaller, w.act_text_larger])
        more.addSeparator()
        more.addActions([w.act_highlight_fields, w.act_clear_form])
        more.addSeparator()
        more.addActions([w.act_flatten_page, w.act_flatten_all, w.act_flatten_form])
        more.addSeparator()
        more.addActions([mui.act_digital_sign, mui.act_check_signatures, mui.act_manage_signatures])
        _menu_button(bar, "More", "ellipsis", more)
        self._finish(bar)

    def tool_indicators(self) -> list[tuple[object, bool]]:
        """Each tool button and whether its tool is on now (user 2026-10-17: the tool that's
        on is highlighted, its name bold). Buttons of bars not built yet are left out."""
        w, m, e = self.w, self.w.markup, self.w.edit
        pick = m.pick_name
        eui = w.edit_ui
        out = [
            (eui.act_add_text, e.tool == "text"),
            (eui.act_add_image, e.tool == "image"),
            (eui.act_erase, e.tool == "erase"),
            (w.act_add_placed_text, m.tool is Kind.TEXT),
        ]
        if hasattr(w, "act_edit_objects"):
            out.append((w.act_edit_objects, e.mode == "edit" and e.tool is None and not pick))
        if getattr(self, "link_button", None) is not None:
            out.append((self.link_button, pick == "link"))
        if hasattr(w, "act_crop_draw"):
            out.append((w.act_crop_draw, pick == "crop"))
        for name, action in getattr(w, "symbol_actions", {}).items():
            out.append((action, m.tool is Kind.SYMBOL and m.symbol_name == name))
        return out

    def sync_tool_buttons(self, *_args) -> None:
        """Switch on the button of the tool that's on, and off every other one."""
        for target, on in self.tool_indicators():
            if not target.isCheckable():
                target.setCheckable(True)
            if target.isChecked() != on:
                target.blockSignals(True)
                target.setChecked(on)
                target.blockSignals(False)

    def circle_menu(self) -> QMenu:
        """The Fill & Sign circle's ▾: Outline / Solid Fill and Line Width, for the next circles
        and the selected one."""
        from planetpdf.core.symbols import LINE_WIDTHS

        w = self.w
        menu = QMenu(w)
        outline = menu.addAction("Outline", lambda: w.markup.set_circle_style(filled=False))
        solid = menu.addAction("Solid Fill", lambda: w.markup.set_circle_style(filled=True))
        menu.addSeparator()
        widths = menu.addMenu("Line Width")
        width_actions = {}
        for width in LINE_WIDTHS:
            action = widths.addAction(f"{width:g} pt", lambda v=width: w.markup.set_circle_style(line=v))
            action.setCheckable(True)
            width_actions[width] = action
        for action in (outline, solid):
            action.setCheckable(True)

        def show_current() -> None:  # (the circle selected, or the next one's settings)
            s = w.settings
            item = w.markup._item(w.view.selected_markup or "")
            spec = item.spec if item is not None and item.spec is not None and item.spec.text == "circle" else None
            filled = spec.style.fill is not None if spec is not None else s.circle_filled
            line = spec.style.width if spec is not None else s.circle_line
            outline.setChecked(not filled)
            solid.setChecked(filled)
            widths.setEnabled(not filled)
            for value, action in width_actions.items():
                action.setChecked(abs(value - line) < 0.01)

        menu.aboutToShow.connect(show_current)
        return menu

    def symbol_action(self, name: str) -> QAction:
        """Fill & Sign's ✗ ✓ ○ — • (``window.symbol_actions``)."""
        w = self.w
        if not hasattr(w, "symbol_actions"):
            w.symbol_actions = {}
        if name not in w.symbol_actions:
            _n, icon, tip = next(s for s in SYMBOL_BUTTONS if s[0] == name)
            action = QAction(fa.icon(icon), tip, w, checkable=True)
            action.setToolTip(f"Add a {tip.lower()} (click on the page; − / + to resize it)")
            action.triggered.connect(lambda _c=False, n=name: w.markup.place_symbol(n))
            w._doc_actions.append(action)
            w.symbol_actions[name] = action
        return w.symbol_actions[name]

    def _build_comment(self, bar: QToolBar) -> None:
        # Comment: the markup tools and style controls (their actions are MarkupUi's)
        from PySide6.QtWidgets import QWidgetAction

        from planetpdf.ui.text_format import TextFormatBar

        w, mui = self.w, self.w.markup_ui
        name = tool_label("comment", "Comment", bar)
        first = bar.actions()[0] if bar.actions() else None
        bar.insertWidget(first, name)
        bar.insertWidget(first, _gap(bar))  # (locked to the left: nothing moves when buttons change)
        # Aa: the text format bar for text boxes and text comments (font, size, B I U S, color)
        holder = QWidgetAction(mui.text_menu)
        w.comment_text_format = TextFormatBar(mui.text_menu, rows=2)
        w.comment_text_format.setContentsMargins(8, 6, 8, 6)
        holder.setDefaultWidget(w.comment_text_format)
        mui.text_menu.addAction(holder)
        w.text_format.add_bar(w.comment_text_format)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._finish(bar)

    def _build_prepare_form(self, bar: QToolBar) -> None:
        # Prepare Form: the field tools (their actions are EditUi's)
        eui = self.w.edit_ui
        name = tool_label("prepare_form", "Prepare Form", bar)
        first = bar.actions()[0] if bar.actions() else None
        bar.insertWidget(first, name)  # (the name on the left, the buttons right after it)
        bar.insertWidget(first, _gap(bar))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)  # (Acrobat: icons, names as tooltips)
        preview = QPushButton("Preview", bar)
        preview.setCheckable(True)
        preview.setToolTip(eui.act_preview_form.toolTip())
        # (the same outlined pill as Close beside it: user 2026-10-04, the blue one looked odd)
        preview.setStyleSheet(
            "QPushButton { border: 1px solid #8a8a8a; border-radius: 12px; padding: 3px 16px; color: palette(button-text); }"
            "QPushButton:checked { background: palette(midlight); }")
        preview.setChecked(eui.act_preview_form.isChecked())
        preview.setText("Edit" if eui.act_preview_form.isChecked() else "Preview")
        preview.toggled.connect(eui.act_preview_form.setChecked)
        eui.act_preview_form.toggled.connect(lambda on: (preview.setChecked(on), preview.setText("Edit" if on else "Preview")))
        bar.preview_button = preview
        self._finish(bar, before_close=preview)

    def _build_stamp(self, bar: QToolBar) -> None:
        # Stamp: on top of everything under it (a watermark goes underneath)
        # Acrobat (screenshot 2026-10-03): Stamp ▾ · Stamps Palette · Custom Stamps ▾ · Bates Numbering ▾
        w, mui, sui = self.w, self.w.markup_ui, self.w.stamp_ui
        self._start("stamp", bar)
        stamps = _menu_button(bar, "Stamp", "stamp", mui.stamp_menu)
        stamps.setToolTip("Pick a stamp, then click on the page (drag to size it). It sits on top of the page.")
        _button(bar, sui.act_palette, icon="table-cells")
        _menu_button(bar, "Custom Stamps", "pen-ruler", sui.custom_menu)
        bates = QMenu("Bates Numbering", w)
        bates.addAction("Add…", w.act_bates.trigger)
        bates.addAction(w.act_bates_remove)
        _menu_button(bar, "Bates Numbering", "file-binary", bates)
        self._finish(bar)

    def _build_organize(self, bar: QToolBar) -> None:
        # Organize Pages
        # Acrobat (screenshot): All Pages ▾ · rotate ↺ ↻ · delete | Extract · Insert ▾ ·
        # Replace · Split · More ▾ — the pages show as a grid in place of the page view
        from planetpdf.ui.organizer import PAGE_SETS

        w = self.w
        self._start("organize", bar)
        # (Acrobat, screenshot 2026-10-03: the box shows the selected pages, e.g. "3", and
        # takes a page range typed in; the list offers All / Even / Odd … pages)
        page_set = QComboBox(bar)
        page_set.setEditable(True)
        page_set.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        page_set.addItems(PAGE_SETS)
        page_set.setMinimumContentsLength(14)
        page_set.setToolTip("The selected pages: type pages (e.g. 1-3, 7) or choose a set")
        page_set.activated.connect(lambda i: w.organizer.select_set(PAGE_SETS[i]))
        page_set.lineEdit().returnPressed.connect(lambda: w.organizer.select_range(page_set.currentText()))
        w.organizer.itemSelectionChanged.connect(
            lambda: page_set.lineEdit().hasFocus() or page_set.setEditText(w.organizer.selection_text()))
        bar.addWidget(page_set)
        bar.page_set = page_set
        _button(bar, w.act_rotate_ccw, text=False, icon="rotate-left")
        _button(bar, w.act_rotate_cw, text=False, icon="rotate-right")
        _button(bar, w.act_delete_pages_menu, text=False, icon="trash")
        bar.addSeparator()
        extract = QAction(fa.icon("file-export"), "Extract", w)
        extract.setToolTip("Save the selected pages as a new PDF")
        extract.triggered.connect(w.act_export_pages.trigger)
        _button(bar, extract)
        insert = QMenu("Insert", w)
        insert.addAction("From File…", w.insert_pages_dialog)
        insert.addAction("From Clipboard…", w.insert_pages_from_clipboard)
        insert.addAction("Blank Page", w.insert_blank_page)
        insert.addActions([w.act_scan_insert, w.act_web_insert])
        _menu_button(bar, "Insert", "file-circle-plus", insert)
        w.act_replace_pages.setIcon(fa.icon("arrow-right-arrow-left"))
        w.act_replace_pages.setIconText("Replace")  # (Acrobat's label on the bar; the menu keeps the full name)
        _button(bar, w.act_replace_pages)
        split = QAction(fa.icon("scissors"), "Split", w)
        split.setToolTip("Split the document into several files")
        split.triggered.connect(w.act_split.trigger)
        _button(bar, split)
        more = QMenu("More", w)
        more.addActions([w.act_rotate, w.act_crop, w.act_page_numbers, w.act_header_footer, w.act_page_labels])
        _menu_button(bar, "More", "ellipsis", more)
        self._finish(bar)

    def _build_protect(self, bar: QToolBar) -> None:
        # Protect (screenshot 2026-10-03): Protect Using Password · Remove Hidden Information ·
        # Advanced Options ▾ [Encrypt with Certificate, Encrypt with Password | Document
        # Security ▸, Security Properties, Create Security Envelope…, Manage Security
        # Policies…, Remove Security]
        w, sec = self.w, self.w.security_ui
        self._start("protect", bar)
        protect = QAction(fa.icon("file-lock"), "Protect Using Password", w)
        protect.setToolTip(w.act_protect.toolTip() or "Require a password to open or change this document")
        protect.triggered.connect(w.act_protect.trigger)
        _button(bar, protect)
        hidden = QAction(fa.icon("file-magnifying-glass"), "Remove Hidden Information", w)
        hidden.setToolTip(w.act_sanitize.toolTip())
        hidden.triggered.connect(w.act_sanitize.trigger)
        _button(bar, hidden)
        advanced = QMenu("Advanced Options", w)
        advanced.addAction("&1 Encrypt with Certificate…", w.act_encrypt_certificate.trigger)
        advanced.addAction("&2 Encrypt with Password…", w.act_protect.trigger)
        advanced.addSeparator()
        document_security = advanced.addMenu(fa.icon("user-lock"), "Document &Security")
        document_security.addActions(sec.method_actions)
        document_security.aboutToShow.connect(sec.show_method)
        advanced.addAction(fa.icon("file-shield"), "Security Propert&ies…", sec.properties_dialog)
        advanced.addAction(fa.icon("envelope"), "Create Security &Envelope…", sec.envelope_dialog)
        advanced.addAction(fa.icon("list-check"), "&Manage Security Policies…", sec.policies_dialog)
        advanced.addAction(fa.icon("lock-open"), "&Remove Security", w.act_unprotect.trigger)
        _menu_button(bar, "Advanced Options", "sliders", advanced)
        self._finish(bar)

    def _build_redact(self, bar: QToolBar) -> None:
        # Redact (screenshot 2026-10-03): Redact Text & Images ▾ [Redact Pages, Find Text &
        # Redact | Properties] · Sanitize Document · … Apply (beside Close, grayed until
        # there are marks) · Close
        w = self.w
        self._start("redact", bar)
        redact_menu = QMenu("Redact Text & Images", w)
        redact_menu.addAction(w.act_redact_pages)
        find = redact_menu.addAction("Find Text && Redact…", w.act_redact_find.trigger)
        find.setToolTip("Search for words or patterns (SSN, phone, email…) and mark them")
        redact_menu.addSeparator()
        redact_menu.addAction("Properties…", lambda: w.show_tool_panel(w._redact_dock))  # fill color, overlay text: the panel
        # click = draw boxes to redact (the Mark Area tool, kept in step); ▾ = the menu
        draw = QAction(fa.icon("eraser"), "Redact Text && Images", w, checkable=True)
        draw.setToolTip("Drag boxes over text or images to mark them for redaction")
        draw.setChecked(w.act_redact_area.isChecked())
        draw.triggered.connect(lambda on: w.act_redact_area.isChecked() != on and w.act_redact_area.trigger())
        w.act_redact_area.toggled.connect(draw.setChecked)
        area = QToolButton(bar)
        area.setDefaultAction(draw)
        area.setAutoRaise(True)
        area.setMenu(redact_menu)
        area.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        area.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.addWidget(area)
        bar.redact_button = area
        _button(bar, w.act_sanitize)
        apply = QPushButton("Apply", bar)
        apply.setToolTip("Apply the redaction marks: saves a redacted copy and checks it")
        apply.setStyleSheet(
            "QPushButton { border: none; border-radius: 12px; padding: 3px 16px; background: #1473e6; color: white; }"
            "QPushButton:disabled { background: #e6e6e6; color: #9a9a9a; }")
        apply.clicked.connect(w.act_redact_apply.trigger)
        bar.apply_button = apply
        self._finish(bar, before_close=apply)

        def marks_changed(*_args) -> None:
            from planetpdf.core.redaction import marks as M

            apply.setEnabled(w.session is not None and bool(M.list_marks(w.session)))

        w.undo_group.indexChanged.connect(marks_changed)
        w.undo_group.activeStackChanged.connect(marks_changed)
        bar.visibilityChanged.connect(lambda shown: shown and marks_changed())
        marks_changed()

    def _build_compress(self, bar: QToolBar) -> None:
        # Compress PDF (Acrobat's Compress / Optimize PDF): make this document smaller (one
        # undo step), or save a smaller copy; and see what takes the room
        w = self.w
        self._start("compress", bar)
        compress = QAction(fa.icon("file-zipper"), "Compress…", w)
        compress.setToolTip("Make this document smaller: scanned pages, pictures, and fonts (Undo puts it back)")
        compress.triggered.connect(w.reduce_size_dialog)
        _button(bar, compress)
        usage = QAction(fa.icon("chart-pie"), "Space Usage", w)
        usage.setToolTip("What takes up the room in this file: pictures, fonts, page content…")
        usage.triggered.connect(w.space_usage_dialog)
        _button(bar, usage)
        self._finish(bar)

    def _build_bookmark(self, bar: QToolBar) -> None:
        # Bookmarks: select text with the I-beam, then Enter / + / right-click to bookmark it;
        # or find the table of contents / headings (user 2026-10-04)
        w, bt = self.w, self.w.bookmark_tool
        self._start("bookmark", bar)
        _button(bar, bt.act_add)
        _button(bar, bt.act_auto)
        delete = QAction(fa.icon("trash"), "Delete Bookmark", w)
        delete.setToolTip("Delete the bookmark selected in the Bookmarks panel (and the ones under it)")
        delete.triggered.connect(w.bookmarks_panel.delete_selected)
        w._doc_actions.append(delete)
        _button(bar, delete)
        self._finish(bar)

    def _build_standards(self, bar: QToolBar) -> None:
        # PDF Standards (user screenshot 2026-10-04): Save as PDF/A, PDF/X, PDF/E, Check Compliance (not "Preflight": Adobe's name)
        w = self.w
        self._start("standards", bar)
        for text, icon, tip, slot in (
            ("Save as PDF/A", "box-archive", "A copy for long-term archiving (PDF/A-2b)",
             lambda: w.save_as_standard("pdfa")),
            ("Save as PDF/X", "print", "A copy for a printing press (PDF/X-3, CMYK)", lambda: w.save_as_standard("pdfx")),
            ("Save as PDF/E", "compass-drafting", "A copy for engineering documents (PDF/E-1)",
             lambda: w.save_as_standard("pdfe")),
            ("Check Compliance", "clipboard-check", "Check this document against PDF/A, PDF/X, or PDF/E", w.check_compliance_dialog),
        ):
            action = QAction(fa.icon(icon), text, w)
            action.setToolTip(tip)
            action.triggered.connect(lambda _c=False, s=slot: s())
            w._doc_actions.append(action)
            _button(bar, action)
        self._finish(bar)

    def _build_scans(self, bar: QToolBar) -> None:
        # Optimize Scans: all steps (dialog with a preview), then the common ones in one click
        w, tools = self.w, self.w.scan_tools
        self._start("scans", bar)
        for text, icon, tip, slot in (
            ("Optimize…", "scanner-image", "Choose clean-up steps, see before and after, optionally recognize text",
             tools.optimize_dialog),
            ("Black & White", "circle-half-stroke", "Make the scanned pages black and white (crisp text, much smaller)",
             tools.black_white),
            ("Straighten & Clean", "broom", "Straighten crooked pages, remove dark edges, specks, and bleed-through, "
             "white paper", tools.straighten_clean),
            ("Fix Page Direction", "arrows-rotate", "Turn pages scanned sideways or upside down the right way up",
             tools.fix_direction),
            ("Remove Blank Pages…", "file-slash", "Find empty pages (the backs of double-sided scans) and delete them",
             lambda: tools.remove_blank_pages()),
        ):
            action = QAction(fa.icon(icon), text, w)
            action.setToolTip(tip)
            action.triggered.connect(lambda _c=False, s=slot: s())
            _button(bar, action)
        self._finish(bar)

    def _build_ocr(self, bar: QToolBar) -> None:
        # Scan & OCR (screenshots 2026-10-03): Insert ▾ [From File… Shift+Ctrl+I, From
        # Scanner] · Enhance ▾ [Scanned Document, Camera Image] · Recognize Text ▾ [In This
        # File, In Multiple Files… | Correct Recognized Text] · Bates Numbering ▾ [Add…,
        # Remove…] | page buttons: Crop Pages (user screenshot 2026-10-03), rotate ↺ ↻, delete
        w = self.w
        self._start("ocr", bar)
        insert = QMenu("Insert", w)
        insert.addAction(fa.icon("file-import"), "From &File…\tShift+Ctrl+I", w.act_insert_file.trigger)
        insert.addAction(fa.icon("scanner-image"), "From &Scanner", w.act_scan_insert.trigger)
        _menu_button(bar, "Insert", "file-circle-plus", insert)
        enhance = QMenu("Enhance", w)
        enhance.addActions([w.act_enhance_scan, w.act_enhance_camera])
        enhance.addSeparator()
        enhance.addAction(w.act_optimize_scans)
        _menu_button(bar, "Enhance", "wand-magic-sparkles", enhance)
        recognize = QMenu("Recognize Text", w)
        recognize.addAction(fa.icon("file-lines"), "In This File", w.act_ocr.trigger)
        recognize.addAction(w.act_ocr_multiple)
        recognize.addSeparator()
        recognize.addAction(w.act_correct_ocr)
        _menu_button(bar, "Recognize Text", "text-size", recognize)
        bates = QMenu("Bates Numbering", w)
        bates.addAction("&Add…", w.act_bates.trigger)
        bates.addAction("&Remove…", w.act_bates_remove.trigger)
        _menu_button(bar, "Bates Numbering", "file-binary", bates)
        bar.addSeparator()
        crop = QAction(fa.icon("crop-simple"), "Crop Pages", w, checkable=True)  # (user screenshot: "Click and select area")
        w.act_crop_draw = crop
        crop.setToolTip("Crop Pages: drag a box around the part of the page to keep")
        crop.triggered.connect(lambda: w.crop_by_drawing())
        w._doc_actions.append(crop)
        _button(bar, crop, text=False)
        _button(bar, w.act_rotate_ccw, text=False, icon="rotate-left")
        _button(bar, w.act_rotate_cw, text=False, icon="rotate-right")
        _button(bar, w.act_delete_pages_menu, text=False, icon="trash")
        self._finish(bar)

    def _build_export(self, bar: QToolBar) -> None:
        # Export PDF
        w = self.w
        self._start("export", bar)
        for text, fmt, icon in (("Word", ExportFormat.DOCX, "file-word"), ("Rich Text", ExportFormat.RTF, "file-lines"),
                                ("Text", ExportFormat.TXT, "file-lines"), ("Image-only PDF", ExportFormat.IMAGE_PDF, "file-pdf")):
            action = QAction(fa.icon(icon), text, w)
            action.triggered.connect(lambda _c=False, f=fmt: w.export_dialog(f))
            _button(bar, action)
        images = QMenu("Image", w)
        for text, fmt in (("JPEG", ExportFormat.JPG), ("JPEG 2000", ExportFormat.JPX), ("PNG", ExportFormat.PNG),
                          ("TIFF", ExportFormat.TIFF), ("Multi-page TIFF", ExportFormat.TIFF_MULTI)):
            images.addAction(text, lambda f=fmt: w.export_dialog(f))
        _menu_button(bar, "Image", "file-image", images)
        sheet = QMenu("Spreadsheet", w)
        sheet.addAction("Microsoft Excel Workbook", lambda: w.export_dialog(ExportFormat.XLSX))
        sheet.addAction("XML Spreadsheet 2003", lambda: w.export_dialog(ExportFormat.XML_SPREADSHEET))
        _menu_button(bar, "Spreadsheet", "file-excel", sheet)
        for text, fmt, icon in (("PowerPoint", ExportFormat.PPTX, "file-powerpoint"), ("HTML", ExportFormat.HTML, "file-code")):
            action = QAction(fa.icon(icon), text, w)
            action.triggered.connect(lambda _c=False, f=fmt: w.export_dialog(f))
            _button(bar, action)
        more = QMenu("More Formats", w)
        for text, fmt in (("Word 97-2003", ExportFormat.DOC), ("PostScript", ExportFormat.PS),
                          ("Encapsulated PostScript", ExportFormat.EPS), ("XML 1.0", ExportFormat.XML),
                          ("Text (Accessible)", ExportFormat.TXT_ACCESSIBLE)):
            more.addAction(text, lambda f=fmt: w.export_dialog(f))
        _menu_button(bar, "More Formats", "ellipsis", more)
        self._finish(bar)

    def _build_create(self, bar: QToolBar) -> None:
        # Create PDF
        w = self.w
        self._start("create", bar)
        for text, slot, icon in (("PDF from File", w.create_pdf_from_file, "file-import"),
                                 ("Scanner", w.act_scan_create.trigger, "scanner-image"),
                                 ("Web Page", w.act_web_create.trigger, "globe"),
                                 ("PDF from Clipboard", w.create_pdf_from_clipboard, "clipboard"),
                                 ("Blank Page", w.create_blank_pdf, "file"),
                                 ("Combine Files", w.act_combine.trigger, "object-group"),
                                 ("Multiple PDF Files", w.act_create_multiple.trigger, "files"),
                                 ("Portfolio", w.act_portfolio.trigger, "briefcase"),
                                 ("Create Form", lambda: self.open("prepare_form"), "rectangle-list")):
            action = QAction(fa.icon(icon), text, w)
            action.triggered.connect(slot)
            _button(bar, action)
        self._finish(bar)


class LazyBars(dict):
    """The tool bars by key. Asking for one (``bars["redact"]``) builds it first; going
    through them all (``values()`` / ``items()``) doesn't."""

    def __init__(self, ensure) -> None:
        super().__init__()
        self._ensure = ensure

    def __getitem__(self, key: str) -> QToolBar:
        self._ensure(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self[key] if key in self else default
