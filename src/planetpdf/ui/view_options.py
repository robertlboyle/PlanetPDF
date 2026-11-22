"""View-menu options that only change what the window shows (Acrobat; built 2026-10-02):
Page Display ▸ Show Gaps Between Pages; Show/Hide ▸ Rulers & Grids (rulers, grid, snap to
grid, guides), Cursor Coordinates, Button Labels, Page Controls, Tools Pane; and
Edit ▸ Search More Tools (find any command by name). Choices are remembered."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

LABEL_MODES = {"none": "&No Labels", "default": "&Default Labels", "all": "&All Labels"}


class ViewOptions:
    def __init__(self, window: MainWindow) -> None:
        self.w = window
        s = window.settings
        view = window.view

        def toggle(text: str, setting: str, apply, tip: str = "") -> QAction:
            action = QAction(text, window, checkable=True)
            action.setChecked(bool(getattr(s, setting)))
            action.toggled.connect(lambda on: (setattr(s, setting, on), apply(on)))
            if tip:
                action.setToolTip(tip)
            return action

        self.act_gaps = toggle("Show &Gaps Between Pages", "show_page_gaps", view.set_show_gaps)
        self.act_rulers = toggle("&Rulers", "show_rulers", view.set_rulers,
                                 "Inch rulers along the top and left; drag from one onto a page for a guide")
        self.act_grid = toggle("&Grid", "show_grid", view.set_grid, "Lines every inch (with quarter-inch lines)")
        self.act_snap = toggle("&Snap to Grid", "snap_to_grid", view.set_snap,
                               "Drawing, moving, and guides land on the nearest quarter inch")
        self.act_guides = toggle("G&uides", "show_guides", view.set_guides_shown,
                                 "Show the guides (drag from a ruler onto a page to add one)")
        self.act_clear_guides = QAction("&Clear Guides", window)
        self.act_clear_guides.triggered.connect(view.clear_guides)
        self.act_cursor = toggle("C&ursor Coordinates", "cursor_coordinates", self._show_coordinates,
                                 "Where the mouse is on the page, in inches from its top-left corner")
        self.act_page_controls = toggle("Show &Page Controls", "page_controls", self._show_page_controls,
                                        "A small page and zoom bar at the bottom of the page view")
        self.act_tools_pane = toggle("&Tools Pane", "tools_pane", self._show_tools_pane, "The tool strip on the right")
        self.label_group = QActionGroup(window)
        self.label_actions: dict[str, QAction] = {}
        for key, text in LABEL_MODES.items():
            action = QAction(text, window, checkable=True)
            action.setChecked(s.button_labels == key)
            action.triggered.connect(lambda _c=False, k=key: self.set_button_labels(k))
            self.label_group.addAction(action)
            self.label_actions[key] = action
        self.act_search_tools = QAction("Searc&h More Tools…", window)
        self.act_search_tools.setToolTip("Find any command by name")
        self.act_search_tools.triggered.connect(self.search_tools)
        for name in ("act_gaps", "act_rulers", "act_grid", "act_snap", "act_guides", "act_clear_guides", "act_cursor",
                     "act_page_controls", "act_tools_pane", "act_search_tools"):
            setattr(window, name, getattr(self, name))  # (ui/shortcuts.py finds them on the window)
        self.coordinates = QLabel(window)
        self.coordinates.setMinimumWidth(170)
        self.coordinates.hide()
        view.cursorMoved.connect(self._on_cursor)
        self.page_controls: PageControls | None = None
        self._styles: dict[int, Qt.ToolButtonStyle] = {}

    def apply_saved(self) -> None:
        """Once the toolbars exist: put every remembered choice into effect."""
        s = self.w.settings
        view = self.w.view
        view.set_show_gaps(s.show_page_gaps)
        view.set_grid(s.show_grid)
        view.set_snap(s.snap_to_grid)
        view.set_guides_shown(s.show_guides)
        if s.show_rulers:
            view.set_rulers(True)
        if s.cursor_coordinates:  # (the status bar only when it's needed: Qt makes it on first use)
            self._show_coordinates(True)
        self._show_page_controls(s.page_controls)
        self._show_tools_pane(s.tools_pane)
        if s.button_labels != "default":
            self.set_button_labels(s.button_labels)

    # --- cursor coordinates -----------------------------------------------------------------------

    def _show_coordinates(self, on: bool) -> None:
        if on and self.coordinates.parent() is self.w:  # (not on the status bar yet)
            self.w.statusBar().addPermanentWidget(self.coordinates)
        self.w.view.track_cursor = on
        self.coordinates.setVisible(on)
        self.coordinates.setText("")

    def _on_cursor(self, page: int, point: QPointF) -> None:
        if not self.coordinates.isVisible():
            return
        if page < 0:
            self.coordinates.setText("")
            return
        self.coordinates.setText(f"Page {page + 1}   X: {point.x() / 72:.2f} in   Y: {point.y() / 72:.2f} in")

    # --- tools pane, button labels -----------------------------------------------------------------

    def _show_tools_pane(self, on: bool) -> None:
        panes = getattr(self.w, "tool_panes", None)
        if panes is not None:
            panes.strip.setVisible(on)

    def _buttons(self) -> list[QToolButton]:
        panes = getattr(self.w, "tool_panes", None)
        strip = panes.strip if panes is not None else None
        out = []
        for bar in self.w.findChildren(QToolBar):
            if bar is strip:  # (the tool strip stays icons only, as in Acrobat)
                continue
            out += [b for b in bar.findChildren(QToolButton) if not b.icon().isNull()]
        return out

    def set_button_labels(self, mode: str) -> None:
        """View ▸ Show/Hide ▸ Button Labels: no labels (icons only), the default mix, or a
        label beside every icon."""
        self.w.settings.button_labels = mode
        self.label_actions[mode].setChecked(True)
        for button in self._buttons():
            original = self._styles.setdefault(id(button), button.toolButtonStyle())
            if mode == "none":
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            elif mode == "all":
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            else:
                button.setToolButtonStyle(original)

    # --- page controls ---------------------------------------------------------------------------

    def _show_page_controls(self, on: bool) -> None:
        if on and self.page_controls is None:
            self.page_controls = PageControls(self.w)
        if self.page_controls is not None:
            self.page_controls.setVisible(on)
            self.page_controls.place()

    # --- search more tools -----------------------------------------------------------------------

    def search_tools(self) -> None:
        dialog = CommandSearch(self.w)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen is not None:
            dialog.chosen.trigger()


class PageControls(QFrame):
    """Show Page Controls: previous / next page, "3 / 10", zoom out / in, floating over the
    bottom of the page view (Acrobat)."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window.view)
        self.w = window
        self.setObjectName("pageControls")
        self.setStyleSheet("#pageControls { background: rgba(40, 40, 40, 210); border-radius: 8px; }"
                           "#pageControls QLabel { color: white; padding: 0 6px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        for action in (window.act_prev, window.act_next):
            row.addWidget(self._button(action))
        self.label = QLabel(self)
        row.addWidget(self.label)
        for action in (window.act_zoom_out, window.act_zoom_in):
            row.addWidget(self._button(action))
        window.view.currentPageChanged.connect(lambda _p: self._update())
        window.view.zoomChanged.connect(lambda _z: self._update())
        window.view.installEventFilter(self)
        self._update()

    def _button(self, action: QAction) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        return button

    def _update(self) -> None:
        view = self.w.view
        count = view.page_count
        self.label.setText(f"{view.current_page + 1} / {count}   {round(view.zoom * 100)}%" if count else "")
        self.adjustSize()
        self.place()

    def place(self) -> None:
        vp = self.w.view.viewport().geometry()
        self.move(vp.center().x() - self.width() // 2, vp.bottom() - self.height() - 14)
        self.raise_()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.place()
        return False


class CommandSearch(QDialog):
    """Edit ▸ Search More Tools (Acrobat): type part of a command's name; Enter runs it."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.setWindowTitle("Search Tools")
        self.resize(520, 420)
        self.chosen: QAction | None = None
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Search for a command, e.g. watermark, rotate, OCR")
        self.list = QListWidget(self)
        self.list.setAccessibleName("Commands")
        self.commands = list(_menu_commands(window))
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda item: self._run(item))
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(self.list)
        hint = QLabel("Enter runs the selected command.", self)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        self._filter("")

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self.list.clear()
        for path, action in self.commands:
            if all(w in path.lower() for w in words):
                item = QListWidgetItem(path, self.list)
                item.setData(Qt.ItemDataRole.UserRole, action)
                if not action.isEnabled():
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(i)
                break

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._run(item)

    def _run(self, item: QListWidgetItem) -> None:
        action = item.data(Qt.ItemDataRole.UserRole)
        if action is not None and action.isEnabled():
            self.chosen = action
            self.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):  # (while typing: move through the list)
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            row = self.list.currentRow() + step
            while 0 <= row < self.list.count():
                if self.list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                    self.list.setCurrentRow(row)
                    break
                row += step
            return
        super().keyPressEvent(event)


def _menu_commands(window: QWidget):
    """(menu path ▸ command, action) for every command on the menus (not the grayed-out
    "not available yet" ones)."""
    seen: set[int] = set()

    def walk(menu: QMenu, path: str):
        for action in menu.actions():
            if action.isSeparator() or action.property("not_yet"):
                continue
            text = action.text().replace("&&", "\0").replace("&", "").replace("\0", "&").split("\t")[0]
            if action.menu() is not None:
                yield from walk(action.menu(), f"{path} ▸ {text}")
            elif text and id(action) not in seen:
                seen.add(id(action))
                yield f"{path} ▸ {text}", action

    for top in window.menuBar().actions():
        if top.menu() is not None:
            yield from walk(top.menu(), top.text().replace("&", ""))
