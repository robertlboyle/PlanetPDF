from __future__ import annotations

import json

from planetpdf.core.storage.settings import (
    MAX_RECENT_FILES,
    SETTINGS_FILENAME,
    Settings,
    load_settings,
    save_settings,
)


def test_defaults_when_missing(tmp_path):
    assert load_settings(tmp_path) == Settings()


def test_round_trip(tmp_path):
    s = Settings(user_name="Robert", timestamp_url="https://tsa.example", show_thumbnails=False)
    s.add_recent_file(tmp_path / "a.pdf")
    save_settings(s, tmp_path)
    loaded = load_settings(tmp_path)
    assert loaded.user_name == "Robert"
    assert loaded.timestamp_url == "https://tsa.example"
    assert loaded.show_thumbnails is False
    assert loaded.recent_files == [str(tmp_path / "a.pdf")]


def test_creates_missing_directory(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    save_settings(Settings(user_name="x"), target)
    assert load_settings(target).user_name == "x"


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / SETTINGS_FILENAME).write_text("{not json", encoding="utf-8")
    assert load_settings(tmp_path) == Settings()


def test_wrong_types_are_ignored(tmp_path):
    (tmp_path / SETTINGS_FILENAME).write_text(
        json.dumps({"user_name": 5, "show_thumbnails": "yes", "recent_files": ["ok", 3]}),
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.user_name == ""
    assert s.show_thumbnails is True
    assert s.recent_files == ["ok"]


def test_unknown_keys_survive_a_save(tmp_path):
    (tmp_path / SETTINGS_FILENAME).write_text(
        json.dumps({"user_name": "a", "future_option": {"x": 1}}), encoding="utf-8"
    )
    s = load_settings(tmp_path)
    s.user_name = "b"
    save_settings(s, tmp_path)
    raw = json.loads((tmp_path / SETTINGS_FILENAME).read_text(encoding="utf-8"))
    assert raw["future_option"] == {"x": 1}
    assert raw["user_name"] == "b"


def test_recent_files_dedup_and_cap(tmp_path):
    s = Settings()
    for i in range(MAX_RECENT_FILES + 5):
        s.add_recent_file(tmp_path / f"{i}.pdf")
    s.add_recent_file(tmp_path / "3.pdf")
    assert len(s.recent_files) == MAX_RECENT_FILES
    assert s.recent_files[0] == str(tmp_path / "3.pdf")
    assert s.recent_files.count(str(tmp_path / "3.pdf")) == 1
