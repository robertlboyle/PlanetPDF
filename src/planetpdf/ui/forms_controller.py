"""Form filling in the page view.

One editor at a time sits exactly over the active field (it zooms with the page).
Checkboxes and radio buttons have no editor: a click (or Space) sets them. Tab and
Shift+Tab follow the form's tab order across pages. Every change is an undo command.

Field keys used by the view are ``"<field name>\\x00<widget xref>"``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from planetpdf.core.forms import fields as F
from planetpdf.core.forms import xfa
from planetpdf.core.forms.flatten import flatten_forms
from planetpdf.core.redaction import marks as M
from planetpdf.ui.commands import FieldDesignCommand, SetFieldValuesCommand

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

SEP = "\x00"


def field_key(name: str, xref: int) -> str:
    return f"{name}{SEP}{xref}"


def split_key(key: str) -> tuple[str, int]:
    name, xref = key.rsplit(SEP, 1)
    return name, int(xref)


def xfa_values(fields: list[F.FormField]) -> dict[str, str]:
    """AcroForm values in the text form XFA datasets use."""
    out = {}
    for f in fields:
        if not f.kind.fillable:
            continue
        if f.kind is F.FieldKind.CHECKBOX:
            out[f.name] = "1" if f.value else "0"
        elif f.kind is F.FieldKind.RADIO:
            out[f.name] = f.value or ""
        elif f.kind is F.FieldKind.LIST:
            out[f.name] = "\n".join(f.value)
        else:
            out[f.name] = str(f.value)
    return out


class _KeyFilter(QObject):
    """Tab / Shift+Tab / Esc / Enter handling for an editor widget."""

    def __init__(self, controller: FormController, single_line: bool) -> None:
        super().__init__()
        self._controller = controller
        self._single_line = single_line

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Tab:
                self._controller.move(forward=True)
                return True
            if key == Qt.Key.Key_Backtab:
                self._controller.move(forward=False)
                return True
            if key == Qt.Key.Key_Escape:
                self._controller.cancel_edit()
                return True
            if self._single_line and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._controller.commit()
                return True
            if key == Qt.Key.Key_F4 or (key == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier):
                self._controller.open_active_choices(obj)  # (a dropdown you can type in: its list)
                return True
        elif event.type() == QEvent.Type.FocusOut and event.reason() in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
        ):
            if isinstance(obj, QComboBox) and obj.view().isVisible():
                return False  # (focus went to its own list of choices: not done yet)
            self._controller.commit()
        return False


def appearance_text_size(doc, xref: int, rect: QRectF, *, multiline: bool = False) -> float:
    """The text size the field's own drawing uses (its /AP stream's ``Tf``; so the editor
    over it shows the text exactly as the PDF does: user 2026-10-02), else its /DA size,
    else what MuPDF picks for an automatic size."""
    import re

    kind, value = doc.xref_get_key(xref, "AP/N")
    if kind == "xref":
        try:
            stream = doc.xref_stream(int(value.split()[0])) or b""
            match = re.search(rb"/\w+\s+([\d.]+)\s+Tf", stream)
            if match and float(match.group(1)) > 0:
                return float(match.group(1))
        except (ValueError, RuntimeError):
            pass
    size = F.da_size(doc, xref)
    if size > 0:
        return size
    return 12.0 if multiline else max(6.0, min(rect.height() * 0.7, 24.0))


def editor_style(size: float) -> str:
    """An editor that looks like the field in the PDF: Helvetica (Arial on Windows) at the
    same size, black on white, the text where the PDF puts it (2 pt in)."""
    return (f"font-family: Helvetica, Arial; font-size: {size:.2f}px; color: black; background: white;"
            " border: 1px solid #1e5adc; padding: 0px 0px 0px 1px; margin: 0px;")


class FormController(QObject):
    changed = Signal()

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.fields: list[F.FormField] = []
        self._by_name: dict[str, F.FormField] = {}
        self._stops: list[str] = []
        self._active: str | None = None
        self._editor_value = None  # () -> current editor value
        self._filters: list[_KeyFilter] = []
        self.values_edited = False  # any field changed since the document was opened
        self._xfa_decision: bool | None = None  # hybrid XFA: keep (True) / drop (False)
        view = window.view
        view.fieldClicked.connect(self._on_clicked)
        view.fieldTab.connect(lambda forward: self.move(forward=forward))
        view.fieldKey.connect(self._on_key)

    # --- document -------------------------------------------------------------------

    @property
    def session(self):
        return self._window.session

    def document_opened(self) -> None:
        self.values_edited = False
        self._xfa_decision = None
        self._active = None
        self.refresh()
        if self.session is None:
            return
        if F.old_style_buttons(self.session):
            # radio buttons / checkboxes PlanetPDF made before 2026-10-04 kept their text inside
            # the field, which Acrobat drops when it redraws fields: stored the standard way
            # now (one undo step; saved with the file)
            from planetpdf.ui.commands import DocumentSnapshotCommand

            try:
                command = DocumentSnapshotCommand.applied(self._window, F.upgrade_old_buttons,
                                                          "Update Form Buttons")
            except Exception as exc:  # (left as they were)
                self._window.report_error("Update Form Buttons", exc)
            else:
                self._window.push_command(command)
                self._window.statusBar().showMessage(
                    "Radio buttons and checkboxes made by an earlier PlanetPDF were updated to the standard way "
                    "(their text is page text now, so Acrobat shows it). Save to keep it.", 12000)
        kind = xfa.xfa_kind(self.session.doc)
        if kind is xfa.XfaKind.PURE:
            self._window.show_message(
                "XFA Form Not Supported",
                "This is a dynamic XFA form. PlanetPDF can't display or fill XFA forms; "
                "what you see is only the placeholder page stored in the file.\n\n"
                "Open it in Adobe Acrobat or Reader to fill it in.",
            )

    def refresh(self) -> None:
        """Re-read fields from the document and rebuild overlays and the panel."""
        session = self.session
        self._window.view.remove_editor()
        if session is None:
            self.fields, self._by_name, self._stops = [], {}, []
            self._window.view.set_field_overlays({})
            self._window.forms_panel.populate([], xfa.XfaKind.NONE)
            return
        self.fields = F.list_fields(session)
        self._by_name = {f.name: f for f in self.fields}
        self._stops = [field_key(n, x) for n, x in F.tab_stops(session, self.fields)]
        overlays = self._show_overlays()
        if self._active and self._active not in {e[0] for entries in overlays.values() for e in entries}:
            self._active = None
        self._window.view.set_focused_field(self._active)
        self._window.forms_panel.populate(self.fields, xfa.xfa_kind(session.doc))

    _HINTS = {
        F.FieldKind.LIST: "Click a choice to pick it",
        F.FieldKind.COMBO: "Click to choose from the list",
        F.FieldKind.RADIO: "Click to pick this one",
        F.FieldKind.CHECKBOX: "Click to check or uncheck",
    }

    def _show_overlays(self) -> dict:
        """The clickable field areas, with their viewer-only looks (a dropdown's ▾ and
        "Choose…" while empty, an unsigned signature field's box) and a hint each."""
        session = self.session
        overlays: dict[int, list[tuple]] = {}
        hints: dict[str, str] = {}
        for f in self.fields:
            for ref in f.widgets:
                page = session.doc[ref.page]
                shown = M.to_display(page, ref.rect)
                rect = QRectF(shown.x0, shown.y0, shown.width, shown.height)
                fillable = f.kind.fillable and not f.read_only
                look = (("dropdown" if f.value else "dropdown-empty") if f.kind is F.FieldKind.COMBO else
                        "signature" if f.kind is F.FieldKind.SIGNATURE
                        and session.doc.xref_get_key(ref.xref, "V")[0] in ("null", "") else
                        # (the cursor over it, user 2026-10-24: the I-beam where you type, a hand on a button)
                        "text" if f.kind in (F.FieldKind.TEXT, F.FieldKind.DATE) else
                        "button" if f.kind is F.FieldKind.BUTTON and not f.read_only else "")
                key = field_key(f.name, ref.xref)
                overlays.setdefault(ref.page, []).append((key, rect, fillable, look))
                hint = ("Click the choices you want; click one again to take it off" if f.kind is F.FieldKind.LIST
                        and f.multiselect else self._HINTS.get(f.kind, ""))
                hints[key] = f"{f.name}: {hint}" if hint and fillable else f.name
        view = self._window.view
        view.set_field_overlays(overlays)
        for key, item in view._field_items.items():
            item.setToolTip(hints.get(key, ""))
        return overlays

    def values_changed(self, names: set[str]) -> None:
        """Called by field commands (do/undo)."""
        from planetpdf.core.forms.barcodes import barcode_widgets

        self.values_edited = True
        pages = {ref.page for n in names if n in self._by_name for ref in self._by_name[n].widgets}
        pages |= {page for page, _xref in barcode_widgets(self.session.doc)}  # (they show the values: redrawn)
        for page in pages:
            self._window.view.refresh_page(page)
            self._window.thumbnails.refresh_page(page)
        self.fields = F.list_fields(self.session)
        self._by_name = {f.name: f for f in self.fields}
        if any(self._by_name[n].kind is F.FieldKind.COMBO for n in names if n in self._by_name):
            self._show_overlays()  # (the "Choose…" hint goes once a value is picked; an open editor stays)
            self._window.view.set_focused_field(self._active)
        self._window.forms_panel.update_values(self.fields)
        self.changed.emit()

    # --- activation and navigation ----------------------------------------------------------

    def _on_clicked(self, key: str) -> None:
        if not key:
            self.commit()
            self.deactivate()
            return
        self.activate(key, toggle=True)

    def activate(self, key: str, *, toggle: bool = False) -> None:
        name, _xref = split_key(key)
        f = self._by_name.get(name)
        if f is None:
            return
        if f.kind is F.FieldKind.BUTTON and not f.read_only and toggle:
            self.press_button(f, key)
            return
        if not f.kind.fillable or f.read_only:
            self._window.statusBar().showMessage(f"“{name}” is read-only.", 3000)
            return
        if key != self._active:
            self.commit()
        self._active = key
        view = self._window.view
        view.set_focused_field(key)
        view.setFocus()
        self._window.forms_panel.select_field(name)
        if f.kind in (F.FieldKind.CHECKBOX, F.FieldKind.RADIO):
            view.remove_editor()
            self._editor_value = None
            if toggle:
                self._toggle(f, key)
            return
        if f.kind is F.FieldKind.LIST and toggle and self._pick_in_list(f, key):
            return
        self._open_editor(f, key, open_choices=toggle)  # (a click opens a dropdown's choices; Tab doesn't)

    def _list_lines(self, f: F.FormField, key: str) -> tuple[QRectF, float] | None:
        """A list box's place and line height, when all its choices show in it (as our own
        appearance draws them: Helvetica at the field's size, 11 pt by default)."""
        located = self._window.view.field_rect(key)
        if located is None:
            return None
        _page, rect = located
        _name, xref = split_key(key)
        line = (F.da_size(self.session.doc, xref) or 11.0) * 1.15
        if len(f.labels or f.options) * line > rect.height() - 2:
            return None  # (they don't all fit: the editor, with a scroll bar)
        return rect, line

    def _pick_in_list(self, f: F.FormField, key: str) -> bool:
        """Click a choice on the page itself (user 2026-10-02: show fields as in the PDF,
        not a different-looking editor). Multi-select lists toggle the choice clicked."""
        view = self._window.view
        point, view.field_click_point = getattr(view, "field_click_point", None), None
        found = self._list_lines(f, key)
        if point is None or found is None:
            return False
        rect, line = found
        index = int((point.y() - rect.top() - 1) // line)
        view.remove_editor()
        self._editor_value = None
        if not 0 <= index < len(f.options):
            return True  # (the empty part below the choices)
        choice = f.options[index]
        current = list(f.value or [])
        if f.multiselect:
            chosen = [c for c in current if c != choice] if choice in current else current + [choice]
            chosen = [o for o in f.options if o in chosen]  # (in list order)
        else:
            chosen = [choice]
        if chosen != current:
            self.set_value(f.name, chosen)
        return True

    def deactivate(self) -> None:
        self._window.view.remove_editor()
        self._editor_value = None
        self._active = None
        self._window.view.set_focused_field(None)

    def move(self, *, forward: bool) -> None:
        """Tab / Shift+Tab: commit, then go to the next/previous field in tab order."""
        self.commit()
        if not self._stops:
            return
        if self._active in self._stops:
            index = self._stops.index(self._active) + (1 if forward else -1)
        else:
            index = 0 if forward else len(self._stops) - 1
        self.activate(self._stops[index % len(self._stops)])

    def _on_key(self, key: str, qt_key: int) -> None:
        name, _ = split_key(key)
        f = self._by_name.get(name)
        if f is None:
            return
        if qt_key == Qt.Key.Key_Escape:
            self.deactivate()
        elif f.kind in (F.FieldKind.CHECKBOX, F.FieldKind.RADIO):
            self._toggle(f, key)
        else:
            self._open_editor(f, key)

    def _toggle(self, f: F.FormField, key: str) -> None:
        if f.kind is F.FieldKind.CHECKBOX:
            self.set_value(f.name, not f.value)
        else:
            _name, xref = split_key(key)
            state = next((w.on_state for w in f.widgets if w.xref == xref), None)
            if state and f.value != state:
                self.set_value(f.name, state)

    # --- buttons -----------------------------------------------------------------------

    def press_button(self, f: F.FormField, key: str) -> None:
        """A push button or image field clicked while filling in (Prepare Form ▸ Button /
        Image; core.forms.buttons). Scripts from other programs are never run."""
        from planetpdf.core.forms import buttons

        self.commit()
        _name, xref = split_key(key)
        action = buttons.button_action(self.session.doc, xref)
        if action == "reset":
            self.clear_form()
        elif action == "print":
            self._window.act_print.trigger()
        elif action.startswith("url:"):
            from planetpdf.ui.outside_links import open_outside_link

            open_outside_link(self._window, action[4:])
        elif action == "image":
            self.choose_picture(f.name, xref)
        elif action == "barcode":
            self._window.statusBar().showMessage(
                f"“{f.name}” is a barcode of the form's values: it changes as you fill in the form.", 5000)
        elif action == "other":
            self._window.statusBar().showMessage(
                f"“{f.name}” runs a script made for another program; PlanetPDF doesn't run form scripts.", 5000)

    def clear_form(self) -> bool:
        """Every field emptied (a "Clear the form" button): one undo step."""
        changes = []
        for f in F.list_fields(self.session):
            if not f.kind.fillable or f.read_only:
                continue
            empty = (False if f.kind is F.FieldKind.CHECKBOX else None if f.kind is F.FieldKind.RADIO
                     else [] if f.kind is F.FieldKind.LIST and f.multiselect else "")
            if f.value not in (empty, None, "", [], False):
                changes.append((f.name, f.value, empty))
        if not changes:
            return False
        self._window.push_command(SetFieldValuesCommand(self._window, changes, "Clear Form"))
        return True

    def choose_picture(self, name: str, xref: int, path=None) -> bool:
        """An image field: pick its picture (one undo step)."""
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from planetpdf.core.forms import buttons
        from planetpdf.ui.edit_controller import IMAGE_FILTER

        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(self._window, "Choose a Picture", self._window.settings.last_directory,
                                                    IMAGE_FILTER)
            if not chosen:
                return False
            path = Path(chosen)
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self._window.show_message("Choose a Picture", f"That picture couldn't be read: {exc}")
            return False
        ref = next((w for w in F.get_field(self.session, name).widgets if w.xref == xref), None)
        if ref is None:
            return False
        old = buttons.stored_picture(self.session.doc, xref)

        def show(s, picture) -> None:
            # (found by name and place: undo of other steps may recreate the field)
            widget = min((w for w in F.get_field(s, name).widgets if w.page == ref.page),
                         key=lambda w: sum(abs(a - b) for a, b in zip(w.rect, ref.rect)))
            buttons.set_image(s, ref.page, widget.xref, picture)

        try:
            show(self.session, data)
        except Exception as exc:  # (nothing changed: set_image reads the picture first)
            self._window.report_error("Choose a Picture", exc)
            return False
        self._window.push_command(FieldDesignCommand(self._window, "Choose Picture", lambda s: show(s, data),
                                                     lambda s: show(s, old), {ref.page}))
        return True

    # --- editing ------------------------------------------------------------------------

    def set_value(self, name: str, value) -> None:
        f = self._by_name.get(name)
        if f is None or f.value == value:
            return
        self._window.push_command(SetFieldValuesCommand(self._window, [(name, f.value, value)]))

    def commit(self) -> None:
        """Write the open editor's value (if it changed)."""
        if self._editor_value is None or self._active is None:
            return
        getter, self._editor_value = self._editor_value, None
        name, _ = split_key(self._active)
        try:
            value = getter()
        except ValueError as exc:
            self._window.statusBar().showMessage(str(exc), 5000)
            return
        except RuntimeError:  # (the editor is gone)
            return
        self.set_value(name, value)

    def cancel_edit(self) -> None:
        self._editor_value = None
        self.deactivate()

    def open_active_choices(self, edit) -> None:
        if self._active is None:
            return
        f = self._by_name.get(split_key(self._active)[0])
        if f is not None and f.kind is F.FieldKind.COMBO:
            self.show_choices(f, self._active, edit if isinstance(edit, QLineEdit) else None)

    def show_choices(self, f: F.FormField, key: str, edit: QLineEdit | None = None) -> QMenu | None:
        """A dropdown's choices in a menu just under the field ("(none)" clears it); the one
        picked is saved at once. ``edit``: a dropdown you can type in shows it there too."""
        rect = self._window.view.field_screen_rect(key)
        if rect is None:
            return None
        menu = QMenu(self._window)
        menu.setMinimumWidth(rect.width())

        def choose(value: str) -> None:
            if edit is not None:
                edit.setText(value)
            self.set_value(f.name, value)

        clear = menu.addAction("(none)")
        clear.triggered.connect(lambda _c=False: choose(""))
        menu.addSeparator()
        for value, label in zip(f.options, f.labels or f.options):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == f.value)
            action.triggered.connect(lambda _c=False, v=value: choose(v))
        current = next((a for a in menu.actions() if a.isChecked()), None)
        if current is not None:
            menu.setActiveAction(current)
        self.choices_menu = menu  # (kept alive while open; tests pick from it)
        menu.popup(rect.bottomLeft())
        return menu

    def _open_editor(self, f: F.FormField, key: str, open_choices: bool = True) -> None:
        view = self._window.view
        located = view.field_rect(key)
        if located is None:
            return
        _page, rect = located
        _name, xref = split_key(key)
        size = appearance_text_size(self.session.doc, xref, rect, multiline=f.multiline)
        style = editor_style(size)
        single_line = True
        focus_target: QWidget

        if f.kind is F.FieldKind.TEXT and f.multiline:
            edit = QPlainTextEdit(str(f.value))
            edit.setStyleSheet(style)
            widget = focus_target = edit
            single_line = False
            self._editor_value = lambda: edit.toPlainText()
        elif f.kind is F.FieldKind.TEXT:
            edit = QLineEdit(str(f.value))
            if f.max_length:
                edit.setMaxLength(f.max_length)
            edit.setStyleSheet(style)
            edit.selectAll()
            widget = focus_target = edit
            self._editor_value = lambda: edit.text()
        elif f.kind is F.FieldKind.DATE:
            widget, focus_target, getter = self._date_editor(f, style, key)
            self._editor_value = getter
        elif f.kind is F.FieldKind.COMBO and not f.editable_combo:
            # The choices open in a menu of the window's own, under the field where it really
            # is on the screen (user bug 2026-10-04, "it always says choose"). A QComboBox on
            # the page opened its list in the wrong place on a zoomed / scaled page (its idea
            # of its own screen position is off), so a real click on a choice landed on the
            # page under it: "clicked away", the editor closed, nothing picked.
            view.remove_editor()
            self._editor_value = None
            if open_choices:
                self.show_choices(f, key)
            return
        elif f.kind is F.FieldKind.COMBO:  # (typing allowed: a text box, the choices from its ▾)
            edit = QLineEdit(str(f.value or ""))
            edit.setPlaceholderText("Choose or type…")
            edit.setStyleSheet(style)
            edit.selectAll()
            widget = focus_target = edit
            self._editor_value = lambda: edit.text()
            point, view.field_click_point = getattr(view, "field_click_point", None), None
            if open_choices and point is not None and point.x() > rect.width() - max(14.0, rect.height()):
                QTimer.singleShot(0, lambda: self.show_choices(f, key, edit))  # (a click on its ▾)
        elif f.kind is F.FieldKind.LIST:
            lst = QListWidget()
            lst.setSelectionMode(
                QAbstractItemView.SelectionMode.MultiSelection if f.multiselect else QAbstractItemView.SelectionMode.SingleSelection
            )
            for value, label in zip(f.options, f.labels or f.options):
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, value)
                lst.addItem(item)
                item.setSelected(value in f.value)
            # (only when the choices don't all fit; looks like the PDF's own list: Helvetica /
            # Arial at the field's size, the same line spacing and highlight)
            size = F.da_size(self.session.doc, xref) or 11.0
            lst.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            lst.setStyleSheet(
                f"QListWidget {{ font-family: Helvetica, Arial; font-size: {size:.1f}px; border: 1px solid #595959;"
                " background: white; padding: 0px; outline: none; }"
                f" QListWidget::item {{ padding: 0px 2px; margin: 0px; height: {size * 1.15:.1f}px; color: black; }}"
                " QListWidget::item:selected { background: rgb(41, 102, 199); color: white; }")
            if f.multiselect:  # (check boxes, as the PDF's own list shows them)
                for n in range(lst.count()):
                    item = lst.item(n)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked)
                lst.itemSelectionChanged.connect(lambda: [lst.item(n).setCheckState(
                    Qt.CheckState.Checked if lst.item(n).isSelected() else Qt.CheckState.Unchecked)
                    for n in range(lst.count())])
            widget = focus_target = lst
            single_line = False
            self._editor_value = lambda: [i.data(Qt.ItemDataRole.UserRole) for i in lst.selectedItems()]
            lst.itemSelectionChanged.connect(
                lambda: self.set_value(f.name, [i.data(Qt.ItemDataRole.UserRole) for i in lst.selectedItems()]))
        else:
            return
        key_filter = _KeyFilter(self, single_line)
        focus_target.installEventFilter(key_filter)
        self._filters = [key_filter]  # keep alive with the editor
        view.show_editor(key, widget)
        focus_target.setFocus()

    def commit_keep_open(self) -> None:
        """Commit but keep the editor (dropdown picks, list selections)."""
        getter = self._editor_value
        self.commit()
        self._editor_value = getter

    def _date_editor(self, f: F.FormField, style: str, key: str):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        edit = QLineEdit(str(f.value))
        edit.setStyleSheet(style)
        edit.setPlaceholderText(f.date_format)
        edit.selectAll()
        button = QToolButton()
        button.setText("▾")
        button.setToolTip("Pick a date")
        button.setStyleSheet("padding: 0px;")
        layout.addWidget(edit, 1)
        layout.addWidget(button)

        menu = QMenu(button)
        calendar = QCalendarWidget(menu)
        current = F.parse_date(edit.text(), f.date_format)
        if current:
            calendar.setSelectedDate(QDate(current.year, current.month, current.day))
        action = QWidgetAction(menu)
        action.setDefaultWidget(calendar)
        menu.addAction(action)

        def picked(qdate: QDate) -> None:
            edit.setText(F.format_date(date(qdate.year(), qdate.month(), qdate.day()), f.date_format))
            menu.close()
            self.commit_keep_open()

        calendar.clicked.connect(picked)
        def open_calendar() -> None:
            # under the field where it really is on the screen (not where the button inside
            # the page thinks it is: user bug 2026-10-04, see show_choices)
            rect = self._window.view.field_screen_rect(key)
            menu.popup(rect.bottomLeft() if rect is not None else button.mapToGlobal(button.rect().bottomLeft()))

        button.clicked.connect(open_calendar)

        def value() -> str:
            text = edit.text().strip()
            if not text:
                return ""
            parsed = F.parse_date(text, f.date_format)
            if parsed is None:
                raise ValueError(f"“{text}” isn't a date. Use {f.date_format}, e.g. {F.format_date(date.today(), f.date_format)}.")
            return F.format_date(parsed, f.date_format)

        return container, edit, value

    # --- form-wide actions -----------------------------------------------------------------

    def clear_form(self) -> None:
        changes = [
            (f.name, f.value, F.empty_value(f))
            for f in self.fields
            if f.kind.fillable and not f.read_only and f.value != F.empty_value(f)
        ]
        if changes:
            self.deactivate()
            self._window.push_command(SetFieldValuesCommand(self._window, changes, "Clear Form"))

    def flatten(self) -> bool:
        session = self.session
        if session is None or not self.fields:
            return False
        self.commit()
        signed = session.has_digital_signatures
        answer = self._window.ask(
            "Flatten Form",
            "Flattening turns every form field into ordinary page content: the values stay "
            "visible but can no longer be edited, and the fields are removed.\n\n"
            + ("This document is digitally signed; flattening will invalidate the signatures.\n\n" if signed else "")
            + "Flatten the form? (Undo brings the fields back.)",
        )
        if not answer:
            return False
        from planetpdf.ui.commands import DocumentSnapshotCommand

        self.deactivate()
        self._window.push_command(DocumentSnapshotCommand.applied(self._window, flatten_forms, "Flatten Form"))
        self._window.statusBar().showMessage("Form flattened. Save to keep the change; Undo brings the fields back.", 8000)
        return True

    # --- saving hybrid XFA forms ----------------------------------------------------------------

    def before_save(self) -> bool:
        """Hybrid XFA: ask once whether the form will be submitted electronically.
        Returns False if the user canceled the save."""
        session = self.session
        if session is None or xfa.xfa_kind(session.doc) is not xfa.XfaKind.HYBRID:
            return True
        if self._xfa_decision is None:
            answer = self._window.ask_three(
                "Form Submission",
                "This form contains an XFA copy (used when a form is submitted electronically "
                "through Adobe software).\n\nWill this form be submitted electronically?\n\n"
                "Yes: keep the XFA part and copy your entries into it.\n"
                "No: remove the XFA part (recommended for printing, emailing, or archiving).",
            )
            if answer is None:
                return False
            self._xfa_decision = answer
        if self._xfa_decision:
            result = xfa.update_datasets(session.doc, xfa_values(self.fields))
            if result.unmatched:
                self._window.statusBar().showMessage(
                    f"Couldn't match {len(result.unmatched)} field(s) in the XFA data: "
                    + ", ".join(result.unmatched[:5])
                    + ("…" if len(result.unmatched) > 5 else ""),
                    12000,
                )
        else:
            xfa.remove_xfa(session.doc)
            self._window.forms_panel.populate(self.fields, xfa.XfaKind.NONE)
        session.mark_modified()
        return True
