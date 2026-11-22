"""Form field auto detection (Acrobat's Prepare Form; user 2026-10-03: "If you send it a
standard PDF page without form fields, it creates a form to fill").

What counts as a blank to fill in:

* a run of underscores in the text ("Name: ______");
* a drawn horizontal line (vector), and, on scanned pages, a line in the picture;
* an empty box (vector, or in the picture): a text field (several lines when it's tall);
* a small square, a "☐", or an empty "(   )": a check box.

A line with text standing on it (an answer already typed, underlined words) isn't a
blank. Each field is named after the words to its left on the same line ("Total Number
of Units:" → "Total Number of Units"), or, for lines with nothing to their left, the words
above (several blank lines under one question: "… 1", "… 2"). Names with "date" make date
fields; with "signature" / "sign here", signature fields (when the document requires
signatures). ``detect_fields`` only finds; ``add_detected_fields`` makes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import pymupdf

from planetpdf.core.forms.design import FieldKind, FieldSpec, create_field, field_names
from planetpdf.core.progress import Progress, report

MIN_LINE = 36.0  # points: shorter lines aren't blanks (underlined words, rules in letters)
FIELD_HEIGHT = 16.0
CHECK_MIN, CHECK_MAX = 6.0, 18.0


@dataclass
class _Blank:
    page: int
    rect: tuple[float, float, float, float]  # unrotated
    kind: FieldKind
    multiline: bool = False


# --- what's on a page -------------------------------------------------------------------------------------


def _words(page: pymupdf.Page) -> list[tuple]:
    """(x0, y0, x1, y1, word) of the page's text, underscores and check-box glyphs left out."""
    out = []
    for x0, y0, x1, y1, word, *_rest in page.get_text("words"):
        cleaned = word.strip("_").strip()
        if cleaned and cleaned not in ("☐", "□", "❑", "❒", "▢"):
            out.append((x0, y0, x1, y1, cleaned))
    return out


