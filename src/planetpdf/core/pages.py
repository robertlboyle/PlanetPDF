"""Page structure operations: reorder, delete, insert.

Every operation that changes page indices returns a ``PageMap``: for each page index
in the *new* document, the index it had before, or ``None`` for a newly inserted page.
Anything keyed by page index (rendered-page caches, thumbnails, and later redaction
marks) must be remapped with it.

Each operation has an exact inverse so the UI can undo it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from planetpdf.core.document import PdfError, PdfSession, open_pdf_bytes
from planetpdf.core.progress import Progress, report

PageMap = list[int | None]


def _check_indices(session: PdfSession, pages: Sequence[int]) -> list[int]:
    indices = sorted(set(pages))
    if not indices:
        raise PdfError("No pages selected.")
    if indices[0] < 0 or indices[-1] >= session.page_count:
        raise PdfError("Page number out of range.")
    return indices


# --- reorder -------------------------------------------------------------------------


def plan_move(page_count: int, pages: Sequence[int], dest: int) -> list[int]:
    """New page order after moving ``pages`` to sit just before old index ``dest``.

    ``dest`` ranges over 0..page_count (page_count means "to the end"). Moved pages keep
    their relative order. The result is a permutation: ``order[new] == old``.
    """
    moving = sorted(set(pages))
    if not 0 <= dest <= page_count:
        raise PdfError("Drop position out of range.")
    moving_set = set(moving)
    rest = [i for i in range(page_count) if i not in moving_set]
    insert_at = sum(1 for i in rest if i < dest)
    return rest[:insert_at] + moving + rest[insert_at:]


def reorder_pages(session: PdfSession, order: Sequence[int]) -> PageMap:
    """Rearrange pages so that new page ``k`` is old page ``order[k]``."""
    order = list(order)
    if sorted(order) != list(range(session.page_count)):
        raise PdfError("Page order must list every page exactly once.")
    if order != list(range(session.page_count)):
        session.doc.select(order)
        session.mark_modified()
    return list(order)


def inverse_order(order: Sequence[int]) -> list[int]:
    """The order that undoes ``reorder_pages(order)``."""
    inverse = [0] * len(order)
    for new, old in enumerate(order):
        inverse[old] = new
    return inverse


# --- delete / restore -----------------------------------------------------------------


@dataclass(frozen=True)
class RemovedPages:
    """Snapshot of deleted pages, enough to put them back exactly where they were."""

    indices: tuple[int, ...]  # original positions, ascending
    pdf: bytes  # one page per entry in ``indices``, same order


def delete_pages(session: PdfSession, pages: Sequence[int]) -> tuple[RemovedPages, PageMap]:
    indices = _check_indices(session, pages)
    if len(indices) >= session.page_count:
        raise PdfError("A PDF must keep at least one page.")
    doc = session.doc
    snapshot = pymupdf.open()
    for i in indices:
        snapshot.insert_pdf(doc, from_page=i, to_page=i)
    removed = RemovedPages(tuple(indices), snapshot.tobytes())
    snapshot.close()

    gone = set(indices)
    mapping: PageMap = [i for i in range(session.page_count) if i not in gone]
    doc.delete_pages(indices)
    session.mark_modified()
    return removed, mapping


def restore_pages(session: PdfSession, removed: RemovedPages) -> PageMap:
    """Undo ``delete_pages``: re-insert the snapshot pages at their original positions."""
    old_count = session.page_count
    source = open_pdf_bytes(removed.pdf, "deleted pages")
    try:
        # Ascending order: each page lands at its final index.
        for k, index in enumerate(removed.indices):
            session.doc.insert_pdf(source, from_page=k, to_page=k, start_at=index)
    finally:
        source.close()
    session.mark_modified()

    restored = set(removed.indices)
    mapping: PageMap = []
    survivor = 0
    for new in range(old_count + len(restored)):
        if new in restored:
            mapping.append(None)
        else:
            mapping.append(survivor)
            survivor += 1
    return mapping


# --- insert ------------------------------------------------------------------------------


def _insert_mapping(old_count: int, index: int, count: int) -> PageMap:
    return [*range(index), *([None] * count), *range(index, old_count)]


def _check_insert_index(session: PdfSession, index: int) -> None:
    if not 0 <= index <= session.page_count:
        raise PdfError("Insert position out of range.")


def insert_blank_page(
    session: PdfSession, index: int, size: tuple[float, float] | None = None
) -> PageMap:
    """Insert an empty page before ``index`` (``page_count`` appends).

    Default size: the page it is inserted after, or the first page when inserting at 0.
    """
    _check_insert_index(session, index)
    old_count = session.page_count
    if size is None:
        size = session.page_size(max(index - 1, 0)) if old_count else (612.0, 792.0)
    width, height = size
    session.doc.new_page(pno=index if index < old_count else -1, width=width, height=height)
    session.mark_modified()
    return _insert_mapping(old_count, index, 1)


def insert_pdf_pages(
    session: PdfSession, data: bytes, index: int, *, name: str = "PDF", password: str | None = None,
    salt: str | None = None,
) -> tuple[int, PageMap]:
    """Insert every page of the PDF in ``data`` before ``index``. Returns (count, map). PlanetPDF's
    items on the inserted pages get new ids (``markup.renew_ids``; the same ``salt`` gives the same
    ones, for an undo and redo), so copies of pages never share an id with their originals."""
    _check_insert_index(session, index)
    source = open_pdf_bytes(data, name, password)
    try:
        count = source.page_count
        old_count = session.page_count
        session.doc.insert_pdf(source, start_at=index if index < old_count else -1)
    finally:
        source.close()
    from planetpdf.core.markup import new_id, renew_ids

    renew_ids(session.doc, range(index if index < old_count else old_count, (index if index < old_count else old_count)
                                 + count), salt or new_id())
    session.mark_modified()
    return count, _insert_mapping(old_count, index, count)


def remove_inserted(session: PdfSession, index: int, count: int) -> PageMap:
    """Undo an insert of ``count`` pages at ``index``."""
    _, mapping = delete_pages(session, range(index, index + count))
    return mapping


# --- rotate ------------------------------------------------------------------------------


def rotate_pages(session: PdfSession, pages: Sequence[int], degrees: int) -> PageMap:
    """Rotate pages by ``degrees`` (multiple of 90; positive = clockwise).

    Only the page's /Rotate entry changes, so it is lossless and exactly reversible
    with ``-degrees``. Rotated pages map to None (their renders are stale).
    """
    if degrees % 90:
        raise PdfError("Pages can only be rotated in steps of 90 degrees.")
    indices = _check_indices(session, pages)
    if degrees % 360:
        for i in indices:
            page = session.doc[i]
            page.set_rotation((page.rotation + degrees) % 360)
        session.mark_modified()
    rotated = set(indices) if degrees % 360 else set()
    return [None if i in rotated else i for i in range(session.page_count)]


# --- crop ----------------------------------------------------------------------------------


def crop_pages(
    session: PdfSession,
    pages: Sequence[int],
    *,
    margins: tuple[float, float, float, float] | None = None,  # left, top, right, bottom as displayed
    rect: tuple[float, float, float, float] | None = None,  # a drawn box, unrotated coordinates
) -> dict[int, tuple]:
    """Set the visible area (CropBox). Lossless: nothing is deleted. Returns the old
    crop boxes (for undo)."""
    indices = _check_indices(session, pages)
    old = {}
    for i in indices:
        page = session.doc[i]
        old[i] = tuple(page.cropbox)
        mediabox = page.mediabox
        if rect is not None:
            new = pymupdf.Rect(rect) & mediabox
        else:
            left, top, right, bottom = margins or (0, 0, 0, 0)
            shown = page.rect  # displayed size
            box = pymupdf.Rect(left, top, shown.width - right, shown.height - bottom)
            if box.is_empty or box.width < 36 or box.height < 36:
                raise PdfError("Those margins leave almost nothing of the page.")
            # Displayed -> unrotated, then make it relative to the current crop box.
            new = (box * page.derotation_matrix).normalize()
            current = page.cropbox
            new = pymupdf.Rect(new.x0 + current.x0, new.y0 + current.y0, new.x1 + current.x0, new.y1 + current.y0) & mediabox
        if new.is_empty or new.width < 36 or new.height < 36:
            raise PdfError("The crop area is too small.")
        page.set_cropbox(new)
    session.mark_modified()
    return old


def restore_crop(session: PdfSession, old: dict[int, tuple]) -> None:
    for i, box in old.items():
        session.doc[i].set_cropbox(pymupdf.Rect(box))
    session.mark_modified()


# --- split ---------------------------------------------------------------------------------


def split_groups_every(page_count: int, n: int) -> list[list[int]]:
    if n < 1:
        raise PdfError("Choose at least 1 page per file.")
    return [list(range(i, min(i + n, page_count))) for i in range(0, page_count, n)]


def split_groups_by_bookmarks(session: PdfSession) -> list[tuple[str, list[int]]]:
    """(title, pages) for each top-level bookmark: its page up to the next one."""
    starts = [(entry[1], entry[2] - 1) for entry in session.doc.get_toc(simple=True) if entry[0] == 1 and entry[2] > 0]
    starts = sorted({p: t for t, p in starts}.items())
    if not starts:
        raise PdfError("This document has no top-level bookmarks to split at.")
    groups = []
    for n, (page, title) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else session.page_count
        if n == 0 and page > 0:
            groups.append(("Start", list(range(0, page))))
        groups.append((title, list(range(page, end))))
    return groups


def _safe_name(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_." else "_" for c in text).strip(" .")
    return cleaned[:80] or "part"


def split_names(groups: Sequence[tuple[str, Sequence[int]]], directory: Path, base_name: str) -> list[Path]:
    """The files ``split_document`` writes, in order (to see what is already there first)."""
    width = len(str(len(groups)))
    names = []
    for n, (label, pages) in enumerate(groups, start=1):
        if pages:
            suffix = f" - {_safe_name(label)}" if label else ""
            names.append(Path(directory) / f"{_safe_name(base_name)}_{n:0{width}d}{suffix}.pdf")
    return names


def split_document(
    session: PdfSession, groups: Sequence[tuple[str, Sequence[int]]], directory: Path, base_name: str,
    progress: Progress | None = None, protection=None,
) -> list[Path]:
    """Write each (label, pages) group as its own PDF in ``directory`` (encrypted as written with
    ``protection``). ``progress``: "Writing file x of y"; False from it stops after the files
    written so far."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = iter(split_names(groups, directory, base_name))
    paths = []
    files = sum(1 for _label, pages in groups if pages)
    for _label, pages in groups:
        if not pages:
            continue
        if not report(progress, len(paths), files, "Writing file"):
            break
        target = next(names)
        export_pages(session, list(pages), target, protection)
        paths.append(target)
    return paths


