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