def _underscore_runs(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """(x0, x1, baseline y) of each run of 3+ underscores."""
    runs = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                current = None
                for ch in span["chars"]:
                    if ch["c"] == "_":
                        x0, _y0, x1, _y1 = ch["bbox"]
                        if current and abs(x0 - current[1]) < 2:
                            current[1] = x1
                            current[3] += 1
                        else:
                            if current and current[3] >= 3:
                                runs.append((current[0], current[1], current[2]))
                            current = [x0, x1, ch["origin"][1], 1]
                    else:
                        if current and current[3] >= 3:
                            runs.append((current[0], current[1], current[2]))
                        current = None
                if current and current[3] >= 3:
                    runs.append((current[0], current[1], current[2]))
    return runs


def _vector_lines_and_boxes(page: pymupdf.Page):
    """Horizontal lines (x0, x1, y) and boxes (rects) from the page's drawings. A box counts
    only with a visible border and nothing (or white) inside (user 2026-10-04: a resume's
    shaded strips and gray rules became fields): filled shapes are design, not blanks."""
    lines, boxes = [], []
    for path in page.get_drawings():
        fill = path.get("fill")
        stroked = path.get("color") is not None and (path.get("width") or 1) > 0
        empty = fill is None or all(v > 0.97 for v in fill)
        for item in path["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1.0 and abs(a.x - b.x) >= MIN_LINE:
                    lines.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
            elif item[0] == "re":
                r = pymupdf.Rect(item[1])
                if r.height <= 1.6 and r.width >= MIN_LINE:  # (a line drawn as a thin box)
                    lines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width >= CHECK_MIN and r.height >= CHECK_MIN and stroked and empty:
                    boxes.append(r)
    return lines, boxes


def _ocr_words(page: pymupdf.Page) -> list[tuple]:
    """A scan without a text layer: its words read with OCR (labels for the names), if Tesseract is here."""
    from planetpdf.core import ocr

    tessdata = ocr.find_tessdata()
    if tessdata is None:
        return []
    try:
        found = ocr._recognize_page(page, "eng", 200, tessdata)
    except Exception:  # (names are a nicety: the fields are found without them)
        return []
    out = []
    for word in found:
        cleaned = word.text.strip("_").strip()
        letters = sum(c.isalnum() for c in cleaned)
        if not cleaned or letters < 0.6 * len(cleaned):
            continue  # (an answer line or a box read as letters: "———“‘C:*s*S")
        x0, y0, x1, y1 = word.rect
        # Tesseract takes the blank line after a label into the word ("Name:______" = one wide word)
        x1 = min(x1, x0 + len(word.text.rstrip("_")) * (y1 - y0) * 0.8)
        out.append((x0, y0, x1, y1, cleaned))
    return out


def _picture_lines_and_boxes(page: pymupdf.Page):
    """Scanned pages: long horizontal lines and boxes in the picture (unrotated points)."""
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return [], []
    dpi = 150
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    ink = (gray < cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[0]).astype(np.uint8) * 255
    px = dpi / 72
    long = int(MIN_LINE * px)
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (long, 1)))
    back = page.derotation_matrix
    lines = []
    count, _labels, stats, _c = cv2.connectedComponentsWithStats(horizontal, connectivity=8)
    for n in range(1, count):
        x, y, w, h, _area = stats[n]
        if h <= 4 * px:
            r = pymupdf.Rect(x / px, y / px, (x + w) / px, (y + h) / px) * back
            if r.width >= r.height:  # (the page shown upright: lines run across)
                lines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
    boxes = []
    contours, _hier = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.03 * cv2.arcLength(contour, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            x, y, w, h = cv2.boundingRect(approx)
            if w >= CHECK_MIN * px and h >= CHECK_MIN * px:
                boxes.append(pymupdf.Rect(x / px, y / px, (x + w) / px, (y + h) / px) * back)
    return lines, boxes


# Placeholders printed where the answer goes (user 2026-10-23): (pattern, kind, name when no label)
PLACEHOLDERS = [
    (re.compile(r"\b(?:MM|DD)[/\-.](?:DD|MM)[/\-.](?:YYYY|YY)\b", re.IGNORECASE), FieldKind.DATE, "Date"),
    (re.compile(r"_{2}\s*/\s*_{2}\s*/\s*_{2,4}"), FieldKind.DATE, "Date"),
    (re.compile(r"\(\s*(?:_{3}|[xX0#]{3}|\s{2,})\s*\)\s*(?:_{3}|[xX0#]{3})\s*-\s*(?:_{4}|[xX0#]{4})"), FieldKind.TEXT, "Phone"),
    (re.compile(r"\b(?:XXX|xxx|###)-(?:XX|xx|##)-(?:XXXX|xxxx|####)\b|_{3}-_{2}-_{4}"), FieldKind.TEXT, "SSN"),
    (re.compile(r"\$\s?_{3,}(?:\.\s?_{2})?"), FieldKind.TEXT, "Amount"),
]
PLACEHOLDER_MIN_WIDTH = 72.0  # (room to type in, even over a short "MM/DD/YY")


def _placeholders(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, FieldKind, str]]:
    """(rect, kind, fallback name) of each placeholder on the page's text lines. One in
    brackets is a hint for the blank beside it ("Date of Birth (MM/DD/YYYY): ____"), not one."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            chars = [ch for span in line["spans"] for ch in span["chars"]]
            text = "".join(ch["c"] for ch in chars)
            for pattern, kind, name in PLACEHOLDERS:
                for match in pattern.finditer(text):
                    before, after = text[:match.start()].rstrip()[-1:], text[match.end():].lstrip()[:1]
                    if before in "([" and after in ")]" and before and after:
                        continue  # (a hint)
                    rect = pymupdf.Rect()
                    for ch in chars[match.start():match.end()]:
                        rect |= pymupdf.Rect(ch["bbox"])
                    if not rect.is_empty and not any(rect.intersects(r) for r, _k, _n in out):
                        out.append((rect, kind, name))
    return out


TABLE_EMPTY_SHARE = 0.25  # (a table counts as a form when this much of its body is empty, or a row is)


def _form_tables(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, list[tuple[str, tuple, bool]]]]:
    """Tables meant to be filled in (user 2026-10-23): a header row of words, and empty body
    cells, a whole empty row or at least ``TABLE_EMPTY_SHARE`` of them (a price list, full of
    data, is left alone). (table area, [(name, cell rect, tall), …]): each empty cell named by
    its column and its row's label in the first column, else the row's number ("Qty 3")."""
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):  # (PyMuPDF prints a notice about another package)
        try:
            tables = page.find_tables().tables
        except Exception:  # (a page the table finder can't read: no tables)
            return []
    out = []
    for table in tables:
        rows = table.extract()
        if len(rows) < 2 or table.header.external:
            continue
        headers = [(h or "").strip() for h in rows[0]]
        if not all(headers) or any(re.fullmatch(r"[\d.,$%\s-]+", h) for h in headers):
            continue  # (no header row: a first row of data)
        cells = []
        empty_rows = False
        for r, row in enumerate(rows[1:], start=1):
            blank = [c for c, text in enumerate(row) if not (text or "").strip() and table.rows[r].cells[c] is not None]
            empty_rows |= len(blank) == len(row)
            row_label = _clean((row[0] or "").replace("\n", " "))
            for c in blank:
                column = _clean(headers[c].replace("\n", " ")) or f"Column {c + 1}"
                name = f"{column} {row_label}" if c > 0 and row_label else f"{column} {r}"
                x0, y0, x1, y1 = table.rows[r].cells[c]
                cells.append((name, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), y1 - y0 > 40))
        body = sum(1 for row in table.rows[1:] for cell in row.cells if cell is not None)
        if cells and (empty_rows or len(cells) >= TABLE_EMPTY_SHARE * body):
            out.append((pymupdf.Rect(table.bbox), cells))
    return out


def _paren_checkboxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """"(   )" with nothing inside: a check box between the brackets."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            chars = [ch for span in line["spans"] for ch in span["chars"]]
            for n, ch in enumerate(chars):
                if ch["c"] != "(":
                    continue
                for m in range(n + 1, min(n + 8, len(chars))):
                    c = chars[m]["c"]
                    if c == ")":
                        x0, x1 = ch["bbox"][2], chars[m]["bbox"][0]
                        y0, y1 = ch["bbox"][1], ch["bbox"][3]
                        if x1 - x0 >= 4:
                            size = min(y1 - y0, 12.0)
                            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                            out.append(pymupdf.Rect(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2))
                        break
                    if c.strip():
                        break
    return out


