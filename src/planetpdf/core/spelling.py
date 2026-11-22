"""Edit ▸ Check Spelling (Acrobat; backlog Phase D, built 2026-10-02): misspelled words in
comments, form fields and the page text, with suggestions, and the user's own dictionary
(Edit Dictionary). English, from pyspellchecker's built-in word list.

Words that are probably not mistakes are skipped: ALL-CAPS (acronyms), words with digits,
web and e-mail addresses, and anything in the user's dictionary.

**Word lists** (user request 2026-10-02: medical, legal, technical… terms and proper nouns
shouldn't show as misspelled): bundled ones in ``resources/dictionaries/*.txt`` plus lists
the user imports (``<profile>/spelling/dictionaries``); each can be switched off. A list
keeps every word in its proper capitalization, and that is how a word is matched (user:
"look for capitalization to see if that is the intent"):

* an all-lowercase entry (``metformin``) accepts any capitalization;
* an entry with capitals inside (``JavaScript``, ``iPhone``) is the only right way to
  write it: ``Javascript`` / ``javascript`` are flagged with ``JavaScript`` suggested;
* a plain capitalized name (``Nashville``, ``Python``) is accepted as written; its
  lowercase form is left to the general dictionary ("python" the snake, "rust").

Plurals (``-s`` / ``-es``) and possessives of list words are accepted too.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from planetpdf.core.document import PdfSession
from planetpdf.core.resources import resources_dir

WORD = re.compile(r"[A-Za-z][A-Za-z']*[A-Za-z]|[A-Za-z]")
SKIP = re.compile(r"(https?://|www\.)\S+|\S+@\S+\.\S+")


@dataclass(frozen=True)
class WordList:
    key: str  # file stem; "user:<stem>" for imported lists
    name: str
    about: str
    path: Path
    builtin: bool


def builtin_dir() -> Path:
    return resources_dir() / "dictionaries"


def _header(path: Path) -> tuple[str, str]:
    name, about = path.stem.replace("_", " ").title(), ""
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            if not line.startswith("#"):
                break
            key, _, value = line[1:].partition(":")
            if key.strip().lower() == "name":
                name = value.strip()
            elif key.strip().lower() == "about":
                about = value.strip()
    return name, about


def word_lists(user_folder: Path | None = None) -> list[WordList]:
    """Bundled word lists (alphabetical by name), then the user's imported ones."""
    out = []
    for path in sorted(builtin_dir().glob("*.txt")):
        name, about = _header(path)
        out.append(WordList(path.stem, name, about, path, True))
    out.sort(key=lambda w: w.name.lower())
    if user_folder is not None and user_folder.is_dir():
        for path in sorted(user_folder.glob("*.txt")):
            name, about = _header(path)
            out.append(WordList("user:" + path.stem, name, about or "Imported word list", path, False))
    return out


def read_words(path: Path) -> list[str]:
    """The words of a list: plain text (words separated by spaces or lines, ``#``
    comments) or a Hunspell ``.dic`` (a count on the first line, ``word/FLAGS``)."""
    words = []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if lines and lines[0].strip().isdigit():
        lines = lines[1:]  # Hunspell: the number of words
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        for token in line.split():
            words.extend(WORD.findall(token.split("/")[0]))
    return words


def import_word_list(source: Path, folder: Path, name: str | None = None) -> WordList:
    """Copy a word list into the user's folder (as a plain-text list with a name header)."""
    words = sorted(set(read_words(source)), key=str.lower)
    if not words:
        raise ValueError(f"No words were found in {source.name}.")
    name = (name or source.stem.replace("_", " ")).strip() or "Word List"
    stem = re.sub(r"[^A-Za-z0-9-]+", "_", name).strip("_").lower() or "words"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stem}.txt"
    n = 2
    while target.exists():
        target, n = folder / f"{stem}_{n}.txt", n + 1
    target.write_text(f"# name: {name}\n# about: Imported from {source.name} ({len(words)} words)\n"
                      + "\n".join(words) + "\n", encoding="utf-8")
    return WordList("user:" + target.stem, name, f"Imported from {source.name}", target, False)


def remove_word_list(word_list: WordList) -> None:
    if word_list.builtin:
        raise ValueError("Bundled word lists can be switched off, not removed.")
    word_list.path.unlink(missing_ok=True)


def _camel(form: str) -> bool:
    """Mixed case with capitals after the first letter (JavaScript, iPhone, McDonald):
    the capitals are part of the name. Not acronyms (BASIC, MAUI): "Basic" stays fine."""
    return any(c.isupper() for c in form[1:]) and any(c.islower() for c in form)


