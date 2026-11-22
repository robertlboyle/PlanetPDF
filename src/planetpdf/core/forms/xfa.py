"""XFA detection and (for hybrid forms) keeping the XFA data in step.

* Pure (dynamic) XFA: the form exists only as XFA; there are no usable AcroForm
  fields. PlanetPDF can't display or fill it and says so.
* Hybrid: AcroForm fields plus an XFA copy. Filled as a normal AcroForm (decision in
  CLAUDE.md). On save the user chooses: drop the XFA part, or keep it and copy the
  values into its ``datasets`` packet so XFA-aware software sees them too.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum

import pymupdf

_REF = re.compile(r"(\d+)\s+0\s+R")
_PACKET = re.compile(r"\((\w+)\)\s*(\d+)\s+0\s+R")
_INDEX = re.compile(r"\[\d+\]")


class XfaKind(Enum):
    NONE = "none"
    HYBRID = "hybrid"
    PURE = "pure"


def xfa_kind(doc: pymupdf.Document) -> XfaKind:
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "AcroForm/XFA")[0] == "null":
        return XfaKind.NONE
    needs_rendering = doc.xref_get_key(catalog, "NeedsRendering")[1] == "true"
    has_fields = any(True for page in doc for _ in page.widgets())
    return XfaKind.PURE if needs_rendering or not has_fields else XfaKind.HYBRID


def remove_xfa(doc: pymupdf.Document) -> None:
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "AcroForm/XFA", "null")
    doc.xref_set_key(catalog, "NeedsRendering", "null")


def _packets(doc: pymupdf.Document) -> dict[str, int]:
    """XFA packet name -> stream xref (only the array form has named packets)."""
    kind, value = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")
    if kind == "array":
        return {name: int(xref) for name, xref in _PACKET.findall(value)}
    return {}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xfa_path(field_name: str) -> list[str]:
    """'form1[0].#subform[0].Name[0]' -> ['form1', 'Name'] (unnamed subforms dropped)."""
    parts = [_INDEX.sub("", p) for p in field_name.split(".")]
    return [p for p in parts if p and not p.startswith("#")]


@dataclass
class XfaSyncResult:
    matched: list[str]
    unmatched: list[str]


def update_datasets(doc: pymupdf.Document, values: dict[str, str]) -> XfaSyncResult:
    """Best-effort: write AcroForm values into the XFA datasets, matching each field by
    its name path (falling back to its last name part). Returns matched/unmatched."""
    packets = _packets(doc)
    xref = packets.get("datasets")
    if xref is None:
        return XfaSyncResult([], sorted(values))
    raw = doc.xref_stream(xref) or b""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return XfaSyncResult([], sorted(values))
    data = next((el for el in root.iter() if _local_name(el.tag) == "data"), None)
    if data is None:
        return XfaSyncResult([], sorted(values))

    matched, unmatched = [], []
    for name, value in values.items():
        path = _xfa_path(name)
        # Explicit None checks: an XML element with no children is falsy.
        element = _find_path(data, path)
        if element is None:
            element = _find_unique_leaf(data, path[-1] if path else name)
        if element is None:
            unmatched.append(name)
            continue
        element.text = value
        matched.append(name)
    for prefix, uri in _namespaces(raw).items():
        ET.register_namespace(prefix, uri)
    doc.update_stream(xref, ET.tostring(root, encoding="utf-8"))
    return XfaSyncResult(sorted(matched), sorted(unmatched))


def _find_path(data: ET.Element, path: list[str]) -> ET.Element | None:
    """Follow the name path below <xfa:data>, allowing the path to skip levels."""
    if not path:
        return None
    candidates = [data]
    for part in path:
        found = []
        for node in candidates:
            found += [el for el in node.iter() if el is not node and _local_name(el.tag) == part]
        if not found:
            return None
        candidates = found
    leaves = [el for el in candidates if len(el) == 0]
    return leaves[0] if len(leaves) == 1 else None


def _find_unique_leaf(data: ET.Element, name: str) -> ET.Element | None:
    leaves = [el for el in data.iter() if _local_name(el.tag) == name and len(el) == 0]
    return leaves[0] if len(leaves) == 1 else None


def _namespaces(raw: bytes) -> dict[str, str]:
    return {prefix.decode(): uri.decode() for prefix, uri in re.findall(rb'xmlns:(\w+)="([^"]+)"', raw)}