# --- from blanks to fields --------------------------------------------------------------------------------------


def _has_text_on(words, x0: float, x1: float, y: float) -> bool:
    """Words standing on the line, or crossed by it (an answer typed on it, underlined words,
    a link's underline running through the descenders)."""
    for wx0, wy0, wx1, wy1, _w in words:
        if wy0 <= y + 2 and wy1 >= y - 12 and min(wx1, x1) - max(wx0, x0) > 0.25 * (wx1 - wx0):
            return True
    return False


def _ends_like_a_prompt(text: str) -> bool:
    """A label that asks for something: "Name:", "Why do you want this job?", or one of the
    usual form labels with no colon at all ("Date of Birth", user 2026-10-23)."""
    return text.rstrip().endswith((":", "?")) or is_known_label(text)


# Labels forms use without a colon (user 2026-10-23: "known form words", not any short label,
# which gave a resume false fields). Matched whole, ignoring case and a trailing "*" / "#".
KNOWN_LABELS = frozenset("""
name|first name|last name|middle name|full name|middle initial|printed name|print name|your name|
date|today's date|todays date|date of birth|birth date|birthdate|dob|start date|end date|
address|street|street address|mailing address|home address|address line 1|address line 2|
city|state|zip|zip code|postal code|county|country|city state zip|apt|suite|
phone|phone number|telephone|home phone|work phone|cell|cell phone|mobile|mobile phone|fax|
email|e-mail|email address|e-mail address|website|
signature|applicant signature|employee signature|initials|witness|title|job title|position|
company|company name|employer|organization|department|occupation|
ssn|social security number|account number|policy number|member id|employee id|
amount|total|relationship|emergency contact|age|gender
""".replace("\n", "").split("|")) - {""}


