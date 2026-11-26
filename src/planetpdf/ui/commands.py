"""Undoable commands. Each wraps a core operation and its exact inverse.

Rule: every UI change to the document goes through the undo stack (or, for
non-undoable commit points, clears it and calls ``undo_stack.resetClean()``),
because the window's unsaved-changes state is the stack's clean state.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from PySide6.QtGui import QUndoCommand

from planetpdf.core import markup as K
from planetpdf.core import ocr
from planetpdf.core import pages as P
from planetpdf.core.forms import fields as F
from planetpdf.core.redaction import marks as M

if TYPE_CHECKING:
    from planetpdf.ui.main_window import MainWindow


def _page_word(count: int) -> str:
    return "Page" if count == 1 else f"{count} Pages"


class MovePagesCommand(QUndoCommand):
    def __init__(self, window: MainWindow, order: list[int], moved: list[int]) -> None:
        super().__init__(f"Move {_page_word(len(moved))}")
        self._window = window
        self._order = order
        self._moved = sorted(moved)

    def redo(self) -> None:
        mapping = P.reorder_pages(self._window.session, self._order)
        new_positions = [mapping.index(old) for old in self._moved]
        self._window.pages_changed(mapping, focus=new_positions[0], select=new_positions)

    def undo(self) -> None:
        mapping = P.reorder_pages(self._window.session, P.inverse_order(self._order))
        self._window.pages_changed(mapping, focus=self._moved[0], select=self._moved)


class DeletePagesCommand(QUndoCommand):
    def __init__(self, window: MainWindow, pages: list[int]) -> None:
        super().__init__(f"Delete {_page_word(len(pages))}")
        self._window = window
        self._pages = sorted(pages)
        self._removed: P.RemovedPages | None = None

    def redo(self) -> None:
        self._removed, mapping = P.delete_pages(self._window.session, self._pages)
        self._window.pages_changed(mapping, focus=min(self._pages[0], len(mapping) - 1))

    def undo(self) -> None:
        assert self._removed is not None
        mapping = P.restore_pages(self._window.session, self._removed)
        self._window.pages_changed(mapping, focus=self._pages[0], select=self._pages)


class InsertBlankPageCommand(QUndoCommand):
    def __init__(self, window: MainWindow, index: int) -> None:
        super().__init__("Insert Blank Page")
        self._window = window
        self._index = index

    def redo(self) -> None:
        mapping = P.insert_blank_page(self._window.session, self._index)
        self._window.pages_changed(mapping, focus=self._index, select=[self._index])

    def undo(self) -> None:
        mapping = P.remove_inserted(self._window.session, self._index, 1)
        self._window.pages_changed(mapping, focus=max(self._index - 1, 0))


class RotatePagesCommand(QUndoCommand):
    def __init__(self, window: MainWindow, pages: list[int], degrees: int) -> None:
        direction = "Clockwise" if degrees > 0 else "Counterclockwise"
        super().__init__(f"Rotate {_page_word(len(pages))} {abs(degrees)}° {direction}")
        self._window = window
        self._pages = sorted(pages)
        self._degrees = degrees

    def redo(self) -> None:
        mapping = P.rotate_pages(self._window.session, self._pages, self._degrees)
        self._window.pages_changed(mapping, focus=self._window.view.current_page, select=self._pages)

    def undo(self) -> None:
        mapping = P.rotate_pages(self._window.session, self._pages, -self._degrees)
        self._window.pages_changed(mapping, focus=self._window.view.current_page, select=self._pages)


class SetProtectionCommand(QUndoCommand):
    """Password protection is applied on save; this changes what the next save does."""

    def __init__(self, window: MainWindow, new, text: str) -> None:
        super().__init__(text)
        self._window = window
        self._old = window.session.pending_protection
        self._new = new

    def redo(self) -> None:
        self._window.session.set_protection(self._new)
        self._window.protection_changed()

    def undo(self) -> None:
        self._window.session._pending_protection = self._old
        self._window.protection_changed()


class PageLabelsCommand(QUndoCommand):
    def __init__(self, window: MainWindow, old: list, new: list) -> None:
        super().__init__("Remove Page Labels" if not new else "Set Page Labels")
        self._window = window
        self._old, self._new = list(old), list(new)

    def _apply(self, rules: list) -> None:
        from planetpdf.core.page_labels import set_rules

        set_rules(self._window.session, rules)
        for page_list in self._window.page_lists():
            page_list.refresh_labels()

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)


class LinksCommand(QUndoCommand):
    """Edit PDF ▸ Link: links taken off and put on (as PyMuPDF link dicts); undo swaps them."""

    def __init__(self, window: MainWindow, removed: list, added: list, text: str) -> None:
        super().__init__(text)
        self._window = window
        self._removed, self._added = list(removed), list(added)

    def _swap(self, take_off: list, put_on: list) -> None:
        from planetpdf.core import links as L

        session = self._window.session
        if take_off:
            L.delete_links_at(session, take_off)
        if put_on:
            L.insert_links(session, put_on)
        self._window.links_changed()

    def redo(self) -> None:
        self._swap(self._removed, self._added)

    def undo(self) -> None:
        self._swap(self._added, self._removed)


class PageContentCommand(QUndoCommand):
    """Any change to page content (headers, watermarks, content edits): undo restores
    the pages' content in place (core.snapshots)."""

    def __init__(self, window: MainWindow, pages: list[int], apply, text: str) -> None:
        super().__init__(text)
        self._window = window
        self._pages = list(pages)
        self._apply = apply
        self._before = None
        self._after = None

    @classmethod
    def applied(cls, window: MainWindow, pages: list[int], apply, text: str) -> PageContentCommand:
        """Run ``apply`` now (errors propagate, nothing changed) and return the command
        to push, so a failure never happens inside ``redo()``."""
        from planetpdf.core import snapshots

        session = window.session
        before = snapshots.take(session, pages)
        try:
            apply(session)
        except Exception:
            snapshots.restore(session, before)
            raise
        command = cls(window, pages, apply, text)
        command._before, command._after = before, snapshots.take(session, pages)
        return command

    def redo(self) -> None:
        from planetpdf.core import snapshots

        session = self._window.session
        if self._after is None:
            self._before = snapshots.take(session, self._pages)
            self._apply(session)
            self._after = snapshots.take(session, self._pages)
        else:
            snapshots.restore(session, self._after)
        self._window.content_changed(set(self._pages))

    def undo(self) -> None:
        from planetpdf.core import snapshots

        snapshots.restore(self._window.session, self._before)
        self._window.content_changed(set(self._pages))


