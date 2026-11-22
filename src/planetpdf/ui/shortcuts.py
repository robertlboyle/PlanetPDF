"""Adobe Acrobat keyboard shortcuts (user decision 2026-09-30: Acrobat users should feel
at home), in one table so they're easy to review, and the Help ▸ Keyboard Shortcuts list.

Qt maps "Ctrl" to Cmd on macOS, which matches Acrobat for Mac. Acrobat's single-key
tool accelerators (H, V, Z, ...) are off by default in Acrobat too and aren't used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QMenu, QTableWidget, QTableWidgetItem, QVBoxLayout

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

# window attribute -> shortcuts (first one is shown in menus)
ACROBAT: dict[str, list[str]] = {
    "act_open": ["Ctrl+O"],
    "act_save": ["Ctrl+S"],
    "act_save_as": ["Ctrl+Shift+S"],
    "act_close": ["Ctrl+W", "Ctrl+F4"],
    "act_next_tab": ["Ctrl+Tab", "Ctrl+F6"],  # the next / previous document tab
    "act_previous_tab": ["Ctrl+Shift+Tab", "Ctrl+Shift+F6"],
    "act_next_pane": ["F6"],  # the keyboard to the next / previous part of the window
    "act_previous_pane": ["Shift+F6"],
    "act_quit": ["Ctrl+Q"],
    "act_print": ["Ctrl+P"],
    "act_properties": ["Ctrl+D"],  # Document Properties
    "act_preferences": ["Ctrl+K", "Ctrl+,"],
    "act_undo": ["Ctrl+Z"],
    "act_redo": ["Ctrl+Shift+Z"],  # (Ctrl+Y is Zoom To in Acrobat)
    "act_find": ["Ctrl+F"],
    "act_advanced_search": ["Ctrl+Shift+F"],
    "act_zoom_in": ["Ctrl+=", "Ctrl++"],
    "act_zoom_out": ["Ctrl+-"],
    "act_zoom_to": ["Ctrl+Y"],
    "act_actual": ["Ctrl+1"],  # Actual Size
    "act_fit_page": ["Ctrl+0"],  # Zoom to Page Level
    "act_fit_width": ["Ctrl+2"],  # Fit Width
    "act_fit_visible": ["Ctrl+3"],  # Fit Visible
    "act_reflow": ["Ctrl+4"],
    "act_rotate_view_cw": ["Ctrl+Shift++"],  # View ▸ Rotate View (display only)
    "act_rotate_view_ccw": ["Ctrl+Shift+-"],
    "act_go_to_page": ["Ctrl+Shift+N"],
    "act_back": ["Alt+Left"],  # Previous View
    "act_forward": ["Alt+Right"],  # Next View
    "act_read_mode": ["Ctrl+H"],
    "act_auto_scroll": ["Shift+Ctrl+H"],  # Automatically Scroll
    "act_rotate": ["Ctrl+Shift+R"],  # Rotate Pages
    "act_insert_file": ["Ctrl+Shift+I"],  # Insert Pages from File
    "act_delete_pages_menu": ["Ctrl+Shift+D"],  # Delete Pages
    "act_crop": ["Ctrl+Shift+T"],  # Crop Pages
    "act_full_screen": ["Ctrl+L"],
    "act_toggle_nav": ["F4"],  # Show/Hide Navigation Panes
    "act_tools_pane": ["Shift+F4"],  # Show/Hide Tools Pane
    "act_rulers": ["Ctrl+R"],  # Rulers & Grids
    "act_grid": ["Ctrl+U"],
    "act_snap": ["Shift+Ctrl+U"],
    "act_guides": ["Ctrl+;"],
    "act_toggle_toolbars": ["F8"],
    "act_toggle_menubar": ["F9"],
    "act_help": ["F1"],  # Acrobat: F1 = Help (Keyboard Shortcuts is in the manual too)
    # View ▸ Read Out Loud (Acrobat's keys)
    "act_read_activate": ["Shift+Ctrl+Y"],
    "act_read_page": ["Shift+Ctrl+V"],
    "act_read_end": ["Shift+Ctrl+B"],
    "act_read_pause": ["Shift+Ctrl+C"],
    "act_read_stop": ["Shift+Ctrl+E"],
    "act_text_larger": ["Ctrl+]"],  # placed text / text box (not an Acrobat default)
    "act_text_smaller": ["Ctrl+["],
}
# Only while the page view has focus (arrow keys belong to lists and text boxes elsewhere).
VIEW_ONLY: dict[str, list[str]] = {
    "act_view_prev": ["Left", "Shift+Left"],  # with an item selected: nudge it 1 pt (Shift: 10 pt)
    "act_view_next": ["Right", "Shift+Right"],
    "act_view_up": ["Up", "Shift+Up"],
    "act_view_down": ["Down", "Shift+Down"],
    "act_view_first": ["Home", "Ctrl+Home"],
    "act_view_last": ["End", "Ctrl+End"],
    # Edit menu (page view: lists and text boxes keep their own clipboard keys)
    "act_cut": ["Ctrl+X"],
    "act_copy": ["Ctrl+C"],
    "act_paste": ["Ctrl+V"],
    "act_select_all": ["Ctrl+A"],
    "act_deselect_all": ["Ctrl+Shift+A"],
}
VIEW_ONLY_LABELS = {
    "act_view_prev": "Previous Page, or nudge the selected item left", "act_view_next": "Next Page, or nudge right",
    "act_view_up": "Scroll up, or nudge the selected item up", "act_view_down": "Scroll down, or nudge down",
    "act_view_first": "First Page", "act_view_last": "Last Page",
}


def apply_acrobat_shortcuts(window: MainWindow) -> None:
    for table in (ACROBAT, VIEW_ONLY):
        for name, keys in table.items():
            action: QAction | None = getattr(window, name, None)
            if action is None:
                continue
            action.setShortcuts([QKeySequence(k) for k in keys])
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and not hasattr(window, "_rotate_view_keys"):
        window._rotate_view_keys = RotateViewKeys(window)
        app.installEventFilter(window._rotate_view_keys)


class RotateViewKeys(QObject):
    """Shift+Ctrl+Plus / Minus always turn the view (Acrobat's Rotate View), never zoom.

    On a keyboard where "+" is Shift+=, the key Qt gets is Ctrl+Shift++, which matches both
    Rotate View (Ctrl+Shift++) and Zoom In (Ctrl++), and Qt picked either one (user 2026-11-21:
    "zooms in most of the time but sometimes rotates"). So the key is taken here first: the
    shortcut is held back (ShortcutOverride accepted) and the press turns the view."""

    CW = (Qt.Key.Key_Plus, Qt.Key.Key_Equal)
    CCW = (Qt.Key.Key_Minus, Qt.Key.Key_Underscore)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window

    def action_for(self, event) -> QAction | None:
        wanted = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        held = event.modifiers() & (wanted | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        if held != wanted:
            return None
        name = "act_rotate_view_cw" if event.key() in self.CW else (
            "act_rotate_view_ccw" if event.key() in self.CCW else None)
        return getattr(self.window, name, None) if name else None

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        action = self.action_for(event)
        if action is None:
            return False
        from PySide6.QtWidgets import QApplication

        if QApplication.activeWindow() is not self.window:
            return False
        event.accept()
        if kind == QEvent.Type.KeyPress and action.isEnabled():
            action.trigger()
        return True


def _plain(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").replace("…", "")


def shortcut_rows(window: MainWindow) -> list[tuple[str, str, str]]:
    """(menu path, command, shortcuts) for every menu command that has a shortcut."""
    rows: list[tuple[str, str, str]] = []

    def walk(menu: QMenu, path: str) -> None:
        for action in menu.actions():
            if action.menu() is not None:
                walk(action.menu(), f"{path} ▸ {_plain(action.menu().title())}")
            elif action.isSeparator() or action.property("not_yet"):
                continue
            elif action.shortcuts():
                keys = ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts())
                rows.append((path, _plain(action.text().split("\t")[0]), keys))
            elif "\t" in action.text():  # shown like Acrobat; the key works in the page view
                command, keys = action.text().split("\t", 1)
                rows.append((path, _plain(command), keys))

    for top in window.menuBar().actions():
        if top.menu() is not None:
            walk(top.menu(), _plain(top.menu().title()))
    extra = [(getattr(window, name), f"{label} (in the page view)", "View ▸ Page Navigation")
             for name, label in VIEW_ONLY_LABELS.items()]
    extra += [(window.find_bar.act_next, "Find Next", "Edit"), (window.find_bar.act_prev, "Find Previous", "Edit")]
    for action, text, path in extra:
        keys = ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts())
        rows.append((path, text, keys))
    return rows


class ShortcutsDialog(QDialog):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(620, 560)
        rows = shortcut_rows(window)
        self.table = QTableWidget(len(rows), 3, self)
        self.table.setAccessibleName("Keyboard shortcuts")
        self.table.setHorizontalHeaderLabels(["Menu", "Command", "Shortcut"])
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, item)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(buttons)