def is_known_label(text: str) -> bool:
    cleaned = re.sub(r"[\s*#:]+$", "", text.strip()).lower()
    return re.sub(r"\s+", " ", cleaned) in KNOWN_LABELS


def _text_lines(page: pymupdf.Page, words) -> list[tuple[float, float, float, float, str]]:
    """The page's lines of words: (x0, y0, x1, y1, text), top to bottom (words on one
    baseline and close together; a big gap starts another line, e.g. a second column)."""
    out: list[list] = []
    for w in sorted(words, key=lambda w: (round(w[3]), w[0])):
        last = out[-1] if out else None
        if last is not None and abs(last[3] - w[3]) < 3 and w[0] - last[2] < 30:
            last[2], last[1], last[4] = w[2], min(last[1], w[1]), last[4] + " " + w[4]
        else:
            out.append([w[0], w[1], w[2], w[3], w[4]])
    return [tuple(l) for l in out]


def _open_prompts(page: pymupdf.Page, words, taken) -> list[tuple[str, tuple]]:
    """A label ending in ":" or "?" with empty space after it on its line (no line drawn:
    "Phone:" then blank to the margin): a field in that space. Not a heading (its content
    on the lines under it, "Skills:" in a resume), and not when something's already there."""
    lines = _text_lines(page, words)
    if not lines:
        return []
    right = max(l[2] for l in lines)  # (the text's right edge: the margin it would be filled to)
    found = []
    for n, (x0, y0, x1, y1, text) in enumerate(lines):
        if not _ends_like_a_prompt(text) or len(text.split()) > (14 if text.rstrip().endswith("?") else 6):
            continue
        if right - x1 < 108:  # (less than 1.5 in. of room after it)
            continue
        if any(o is not lines[n] and abs(o[3] - y1) < 3 and o[0] > x1 for o in lines):
            continue  # (something after it on its line)
        if not text.rstrip().endswith((":", "?")) and any(o is not lines[n] and abs(o[3] - y1) < 3 for o in lines):
            continue  # (a label with no colon stands alone: else it's a column heading, "Name   Amount")
        below = [o for o in lines if 0 < o[1] - y1 < (y1 - y0) * 1.6 and o[0] < x0 + 20]
        if below and not all(_ends_like_a_prompt(o[4]) for o in below):
            continue  # (a heading: its content is on the lines under it)
        rect = (x1 + 6, y0 - 2, right, y1 + 2)
        if any(pymupdf.Rect(rect).intersects(pymupdf.Rect(t)) for t in taken):
            continue
        found.append((text, rect))
    return found


def _room_above(words, x0: float, x1: float, y: float) -> float:
    """Free height above a line before the nearest words over it."""
    room = 26.0
    for wx0, wy0, wx1, wy1, _w in words:
        if min(wx1, x1) - max(wx0, x0) > 0 and wy1 <= y + 1:
            room = min(room, y - wy1)
    return room


def _label_left(words, x0: float, y: float) -> str:
    """The words just left of a blank, on its line."""
    line = sorted((w for w in words if w[2] <= x0 + 2 and abs(w[3] - y) < 6), key=lambda w: w[0])
    picked = []
    edge = x0
    for wx0, _wy0, wx1, _wy1, word in reversed(line):
        if edge - wx1 > 40:
            break
        picked.insert(0, word)
        edge = wx0
    return " ".join(picked)


