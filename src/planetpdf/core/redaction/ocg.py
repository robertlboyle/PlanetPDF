"""Remove content that belongs to hidden optional-content layers (OCGs).

Hidden = OFF in the default configuration (``/OCProperties/D``). Removed:
* marked-content blocks ``/OC /Name BDC ... EMC`` whose OCG/OCMD is hidden, in page
  content and (recursively) in form XObjects;
* ``/Name Do`` invocations of XObjects that carry a hidden ``/OC``;
* annotations with a hidden ``/OC``.
Afterwards ``/OCProperties`` is deleted, so any remaining optional content is simply
always visible. That must never show what was hidden (security review 2026-11-25: a file whose
list of hidden layers was its own object, ``/OFF 5 0 R``, was read as "nothing hidden", and
Sanitize showed the hidden text): every list is read through references, content whose layer
can't be told stops it (``LayerError``), and the words on each page afterwards are checked
against the words shown before.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from planetpdf.core.contents import replace_contents

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_REF = re.compile(r"(\d+)\s+0\s+R")
_NAMED_REF = re.compile(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R")
_EI = re.compile(rb"[\s\x00]EI(?=[\s\x00/<\[(%]|$)")


@dataclass(frozen=True)
class Token:
    kind: str  # name, num, str, hex, op, dict_start, dict_end, arr_start, arr_end
    value: bytes
    start: int
    end: int


def tokenize(data: bytes):
    """Tokens of a PDF content stream. Inline image data is skipped as one 'op' token."""
    i, n = 0, len(data)
    while i < n:
        c = data[i : i + 1]
        if c in _WS:
            i += 1
        elif c == b"%":
            while i < n and data[i : i + 1] not in b"\r\n":
                i += 1
        elif c == b"(":
            start, depth = i, 0
            while i < n:
                ch = data[i : i + 1]
                if ch == b"\\":
                    i += 2
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            yield Token("str", data[start:i], start, i)
        elif data.startswith(b"<<", i):
            yield Token("dict_start", b"<<", i, i + 2)
            i += 2
        elif data.startswith(b">>", i):
            yield Token("dict_end", b">>", i, i + 2)
            i += 2
        elif c == b"<":
            end = data.find(b">", i)
            end = n if end < 0 else end + 1
            yield Token("hex", data[i:end], i, end)
            i = end
        elif c == b"[":
            yield Token("arr_start", c, i, i + 1)
            i += 1
        elif c == b"]":
            yield Token("arr_end", c, i, i + 1)
            i += 1
        elif c == b"/":
            start = i
            i += 1
            while i < n and data[i : i + 1] not in _WS and data[i : i + 1] not in _DELIM:
                i += 1
            yield Token("name", data[start:i], start, i)
        else:
            start = i
            while i < n and data[i : i + 1] not in _WS and data[i : i + 1] not in _DELIM:
                i += 1
            if i == start:  # stray delimiter such as ')' or '{'
                i += 1
            word = data[start:i]
            if word == b"ID":
                # Inline image: binary data runs until whitespace + EI.
                match = _EI.search(data, i + 1)
                end = match.end() if match else n
                yield Token("op", b"ID", start, end)
                i = end
                continue
            kind = "num" if re.fullmatch(rb"[+-]?(\d+\.?\d*|\.\d+)", word) else "op"
            yield Token(kind, word, start, i)


class LayerError(Exception):
    """Hidden layers couldn't be removed without the risk of showing what they hide."""


def _refs(value: str) -> list[int]:
    return [int(x) for x in _REF.findall(value)]


def _listed(doc: pymupdf.Document, xref: int, key: str) -> list[int]:
    """The objects an entry lists, whether the list is written in place (``[5 0 R 6 0 R]``), is an
    object of its own (``7 0 R`` holding the list), or is a single object (``5 0 R``)."""
    kind, value = doc.xref_get_key(xref, key)
    if kind == "array":
        return _refs(value)
    if kind == "xref":
        target = _refs(value)
        if not target:
            return []
        text = doc.xref_object(target[0]).strip()
        return _refs(text) if text.startswith("[") else target
    return []


