"""The local API over a real pipe / socket, with the Python client PlanetPDF ships (resources/sdk)."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from planetpdf.core.resources import resources_dir
from planetpdf.ui import api_server
from tests.fixtures.generate import make_text_pdf


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def client_module():
    spec = importlib.util.spec_from_file_location("planetpdf_client", resources_dir() / "sdk" / "planetpdf_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "server_name", lambda: f"PlanetPDF-API-test-{os.getpid()}-{time.time_ns()}")
    monkeypatch.setattr(api_server, "info_path", lambda: tmp_path / "api.json")
    from planetpdf.ui import approval

    asked = []
    monkeypatch.setattr(approval, "ask_to_connect", lambda *args: asked.append(args) or True)
    s = api_server.ApiServer(tmp_path / "profile")
    s.asked = asked
    assert s.listening
    yield s
    s.close()


def in_thread(fn, timeout=60):
    """Run a blocking client in a thread while Qt serves it here."""
    box = {}

    def go():
        try:
            box["result"] = fn()
        except BaseException as e:
            box["error"] = e

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    end = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    assert not thread.is_alive(), "the client didn't finish"
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_api_json_says_where_it_is(server, tmp_path):
    info = json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))
    assert info["address"] == server.address and info["protocol"] == "jsonrpc-lines"
    server.close()
    assert not (tmp_path / "api.json").exists()


def test_the_python_client_runs_commands_with_progress(server, client_module, tmp_path):
    pdf = make_text_pdf(tmp_path / "a.pdf")
    token_file = tmp_path / "token.txt"
    progress = []

    def work():
        with client_module.PlanetPDF("Test Client", publisher="Tests", token_file=token_file,
                                     address=server.address, key=server.key) as pdf_api:
            info = pdf_api.call("info", file=str(pdf))
            out = pdf_api.call("rotate", file=str(pdf), out=str(tmp_path / "r.pdf"), degrees="90")
            text = pdf_api.call("text", file=str(pdf), on_progress=progress.append)
            return info, out, text

    info, out, text = in_thread(work)
    assert info["pages"] >= 1 and (tmp_path / "r.pdf").is_file() and text["text"].strip()
    assert progress and progress[0]["text"].startswith("Reading page 1 of")
    assert token_file.read_text(encoding="utf-8").strip()

    def again():  # (the saved token: the same program, no new record)
        with client_module.PlanetPDF("Test Client", token_file=token_file, address=server.address, key=server.key) as pdf_api:
            return pdf_api.call("planetpdf.commands")

    assert in_thread(again)["commands"]
    assert len(server.service.clients.load()) == 1


def test_errors_reach_the_client(server, client_module, tmp_path):
    def work():
        with client_module.PlanetPDF("Test Client", address=server.address, key=server.key) as pdf_api:
            try:
                pdf_api.call("info", file=str(tmp_path / "missing.pdf"))
            except client_module.PlanetPDFError as e:
                return e.code

    assert in_thread(work) == -32000


def test_signing_is_asked_on_the_gui_thread(server, client_module, tmp_path, monkeypatch):
    asked = []

    def ask(who, description, remember=False):
        asked.append((threading.current_thread() is threading.main_thread(), who, remember))
        return False

    monkeypatch.setattr("planetpdf.ui.approval.ask_to_sign", ask)
    pdf = make_text_pdf(tmp_path / "a.pdf")

    def work():
        with client_module.PlanetPDF("Signer", address=server.address, key=server.key) as pdf_api:
            try:
                pdf_api.call("sign", file=str(pdf), pfx=str(pdf), out=str(tmp_path / "s.pdf"))
            except client_module.PlanetPDFError as e:
                return e.code

    assert in_thread(work) == -32002
    assert len(asked) == 1 and asked[0][0] and asked[0][2]
    assert asked[0][1].startswith("The program “Signer” (") and "python" in asked[0][1].lower()  # (its real program file)


def test_a_new_program_is_asked_about_by_its_real_program_file(server, client_module, tmp_path, monkeypatch):
    """Security review 2026-11-21: the program file comes from the operating system."""
    import sys

    from planetpdf.core import api
    from planetpdf.ui import approval

    def work():
        with client_module.PlanetPDF("Adobe Acrobat", publisher="Adobe", token_file=tmp_path / "t.txt",
                                     address=server.address, key=server.key) as pdf_api:
            return pdf_api.call("planetpdf.commands")

    in_thread(work)
    name, publisher, program, before = server.asked[0]
    assert name == "Adobe Acrobat" and before == ""
    assert api.same_program(program, sys._base_executable) or api.same_program(program, sys.executable)  # (not what it said)
    # refused: the program gets an error, and isn't asked about again while this runs
    monkeypatch.setattr(approval, "ask_to_connect", lambda *args: server.asked.append(args) or False)

    def refused():
        with client_module.PlanetPDF("Other", token_file=tmp_path / "u.txt", address=server.address, key=server.key) as pdf_api:
            return pdf_api.call("planetpdf.commands")

    with pytest.raises(Exception, match="didn't allow"):
        in_thread(refused)
    with pytest.raises(Exception, match="didn't allow"):
        in_thread(refused)
    assert len(server.asked) == 2


# --- nobody else can stand in for the API (security list 2026-11-26, item 2) ------------------------


def test_the_api_listens_on_a_random_name_and_says_so_privately(server, tmp_path):
    import getpass
    import sys

    info = json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))
    assert info["key"] == server.key and len(server.key) == 64
    assert getpass.getuser().lower() not in server.address.lower()
    # one API per user: another process finds this one (it proves it holds api.json's key) and doesn't listen
    import subprocess
    from pathlib import Path

    import planetpdf

    code = ("from pathlib import Path; from PySide6.QtWidgets import QApplication; app = QApplication([]); "
            "from planetpdf.ui import api_server; "
            f"api_server.info_path = lambda: Path({str(tmp_path / 'api.json')!r}); "
            f"print(api_server.ApiServer(Path({str(tmp_path / 'profile2')!r})).listening)")
    env = dict(os.environ, PYTHONPATH=str(Path(planetpdf.__file__).parents[1]), QT_QPA_PLATFORM="offscreen")
    other = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    end = time.monotonic() + 60
    while other.poll() is None and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    assert other.stdout.read().strip() == "False"
    assert json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))["key"] == server.key  # (left alone)
    if sys.platform != "win32":
        assert (tmp_path / "api.json").stat().st_mode & 0o077 == 0


def test_the_client_has_the_api_prove_itself_first(server, client_module, tmp_path, monkeypatch):
    monkeypatch.setattr(client_module, "default_server", lambda: (server.address, server.key))

    def work():
        with client_module.PlanetPDF("Test Client", token_file=tmp_path / "t.txt") as pdf_api:
            return pdf_api.call("planetpdf.commands")

    assert in_thread(work)["commands"]


def test_the_client_says_nothing_to_what_cannot_prove_it_is_planetpdf(server, client_module, tmp_path, monkeypatch):
    """An old address someone else took (here: the real API, but api.json's key is another run's)."""
    token_file = tmp_path / "t.txt"
    token_file.write_text("the-programs-secret-token", encoding="utf-8")
    monkeypatch.setattr(client_module, "default_server", lambda: (server.address, "0" * 64))
    heard = []
    real = server.service.handle

    def handle(conn, message, send):
        heard.append(message.get("method"))
        return real(conn, message, send)

    monkeypatch.setattr(server.service, "handle", handle)

    def work():
        try:
            client_module.PlanetPDF("Test Client", token_file=token_file)
        except client_module.PlanetPDFError as e:
            return e.code

    assert in_thread(work) == -32097
    assert heard == ["planetpdf.prove"]  # (no hello, no token, no file names)


def test_a_plugin_is_given_the_key(server, tmp_path, monkeypatch):
    import sys

    from planetpdf.core import plugins

    folder = tmp_path / "plug"
    folder.mkdir()
    (folder / "show.py").write_text("import os, pathlib; pathlib.Path('key.txt').write_text("
                                    "os.environ.get('PLANETPDF_API_KEY', ''))", encoding="utf-8")
    plugin = plugins.Plugin(id="t", name="T", version="1", publisher="", run=[sys.executable, "show.py"],
                            permissions=[], menu_items=[], folder=folder)
    item = plugins.MenuItem(menu="Tools", title="Show", command="show")
    monkeypatch.setattr(plugins, "command_line", lambda p: [sys.executable, "show.py"])
    process = plugins.start(plugin, item, address=server.address, token="t", key=server.key,
                            log_folder=tmp_path / "logs")
    assert process.wait(30) == 0
    assert (folder / "key.txt").read_text(encoding="utf-8") == server.key


def test_the_client_sends_nothing_without_a_key(server, client_module, tmp_path):
    """Security review 2026-11-27: without a key the client skipped the proof and sent its token to
    whatever answered at the address."""
    def work():
        with client_module.PlanetPDF("Test Client", token="made-up", address=server.address, key=""):
            pass

    with pytest.raises(client_module.PlanetPDFError) as error:
        in_thread(work)
    assert "no key" in str(error.value)