# --- export ------------------------------------------------------------------------------


def pages_as_pdf(session: PdfSession, pages: Sequence[int]) -> bytes:
    """These pages (in this order, current state, with their comments) as a small PDF:
    a copy to insert elsewhere (Shift+drag in Organize Pages / the Pages panel)."""
    pages = _check_indices(session, pages)
    out = pymupdf.open()
    try:
        for p in pages:
            out.insert_pdf(session.doc, from_page=p, to_page=p)
        return out.tobytes(garbage=1, deflate=True)
    finally:
        out.close()


def export_pages(session: PdfSession, pages: Sequence[int], target: Path, protection=None) -> None:
    """Write a new PDF containing ``pages`` (in the given order) of the current state,
    including unsaved edits. The open document is not changed.

    The output is encrypted (as it is written) only with ``protection``, whatever the source's.
    """
    if not pages:
        raise PdfError("No pages selected.")
    count = session.page_count
    if any(not 0 <= p < count for p in pages):
        raise PdfError("Page number out of range.")
    target = Path(target).resolve()
    if target == session.path:
        raise PdfError("Choose a different file: exporting can't overwrite the open document.")
    out = pymupdf.open()
    try:
        for p in pages:
            out.insert_pdf(session.doc, from_page=p, to_page=p)
        from planetpdf.core.document import save_pdf
        from planetpdf.core.font_embedding import embed_quietly

        embed_quietly(out)  # (fonts it only names, from this computer)
        save_pdf(out, target, protection, garbage=3, deflate=True)
    finally:
        out.close()