def _label_above(words, x0: float, x1: float, y: float) -> str:
    """The line of words just above a blank (a question with answer lines under it)."""
    above = [w for w in words if w[3] <= y - 4 and y - w[3] < 40 and min(w[2], x1) - max(w[0], x0) > -40]
    if not above:
        return ""
    nearest = max(w[3] for w in above)
    return " ".join(w[4] for w in sorted((w for w in above if abs(w[3] - nearest) < 4), key=lambda w: w[0]))


def _clean(name: str) -> str:
    for pattern, _kind, _name in PLACEHOLDERS:  # ("Date of Birth (MM/DD/YYYY)": the hint isn't the name)
        name = pattern.sub("", name)
    name = re.sub(r"\(\s*\)", "", name)
    name = re.sub(r"[^\w\s\-#/]", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    if len(name) > 60:
        name = name[:60].rsplit(" ", 1)[0]
    return name.strip(" -/")


def _kind_for(name: str, signatures: bool) -> FieldKind:
    lower = name.lower()
    if signatures and ("signature" in lower or "sign here" in lower or lower.startswith("signed")):
        return FieldKind.SIGNATURE
    if re.search(r"\bdate\b", lower):
        return FieldKind.DATE
    return FieldKind.TEXT


def _merge_lines(lines):
    """Collinear line pieces (an underscore run split in spans, a line drawn twice) joined."""
    out = []
    for x0, x1, y in sorted(lines, key=lambda l: (round(l[2]), l[0])):
        if out and abs(out[-1][2] - y) < 2 and x0 <= out[-1][1] + 3:
            px0, px1, py = out[-1]
            out[-1] = (px0, max(px1, x1), py)
        else:
            out.append((x0, x1, y))
    return out


def detect_page(doc: pymupdf.Document, index: int, *, signatures: bool = False) -> list[FieldSpec]:
    page = doc[index]
    words = _words(page)
    lines = _underscore_runs(page)
    typed = list(lines)  # (underscores: typed to be filled in, always a blank)
    vector_lines, boxes = _vector_lines_and_boxes(page)
    lines += vector_lines
    scanned = not words or (len(page.get_images()) == 1 and not vector_lines and not lines)
    if scanned:
        if not words:
            words = _ocr_words(page)
        picture_lines, picture_boxes = _picture_lines_and_boxes(page)
        # (a box's top and bottom edges aren't answer lines of their own)
        lines += [l for l in picture_lines
                  if not any(abs(l[2] - y) < 4 and l[0] >= b.x0 - 4 and l[1] <= b.x1 + 4
                             for b in picture_boxes if b.height > CHECK_MAX for y in (b.y0, b.y1))]
        boxes += picture_boxes
    found: list[tuple[str, _Blank]] = []
    tables = [] if scanned else _form_tables(page)
    for _area, cells in tables:
        for name, rect, tall in cells:
            found.append((name, _Blank(index, rect, _kind_for(name, signatures), multiline=tall)))
    table_areas = [area + (-2, -2, 2, 2) for area, _cells in tables]

    def in_a_table(rect) -> bool:
        """Inside a form table, only its empty cells are blanks (its rules aren't answer lines)."""
        return any(area.contains(pymupdf.Rect(rect)) for area in table_areas)

    last_label = ""
    last_x = last_y = None
    last_left = False
    def is_typed(x0: float, x1: float, y: float) -> bool:
        return any(abs(t[2] - y) < 2 and t[0] < x1 and t[1] > x0 for t in typed)

    # Something counts as a blank only with a reason to think it's meant to be filled in
    # (user 2026-10-04: a resume's section rules, link underlines and shaded strips became
    # fields; "it's a resume so it shouldn't find anything"): underscores; a drawn line or
    # box with a label just before it; answer lines under a label ending in ":" or "?";
    # a label ending in ":" or "?" with empty space after it; check boxes with words beside.
    merged = _merge_lines(lines)
    for x0, x1, y in merged:
        if x1 - x0 < MIN_LINE or _has_text_on(words, x0, x1, y) or in_a_table((x0, y - 1, x1, y + 1)):
            continue
        height = max(10.0, min(FIELD_HEIGHT, _room_above(words, x0, x1, y) - 1))
        rect = (x0, y - height, x1, y - 0.5)
        left = _label_left(words, x0, y)
        label = _clean(left)
        if not label:  # (an answer line under a question, or under the last labeled one)
            follows = last_x is not None and abs(last_x - x0) < 60 and 0 < y - last_y < 45 and not last_left
            above = _label_above(words, x0, x1, y)
            if follows:
                label = last_label
            elif _ends_like_a_prompt(above) or is_typed(x0, x1, y):
                label = _clean(above)
            else:
                continue  # (a rule under a heading, a divider: design, not a blank)
            last_left = False
        else:
            last_left = True
        last_label, last_x, last_y = label, x0, y
        found.append((label, _Blank(index, rect, _kind_for(label, signatures))))
    for rect, kind, fallback in _placeholders(page):
        area = _placeholder_area(rect, merged, words)
        if any(pymupdf.Rect(b.rect).intersects(area) for _l, b in found):
            continue
        above = _label_above(words, rect.x0, rect.x1, rect.y0 + 2)
        label = _clean(_label_left(words, rect.x0, rect.y1)) or (_clean(above) if _ends_like_a_prompt(above) else "")
        label = label or fallback
        found.append((label, _Blank(index, tuple(area), kind if kind is FieldKind.DATE else _kind_for(label, signatures))))
    for box in _dedupe_boxes(boxes):
        if in_a_table(box):
            continue
        inside = [w for w in words if pymupdf.Rect(w[:4]).intersects(box) and not _is_the_box(w, box)]
        if inside:
            continue  # (a box with text in it: a frame, a table cell already filled)
        if box.width <= CHECK_MAX and box.height <= CHECK_MAX and abs(box.width - box.height) < 4:
            label = _clean(_label_right(words, box))
            if label:  # (a small square with no words beside it is a bullet or a decoration)
                found.append((label, _Blank(index, tuple(box), FieldKind.CHECKBOX)))
        elif box.width >= MIN_LINE and 10 <= box.height <= 300 and box.width < page.rect.width * 0.95:
            left = _label_left(words, box.x0, box.y1 - 3)
            above = _label_above(words, box.x0, box.x1, box.y0 + 2)
            if not left and not _ends_like_a_prompt(above):
                continue  # (an empty frame with nothing asking for it)
            label = _clean(left or above)
            inner = (box.x0 + 1, box.y0 + 1, box.x1 - 1, box.y1 - 1)
            found.append((label, _Blank(index, inner, _kind_for(label, signatures), multiline=box.height > 40)))
    for box in _paren_checkboxes(page) + _glyph_checkboxes(page):
        if any(box.intersects(pymupdf.Rect(b.rect)) for _l, b in found):
            continue  # (the "(   )" of a phone number placeholder)
        label = _clean(_label_right(words, box))
        if label:
            found.append((label, _Blank(index, tuple(box), FieldKind.CHECKBOX)))
    for text, rect in _open_prompts(page, words, [b.rect for _l, b in found]):
        if any(pymupdf.Rect(rect).intersects(area) for area in table_areas):
            continue
        label = _clean(text)
        found.append((label, _Blank(index, rect, _kind_for(label, signatures))))
    existing = [pymupdf.Rect(w.rect) for w in page.widgets()]  # (fields the file already has)
    found = [(label, blank) for label, blank in found
             if not any(pymupdf.Rect(blank.rect).intersects(r) for r in existing)]
    return _named(found)


def _placeholder_area(rect: pymupdf.Rect, lines, words) -> pymupdf.Rect:
    """Where a placeholder's field goes: the answer line it sits on, else the placeholder
    itself made at least ``PLACEHOLDER_MIN_WIDTH`` wide (when nothing is in the way)."""
    line = next((l for l in lines if l[0] < rect.x1 and l[1] > rect.x0 and -2 <= l[2] - rect.y1 <= 6), None)
    if line is not None:
        area = pymupdf.Rect(min(line[0], rect.x0), rect.y0 - 1, max(line[1], rect.x1), line[2] - 0.5)
    else:
        x1 = max(rect.x1, rect.x0 + PLACEHOLDER_MIN_WIDTH)
        if any(w[0] >= rect.x1 - 1 and w[0] < x1 and abs(w[3] - rect.y1) < 4 for w in words):
            x1 = rect.x1  # (words follow it: no room)
        area = pymupdf.Rect(rect.x0 - 1, rect.y0 - 1, x1, rect.y1 + 1)
    if area.height < 12:
        area.y0 = area.y1 - 12
    return area


def _is_the_box(word, box) -> bool:
    """OCR reading a small check box as a "word" ("CL]", "O")."""
    r = pymupdf.Rect(word[:4])
    return len(word[4]) <= 3 and box.width <= CHECK_MAX and (r & box).get_area() > 0.6 * r.get_area()


def _label_right(words, box) -> str:
    """A check box's words: just right of it on its line."""
    line = sorted((w for w in words if w[0] >= box.x1 - 1 and abs((w[1] + w[3]) / 2 - (box.y0 + box.y1) / 2) < 6),
                  key=lambda w: w[0])
    picked, edge = [], box.x1
    for wx0, _wy0, wx1, _wy1, word in line:
        if wx0 - edge > 20 or word.startswith("("):
            break
        picked.append(word)
        edge = wx1
        if len(picked) >= 6:
            break
    return " ".join(picked)


def _glyph_checkboxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"] in "☐□❑❒▢":
                        out.append(pymupdf.Rect(ch["bbox"]))
    return out


def _dedupe_boxes(boxes) -> list[pymupdf.Rect]:
    out: list[pymupdf.Rect] = []
    for box in sorted(boxes, key=lambda r: (r.y0, r.x0)):
        if any(abs(box.x0 - o.x0) < 3 and abs(box.y0 - o.y0) < 3 and abs(box.x1 - o.x1) < 3 and abs(box.y1 - o.y1) < 3
               for o in out):
            continue
        out.append(box)
    return out


def _named(found: list[tuple[str, _Blank]]) -> list[FieldSpec]:
    """Names from labels; a label used more than once gets numbers ("Unit Types 1, 2, 3")."""
    counts: dict[str, int] = {}
    for label, blank in found:
        key = label or {FieldKind.CHECKBOX: "Check Box"}.get(blank.kind, "Text")
        counts[key] = counts.get(key, 0) + 1
    seen: dict[str, int] = {}
    specs = []
    for label, blank in found:
        key = label or {FieldKind.CHECKBOX: "Check Box"}.get(blank.kind, "Text")
        seen[key] = seen.get(key, 0) + 1
        name = f"{key} {seen[key]}" if counts[key] > 1 or not label else key
        specs.append(FieldSpec(name=name, kind=blank.kind, page=blank.page, rect=blank.rect,
                               multiline=blank.multiline, tooltip=label,
                               date_format="mm/dd/yyyy" if blank.kind is FieldKind.DATE else ""))
    return specs


def detect_fields(doc: pymupdf.Document, pages, *, signatures: bool = False,
                  progress: Progress | None = None) -> list[FieldSpec]:
    pages = list(pages)
    found = []
    for n, index in enumerate(pages):
        if not report(progress, n, len(pages), "Looking at page"):
            break
        found += detect_page(doc, index, signatures=signatures)
    return found


def add_detected_fields(session, specs: list[FieldSpec]) -> int:
    """Make the fields (names kept unique in the document)."""
    taken = set(field_names(session))
    made = 0
    for spec in specs:
        name, n = spec.name, 2
        while name in taken:
            name = f"{spec.name} {n}"
            n += 1
        taken.add(name)
        create_field(session, replace(spec, name=name))
        made += 1
    return made
