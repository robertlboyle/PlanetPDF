"""Working without a mouse (user 2026-10-25: blind and low-vision users).

- **F6 / Shift+F6** (Acrobat's keys) move the keyboard focus between the parts of the window:
  the main toolbar, the open tool's bar, the Find bar, the page (or Reflow's text, the Organize
  grid, a start page), the Pages / Bookmarks panel, each open panel, and the tool strip. A screen
  reader is told which part it is.
- **Inside a toolbar** the arrow keys go from button to button (Home / End: first / last),
  Enter or Space presses the button (or opens its menu), Esc goes back to the page.
- **A focus ring**: Qt's Fusion style draws no focus mark on toolbar buttons or around the page,
  so a 2 px ring in the highlight color shows where the keyboard is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QDockWidget, QToolBar, QToolButton, QWidget)

from planetpdf.ui import accessibility

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                    Qt.FocusReason.ShortcutFocusReason)


def _usable(widget: QWidget | None) -> bool:
    return widget is not None and widget.isVisible() and widget.isEnabled()


def toolbar_buttons(bar: QToolBar) -> list[QWidget]:
    """The toolbar's buttons and boxes, in order, that can be used now."""
    out = []
    for action in bar.actions():
        widget = bar.widgetForAction(action)
        if action.isVisible() and _usable(widget) and not action.isSeparator():
            out.append(widget)
    return out


def first_focusable(container: QWidget) -> QWidget | None:
    """Where the keyboard goes when it enters ``container``."""
    if isinstance(container, QToolBar):
        buttons = toolbar_buttons(container)
        return buttons[0] if buttons else None
    if container.focusPolicy() & Qt.FocusPolicy.TabFocus and _usable(container):
        return container
    widget = container.nextInFocusChain()
    while widget is not None and widget is not container:
        if container.isAncestorOf(widget) and widget.focusPolicy() & Qt.FocusPolicy.TabFocus and _usable(widget):
            return widget
        widget = widget.nextInFocusChain()
    return None


class PaneCycler:
    """F6 / Shift+F6 for one main window."""

    def __init__(self, window: MainWindow) -> None:
        self.w = window

    def regions(self) -> list[tuple[str, QWidget]]:
        """(name, container) for every part of the window showing now, in order."""
        w = self.w
        out: list[tuple[str, QWidget]] = []
        bars = {bar.objectName(): bar for bar in w.findChildren(QToolBar)}
        main = bars.get("mainToolbar")
        if _usable(main):
            out.append(("Main toolbar", main))
        for name, bar in bars.items():
            if name.startswith("tool_") and _usable(bar) and toolbar_buttons(bar):
                out.append((f"{bar.windowTitle() or 'Tool'} bar", bar))
        for name, title in (("textFormatBar", "Text format bar"), ("findToolbar", "Find bar")):
            if _usable(bars.get(name)):
                out.append((title, bars[name]))
        center = w.central.currentWidget()
        if _usable(center):
            out.append(("Page" if center is w.view else center.accessibleName() or "Document", center))
        if w.side_panel.is_open and _usable(w.side_panel.stack.currentWidget()):
            current = w.side_panel.stack.currentWidget()
            out.append(("Bookmarks panel" if current is w.bookmarks_panel else "Pages panel", current))
        for dock in w.findChildren(QDockWidget):
            if _usable(dock) and dock.widget() is not None:
                out.append((f"{dock.windowTitle()} panel", dock.widget()))
        strip = bars.get("toolStrip")
        if _usable(strip):
            out.append(("Tools", strip))
        return [(name, widget) for name, widget in out if first_focusable(widget) is not None]

    def step(self, direction: int) -> None:
        regions = self.regions()
        if not regions:
            return
        focus = self.w.focusWidget()  # (where the keyboard is in this window)
        here = next((i for i, (_name, widget) in enumerate(regions)
                     if focus is not None and (widget is focus or widget.isAncestorOf(focus))), None)
        if here is None:  # (from somewhere else: the page first, or the end going back)
            pages = [i for i, (name, _w) in enumerate(regions) if regions[i][1] is self.w.central.currentWidget()]
            index = pages[0] if pages and direction > 0 else (len(regions) - 1 if direction < 0 else 0)
        else:
            index = (here + direction) % len(regions)
        name, container = regions[index]
        target = first_focusable(container)
        target.setFocus(Qt.FocusReason.TabFocusReason)
        accessibility.announce(target, name)