@dataclass(frozen=True)
class Issue:
    where: str  # "comment", "field", "page" (editable page text) or "ocr" (invisible text over a scan)
    page: int
    target: str  # comment: the item id; field: the field name; page text: ""
    word: str
    start: int  # offset of the word in ``text``
    text: str  # the comment / field value / page line it's in
    rect: tuple[float, float, float, float] | None = None  # page text: where the word is (unrotated)
    line: object = None  # page text: the content_edit.TextLine to rewrite

    @property
    def changeable(self) -> bool:
        """Everything can be changed. OCR text (invisible, over a picture of the page) is
        rewritten in its layer (``ocr_correct``): search and copy then find the right word,
        though the picture stays as it is (2026-10-03, with Correct Recognized Text)."""
        return True

    @property
    def context(self) -> str:
        a, b = max(0, self.start - 30), self.start + len(self.word) + 30
        return ("…" if a else "") + self.text[a:b].replace("\n", " ") + ("…" if b < len(self.text) else "")


class Speller:
    def __init__(self, user_words: Path | None = None, *, user_lists: Path | None = None,
                 off: Iterable[str] = ()) -> None:
        from spellchecker import SpellChecker

        self._checker = SpellChecker(language="en", case_sensitive=False)
        self.user_words_path = Path(user_words) if user_words else None
        self._user: set[str] = set()
        if self.user_words_path and self.user_words_path.is_file():
            self._user = {w.strip().lower() for w in self.user_words_path.read_text(encoding="utf-8").splitlines() if w.strip()}
        self._ignored: set[str] = set()  # Ignore All (this session)
        self.user_lists = user_lists
        self._loaded: dict[str, list[str]] = {}  # word list key -> its words
        self._forms: dict[str, set[str]] = {}  # lowercase -> the ways the switched-on lists write it
        self.off: set[str] = set()
        self.set_off(off)

    # --- word lists -------------------------------------------------------------------------------

    def word_lists(self) -> list[WordList]:
        return word_lists(self.user_lists)

    def set_off(self, keys: Iterable[str]) -> None:
        """Switch these word lists off (and every other one on)."""
        self.off = set(keys)
        self._forms = {}
        for wl in self.word_lists():
            if wl.key in self.off:
                continue
            if wl.key not in self._loaded:
                self._loaded[wl.key] = read_words(wl.path)
            for word in self._loaded[wl.key]:
                self._forms.setdefault(word.lower(), set()).add(word)

    def reload_lists(self) -> None:
        """After importing / removing a list."""
        self._loaded.clear()
        self.set_off(self.off)

    def _list_forms(self, low: str) -> set[str] | None:
        """How the lists write this word, also for a plural (-s / -es) of a list word."""
        for stem, cut in ((low, 0), (low[:-1], 1), (low[:-2], 2)):
            if cut == 1 and not low.endswith("s") or cut == 2 and not low.endswith("es"):
                continue
            forms = self._forms.get(stem)
            if forms:
                return {f + low[len(stem):] for f in forms} if cut else forms
        return None

    # --- the user's dictionary ---------------------------------------------------------------

    @property
    def user_words(self) -> list[str]:
        return sorted(self._user)

    def add_word(self, word: str) -> None:
        self._user.add(word.strip().lower())
        self._save()

    def remove_word(self, word: str) -> None:
        self._user.discard(word.strip().lower())
        self._save()

    def _save(self) -> None:
        if self.user_words_path is not None:
            self.user_words_path.parent.mkdir(parents=True, exist_ok=True)
            self.user_words_path.write_text("\n".join(sorted(self._user)) + "\n", encoding="utf-8")

    def ignore_all(self, word: str) -> None:
        self._ignored.add(word.lower())

    # --- checking -------------------------------------------------------------------------------

    def is_wrong(self, word: str) -> bool:
        word = word.strip("'")
        if word.lower().endswith("'s"):
            word = word[:-2]
        low = word.lower()
        if len(low) < 2 or word.isupper() or low in self._user or low in self._ignored:
            return False
        if len(word) >= 3 and word.endswith("s") and word[:-1].isupper():
            return False  # an acronym's plural (PDFs, APIs, ISPs)
        forms = self._list_forms(low)
        if forms:
            if word in forms or low in forms:  # as the list writes it, or a lowercase list word
                return False
            if word[:1].isupper() and word[1:].islower() and word.lower() in {f.lower() for f in forms if not _camel(f)}:
                return False  # (a capitalized name at the start of a sentence, …)
            if any(_camel(f) for f in forms):
                return True  # JavaScript written "Javascript": the capitals are part of the name
        return low not in self._checker

    def proper_forms(self, word: str) -> list[str]:
        """The right capitalization(s) of a list word written with the wrong one."""
        forms = self._list_forms(word.lower().strip("'")) or set()
        return sorted(f for f in forms if f != word and f != f.lower())

    def misspelled(self, text: str) -> list[tuple[str, int]]:
        """(word, offset) of each word that looks misspelled."""
        skipped = [m.span() for m in SKIP.finditer(text)]
        out = []
        for m in WORD.finditer(text):
            if any(a <= m.start() < b for a, b in skipped):
                continue
            if m.end() < len(text) and text[m.end()].isdigit() or m.start() and text[m.start() - 1].isdigit():
                continue
            if self.is_wrong(m.group()):
                out.append((m.group(), m.start()))
        return out

    def suggestions(self, word: str, limit: int = 6) -> list[str]:
        out = self.proper_forms(word)  # only the capitals were wrong
        if out:
            return out[:limit]
        low = word.lower()
        candidates = self._checker.candidates(low) if len(low) < 25 else None
        ranked = sorted(candidates or set(), key=lambda w: -self._checker.word_usage_frequency(w))
        ranked = [w for w in ranked if w != low][:limit]
        # close words from the switched-on lists (Kubernetis -> Kubernetes)
        listed = difflib.get_close_matches(low, self._forms.keys(), n=limit, cutoff=0.82)
        seen, result = set(), []
        for cand in listed[:2] + ranked + listed[2:]:
            forms = self._forms.get(cand)
            if forms:
                cand = next((f for f in sorted(forms) if _camel(f)), None) or next(iter(sorted(forms)))
            if cand[:1].islower() and word[:1].isupper() and not _camel(cand):
                cand = cand[:1].upper() + cand[1:]
            if cand.lower() not in seen:
                seen.add(cand.lower())
                result.append(cand)
        return result[:limit]