class CropCommand(QUndoCommand):
    def __init__(self, window: MainWindow, pages: list[int], text: str = "Crop Pages", **crop) -> None:
        super().__init__(text)
        self._window = window
        self._pages = list(pages)
        self._crop = crop
        self._old: dict | None = None
        self._new: dict | None = None

    def _refresh(self) -> None:
        count = self._window.session.page_count
        changed = set(self._pages)
        self._window.pages_changed([None if i in changed else i for i in range(count)], self._pages[0], select=self._pages)

    def redo(self) -> None:
        session = self._window.session
        if self._new is None:
            self._old = P.crop_pages(session, self._pages, **self._crop)
            self._new = {i: tuple(session.doc[i].cropbox) for i in self._pages}
        else:
            P.restore_crop(session, self._new)
        self._refresh()

    def undo(self) -> None:
        P.restore_crop(self._window.session, self._old)
        self._refresh()


class SetMetadataCommand(QUndoCommand):
    def __init__(self, window: MainWindow, old: dict, new: dict) -> None:
        super().__init__("Change Document Properties")
        self._window, self._old, self._new = window, old, new

    def redo(self) -> None:
        from planetpdf.core.properties import set_metadata

        set_metadata(self._window.session, self._new)

    def undo(self) -> None:
        from planetpdf.core.properties import set_metadata

        set_metadata(self._window.session, self._old)


class AddMarkupCommand(QUndoCommand):
    def __init__(self, window: MainWindow, spec: K.MarkupSpec) -> None:
        super().__init__(f"Add {spec.kind.label}")
        self._window = window
        self._spec = spec

    def redo(self) -> None:
        K.add_markup(self._window.session, self._spec)
        # Add Text (text typed onto the page, e.g. filling a form without fields) isn't left
        # selected once typing ends (user 2026-10-22: its handles hid where it sits); stamps and
        # signatures are, to adjust them at once.
        typed = self._spec.kind is K.Kind.TEXT
        self._window.markup.changed({self._spec.page}, select=None if typed else self._spec.mark_id)

    def undo(self) -> None:
        K.remove_markup(self._window.session, self._spec.mark_id)
        self._window.markup.changed({self._spec.page})


