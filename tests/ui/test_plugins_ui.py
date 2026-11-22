"""A plugin end to end: turned on, its menu item in the window, started with its own token, and
held to the permissions it asked for when it calls back through the local API."""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from planetpdf.core import plugins
from planetpdf.core.resources import resources_dir
from planetpdf.ui import api_server
from planetpdf.ui.main_window import MainWindow
from tests.fixtures.generate import make_text_pdf

PLUGIN = '''
import json, os, sys
from planetpdf_client import PlanetPDF, PlanetPDFError
out = {}
with PlanetPDF.from_environment() as pdf:
    out["command"] = os.environ["PLANETPDF_COMMAND"]
    out["info"] = pdf.call("info", file=os.environ["PLANETPDF_DOCUMENT"])
    try:
        pdf.call("rotate", file=os.environ["PLANETPDF_DOCUMENT"], out="x.pdf", degrees="90")
    except PlanetPDFError as e:
        out["rotate_refused"] = e.code
with open("result.json", "w") as f:
    json.dump(out, f)
'''


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "server_name", lambda: f"PlanetPDF-API-test-{os.getpid()}-{time.time_ns()}")
    monkeypatch.setattr(api_server, "info_path", lambda: tmp_path / "api.json")
    w = MainWindow(tmp_path / "profile")
    monkeypatch.setattr(w, "save_settings_now", lambda: None)
    server = api_server.attach(w, w.profile.root)
    w.show()
    QCoreApplication.processEvents()
    yield w
    server.close()
    QApplication.instance().planetpdf_api = None
    w.undo_stack.setClean()
    w.close()


def make_plugin(win):
    folder = win.profile.root / "plugins" / "reader"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({
        "id": "com.example.reader", "name": "Reader", "publisher": "Tests", "run": ["python", "main.py"],
        "permissions": ["read"],
        "menu_items": [{"menu": "Tools", "title": "Read It", "command": "read-it", "needs_document": True}]}),
        encoding="utf-8")
    (folder / "main.py").write_text(PLUGIN, encoding="utf-8")
    shutil.copy(resources_dir() / "sdk" / "planetpdf_client.py", folder)
    return folder


def test_a_plugin_runs_from_its_menu_item_within_its_permissions(win, tmp_path):
    folder = make_plugin(win)
    win.plugin_menus.install()
    assert not win.plugin_menus.actions()  # (not turned on yet)
    [plugin] = plugins.discover(win.profile.root)
    win.settings.plugins_enabled = {plugin.id: plugin.fingerprint()}
    win.plugin_menus.install()
    [action] = win.plugin_menus.actions()
    assert action.text() == "Read It"
    win.open_file(make_text_pdf(tmp_path / "doc.pdf"))
    action.trigger()
    result = folder / "result.json"
    end = time.monotonic() + 60
    while not result.exists() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    log = win.profile.root / "logs" / "plugins" / "com.example.reader.log"
    assert result.exists(), log.read_text(errors="replace") if log.exists() else "no log"
    out = json.loads(result.read_text(encoding="utf-8"))
    assert out["command"] == "read-it" and out["info"]["pages"] >= 1
    assert out["rotate_refused"] == -32004  # (it only asked to read)


def test_a_changed_plugin_is_off_until_turned_on_again(win):
    folder = make_plugin(win)
    [plugin] = plugins.discover(win.profile.root)
    win.settings.plugins_enabled = {plugin.id: plugin.fingerprint()}
    win.plugin_menus.install()
    assert win.plugin_menus.actions()
    (folder / "main.py").write_text("print('changed')\n", encoding="utf-8")
    win.plugin_menus.install()
    assert not win.plugin_menus.actions()


def test_preferences_list_programs_and_plugins(win):
    from planetpdf.ui.dialogs.scripting_page import ScriptingPage

    make_plugin(win)
    store = QApplication.instance().planetpdf_api.service.clients
    client, _token = store.register("Invoice Mailer", "Accounting")
    store.change(client.id, can_sign=True)
    page = ScriptingPage(win)
    assert page.programs.topLevelItemCount() == 1 and page.plugin_list.topLevelItemCount() == 1
    page.programs.setCurrentItem(page.programs.topLevelItem(0))
    page._stop_signing()
    assert not store.load()[0].can_sign
    page.programs.setCurrentItem(page.programs.topLevelItem(0))
    page._toggle_block()
    assert store.load()[0].blocked
