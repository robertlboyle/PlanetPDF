"""PlanetPDF's user manual (user 2026-10-18: "a help file that explains everything … include it in
the program"; shared rule: a real manual, getting started, tasks, a reference of every menu,
window and setting, troubleshooting, an index and search, kept current with every change).

The manual is HTML in ``resources/help``: ``chapters.json`` lists the chapters in order
(``[{"file": "getting-started.html", "title": "Getting Started"}, …]``). In a chapter, every
``<h2 id="…">`` / ``<h3 id="…">`` is a section: it's in the contents, the index and search, and
can be linked to as ``chapter.html#id``. More index words for a section go in a comment right
after its heading: ``<!-- index: Bates numbers; numbering pages -->``. Pages the program makes
itself (every menu command, every shortcut) are listed with ``"generated": true``.

This module only reads and searches (no Qt); ``ui/help_window.py`` shows it.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from planetpdf.core.resources import resources_dir

HEADING = re.compile(r"<h([1-3])\s+id=\"([A-Za-z0-9_.-]+)\"\s*>(.*?)</h\1>", re.S | re.I)
INDEX_NOTE = re.compile(r"<!--\s*index:(.*?)-->", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def help_dir() -> Path:
    return resources_dir() / "help"


def plain(fragment: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", fragment)).split())


@dataclass
class Section:
    file: str
    anchor: str
    title: str
    level: int  # 1 = the chapter's title, 2, 3
    text: str = ""  # its words (for search), up to the next section
    index_terms: list[str] = field(default_factory=list)

    @property
    def link(self) -> str:
        return f"{self.file}#{self.anchor}"


@dataclass
class Chapter:
    file: str
    title: str
    generated: bool = False
    sections: list[Section] = field(default_factory=list)


def read_chapter(file: str, title: str, source: str) -> Chapter:
    chapter = Chapter(file, title)
    body = re.sub(r"(?is)<head>.*?</head>", "", source)
    matches = list(HEADING.finditer(body))
    for n, m in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(body)
        after = body[m.end():end]
        note = INDEX_NOTE.match(after.lstrip())
        terms = [t.strip() for t in note.group(1).split(";") if t.strip()] if note else []
        chapter.sections.append(Section(file, m.group(2), plain(m.group(3)), int(m.group(1)),
                                        plain(INDEX_NOTE.sub(" ", after)), terms))
    return chapter


@lru_cache(maxsize=1)
def chapters(folder: Path | None = None) -> tuple[Chapter, ...]:
    folder = folder or help_dir()
    listed = json.loads((folder / "chapters.json").read_text(encoding="utf-8"))
    out = []
    for entry in listed:
        if entry.get("generated"):
            out.append(Chapter(entry["file"], entry["title"], generated=True))
            continue
        source = (folder / entry["file"]).read_text(encoding="utf-8")
        out.append(read_chapter(entry["file"], entry["title"], source))
    return tuple(out)


def prepared(source: str) -> str:
    """For Qt's HTML viewer: each heading's id becomes an anchor it can scroll to."""
    return HEADING.sub(lambda m: f'<h{m.group(1)}><a name="{m.group(2)}"></a>{m.group(3)}</h{m.group(1)}>', source)


def index_entries(all_chapters=None) -> list[tuple[str, str]]:
    """(term, link), sorted: every section's title and its extra index words."""
    entries = set()
    for chapter in all_chapters or chapters():
        for s in chapter.sections:
            if s.level >= 2:
                entries.add((s.title, s.link))
            for term in s.index_terms:
                entries.add((term, s.link))
    return sorted(entries, key=lambda e: (e[0].casefold(), e[1]))


@dataclass
class Hit:
    section: Section
    chapter: str
    score: int
    snippet: str


