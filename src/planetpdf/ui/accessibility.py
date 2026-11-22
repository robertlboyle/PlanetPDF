"""Screen readers (user 2026-10-25: "Make sure visually impaired or blind people can use the app
without any problems"). Narrator, NVDA and VoiceOver read a control by its accessible name, which
Qt takes from a button's text or a label tied to the control (``setBuddy``, a form layout's row).
Icon-only buttons, boxes without a label and lists have none, and a reader says only "button" or
"edit". So:

- important controls get a name where they are made (``name(widget, "Find")``);
- every other control still without one when it is first shown gets one from what a sighted user
  sees: its action's text, its tooltip, its placeholder (``Namer``, one per application, installed
  by the main window), so no dialog or tool bar is missed;
- ``unnamed(root)`` lists what is left (the accessibility test fails on any).
"""

from __future__ import annotations

import re
import time
from collections import deque

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QApplication,
                               QComboBox, QFormLayout, QGroupBox, QHeaderView, QLabel, QLayout, QLineEdit,
                               QPlainTextEdit, QScrollBar, QStatusBar, QTabBar, QTableView, QTextEdit, QToolButton,
                               QWidget)

# what a person operates; scroll bars and a table's headers are left out (readers announce them
# by their role, the headers with each cell)
INTERACTIVE = (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QAbstractSlider, QAbstractItemView,
               QPlainTextEdit, QTextEdit, QTabBar)
SKIPPED = (QScrollBar, QHeaderView)
# Qt's own buttons, by object name
QT_BUTTONS = {"qt_toolbar_ext_button": "More tools", "qt_menubar_ext_button": "More menus",
              "qt_tableview_cornerbutton": "Select all"}


def name(widget: QWidget, text: str, description: str = "") -> QWidget:
    """Gives ``widget`` the name a screen reader says (and a longer description, optional)."""
    widget.setAccessibleName(text)
    if description:
        widget.setAccessibleDescription(description)
    return widget


def plain(text: str) -> str:
    """Menu / tooltip text as it should be spoken: no & mnemonics, no "(Ctrl+F)", no "…",
    first line only."""
    text = (text or "").replace("&&", "\0").replace("&", "").replace("\0", "&")
    text = re.sub(r"<[^>]+>", " ", text)  # (rich-text tooltips)
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"\s*\((?:[A-Z][a-z]*\+)*[^()]{1,12}\)$", "", text)  # a trailing shortcut
    return text.replace("…", "").replace("...", "").strip(" :")


ANNOUNCED: deque[str] = deque(maxlen=50)  # what was said last (for tests)


def announce(widget: QWidget, text: str, *, urgent: bool = False) -> None:
    """Has a running screen reader say ``text`` ("Page 4 of 10", "Saved"), without moving the
    focus. Polite (after what it is saying) unless ``urgent``."""
    ANNOUNCED.append(text)
    if not QAccessible.isActive():
        return
    event = QAccessibleAnnouncementEvent(widget, text)
    event.setPoliteness(QAccessible.AnnouncementPoliteness.Assertive if urgent
                        else QAccessible.AnnouncementPoliteness.Polite)
    QAccessible.updateAccessibility(event)


PROGRESS_EVERY = 5.0  # seconds between spoken progress reports (each page would be too many)


def say_progress(dialog: QWidget, text: str) -> None:
    """A long job's progress ("Exporting page 3 of 10"), spoken when it starts and then at
    most every ``PROGRESS_EVERY`` seconds."""
    now = time.monotonic()
    last = dialog.property("ppdfProgressSaid")
    if last is None or now - float(last) >= PROGRESS_EVERY:
        dialog.setProperty("ppdfProgressSaid", now)
        announce(dialog, ". ".join(line.strip(" …") for line in text.splitlines() if line.strip()))


def speak_status(bar: QStatusBar) -> QStatusBar:
    """Status bar messages ("Saved", "3 matches") are spoken as they appear."""
    if not bar.property("ppdfSpeaks"):
        bar.setProperty("ppdfSpeaks", True)
        bar.messageChanged.connect(lambda text: text.strip() and announce(bar, text))
    return bar


def put_first(window: QWidget, first: QWidget) -> None:
    """Makes ``first`` the first stop of the Tab key in ``window`` (a dialog), keeping the order
    of everything else. Tab follows the order widgets were made, which isn't always the order
    they are seen in."""
    chain, widget = [], window.nextInFocusChain()
    while widget is not None and widget is not window and len(chain) < 2000:
        if widget is not first and window.isAncestorOf(widget) and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
            chain.append(widget)
        widget = widget.nextInFocusChain()
    previous = first
    for widget in chain:
        QWidget.setTabOrder(previous, widget)
        previous = widget


def spoken_name(widget: QWidget) -> str:
    """The name a screen reader gets for ``widget`` now."""
    iface = QAccessible.queryAccessibleInterface(widget)
    return iface.text(QAccessible.Text.Name).strip() if iface is not None else ""


