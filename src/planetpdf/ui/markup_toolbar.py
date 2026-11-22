"""The mode switch (Fill Form / Markup / Edit / Prepare Form), the Markup toolbar, and
the Markup and Sign menus."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolBar,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from planetpdf.core.markup import Kind

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

# In Acrobat's Comment bar order (user screenshot 2026-10-01)
TOOLS = [
    (None, "Select", "Select, move, and resize markup"),
    (Kind.NOTE, "Sticky Note", "Add a note: drag over text to highlight it with a note, or click to place a sticky note"),
    (Kind.HIGHLIGHT, "Highlight", "Highlight text (drag across words)"),
    (Kind.UNDERLINE, "Underline", "Underline text"),
    (Kind.STRIKEOUT, "Strikethrough", "Strike through text"),
    (Kind.REPLACE, "Replace text", "Drag across the words to replace, then type the new words"),
    (Kind.CARET, "Insert text at cursor", "Click between two characters, then type the words to insert"),
    (Kind.TEXTBOX, "Text Box", "Add a text box"),
    (Kind.INK, "Pencil", "Draw free form"),
    (Kind.INK_ERASER, "Erase free-form drawing", "Drag over pencil drawings to erase parts of them (+ / − size)"),
    (Kind.LINE, "Line", "Draw a line"),
    (Kind.ARROW, "Arrow", "Draw an arrow"),
    (Kind.RECT, "Rectangle", "Draw a rectangle"),
    (Kind.ELLIPSE, "Oval", "Draw an oval"),
    (Kind.CALLOUT, "Text Callout", "Drag from the spot you are pointing at to where the text goes, then type"),
    (Kind.POLYGON, "Polygon", "Click each corner; double-click, Enter, or click the first corner to finish"),
    (Kind.CLOUD, "Cloud", "Click each corner of a cloud; double-click, Enter, or click the first corner to finish"),
    (Kind.POLYLINE, "Connected Lines", "Click each point; double-click or Enter to finish"),
]
# Acrobat's drawing tools: shown on the bar, or folded into one drop-down button (Collapse /
# Expand Drawing Tools, user screenshots 2026-10-03; settings.collapse_drawing_tools)
DRAWING_TOOLS = [Kind.LINE, Kind.ARROW, Kind.RECT, Kind.ELLIPSE, Kind.CALLOUT, Kind.POLYGON, Kind.CLOUD, Kind.POLYLINE]
# Acrobat comment tools we don't have yet, shown grayed out: (after this tool, label, icon)
NOT_YET_TOOLS: list = []


class MarkupUi:
    def __init__(self, window: MainWindow) -> None:
        self.window = window
        controller = window.markup
        s = window.settings

        # Mode switch (main toolbar)
        self.mode_group = QActionGroup(window)
        self.act_mode_form = QAction("Fill && Sign", window, checkable=True)
        self.act_mode_form.setToolTip("Fill in form fields and place your signature")
        self.act_mode_markup = QAction("Comment", window, checkable=True)
        self.act_mode_markup.setToolTip("Draw boxes, lines, arrows, notes, and text; place stamps")
        edit_ui = window.edit_ui
        self.act_mode_edit = edit_ui.act_mode_edit
        self.act_mode_design = edit_ui.act_mode_design
        for action in (self.act_mode_form, self.act_mode_markup, self.act_mode_edit, self.act_mode_design):
            self.mode_group.addAction(action)
        self.act_mode_form.setChecked(True)
        self.act_mode_form.triggered.connect(lambda: self.set_mode("form"))
        self.act_mode_markup.triggered.connect(lambda: self.set_mode("markup"))
        self.act_mode_edit.triggered.connect(lambda: self.set_mode("edit"))
        self.act_mode_design.triggered.connect(lambda: self.set_mode("design"))
        controller.modeChanged.connect(self._on_mode)
        window.edit.modeChanged.connect(lambda _m: self._sync_mode_actions())

        # Markup toolbar (shown in Markup mode): Acrobat's Comment bar (user screenshot
        # 2026-10-03): no Select button there (the main toolbar's arrow is it); "Add text
        # comment" (a borderless text box) before Add text box; the style controls are three
        # drop-down buttons: color, line thickness, text (Aa)
        tb = QToolBar("Comment", window)
        tb.setObjectName("markupToolbar")
        tb.setMovable(False)
        self.toolbar = tb
        self.tool_group = QActionGroup(window)
        self.tool_actions: dict[Kind | None, QAction] = {}
        self.not_yet_actions: list[QAction] = []
        for kind, text, tip in TOOLS:
            action = QAction(text, window, checkable=True)
            action.setToolTip(tip)
            if kind is Kind.TEXTBOX:
                action.triggered.connect(lambda _c=False: controller.set_tool(Kind.TEXTBOX, borderless=False))
            else:
                action.triggered.connect(lambda _c=False, k=kind: controller.set_tool(k))
            self.tool_group.addAction(action)
            self.tool_actions[kind] = action
            if kind is not None:
                tb.addAction(action)
            if kind is Kind.CARET:
                self.act_text_comment = QAction("Add text comment", window, checkable=True)
                self.act_text_comment.setToolTip("Click and type: text on the page without a box around it")
                self.act_text_comment.triggered.connect(lambda _c=False: controller.set_tool(Kind.TEXTBOX, borderless=True))
                self.tool_group.addAction(self.act_text_comment)
                tb.addAction(self.act_text_comment)
            if kind is Kind.INK_ERASER:
                # Stamp ▾ and Attach sit after the pencil and eraser, as in Acrobat
                self.stamp_menu = QMenu("Stamp", window)
                self.stamp_menu.aboutToShow.connect(self._fill_stamp_menu)
                self.stamp_button = QToolButton(window)
                self.stamp_button.setText("Stamp")
                self.stamp_button.setToolTip("Add a stamp")
                self.stamp_button.setMenu(self.stamp_menu)
                self.stamp_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                tb.addWidget(self.stamp_button)
                attach = QAction("Attach file", window, checkable=True)
                attach.setToolTip("Attach a file: click where its paperclip goes, then choose the file")
                attach.triggered.connect(lambda _c=False: controller.set_tool(Kind.ATTACH))
                self.tool_group.addAction(attach)
                self.tool_actions[Kind.ATTACH] = attach
                tb.addAction(attach)
        self.tool_actions[None].setChecked(True)
        controller.toolChanged.connect(self._on_tool)
        # Collapse Drawing Tools (last on the bar) / the drop-down that replaces them: a click
        # takes the drawing tool used last, ▾ lists them all and Expand Drawing Tools
        self.act_collapse_drawing = QAction("Collapse Drawing Tools", window)
        self.act_collapse_drawing.setData("arrow-down-left-and-arrow-up-right-to-center")
        self.act_collapse_drawing.setToolTip("Collapse Drawing Tools: one button for all the drawing tools")
        self.act_collapse_drawing.triggered.connect(lambda: self.set_drawing_collapsed(True))
        tb.addAction(self.act_collapse_drawing)
        self.drawing_menu = QMenu("Drawing Tools", window)
        self.drawing_menu_actions: dict[Kind, QAction] = {}
        for kind in DRAWING_TOOLS:
            item = self.drawing_menu.addAction(self.tool_actions[kind].text(),
                                               lambda k=kind: self.tool_actions[k].trigger())
            self.drawing_menu_actions[kind] = item
        self.drawing_menu.addSeparator()
        self.act_expand_drawing = self.drawing_menu.addAction("Expand Drawing Tools",
                                                              lambda: self.set_drawing_collapsed(False))
        self.act_expand_drawing.setData("up-right-and-down-left-from-center")
        self.last_drawing_tool = Kind.RECT
        self.drawing_button = QToolButton(window)
        self.drawing_button.setCheckable(True)
        self.drawing_button.setMenu(self.drawing_menu)
        self.drawing_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.drawing_button.clicked.connect(lambda: self.tool_actions[self.last_drawing_tool].trigger())
        self._drawing_button_action = tb.addWidget(self.drawing_button)
        self.drawing_collapsed = False
        self.set_drawing_collapsed(s.collapse_drawing_tools)
        tb.addSeparator()

        # Color ▾ (line color, fill, opacity) · Line thickness ▾ · Aa ▾ (text)
        self.color_menu = QMenu("Color", window)
        self.act_line_color = self.color_menu.addAction("Line Color…", self._pick_color)
        self.color_menu.addSeparator()
        self.act_fill = self.color_menu.addAction("Fill Shapes")
        self.act_fill.setCheckable(True)
        self.act_fill.setChecked(s.markup_fill_enabled)
        self.act_fill.toggled.connect(self._fill_toggled)
        self.act_fill_color = self.color_menu.addAction("Fill Color…", self._pick_fill)
        self.color_menu.addSeparator()
        opacity = self.color_menu.addMenu("Opacity")
        self.opacity_actions = QActionGroup(window)
        for percent in (100, 75, 50, 25):
            act = opacity.addAction(f"{percent}%")
            act.setCheckable(True)
            act.setChecked(round(s.markup_opacity * 100) == percent)
            act.triggered.connect(lambda _c=False, p=percent: self.set_opacity(p / 100))
            self.opacity_actions.addAction(act)
        self.color_button = QToolButton(window)
        self.color_button.setToolTip("Color: line color, fill, and opacity of what you draw next (or of the selected comment)")
        self.color_button.setMenu(self.color_menu)
        self.color_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(self.color_button)
        self.width_menu = QMenu("Line Thickness", window)
        self.width_actions = QActionGroup(window)
        for width in (0.5, 1, 2, 3, 4, 6, 8, 12):
            act = self.width_menu.addAction(f"{width:g} pt")
            act.setCheckable(True)
            act.setChecked(abs(s.markup_width - width) < 0.01)
            act.triggered.connect(lambda _c=False, v=width: self.set_width(v))
            self.width_actions.addAction(act)
        self.width_button = QToolButton(window)
        self.width_button.setToolTip("Line thickness")
        self.width_button.setMenu(self.width_menu)
        self.width_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(self.width_button)
        self.text_menu = QMenu("Text", window)  # (filled by the window: the text format bar)
        self.text_button = QToolButton(window)
        self.text_button.setText("Aa")
        self.text_button.setToolTip("Text: font, size, bold, italic, underline, and color of text boxes and text comments")
        self.text_button.setMenu(self.text_menu)
        self.text_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(self.text_button)
        tb.setVisible(False)  # the window adds it on its own row, below the main toolbar
        self._update_swatches()

        # Sign menu / toolbar button (available in both modes)
        self.sign_menu = QMenu("&Sign", window)
        self.act_place_signature = QAction("Place &Signature", window)
        self.act_place_signature.triggered.connect(lambda: controller.place_signature("signature"))
        self.act_place_initials = QAction("Place &Initials", window)
        self.act_place_initials.triggered.connect(lambda: controller.place_signature("initials"))
        self.act_manage_signatures = QAction("&Manage Signatures and Initials…", window)
        self.act_manage_signatures.triggered.connect(window.manage_signatures)
        self.act_digital_sign = QAction("&Digitally Sign Document…", window)
        self.act_digital_sign.triggered.connect(lambda: window.digitally_sign())  # (not Qt's "checked" as certify)
        self.act_check_signatures = QAction("&Check Signatures", window)
        self.act_check_signatures.triggered.connect(window.check_signatures)
        for action in (self.act_place_signature, self.act_place_initials, self.act_digital_sign, self.act_check_signatures):
            window._doc_actions.append(action)
        # Every saved signature and initials is listed (with a preview) so you pick which
        # one to place; the list is rebuilt each time the menu opens.
        self._saved_actions: dict[QMenu, list[QAction]] = {}
        self._saved_marker = self.sign_menu.addSeparator()
        self.sign_menu.addAction(self.act_manage_signatures)
        self.sign_menu.addSeparator()
        self.sign_menu.addActions([self.act_digital_sign, self.act_check_signatures])
        self.sign_menu.aboutToShow.connect(lambda: self.fill_saved_signatures(self.sign_menu, self._saved_marker))
        # The same list as a submenu (Fill & Sign menu).
        self.choose_menu = QMenu("Place Signature or &Initials", window)
        self.choose_menu.aboutToShow.connect(lambda: self.fill_saved_signatures(self.choose_menu))
        # (No "Sign ▾" toolbar button any more: Fill & Sign ▸ Sign Yourself replaces it. A
        # button that isn't on a toolbar is drawn over the menu bar's corner.)

        # Markup menu
        self.markup_menu = QMenu("&Comment", window)
        self.markup_menu.addAction(self.act_mode_markup)
        self.markup_menu.addSeparator()
        for kind, text, _tip in TOOLS[1:]:
            self.markup_menu.addAction(text, lambda k=kind: (self.set_mode("markup"), controller.set_tool(k)))
        self.markup_menu.addAction("Attach file", lambda: (self.set_mode("markup"), controller.set_tool(Kind.ATTACH)))
        self.markup_menu.addSeparator()
        self.markup_menu.addMenu(self.stamp_menu)
        self.act_manage_stamps = QAction("Manage &Stamps and Seals…", window)
        self.act_manage_stamps.triggered.connect(window.manage_stamps)
        self.markup_menu.addAction(self.act_manage_stamps)

    # --- choosing a saved signature --------------------------------------------------------------

    def fill_saved_signatures(self, menu: QMenu, before: QAction | None = None) -> list[QAction]:
        """(Re)list the saved signatures and initials in ``menu`` (above ``before``)."""
        from planetpdf.ui.viewer.qimage import png_preview

        if before is None:
            menu.clear()  # a menu of its own: rebuild it whole
        for action in self._saved_actions.pop(menu, []):
            menu.removeAction(action)
            action.deleteLater()
        window = self.window
        library = window.signature_library
        has_doc = window.session is not None
        added: list[QAction] = []

        def add(action: QAction) -> None:
            menu.insertAction(before, action) if before is not None else menu.addAction(action)
            added.append(action)

        for kind, title in (("signature", "Signatures"), ("initials", "Initials")):
            items = library.of_kind(kind)
            if not items:
                continue
            header = menu.insertSection(before, title) if before is not None else menu.addSection(title)
            added.append(header)
            for n, item in enumerate(items):
                try:
                    picture = png_preview(library.png(item), 150, 44)
                except OSError:
                    picture = None
                action = SignatureMenuAction(picture, item.name + ("   (default)" if n == 0 else ""), menu)
                action.setToolTip(f"Place this {kind}")
                action.setEnabled(has_doc)
                action.setData(item.sig_id)
                action.triggered.connect(lambda _c=False, sid=item.sig_id, k=kind: self.place_saved(sid, k))
                add(action)
        if added:
            separator = QAction(menu)
            separator.setSeparator(True)
            add(separator)
        for kind, text in (("signature", "&New Signature…"), ("initials", "New &Initials…")):
            action = QAction(text, menu)
            action.setEnabled(has_doc)
            action.triggered.connect(lambda _c=False, k=kind: self.new_and_place(k))
            add(action)
        if before is None:
            menu.addSeparator()
            menu.addAction(self.act_manage_signatures)
        else:
            self._saved_actions[menu] = added
        return added

    def place_saved(self, sig_id: str, kind: str) -> bool:
        """Place this saved signature/initials; it becomes the default for next time."""
        library = self.window.signature_library
        library.make_default(sig_id)
        return self.window.markup.place_signature(kind, sig_id)

    def new_and_place(self, kind: str) -> bool:
        if not self.window.create_signature(kind):
            return False
        return self.window.markup.place_signature(kind)  # the new one is the default

    # --- reacting to the controller -------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """"form", "markup", "edit" (page content) or "design" (Prepare Form)."""
        if mode in ("edit", "design"):
            self.window.edit.set_mode(mode)
        else:
            self.window.markup.set_markup_mode(mode == "markup")  # also leaves Edit / Prepare Form
        self._sync_mode_actions()

    @property
    def mode(self) -> str:
        edit = self.window.edit.mode
        return edit if edit else ("markup" if self.window.markup.markup_mode else "form")

    def _sync_mode_actions(self) -> None:
        mode = self.mode
        for name, action in (("form", self.act_mode_form), ("markup", self.act_mode_markup),
                             ("edit", self.act_mode_edit), ("design", self.act_mode_design)):
            action.setChecked(name == mode)
        # (which tool bar shows is up to ui/toolbars.ToolPanes)

    def _on_mode(self, markup: bool) -> None:
        self._sync_mode_actions()
        if markup:
            self.window.show_comments_panel()

    def set_drawing_collapsed(self, collapsed: bool) -> None:
        """The eight drawing tools on the bar, or one drop-down button in their place."""
        self.drawing_collapsed = collapsed
        self.window.settings.collapse_drawing_tools = collapsed
        for kind in DRAWING_TOOLS:
            self.tool_actions[kind].setVisible(not collapsed)
        self.act_collapse_drawing.setVisible(not collapsed)
        self._drawing_button_action.setVisible(collapsed)
        self.update_drawing_button()

    def update_drawing_button(self) -> None:
        """The drop-down shows the drawing tool used last, switched on while it's in use."""
        action = self.tool_actions[self.last_drawing_tool]
        self.drawing_button.setIcon(action.icon())
        self.drawing_button.setToolTip(f"{action.text()} (▾ the other drawing tools)")
        self.drawing_button.setChecked(self.window.markup.tool is self.last_drawing_tool)

    def _on_tool(self, tool) -> None:
        if tool in DRAWING_TOOLS:
            self.last_drawing_tool = tool
        self.update_drawing_button()
        action = self.tool_actions.get(tool)
        if tool is Kind.TEXTBOX and self.window.markup.textbox_borderless:
            action = self.act_text_comment
        if action is not None:
            action.setChecked(True)
        else:
            self.tool_actions[None].setChecked(True)
        self._update_swatches()

    def _current_kind(self) -> Kind | None:
        return self.window.markup.tool if self.window.markup.tool in self.tool_actions else None

    def _update_swatches(self) -> None:
        """The color button: a paint bucket over a bar in the current color (Acrobat)."""
        from planetpdf.ui import fa

        kind = self._current_kind()
        style = self.window.markup.style_for(kind or Kind.RECT)
        color = QColor.fromRgbF(*style.color)
        bucket = fa.icon("fill-drip").pixmap(20, 20)
        pixmap = QPixmap(22, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.drawPixmap(1, 0, bucket)
        painter.fillRect(1, 20, 20, 4, color)
        painter.end()
        self.color_button.setIcon(QIcon(pixmap))
        self.color_button.setToolTip(f"Color for {kind.label.lower() if kind else 'shapes'}: line color, fill, and opacity")
        self.act_fill_color.setEnabled(self.act_fill.isChecked())

    def set_width(self, width: float) -> None:
        self.window.settings.markup_width = float(width)
        self.window.markup.update_draw_style()
        self.window.markup.restyle_selected(width=float(width))

    def set_opacity(self, opacity: float) -> None:
        self.window.settings.markup_opacity = float(opacity)
        self.window.markup.update_draw_style()
        self.window.markup.restyle_selected(opacity=float(opacity))

    def _pick_color(self) -> None:
        kind = self._current_kind()
        current = QColor.fromRgbF(*self.window.markup.style_for(kind or Kind.RECT).color)
        color = QColorDialog.getColor(current, self.window, "Markup Color")
        if color.isValid():
            self.window.markup.set_color(kind, color)
            # (and the selected comment, as thickness and opacity do: its tooltip says so)
            self.window.markup.restyle_selected(color=(color.redF(), color.greenF(), color.blueF()))
            self._update_swatches()

    def _pick_fill(self) -> None:
        color = QColorDialog.getColor(QColor(self.window.settings.markup_fill), self.window, "Fill Color")
        if color.isValid():
            self.window.settings.markup_fill = color.name()
            self._update_swatches()
            self.window.markup.update_draw_style()

    def _fill_toggled(self, on: bool) -> None:
        self.window.settings.markup_fill_enabled = on
        self._update_swatches()
        self.window.markup.update_draw_style()

    def _fill_stamp_menu(self) -> None:
        self.window.stamp_ui.fill_menu(self.stamp_menu)  # ui/stamp_ui.py (Acrobat's Stamp ▾)


class SignatureMenuAction(QWidgetAction):
    """A menu row showing the signature itself at a readable size (menu icons are 16 px)."""

    def __init__(self, picture, text: str, menu: QMenu, size: tuple[int, int] = (150, 44)) -> None:
        super().__init__(menu)
        self.setText(text)
        self._menu = menu
        self._picture = picture
        self._size = size

    def createWidget(self, parent):
        row = _SignatureRow(self, self._picture, self.text(), parent, self._size)
        row.setEnabled(self.isEnabled())
        return row


class _SignatureRow(QWidget):
    def __init__(self, action: QWidgetAction, picture, text: str, parent, size: tuple[int, int] = (150, 44)) -> None:
        super().__init__(parent)
        self._action = action
        self.setObjectName("signatureRow")
        self.setAutoFillBackground(True)  # hover highlight is done in enter/leaveEvent (a
        # ":hover QLabel" style rule matched even without the mouse over it: white text)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 3, 12, 3)
        image = QLabel(self)
        image.setFixedSize(*size)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setStyleSheet("background: white; border: 1px solid #d0d4da;")
        if picture is not None:
            image.setPixmap(picture)
        layout.addWidget(image)
        self.label = QLabel(text, self)
        layout.addWidget(self.label, 1)
        self.label.setVisible(bool(text))
        self._highlight(False)

    def _highlight(self, on: bool) -> None:
        from PySide6.QtGui import QPalette

        palette = self.parentWidget().palette() if self.parentWidget() else self.palette()
        background = palette.color(QPalette.ColorRole.Highlight if on else QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.HighlightedText if on else QPalette.ColorRole.WindowText)
        mine = self.palette()
        mine.setColor(QPalette.ColorRole.Window, background)
        self.setPalette(mine)
        label_palette = self.label.palette()
        label_palette.setColor(QPalette.ColorRole.WindowText, text)
        self.label.setPalette(label_palette)

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._highlight(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._highlight(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.isEnabled():
            self._action.trigger()
            menu = self._action.parent()
            while isinstance(menu, QMenu):  # close the menu (and its parent menus)
                menu.close()
                menu = menu.parent() if isinstance(menu.parent(), QMenu) else None
