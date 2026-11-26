"""Remembered text for Add Text's suggestions (user 2026-11-16)."""

from __future__ import annotations

from planetpdf.core.storage import text_history as TH
from planetpdf.core.storage.profile import Profile


def test_remembers_newest_first_and_suggests_by_start(tmp_path):
    path = Profile(tmp_path).text_history_path
    history = TH.TextHistory.load(path)  # (no file yet: empty)
    for text in ("John Smith", "Jane Doe", "  123 Main Street  ", "John Smith"):
        history.remember(text)
    assert history.entries == ["John Smith", "123 Main Street", "Jane Doe"]
    assert history.suggestions("j") == ["John Smith", "Jane Doe"]  # (any case, most recent first)
    assert history.suggestions("JOHN") == ["John Smith"]
    assert history.suggestions("John Smith") == []  # (nothing left to add)
    assert history.suggestions("") == [] and history.suggestions("x") == []

    again = TH.TextHistory.load(path)  # kept in the profile
    assert again.entries == history.entries
    again.forget("Jane Doe")
    assert TH.TextHistory.load(path).entries == ["John Smith", "123 Main Street"]
    again.clear()
    assert TH.TextHistory.load(path).entries == []


def test_skips_empty_and_very_long_text_and_keeps_a_limit(tmp_path, monkeypatch):
    history = TH.TextHistory(None)
    assert not history.remember("   ")
    assert not history.remember("x" * (TH.MAX_LENGTH + 1))
    monkeypatch.setattr(TH, "MAX_ENTRIES", 3)
    for n in range(5):
        history.remember(f"entry {n}")
    assert history.entries == ["entry 4", "entry 3", "entry 2"]


def test_a_damaged_file_starts_an_empty_list(tmp_path):
    path = tmp_path / TH.FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert TH.TextHistory.load(path).entries == []



def test_private_numbers_are_never_remembered(tmp_path):
    """Security review 2026-11-25: typed text was kept as it was, numbers and all."""
    import json

    from planetpdf.core.storage.text_history import TextHistory

    history = TextHistory(tmp_path / "text-history.json")
    for text in ("SSN 123-45-6789", "4111 1111 1111 1111", "Account 0012345678901"):
        assert not history.remember(text)
    for text in ("Robert Boyle", "Call 555-867-5309", "11/26/2026", "Invoice 1234"):
        assert history.remember(text)
    saved = json.loads((tmp_path / "text-history.json").read_text(encoding="utf-8"))["entries"]
    assert sorted(saved) == sorted(["Robert Boyle", "Call 555-867-5309", "11/26/2026", "Invoice 1234"])
    (tmp_path / "text-history.json").write_text(json.dumps({"version": 1, "entries": ["123-45-6789", "Kept"]}),
                                                encoding="utf-8")
    assert TextHistory.load(tmp_path / "text-history.json").entries == ["Kept"]  # (an older file's, dropped)


def test_private_numbers_are_remembered_only_when_the_user_says_so(tmp_path):
    """User 2026-11-26: "allow it to be configured to store cc numbers, ssn, etc. as an option. By
    default, that can be off"."""
    from planetpdf.core.storage.text_history import TextHistory

    path = tmp_path / "text-history.json"
    history = TextHistory(path)
    assert history.remember("SSN 123-45-6789", allow_private=True)
    assert history.remember("Robert Boyle")
    assert TextHistory.load(path, keep_private=True).entries == ["Robert Boyle", "SSN 123-45-6789"]
    assert TextHistory.load(path).entries == ["Robert Boyle"]  # (the option off: not offered)
    assert history.forget_private() == 1 and history.entries == ["Robert Boyle"]