class RemoveMarkupCommand(QUndoCommand):
    """Works for other software's annotations too (restored from their object source)."""

    def __init__(self, window: MainWindow, item_id: str, label: str) -> None:
        super().__init__(f"Delete {label}")
        self._window = window
        self._id = item_id
        self._removed = None
        self._page = 0

    def redo(self) -> None:
        session = self._window.session
        item = K.find_item(session, self._id)
        self._page = item.page
        # its replies go with it (and come back with it)
        self._replies = [K.remove_reply(session, r.item_id)[1] for r in item.replies]
        self._removed = K.remove_markup(session, self._id)
        self._window.markup.changed({self._page})

    def undo(self) -> None:
        session = self._window.session
        self._id = K.restore_markup(session, self._removed)  # foreign items get a new id
        for reply in getattr(self, "_replies", []):
            K.add_reply(session, self._id, reply.text, reply.author,
                        reply_id=reply.item_id if reply.item_id.startswith(K.REPLY_PREFIX) else None)
        self._window.markup.changed({self._page}, select=self._id)


class ReplyCommand(QUndoCommand):
    """Add a reply to a comment, change one's words, or delete one (user 2026-10-04)."""

    def __init__(self, window: MainWindow, text: str, *, parent_id: str = "", reply_id: str = "",
                 new_text: str | None = None, author: str = "") -> None:
        super().__init__(text)
        self._window = window
        self._parent = parent_id
        self._reply = reply_id
        self._new = new_text
        self._author = author
        self._old: str | None = None
        self._removed = None

    def redo(self) -> None:
        session = self._window.session
        if self._new is None:  # delete
            self._parent, self._removed = K.remove_reply(session, self._reply)
        elif not self._reply or self._removed is not None:  # add (or put back)
            self._reply = K.add_reply(session, self._parent, self._new, self._author, reply_id=self._reply or None)
        else:  # change its words (signed by whoever edits them; undo puts the old author back)
            self._old_author = K._find_reply(session, self._reply)[1].author
            self._old = K.set_reply_text(session, self._reply, self._new, self._window.settings.author())
        self._window.markup.refresh()

    def undo(self) -> None:
        session = self._window.session
        if self._new is None:
            r = self._removed
            self._reply = K.add_reply(session, self._parent, r.text, r.author,
                                      reply_id=r.item_id if r.item_id.startswith(K.REPLY_PREFIX) else None)
        elif self._old is not None:
            K.set_reply_text(session, self._reply, self._old, getattr(self, "_old_author", None))
        else:
            K.remove_reply(session, self._reply)
            self._removed = True  # (redo adds it again, same id)
        self._window.markup.refresh()


class PopupCommand(QUndoCommand):
    """A note's box moved or resized (``core.markup.set_popup``; user 2026-10-04)."""

    def __init__(self, window: MainWindow, item_id: str, old, new, text: str) -> None:
        super().__init__(text)
        self._window = window
        self._id = item_id
        self._old = old
        self._new = new

    def redo(self) -> None:
        K.set_popup(self._window.session, self._id, rect=self._new)
        self._window.markup.refresh()

    def undo(self) -> None:
        if self._old is not None:
            K.set_popup(self._window.session, self._id, rect=self._old)
        self._window.markup.refresh()


class ReplaceMarkupCommand(QUndoCommand):
    """Move / resize / restyle / edit text of one of our items."""

    def __init__(self, window: MainWindow, old: K.MarkupSpec, new: K.MarkupSpec, text: str) -> None:
        super().__init__(text)
        self._window = window
        self._old = old
        self._new = new  # (the author changes only when its words are edited: MarkupController._signed)

    NUDGE_ID = 7301  # arrow-key nudges of one item merge into one undo step

    def id(self) -> int:
        return self.NUDGE_ID if self.text().startswith("Nudge") else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, ReplaceMarkupCommand) or other._old.mark_id != self._new.mark_id:
            return False
        self._new = other._new
        return True

    def redo(self) -> None:
        K.replace_markup(self._window.session, self._old, self._new)
        self._window.markup.changed({self._old.page, self._new.page}, select=self._new.mark_id)

    def undo(self) -> None:
        K.replace_markup(self._window.session, self._new, self._old)
        self._window.markup.changed({self._old.page, self._new.page}, select=self._old.mark_id)