def hidden_ocgs(doc: pymupdf.Document) -> set[int]:
    """OCG xrefs that are OFF in the default configuration."""
    catalog = doc.pdf_catalog()
    kind, _ = doc.xref_get_key(catalog, "OCProperties")
    if kind == "null":
        return set()
    all_ocgs = set(_listed(doc, catalog, "OCProperties/OCGs"))
    off = set(_listed(doc, catalog, "OCProperties/D/OFF"))
    on = set(_listed(doc, catalog, "OCProperties/D/ON"))
    base = doc.xref_get_key(catalog, "OCProperties/D/BaseState")[1]
    if base == "/OFF":
        return all_ocgs - on
    return off


def _ve_visible(doc: pymupdf.Document, text: str, hidden: set[int], depth: int = 0) -> bool:
    """A visibility expression (``/VE``: ``[/And 5 0 R [/Not 6 0 R]]``, an OCMD's rule that
    overrides its /OCGs and /P) evaluated with ``hidden`` OFF. ``LayerError`` when it can't be read
    (security review 2026-11-27, finding 7: /VE was ignored, the layer counted as shown, and Sanitize
    dropped the layer settings, so viewers that obey /VE showed what it hid)."""
    if depth > 32:
        raise LayerError("A layer's visibility rule is nested too deeply to be read.")
    tokens = [t for t in tokenize(text.encode("latin-1", "replace"))]

    def operand(i: int) -> tuple[bool, int]:
        tok = tokens[i]
        if tok.kind == "arr_start":
            return expression(i)
        if tok.kind == "num" and i + 2 < len(tokens) and tokens[i + 2].value == b"R":
            ref = int(tok.value)
            target = doc.xref_object(ref).strip() if 0 < ref < doc.xref_length() else ""
            if target.startswith("["):  # (a rule kept as an object of its own)
                return _ve_visible(doc, target, hidden, depth + 1), i + 3
            if doc.xref_get_key(ref, "Type")[1] != "/OCG":
                raise LayerError("A layer's visibility rule names something that isn't a layer.")
            return ref not in hidden, i + 3
        raise LayerError("A layer's visibility rule can't be read.")

    def expression(i: int) -> tuple[bool, int]:
        if tokens[i].kind != "arr_start" or i + 1 >= len(tokens) or tokens[i + 1].kind != "name":
            raise LayerError("A layer's visibility rule can't be read.")
        op = tokens[i + 1].value
        values, i = [], i + 2
        while i < len(tokens) and tokens[i].kind != "arr_end":
            value, i = operand(i)
            values.append(value)
        if i >= len(tokens) or not values:
            raise LayerError("A layer's visibility rule can't be read.")
        if op == b"/And":
            return all(values), i + 1
        if op == b"/Or":
            return any(values), i + 1
        if op == b"/Not" and len(values) == 1:
            return not values[0], i + 1
        raise LayerError("A layer's visibility rule can't be read.")

    try:
        result, end = expression(0)
    except IndexError:
        raise LayerError("A layer's visibility rule can't be read.") from None
    if end != len(tokens):
        raise LayerError("A layer's visibility rule can't be read.")
    return result


def _oc_is_hidden(doc: pymupdf.Document, xref: int, hidden: set[int]) -> bool:
    """For an /OC value (OCG or OCMD xref): is the content invisible by default?"""
    kind, value = doc.xref_get_key(xref, "Type")
    if value == "/OCG":
        return xref in hidden
    if value == "/OCMD":
        ve_kind, ve = doc.xref_get_key(xref, "VE")
        if ve_kind == "xref":
            ve = doc.xref_object(_refs(ve)[0]) if _refs(ve) else ""
        if ve_kind != "null":
            return not _ve_visible(doc, ve, hidden)
        members = _listed(doc, xref, "OCGs")
        if not members:
            return False
        visible = [m not in hidden for m in members]
        policy = doc.xref_get_key(xref, "P")[1] or "/AnyOn"
        shown = {
            "/AllOn": all(visible),
            "/AnyOff": not all(visible),
            "/AllOff": not any(visible),
        }.get(policy, any(visible))  # /AnyOn is the default
        return not shown
    return False


