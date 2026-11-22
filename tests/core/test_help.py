"""The manual (resources/help, core/help.py): every chapter there, links that lead somewhere, the
changes log has this version, the HTML Qt and PyMuPDF both read, US spelling."""

from __future__ import annotations

import json
import re

import pymupdf
import pytest

from planetpdf import __version__
from planetpdf.core import help as H

FOLDER = H.help_dir()
LISTED = json.loads((FOLDER / "chapters.json").read_text(encoding="utf-8"))
WRITTEN = [c["file"] for c in LISTED if not c.get("generated")]
GENERATED = {c["file"] for c in LISTED if c.get("generated")}


def source(name: str) -> str:
    return (FOLDER / name).read_text(encoding="utf-8")


def test_every_chapter_is_there_with_its_title():
    for chapter in H.chapters():
        if chapter.generated:
            continue
        assert chapter.sections and chapter.sections[0].level == 1 and chapter.sections[0].anchor == "top", \
            chapter.file


@pytest.mark.parametrize("name", WRITTEN)
def test_section_ids_are_unique(name):
    ids = re.findall(r'<h[1-3]\s+id="([^"]+)"', source(name))
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


@pytest.mark.parametrize("name", WRITTEN)
def test_only_the_html_both_viewers_read(name):
    text = source(name)
    for tag in ("script", "style", "img", "div", "iframe", "form"):
        assert f"<{tag}" not in text.lower(), f"{name} uses <{tag}>"
    for heading in re.findall(r"<h[23][^>]*>", text):
        assert 'id="' in heading, f"{name}: a heading without an id: {heading}"


def test_links_lead_somewhere():
    anchors = {c.file: {s.anchor for s in c.sections} for c in H.chapters()}
    broken = []
    for name in WRITTEN:
        for href in re.findall(r'href="([^"]+)"', source(name)):
            if href.startswith(("http:", "https:", "mailto:")):
                continue
            file, _, anchor = href.partition("#")
            file = file or name
            if file not in anchors:
                broken.append(f"{name}: {href} (no such chapter)")
            elif anchor and file not in GENERATED and anchor not in anchors[file]:
                broken.append(f"{name}: {href} (no such section)")
    assert not broken, "\n".join(broken)


def test_the_changes_log_has_this_version():
    """Every build adds its changes (user 2026-10-18: "keep a running changes log")."""
    anchor = "v" + __version__.replace(".", "-")
    assert anchor in {s.anchor for s in H.read_chapter("whats-new.html", "", source("whats-new.html")).sections}, \
        f"whats-new.html has no section for version {__version__} (id {anchor!r})"


UK = re.compile(r"\b(colour|centre|cancelled|organis|recognis|behaviour|licence|grey|favourite|catalogue|"
                r"analys(e|ing)|minimis|maximis|optimis|customis|summaris|travell|dialogue)\w*", re.I)


@pytest.mark.parametrize("name", WRITTEN)
def test_us_spelling(name):
    found = sorted({m.group(0) for m in UK.finditer(H.plain(source(name)))})
    assert not found, f"{name}: {found}"


def test_index_and_search():
    entries = H.index_entries()
    assert len(entries) > 100 and all(link.count("#") == 1 for _t, link in entries)
    hits = H.search("Bates numbers")
    assert hits and hits[0].section.file == "pages.html"
    assert H.search("zzzz-nothing") == []


def test_prepared_headings_become_anchors():
    assert H.prepared('<h2 id="x-y">Title</h2>') == '<h2><a name="x-y"></a>Title</h2>'


def test_the_manual_as_a_pdf(tmp_path):
    target = H.write_pdf(tmp_path / H.PDF_NAME, {"menus.html": "<h1>Menu Reference</h1><p>Open</p>"},
                         version=__version__)
    with pymupdf.open(target) as doc:
        titles = [t for _level, t, _page in doc.get_toc()]
        assert titles[0] == "PlanetPDF Manual" and "Getting Started" in titles and "Menu Reference" in titles
        assert "Keyboard Shortcuts" not in titles  # (not given: left out)
        assert doc.page_count > 20
        assert __version__ in doc[0].get_text()
