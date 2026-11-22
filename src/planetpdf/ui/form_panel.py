"""Prepare Form's right-hand panel (Acrobat, user screenshot 2026-10-03):

* ALIGN: left / center / right edges, top / middle / bottom, of the selected fields, to the
  first one selected (Acrobat aligns to the field clicked last; we take the first, which is
  the one the box or Shift+clicks started from).
* CENTER: the selection centered on its page across, down or both.
* MATCH SIZE: the selected fields get the first one's width, height or both.
* DISTRIBUTE: three or more fields spaced evenly across or down (the outer ones stay).
* FIELDS: every field, page by page; click selects it (and goes there), double-click
  opens its properties.

Several fields are selected with a box around them, Shift+click (add) or Ctrl+Shift+click
(remove). Each command is one undo step (``EditController.place_fields``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planetpdf.ui import fa
from planetpdf.ui.edit_panel import _heading

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow

# --- geometry (displayed rects; pure, tested on their own) -----------------------------------------------


def align(rects: list[QRectF], how: str) -> list[QRectF]:
    """``how``: left, center, right, top, middle, bottom: to ``rects[0]``."""
    a = rects[0]
    out = []
    for r in rects:
        r = QRectF(r)
        if how == "left":
            r.moveLeft(a.left())
        elif how == "right":
            r.moveRight(a.right())
        elif how == "center":
            r.moveLeft(a.center().x() - r.width() / 2)
        elif how == "top":
            r.moveTop(a.top())
        elif how == "bottom":
            r.moveBottom(a.bottom())
        elif how == "middle":
            r.moveTop(a.center().y() - r.height() / 2)
        out.append(r)
    return out


def center_on_page(rects: list[QRectF], page: QRectF, how: str) -> list[QRectF]:
    """The selection as a whole centered on the page: ``how`` across, down or both."""
    union = QRectF(rects[0])
    for r in rects[1:]:
        union = union.united(r)
    dx = page.center().x() - union.center().x() if how in ("across", "both") else 0.0
    dy = page.center().y() - union.center().y() if how in ("down", "both") else 0.0
    return [QRectF(r).translated(dx, dy) for r in rects]


def match_size(rects: list[QRectF], how: str) -> list[QRectF]:
    """``how``: width, height or both, of ``rects[0]`` (top-left corners stay)."""
    a = rects[0]
    return [QRectF(r.left(), r.top(), a.width() if how in ("width", "both") else r.width(),
                   a.height() if how in ("height", "both") else r.height()) for r in rects]


def distribute(rects: list[QRectF], how: str) -> list[QRectF]:
    """Equal gaps between neighbors, across (``how`` "across") or down; the first and
    last (by position) stay where they are."""
    across = how == "across"
    order = sorted(range(len(rects)), key=lambda i: rects[i].left() if across else rects[i].top())
    out = [QRectF(r) for r in rects]
    if len(rects) < 3:
        return out
    first, last = rects[order[0]], rects[order[-1]]
    total = sum(rects[i].width() if across else rects[i].height() for i in order)
    span = (last.right() - first.left()) if across else (last.bottom() - first.top())
    gap = (span - total) / (len(rects) - 1)
    edge = first.left() if across else first.top()
    for i in order:
        if across:
            out[i].moveLeft(edge)
            edge += rects[i].width() + gap
        else:
            out[i].moveTop(edge)
            edge += rects[i].height() + gap
    return out


# --- the panel ------------------------------------------------------------------------------------------------

ALIGN = [("left", "objects-align-left", "Align Left"), ("center", "objects-align-center-horizontal", "Align Center"),
         ("right", "objects-align-right", "Align Right"), ("top", "objects-align-top", "Align Top"),
         ("middle", "objects-align-center-vertical", "Align Middle"), ("bottom", "objects-align-bottom", "Align Bottom")]
CENTER = [("across", "arrows-left-right", "Center Across the Page"), ("down", "arrows-up-down", "Center Down the Page"),
          ("both", "arrows-to-dot", "Center on the Page")]
MATCH = [("width", "arrows-left-right-to-line", "Match Width"), ("height", "arrows-up-to-line", "Match Height"),
         ("both", "expand", "Match Width and Height")]
DISTRIBUTE = [("across", "distribute-spacing-horizontal", "Distribute Across"),
              ("down", "distribute-spacing-vertical", "Distribute Down")]


class FormPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.w = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 10)
        self.buttons: dict[str, QToolButton] = {}
        for title, items, group, minimum in (("ALIGN", ALIGN, "align", 2), ("CENTER", CENTER, "center", 1),
                                             ("MATCH SIZE", MATCH, "match", 2), ("DISTRIBUTE", DISTRIBUTE, "distribute", 3)):
            layout.addWidget(_heading(title, self))
            grid = QGridLayout()
            grid.setSpacing(2)
            for n, (how, icon, text) in enumerate(items):
                button = QToolButton(self)
                button.setIcon(fa.icon(icon))
                button.setToolTip(f"{text} (select {minimum} or more fields)" if minimum > 1 else text)
                button.setAutoRaise(True)
                button.clicked.connect(lambda _c=False, g=group, h=how, t=text: self.arrange(g, h, t))
                button.setProperty("minimum", minimum)
                grid.addWidget(button, 0, n)
                self.buttons[f"{group}:{how}"] = button
            grid.setColumnStretch(len(items), 1)
            layout.addLayout(grid)
        self.hint = QLabel("Select fields with a box around them, or Shift+click to add one.", self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.hint)
        layout.addWidget(_heading("FIELDS", self))
        self.fields = QTreeWidget(self)
        self.fields.setAccessibleName("Fields")
        self.fields.setHeaderHidden(True)
        self.fields.itemClicked.connect(lambda item, _col: self._select(item))
        self.fields.itemDoubleClicked.connect(lambda item, _col: self._properties(item))
        layout.addWidget(self.fields, 1)
        self._connected = False

    def connect_window(self) -> None:
        """After the window's edit controller exists."""
        if self._connected:
            return
        self._connected = True
        self.w.view.selectionChanged.connect(self.update_buttons)
        self.w.edit.modeChanged.connect(lambda _m: self.refresh())
        self.w.undo_group.indexChanged.connect(lambda _i: self.refresh() if self.isVisible() else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    # --- arranging --------------------------------------------------------------------------------

    def selected(self) -> list[tuple[str, int, QRectF]]:
        out = []
        for item_id in self.w.view.selected_group:
            place = self.w.edit.field_place(item_id)
            if place is not None:
                out.append((item_id, place[0], place[1]))
        return out

    def page_rect(self, index: int) -> QRectF:
        page = self.w.session.doc[index]  # (page.rect is the page as displayed)
        return QRectF(0, 0, page.rect.width, page.rect.height)

    def arrange(self, group: str, how: str, text: str) -> bool:
        chosen = self.selected()
        if self.w.edit.mode != "design" or not chosen:
            return False
        rects = [r for _i, _p, r in chosen]
        if group == "align" and len(chosen) >= 2:
            new = align(rects, how)
        elif group == "match" and len(chosen) >= 2:
            new = match_size(rects, how)
        elif group == "distribute" and len(chosen) >= 3:
            new = distribute(rects, how)
        elif group == "center":
            # (each page's fields centered on their own page)
            new = list(rects)
            for page_index in {p for _i, p, _r in chosen}:
                picked = [n for n, (_i, p, _r) in enumerate(chosen) if p == page_index]
                moved = center_on_page([rects[n] for n in picked], self.page_rect(page_index), how)
                for n, r in zip(picked, moved):
                    new[n] = r
        else:
            return False
        return self.w.edit.place_fields([(i, r) for (i, _p, _r), r in zip(chosen, new)], text)

    def update_buttons(self) -> None:
        design = self.w.edit.mode == "design"
        count = len(self.selected()) if design and self.w.session is not None else 0
        for button in self.buttons.values():
            button.setEnabled(count >= button.property("minimum"))
        self._mark_selected()

    # --- the field list ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        from planetpdf.core.forms import fields as F

        self.fields.clear()
        session = self.w.session
        if session is None:
            self.update_buttons()
            return
        pages: dict[int, QTreeWidgetItem] = {}
        for f in F.list_fields(session):
            page = f.widgets[0].page if f.widgets else 0
            parent = pages.get(page)
            if parent is None:
                parent = pages[page] = QTreeWidgetItem([f"Page {page + 1}"])
            item = QTreeWidgetItem(parent, [f.name])
            item.setData(0, Qt.ItemDataRole.UserRole, f.name)
            item.setToolTip(0, f"{f.kind.value.title()} field: {f.name} (double-click for its properties)")
        for page in sorted(pages):
            self.fields.addTopLevelItem(pages[page])
            pages[page].setExpanded(True)
        self.update_buttons()

    def _overlay(self, name: str) -> str | None:
        return self.w.edit._overlay_id(name)

    def _select(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if not name or self.w.edit.mode != "design":
            return
        overlay = self._overlay(name)
        view = self.w.view
        groups = getattr(self.w.edit, "_radio_groups", {})
        overlay = next((k for k, (n, _p) in groups.items() if n == name), overlay)
        place = view.markup_rect(overlay) if overlay else None
        if place is not None:
            view.go_to_page(place[0])
            view.set_selected_markup(overlay)
            self.w.edit.selectionChanged.emit(overlay)

    def _properties(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name and self.w.edit.mode == "design":
            self.w.edit.edit_properties(name)

    def _mark_selected(self) -> None:
        """The list follows the page: selected fields are selected in it too."""
        from planetpdf.ui.forms_controller import split_key

        names = set()
        groups = getattr(self.w.edit, "_radio_groups", {})
        for item_id in self.w.view.selected_group:
            if item_id in groups:
                names.add(groups[item_id][0])
            elif item_id.startswith("fld:"):
                names.add(split_key(item_id[4:])[0])
        self.fields.blockSignals(True)
        for n in range(self.fields.topLevelItemCount()):
            top = self.fields.topLevelItem(n)
            for k in range(top.childCount()):
                child = top.child(k)
                child.setSelected(child.data(0, Qt.ItemDataRole.UserRole) in names)
        self.fields.blockSignals(False)