class FocusRing(QWidget):
    """A ring around the widget the keyboard is on, where the style shows none."""

    WIDTH = 2

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.target: QWidget | None = None
        self.hide()

    def show_on(self, target: QWidget) -> None:
        self.target = target
        top_left = target.mapTo(self.parentWidget(), QPoint(0, 0))
        inside = isinstance(target, QAbstractScrollArea)  # (a big area: drawn just inside it)
        pad = -1 if inside else self.WIDTH
        self.setGeometry(QRect(top_left, target.size()).adjusted(-pad, -pad, pad, pad))
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight), self.WIDTH)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 3, 3)


def _ring_for(widget: QWidget) -> bool:
    """Widgets the style draws no focus mark on."""
    from planetpdf.ui.viewer.page_view import PageView

    return (isinstance(widget, QToolButton) and isinstance(widget.parentWidget(), QToolBar)) or isinstance(widget, PageView)


class KeyboardHelper(QObject):
    """Arrow keys inside toolbars and the focus ring (one for the whole application)."""

    @staticmethod
    def ring(window: QWidget) -> FocusRing:
        """The window's focus ring (made on first use)."""
        ring = window.findChild(FocusRing, options=Qt.FindChildOption.FindDirectChildrenOnly)
        return ring if ring is not None else FocusRing(window)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        kind = event.type()
        if kind == QEvent.Type.FocusIn and isinstance(watched, QWidget) and _ring_for(watched):
            if event.reason() in KEYBOARD_REASONS:
                self.ring(watched.window()).show_on(watched)
        elif kind == QEvent.Type.FocusOut and isinstance(watched, QWidget) and _ring_for(watched):
            ring = watched.window().findChild(FocusRing, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if ring is not None and ring.target is watched:
                ring.hide()
        elif kind in (QEvent.Type.Resize, QEvent.Type.Move) and isinstance(watched, QWidget) and _ring_for(watched):
            ring = watched.window().findChild(FocusRing, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if ring is not None and ring.target is watched and ring.isVisible():
                ring.show_on(watched)  # (the window was resized: the ring stays around it)
        elif kind == QEvent.Type.KeyPress and isinstance(watched, QToolButton) \
                and isinstance(watched.parentWidget(), QToolBar) and watched.hasFocus():
            return self._toolbar_key(watched, event)
        return False

    def _toolbar_key(self, button: QToolButton, event) -> bool:
        bar: QToolBar = button.parentWidget()
        buttons = toolbar_buttons(bar)
        if button not in buttons:
            return False
        key = event.key()
        across = bar.orientation() == Qt.Orientation.Horizontal
        back = Qt.Key.Key_Left if across else Qt.Key.Key_Up
        forward = Qt.Key.Key_Right if across else Qt.Key.Key_Down
        index = buttons.index(button)
        target = None
        if key == forward:
            target = buttons[(index + 1) % len(buttons)]
        elif key == back:
            target = buttons[(index - 1) % len(buttons)]
        elif key == Qt.Key.Key_Home:
            target = buttons[0]
        elif key == Qt.Key.Key_End:
            target = buttons[-1]
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if button.menu() is not None and (button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
                                              or event.modifiers() & Qt.KeyboardModifier.AltModifier):
                button.showMenu()
            else:
                button.click()
            return True
        elif key == Qt.Key.Key_Down and across and button.menu() is not None:
            button.showMenu()  # (a button with a ▾ menu)
            return True
        elif key == Qt.Key.Key_Escape:
            window = button.window()
            central = getattr(window, "central", None)
            page = central.currentWidget() if central is not None else None
            if page is not None and first_focusable(page) is not None:
                first_focusable(page).setFocus(Qt.FocusReason.TabFocusReason)
                return True
            return False
        if target is None:
            return False
        target.setFocus(Qt.FocusReason.TabFocusReason)
        return True


def install(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None or getattr(app, "_planetpdf_keys", None) is not None:
        return
    helper = KeyboardHelper(app)
    app.installEventFilter(helper)
    app._planetpdf_keys = helper
