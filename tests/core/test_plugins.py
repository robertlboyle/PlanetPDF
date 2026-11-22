"""Plugins' manifests, fingerprints and turning on (core/plugins.py)."""

from __future__ import annotations

import json

from planetpdf.core import plugins


def make_plugin(root, folder="stamper", **manifest):
    path = root / "plugins" / folder
    path.mkdir(parents=True)
    data = {"id": "com.example.stamper", "name": "Stamper", "version": "1.0", "publisher": "Example",
            "run": ["python", "main.py"], "permissions": ["read"],
            "menu_items": [{"menu": "Tools", "title": "Stamp It", "command": "stamp", "needs_document": True}]}
    data.update(manifest)
    (path / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return path


def test_a_good_manifest(tmp_path):
    make_plugin(tmp_path)
    [plugin] = plugins.discover(tmp_path)
    assert not plugin.problem
    assert plugin.id == "com.example.stamper" and plugin.permissions == ["read"]
    assert plugin.menu_items == [plugins.MenuItem("Tools", "Stamp It", "stamp", True)]


def test_bad_manifests_say_why(tmp_path):
    make_plugin(tmp_path, "a", id="bad id!", run="notalist", permissions=["read", "everything"],
                menu_items=[{"menu": "Nowhere", "title": "x", "command": "y"}])
    [plugin] = plugins.discover(tmp_path)
    for words in ('"id"', '"run"', "everything", "Nowhere"):
        assert words in plugin.problem
    folder = tmp_path / "plugins" / "broken"
    folder.mkdir()
    (folder / "plugin.json").write_text("{not json", encoding="utf-8")
    assert any("can't be read" in p.problem for p in plugins.discover(tmp_path))


def test_on_only_while_unchanged(tmp_path):
    folder = make_plugin(tmp_path)
    [plugin] = plugins.discover(tmp_path)
    enabled = {plugin.id: plugin.fingerprint()}
    assert plugins.is_enabled(plugin, enabled)
    (folder / "main.py").write_text("print('something else')\n", encoding="utf-8")
    assert not plugins.is_enabled(plugin, enabled)
    assert not plugins.is_enabled(plugin, {})


def test_shared_plugin_folders_from_the_environment(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    make_plugin(shared, id="org.it.tool")
    monkeypatch.setenv(plugins.ENV_FOLDERS, str(shared / "plugins"))
    assert [p.id for p in plugins.discover(tmp_path / "profile")] == ["org.it.tool"]


def test_the_program_is_found_in_its_folder_first(tmp_path):
    folder = make_plugin(tmp_path, run=["tool.cmd", "--fast"])
    (folder / "tool.cmd").write_text("", encoding="utf-8")
    [plugin] = plugins.discover(tmp_path)
    assert plugins.command_line(plugin) == [str(folder / "tool.cmd"), "--fast"]