class FlattenPagesCommand(QUndoCommand):
    """Placed text, signatures and stamps become part of the page(s)."""

    def __init__(self, window: MainWindow, pages: list[int]) -> None:
        super().__init__("Flatten Page" if len(pages) == 1 else "Flatten All Pages")
        self._window = window
        self._pages = list(pages)
        self._done: list = []

    def redo(self) -> None:
        self._done = [K.flatten_page(self._window.session, p) for p in self._pages]
        self._window.markup.changed(set(self._pages))

    def undo(self) -> None:
        for done in reversed(self._done):
            K.unflatten_page(self._window.session, done)
        self._window.markup.changed(set(self._pages))


class EditForeignTextCommand(QUndoCommand):
    def __init__(self, window: MainWindow, item_id: str, page: int, new_text: str) -> None:
        super().__init__("Edit Comment")
        self._window = window
        self._id = item_id
        self._page = page
        self._new = new_text
        self._old = ""

    def redo(self) -> None:
        # (its words edited: signed by whoever edits them; undo puts the old author back)
        self._old_author = K.find_item(self._window.session, self._id).author
        self._old = K.set_foreign_text(self._window.session, self._id, self._new, self._window.settings.author())
        self._window.markup.changed({self._page}, select=self._id)

    def undo(self) -> None:
        K.set_foreign_text(self._window.session, self._id, self._old, self._old_author)
        self._window.markup.changed({self._page}, select=self._id)


class EraseInkCommand(QUndoCommand):
    """Comment ▸ Eraser: pencil drawings cut (or removed, when nothing is left)."""

    def __init__(self, controller, changes: list) -> None:
        super().__init__("Erase Drawing")  # (shows as "Undo Erase Drawing")
        self._window = controller._window
        self._changes = list(changes)  # (old spec, new spec or None)

    def redo(self) -> None:
        session = self._window.session
        for old, new in self._changes:
            if new is None:
                K.remove_markup(session, old.mark_id)
            else:
                K.replace_markup(session, old, new)
        self._window.markup.changed({old.page for old, _new in self._changes})

    def undo(self) -> None:
        session = self._window.session
        for old, new in self._changes:
            if new is None:
                K.add_markup(session, old)
            else:
                K.replace_markup(session, new, old)
        self._window.markup.changed({old.page for old, _new in self._changes})


class FlattenMarkupCommand(QUndoCommand):
    def __init__(self, window: MainWindow, item_id: str, label: str) -> None:
        super().__init__(f"Flatten {label}")
        self._window = window
        self._id = item_id
        self._spec: K.MarkupSpec | None = None
        self._contents: list[int] = []

    def redo(self) -> None:
        self._spec, self._contents = K.flatten_markup(self._window.session, self._id)
        self._window.markup.changed({self._spec.page})

    def undo(self) -> None:
        K.unflatten_markup(self._window.session, self._spec, self._contents)
        self._window.markup.changed({self._spec.page}, select=self._spec.mark_id)


class SetFieldValuesCommand(QUndoCommand):
    """One or more form field changes as one undo step: [(name, old, new), ...]."""

    def __init__(self, window: MainWindow, changes: list[tuple[str, object, object]], text: str | None = None) -> None:
        super().__init__(text or (f"Fill “{changes[0][0]}”" if len(changes) == 1 else f"Change {len(changes)} Fields"))
        self._window = window
        self._changes = changes

    def redo(self) -> None:
        for name, _old, new in self._changes:
            F.set_field_value(self._window.session, name, new)
        self._window.forms.values_changed({n for n, _o, _v in self._changes})

    def undo(self) -> None:
        for name, old, _new in reversed(self._changes):
            F.set_field_value(self._window.session, name, old)
        self._window.forms.values_changed({n for n, _o, _v in self._changes})


class OcrTextCommand(QUndoCommand):
    """Insert recognized words (computed beforehand, in the background)."""

    def __init__(self, window: MainWindow, words: dict[int, list[ocr.OcrWord]]) -> None:
        pages = [p for p, w in words.items() if w]
        super().__init__(f"Recognize Text on {_page_word(len(pages))}")
        self._window = window
        self._words = {p: words[p] for p in pages}

    def redo(self) -> None:
        for page, words in self._words.items():
            ocr.insert_ocr_text(self._window.session, page, words)
        self._window.text_layer_changed(set(self._words))

    def undo(self) -> None:
        ocr.remove_ocr_text(self._window.session, list(self._words))
        self._window.text_layer_changed(set(self._words))