def find_issues(session: PdfSession, speller: Speller, *, page_text: bool = False) -> list[Issue]:
    """Every misspelled word in the comments, the form fields' values and (``page_text``)
    the pages' own text, page by page. Page text comes as Edit PDF's editable lines
    (``where="page"``, can be rewritten) plus invisible OCR text (``where="ocr"``)."""
    from planetpdf.core import content_edit as E
    from planetpdf.core import markup as K

    issues: list[Issue] = []
    doc = session.doc
    for index in range(doc.page_count):
        for item in K.list_markup(session, [index]):
            text = item.spec.text if item.spec is not None else item.text
            if not text or (item.kind is not None and item.kind.uses_appearance):
                continue
            for word, start in speller.misspelled(text):
                issues.append(Issue("comment", index, item.item_id, word, start, text))
        page = doc[index]
        for widget in page.widgets():
            if widget.field_type != 7 or not isinstance(widget.field_value, str):  # (7 = text field)
                continue
            for word, start in speller.misspelled(widget.field_value):
                issues.append(Issue("field", index, widget.field_name, word, start, widget.field_value))
        if page_text:
            for line in E.text_lines(session, index):
                for word, start in speller.misspelled(line.text):
                    hits = page.search_for(word, clip=line.rect)
                    issues.append(Issue("page", index, "", word, start, line.text,
                                        tuple(hits[0]) if hits else tuple(line.rect), line))
                page = doc[index]
            for block in page.get_text("dict")["blocks"]:
                for raw in block.get("lines", []):
                    spans = [s for s in raw["spans"] if s["text"].strip()]
                    if not spans or any(s.get("alpha", 255) for s in spans):
                        continue  # (visible text: an editable line above)
                    text = "".join(s["text"] for s in raw["spans"])
                    for word, start in speller.misspelled(text):
                        hits = page.search_for(word, clip=raw["bbox"])
                        issues.append(Issue("ocr", index, "", word, start, text, tuple(hits[0]) if hits else tuple(raw["bbox"])))
    return issues


def replace_word(text: str, issue: Issue, new: str) -> str:
    """``text`` with the issue's word replaced (at its offset)."""
    if text[issue.start:issue.start + len(issue.word)] != issue.word:
        return text.replace(issue.word, new, 1)
    return text[:issue.start] + new + text[issue.start + len(issue.word):]