def _dict_entries(doc: pymupdf.Document, xref: int, key: str) -> dict[str, int]:
    """Name -> xref for a resource sub-dictionary such as Properties or XObject."""
    kind, value = doc.xref_get_key(xref, key)
    if kind == "xref":
        target = _refs(value)
        if not target:
            return {}
        return {name: int(ref) for name, ref in _NAMED_REF.findall(doc.xref_object(target[0]))}
    if kind == "dict":
        return {name: int(ref) for name, ref in _NAMED_REF.findall(value)}
    return {}


def _page_resources_xref_key(doc: pymupdf.Document, page_xref: int) -> tuple[int, str]:
    """Where a page's /Resources lives, following /Parent inheritance."""
    xref = page_xref
    for _ in range(64):
        kind, value = doc.xref_get_key(xref, "Resources")
        if kind != "null":
            return xref, "Resources"
        kind, value = doc.xref_get_key(xref, "Parent")
        if kind != "xref":
            break
        xref = _refs(value)[0]
    return page_xref, "Resources"


def _strip_stream(
    data: bytes, properties: dict[str, int], hidden_xobjects: set[str], doc, hidden: set[int], strict: bool = False
) -> tuple[bytes, int]:
    """Remove hidden marked-content blocks and Do's of hidden XObjects. ``strict``: content whose
    layer can't be found raises ``LayerError`` (it might be hidden)."""
    tokens = list(tokenize(data))
    cut: list[tuple[int, int]] = []
    stack: list[tuple[bool, int]] = []  # (hidden?, start offset) per open marked-content
    hidden_depth = 0
    for i, tok in enumerate(tokens):
        if tok.kind != "op":
            continue
        if tok.value in (b"BDC", b"BMC"):
            is_hidden = False
            start = tok.start
            if tok.value == b"BDC" and i >= 2 and tokens[i - 2].value == b"/OC":
                start = tokens[i - 2].start
                prop = tokens[i - 1]
                if prop.kind == "name":
                    xref = properties.get(prop.value[1:].decode("latin-1"))
                    if xref is None and strict:
                        raise LayerError(f"Some content belongs to a layer the file doesn't define "
                                         f"({prop.value.decode('latin-1')}).")
                    is_hidden = xref is not None and _oc_is_hidden(doc, xref, hidden)
                elif strict:
                    raise LayerError("Some content names its layer in a way PlanetPDF can't read.")
            stack.append((is_hidden, start))
            if is_hidden:
                hidden_depth += 1
        elif tok.value == b"EMC" and stack:
            is_hidden, start = stack.pop()
            if is_hidden:
                hidden_depth -= 1
                if hidden_depth == 0:
                    cut.append((start, tok.end))
        elif tok.value == b"Do" and hidden_depth == 0 and i >= 1:
            name = tokens[i - 1]
            if name.kind == "name" and name.value[1:].decode("latin-1") in hidden_xobjects:
                cut.append((name.start, tok.end))
    if not cut:
        return data, 0
    out, pos = bytearray(), 0
    for start, end in sorted(cut):
        if start < pos:
            continue
        out += data[pos:start] + b" "
        pos = end
    out += data[pos:]
    return bytes(out), len(cut)


def _process_resources(doc, owner_xref: int, key: str, data: bytes, hidden: set[int], seen: set[int]):
    """Strip one content stream given its resource owner; recurse into form XObjects."""
    properties = _dict_entries(doc, owner_xref, f"{key}/Properties")
    xobjects = _dict_entries(doc, owner_xref, f"{key}/XObject")
    hidden_xobjects = set()
    for name, xref in xobjects.items():
        oc_kind, oc_value = doc.xref_get_key(xref, "OC")
        if oc_kind == "xref" and _oc_is_hidden(doc, _refs(oc_value)[0], hidden):
            hidden_xobjects.add(name)
    new_data, removed = _strip_stream(data, properties, hidden_xobjects, doc, hidden, strict=True)
    # Recurse into visible form XObjects (they can contain their own hidden blocks).
    for name, xref in xobjects.items():
        if name in hidden_xobjects or xref in seen:
            continue
        seen.add(xref)
        if doc.xref_get_key(xref, "Subtype")[1] != "/Form":
            continue
        sub_data = doc.xref_stream(xref) or b""
        sub_new, sub_removed = _process_resources(doc, xref, "Resources", sub_data, hidden, seen)
        if sub_removed:
            doc.update_stream(xref, sub_new)
            removed += sub_removed
    return new_data, removed