class AddMarksCommand(QUndoCommand):
    def __init__(self, window: MainWindow, specs: list[M.MarkSpec], text: str | None = None) -> None:
        count = len(specs)
        super().__init__(text or f"Mark {count} Redaction{'s' if count != 1 else ''}")
        self._window = window
        self._specs = specs

    def redo(self) -> None:
        M.add_marks(self._window.session, self._specs)
        # Select a single new mark (e.g. a box just drawn), but not a whole batch of
        # search results: Delete would then remove them all at once.
        select = {self._specs[0].mark_id} if len(self._specs) == 1 else set()
        self._window.marks_changed({s.page for s in self._specs}, select=select)

    def undo(self) -> None:
        M.remove_marks(self._window.session, [s.mark_id for s in self._specs])
        self._window.marks_changed({s.page for s in self._specs})


class RemoveMarksCommand(QUndoCommand):
    def __init__(self, window: MainWindow, mark_ids: list[str]) -> None:
        count = len(mark_ids)
        super().__init__(f"Remove {count} Redaction Mark{'s' if count != 1 else ''}")
        self._window = window
        self._ids = list(mark_ids)
        self._specs: list[M.MarkSpec] | None = None

    def redo(self) -> None:
        ids = self._ids if self._specs is None else [s.mark_id for s in self._specs]
        self._specs = M.remove_marks(self._window.session, ids)
        self._window.marks_changed({s.page for s in self._specs})

    def undo(self) -> None:
        assert self._specs is not None
        M.add_marks(self._window.session, self._specs)
        self._window.marks_changed({s.page for s in self._specs}, select={s.mark_id for s in self._specs})


class InsertPdfPagesCommand(QUndoCommand):
    """Insert all pages of another PDF. The file's bytes are captured up front, so
    redo works even if the source file changes or disappears afterwards."""

    def __init__(
        self, window: MainWindow, data: bytes, index: int, name: str, password: str | None
    ) -> None:
        super().__init__(f"Insert Pages from {name}")
        self._window = window
        self._data = data
        self._index = index
        self._name = name
        self._password = password
        self._count = 0
        self._salt = uuid.uuid4().hex  # (the inserted items' new ids, the same on every redo)

    def redo(self) -> None:
        self._count, mapping = P.insert_pdf_pages(
            self._window.session, self._data, self._index, name=self._name, password=self._password, salt=self._salt
        )
        inserted = list(range(self._index, self._index + self._count))
        self._window.pages_changed(mapping, focus=self._index, select=inserted)

    def undo(self) -> None:
        mapping = P.remove_inserted(self._window.session, self._index, self._count)
        self._window.pages_changed(mapping, focus=max(self._index - 1, 0))


class FieldDesignCommand(QUndoCommand):
    """A Prepare Form change. The caller has already done it once (so errors are shown
    before anything is pushed); ``do``/``undo`` take the session."""

    def __init__(self, window: MainWindow, text: str, do, undo, pages: set[int]) -> None:
        super().__init__(text)
        self._window = window
        self._do = do
        self._undo = undo
        self._pages = set(pages)
        self._first = True

    def redo(self) -> None:
        if self._first:
            self._first = False
        else:
            self._do(self._window.session)
        self._window.fields_designed(self._pages)

    def undo(self) -> None:
        self._undo(self._window.session)
        self._window.fields_designed(self._pages)


class DocumentSnapshotCommand(QUndoCommand):
    """A change page snapshots can't cover (e.g. flattening the form): undo puts back a
    copy of the whole document taken just before it."""

    def __init__(self, window: MainWindow, text: str, before: bytes, after: bytes) -> None:
        super().__init__(text)
        self._window = window
        self._before = before
        self._after = after
        self._first = True

    @classmethod
    def applied(cls, window: MainWindow, apply, text: str) -> DocumentSnapshotCommand:
        """Run ``apply(session)`` now (errors propagate, nothing changed) and return the
        command to push."""
        session = window.session
        before = session.snapshot()
        try:
            apply(session)
        except Exception:
            session.restore_snapshot(before)
            raise
        return cls(window, text, before, session.snapshot())

    def redo(self) -> None:
        if self._first:
            self._first = False
        else:
            self._window.session.restore_snapshot(self._after)
        self._window.document_replaced()

    def undo(self) -> None:
        self._window.session.restore_snapshot(self._before)
        self._window.document_replaced()