def _label_for(widget: QWidget) -> str:
    parent = widget.parentWidget()
    if parent is None:
        return ""
    layout = parent.layout()
    for form in parent.findChildren(QFormLayout):  # ("Digital ID:" before a row holding the box)
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label is not None and isinstance(label.widget(), QLabel) and field is not None \
                    and _holds(field, widget) and label.widget().text().strip():
                return label.widget().text()
    for label in parent.findChildren(QLabel):
        if label.buddy() is widget:
            return label.text()
    before = _label_before(layout, widget) if layout is not None else None
    if before:
        return before
    box = parent
    while box is not None and not box.isWindow():
        if isinstance(box, QGroupBox) and box.title():
            return box.title()  # (a box holding one list, one text box…)
        box = box.parentWidget()
    return ""


def _holds(item, widget: QWidget) -> bool:
    if item.widget() is widget:
        return True
    layout = item.layout()
    return layout is not None and any(_holds(layout.itemAt(i), widget) for i in range(layout.count()))


def _label_before(layout: QLayout, widget: QWidget) -> str | None:
    """The text of a label laid out just before ``widget`` (a heading over a list, "Name:"
    left of a box), searching nested layouts."""
    previous = None
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is widget:
            return previous.text() if isinstance(previous, QLabel) and previous.text().strip() else None
        if item.layout() is not None:
            found = _label_before(item.layout(), widget)
            if found is not None:
                return found
        if item.widget() is not None:
            previous = item.widget()
    return None


def derive(widget: QWidget) -> str:
    """A name for ``widget`` from what a sighted user sees on or near it ("" when nothing)."""
    parent = widget.parentWidget()
    if isinstance(widget, QLineEdit) and isinstance(parent, (QComboBox, QAbstractSpinBox)):
        return spoken_name(parent) or derive(parent)  # (the box's own text field)
    if isinstance(widget, QAbstractItemView) and parent is not None and isinstance(parent.parentWidget(), QComboBox):
        box = parent.parentWidget()  # (a drop-down's list of choices)
        return spoken_name(box) or derive(box)
    table = parent.parentWidget() if parent is not None else None
    if isinstance(table, QTableView) and parent is table.viewport():  # (a box in a table's cell)
        index = table.indexAt(widget.geometry().center())
        if index.isValid():
            header = table.model().headerData(index.column(), Qt.Orientation.Horizontal)
            return f"{plain(str(header or ''))}, row {index.row() + 1}".lstrip(", ")
    if widget.objectName() in QT_BUTTONS:
        return QT_BUTTONS[widget.objectName()]
    if isinstance(widget, QToolButton) and isinstance(parent, QLineEdit) and not widget.toolTip():
        return "Clear"  # (a text box's clear button)
    candidates = []
    if isinstance(widget, QToolButton) and widget.defaultAction() is not None:
        action = widget.defaultAction()
        candidates += [action.text(), action.iconText(), action.toolTip()]
    if isinstance(widget, QAbstractButton):
        candidates.append(widget.text())
    candidates.append(_label_for(widget))
    if isinstance(widget, QLineEdit):
        candidates.append(widget.placeholderText())
    if isinstance(widget, QComboBox):
        candidates.append(widget.placeholderText())
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        candidates.append(widget.placeholderText())
    candidates += [widget.toolTip(), widget.statusTip(), widget.whatsThis()]
    for text in candidates:
        text = plain(text)
        if text:
            return text
    return ""


def name_if_missing(widget: QWidget) -> None:
    if not isinstance(widget, INTERACTIVE) or isinstance(widget, SKIPPED) or widget.accessibleName():
        return
    if spoken_name(widget):
        return
    text = derive(widget)
    if text:
        widget.setAccessibleName(text)


def name_all(root: QWidget) -> None:
    for widget in [root, *root.findChildren(QWidget)]:
        name_if_missing(widget)


def unnamed(root: QWidget) -> list[QWidget]:
    """Controls under ``root`` (and ``root``) a screen reader can't name."""
    return [w for w in [root, *root.findChildren(QWidget)]
            if isinstance(w, INTERACTIVE) and not isinstance(w, SKIPPED) and not spoken_name(w)]


def describe(widget: QWidget) -> str:
    """For test messages: what a control is and where."""
    parents = []
    p = widget.parentWidget()
    while p is not None and len(parents) < 3:
        parents.append(p.objectName() or type(p).__name__)
        p = p.parentWidget()
    tip = f", tooltip {widget.toolTip()[:40]!r}" if widget.toolTip() else ""
    return f"{type(widget).__name__} {widget.objectName() or '-'} in {' < '.join(parents)}{tip}"


class Namer(QObject):
    """Names each control the first time it is shown (one for the whole application)."""

    def eventFilter(self, watched, event):  # noqa: N802 (Qt)
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            if watched.isWindow():
                name_all(watched)
            else:
                name_if_missing(watched)
        return False


def install(app: QApplication | None = None) -> None:
    """Starts naming controls as they show (once per application)."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_planetpdf_namer", None) is not None:
        return
    namer = Namer(app)
    app.installEventFilter(namer)
    app._planetpdf_namer = namer