def remove_layer_content(doc: pymupdf.Document, layers: set[int], pages) -> int:
    """Delete marked content belonging to the OCGs ``layers`` (whatever their visibility)
    from the page content of ``pages``. Used to undo OCR text. Returns blocks removed."""
    removed = 0
    for index in pages:
        page = doc[index]
        owner, key = _page_resources_xref_key(doc, page.xref)
        properties = _dict_entries(doc, owner, f"{key}/Properties")
        data = page.read_contents()
        new_data, count = _strip_stream(data, properties, set(), doc, layers)
        if count:
            replace_contents(doc, index, new_data)  # (never in place: streams can be shared)
            removed += count
    return removed


def _shown_words(doc: pymupdf.Document) -> list[Counter]:
    """The words each page shows (text in hidden layers isn't read)."""
    return [Counter(w[4] for w in page.get_text("words")) for page in doc]


def _drop_unused_xobjects(doc: pymupdf.Document) -> None:
    """Pictures and forms a page's resources still name but its content no longer draws (the hidden
    layer's, cut above) are taken out of the resources, so the file's clean-up drops their data too
    (security review 2026-11-27: a hidden picture was no longer drawn, but its data stayed in the
    file). Resources shared by several pages keep whatever any of them draws."""
    used: dict[tuple[int, str], set[str]] = {}
    for page in doc:
        owner = _page_resources_xref_key(doc, page.xref)
        tokens = list(tokenize(page.read_contents()))
        names = used.setdefault(owner, set())
        names.update(tokens[i - 1].value[1:].decode("latin-1") for i, tok in enumerate(tokens)
                     if tok.kind == "op" and tok.value == b"Do" and i and tokens[i - 1].kind == "name")
    for (owner, key), names in used.items():
        kind, value = doc.xref_get_key(owner, f"{key}/XObject")
        if kind not in ("dict", "xref"):
            continue
        for name in _dict_entries(doc, owner, f"{key}/XObject"):
            if name not in names:
                if kind == "dict":
                    doc.xref_set_key(owner, f"{key}/XObject/{name}", "null")
                else:
                    doc.xref_set_key(_refs(value)[0], name, "null")


def remove_hidden_layers(doc: pymupdf.Document) -> int:
    """Delete hidden-layer content and the layer definitions. Returns items removed. Raises
    ``LayerError`` (with ``doc`` part done: the caller puts it back) rather than show anything
    that was hidden."""
    hidden = hidden_ocgs(doc)
    catalog = doc.pdf_catalog()
    has_layers = doc.xref_get_key(catalog, "OCProperties")[0] != "null"
    if not has_layers:
        return 0
    before = _shown_words(doc)
    removed = 0
    # (every page is looked at even with no layer off: a /VE rule with /Not can hide content then)
    seen: set[int] = set()
    for page in doc:
        owner, key = _page_resources_xref_key(doc, page.xref)
        data = page.read_contents()
        new_data, count = _process_resources(doc, owner, key, data, hidden, seen)
        if count:
            # (a fresh stream: one shared with another page would change that page;
            # the old one goes at the garbage-collecting save once nothing uses it)
            replace_contents(doc, page.number, new_data)
            removed += count
        for annot in list(page.annots()):
            oc_kind, oc_value = doc.xref_get_key(annot.xref, "OC")
            if oc_kind == "xref" and _oc_is_hidden(doc, _refs(oc_value)[0], hidden):
                page.delete_annot(annot)
                removed += 1
    if removed:
        _drop_unused_xobjects(doc)
    doc.xref_set_key(catalog, "OCProperties", "null")
    # (MuPDF keeps the layers it read when the file opened: a fresh copy shows what will be seen)
    after = _shown_words(pymupdf.open("pdf", doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)))
    for number, (was, now) in enumerate(zip(before, after), 1):
        if now - was:
            raise LayerError(f"Removing the hidden layers would have shown hidden text on page {number} "
                             f"(\"{' '.join(list((now - was).elements())[:6])}\").")
    return removed