def search(query: str, all_chapters=None, limit: int = 50) -> list[Hit]:
    """Sections that have every word of ``query``; title and index matches first."""
    words = [w for w in re.findall(r"[\w'-]+", query.casefold()) if w]
    if not words:
        return []
    hits = []
    for chapter in all_chapters or chapters():
        for s in chapter.sections:
            title, terms, text = s.title.casefold(), " ".join(s.index_terms).casefold(), s.text.casefold()
            if not all(w in title or w in terms or w in text for w in words):
                continue
            score = sum(10 * (w in title) + 5 * (w in terms) + min(text.count(w), 5) for w in words)
            at = min((text.find(w) for w in words if w in text), default=-1)
            snippet = s.text[max(0, at - 60): at + 140].strip() if at >= 0 else s.text[:200]
            if at > 60:
                snippet = "…" + snippet
            hits.append(Hit(s, chapter.title, score, snippet))
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


# --- the manual as a PDF (made by every build: "PlanetPDF Manual.pdf" in the install folder) ---------

PDF_NAME = "PlanetPDF Manual.pdf"
PDF_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 22pt; margin: 0 0 10pt 0; }
h2 { font-size: 14pt; margin: 16pt 0 4pt 0; }
h3 { font-size: 11.5pt; margin: 12pt 0 3pt 0; }
p, li { margin: 0 0 5pt 0; }
code { font-family: monospace; }
table { border-collapse: collapse; }
th, td { border: 0.5pt solid #999; padding: 2pt 4pt; text-align: left; vertical-align: top; }
a { color: #1a4f9c; text-decoration: none; }
"""


def chapter_body(source: str) -> str:
    """A chapter's HTML for the PDF: no head, no index notes."""
    body = re.sub(r"(?is)<head>.*?</head>", "", source)
    return INDEX_NOTE.sub("", body)


def write_pdf(target: Path, generated: dict[str, str] | None = None, *, version: str = "",
              progress=None) -> Path:
    """The whole manual as one PDF: a title page, then each chapter on new pages, with bookmarks
    and page numbers. ``generated`` gives the pages the program makes (menu and shortcut
    references); chapters it doesn't give are left out. ``progress(done, total, stage)``."""
    import io

    import pymupdf

    from planetpdf.core.progress import report

    folder = help_dir()
    page_rect = pymupdf.paper_rect("letter")
    frame = page_rect + (60, 60, -60, -66)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    toc, count = [], 0
    title = (f"<h1 style='font-size:30pt; margin-top:200pt'>PlanetPDF Manual</h1>"
             f"<p style='font-size:13pt'>Version {html.escape(version)}</p>" if version else
             "<h1 style='font-size:30pt; margin-top:200pt'>PlanetPDF Manual</h1>")
    parts = [("PlanetPDF Manual", title)]
    for chapter in chapters(folder):
        if chapter.generated:
            source = (generated or {}).get(chapter.file)
        else:
            source = (folder / chapter.file).read_text(encoding="utf-8")
        if source:
            parts.append((chapter.title, chapter_body(source)))
    for n, (name, body) in enumerate(parts):
        report(progress, n, len(parts), "Writing chapter")
        story = pymupdf.Story(html=body, user_css=PDF_CSS, archive=str(folder))
        toc.append([1, name, count + 1])
        more = True
        while more:
            device = writer.begin_page(page_rect)
            more, _filled = story.place(frame)
            story.draw(device)
            writer.end_page()
            count += 1
    writer.close()
    doc = pymupdf.open("pdf", buffer.getvalue())
    try:
        for number, page in enumerate(doc, start=1):
            if number > 1:
                page.insert_text((page_rect.width - 140, page_rect.height - 30),
                                 f"PlanetPDF Manual  ·  {number}", fontsize=8, color=(0.4, 0.4, 0.4))
        doc.set_toc(toc)
        doc.set_metadata({"title": "PlanetPDF Manual", "subject": f"PlanetPDF {version}".strip(),
                          "creator": "PlanetPDF"})
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.save(target, garbage=3, deflate=True)
    finally:
        doc.close()
    return target
