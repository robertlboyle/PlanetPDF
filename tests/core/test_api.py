"""The local API's rules (core/api.py): who's calling, signing approval, limits, the audit log."""

from __future__ import annotations

import json

import pytest

from planetpdf.core import api
from tests.fixtures.generate import make_text_pdf


def request(service, conn, method, rid=1, **params):
    sent = []
    service.handle_line(conn, json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}),
                        sent.append)
    replies = [m for m in sent if m.get("id") == rid and "method" not in m]
    return replies[-1] if replies else None, [m for m in sent if m.get("method") == "progress"]


@pytest.fixture
def service(tmp_path):
    return api.ApiService(tmp_path / "profile")


def hello(service, name="Test Program"):
    conn = api.Connection()
    reply, _ = request(service, conn, "planetpdf.hello", name=name, publisher="Tests")
    return conn, reply["result"]["token"]


def test_a_program_must_say_hello_first(service, tmp_path):
    reply, _ = request(service, api.Connection(), "info", file=str(make_text_pdf(tmp_path / "a.pdf")))
    assert reply["error"]["code"] == api.NOT_IDENTIFIED


def test_hello_gives_a_token_once_and_it_works_next_time(service, tmp_path):
    conn, token = hello(service)
    stored = (tmp_path / "profile" / "api" / "clients.json").read_text(encoding="utf-8")
    assert token not in stored  # (only its hash is kept)
    again = api.Connection()
    reply, _ = request(service, again, "planetpdf.hello", token=token)
    assert "token" not in reply["result"] and again.client.name == "Test Program"
    reply, _ = request(service, api.Connection(), "planetpdf.hello", token="made-up")
    assert reply["error"]["code"] == api.NOT_IDENTIFIED


def test_commands_run_and_report_progress(service, tmp_path):
    conn, _ = hello(service)
    pdf = make_text_pdf(tmp_path / "a.pdf")
    reply, progress = request(service, conn, "text", file=str(pdf))
    assert reply["result"]["text"].strip()
    assert progress and "Reading page 1 of" in progress[0]["params"]["text"]
    reply, _ = request(service, conn, "planetpdf.commands")
    assert any(c["name"] == "export" for c in reply["result"]["commands"])


def test_errors_say_what_went_wrong(service, tmp_path):
    conn, _ = hello(service)
    assert request(service, conn, "no-such-thing")[0]["error"]["code"] == api.NO_METHOD
    assert request(service, conn, "rotate", file="x.pdf")[0]["error"]["code"] == api.BAD_PARAMS
    reply, _ = request(service, conn, "info", file=str(tmp_path / "missing.pdf"))
    assert reply["error"]["code"] == api.FAILED and "missing.pdf" in reply["error"]["message"]
    sent = []
    service.handle_line(conn, b"not json", sent.append)
    assert sent[0]["error"]["code"] == api.PARSE_ERROR


def test_a_blocked_program_is_refused(service, tmp_path):
    conn, token = hello(service)
    service.clients.change(conn.client.id, blocked=True)
    assert request(service, conn, "planetpdf.commands")[0]["error"]["code"] == api.BLOCKED
    assert request(service, api.Connection(), "planetpdf.hello", token=token)[0]["error"]["code"] == api.BLOCKED


def test_requests_are_rate_limited(service):
    conn, _ = hello(service)
    conn.limit = api.RateLimit(rate=0.001, burst=3)
    codes = [request(service, conn, "planetpdf.commands", rid=n)[0].get("error", {}).get("code") for n in range(5)]
    assert codes[:3] == [None, None, None] and codes[3] == api.TOO_MANY


def sign_request(service, conn, tmp_path, rid=1):
    pdf = make_text_pdf(tmp_path / f"s{rid}.pdf")
    return request(service, conn, "sign", rid=rid, file=str(pdf), pfx=str(pdf), out=str(tmp_path / f"o{rid}.pdf"))[0]


def test_signing_asks_and_remembers_always(tmp_path):
    answers, asked = ["no", "always"], []

    def ask(client, description):
        asked.append(description)
        answer = answers.pop(0)
        return {"no": False, "always": "always"}[answer]

    service = api.ApiService(tmp_path / "profile", ask_to_sign=ask)
    conn, _ = hello(service)
    assert sign_request(service, conn, tmp_path, 1)["error"]["code"] == api.NOT_APPROVED
    reply = sign_request(service, conn, tmp_path, 2)  # approved ("always"); the made-up ID then fails
    assert reply["error"]["code"] == api.FAILED
    assert len(asked) == 2 and "Sign" in asked[0]
    assert service.clients.load()[0].can_sign
    sign_request(service, conn, tmp_path, 3)
    assert len(asked) == 2  # (not asked again)


def test_signing_without_anyone_to_ask_is_refused(service, tmp_path):
    conn, _ = hello(service)
    assert sign_request(service, conn, tmp_path)["error"]["code"] == api.NOT_APPROVED


def test_the_audit_log_names_files_never_passwords(service, tmp_path):
    conn, _ = hello(service)
    pdf = make_text_pdf(tmp_path / "a.pdf")
    request(service, conn, "protect", file=str(pdf), out=str(tmp_path / "b.pdf"), open_password="hunter2-secret")
    text = (tmp_path / "profile" / "logs" / "api-audit.log").read_text(encoding="utf-8")
    assert "hunter2-secret" not in text
    entry = [json.loads(line) for line in text.splitlines()][-1]
    assert entry["method"] == "protect" and entry["program"] == "Test Program (Tests)"
    assert str(pdf) in entry["files"] and entry["outcome"] == "ok"


def test_a_key_works_only_for_the_program_that_was_allowed(tmp_path):
    """Security review 2026-11-21: a new program is asked about by its real program file, and a
    key used by another program file is asked about again."""
    asked = []
    service = api.ApiService(tmp_path / "profile",
                             ask_to_connect=lambda name, publisher, program, before: asked.append(
                                 (name, program, before)) or program.endswith("good.exe"))
    first = api.Connection(program=r"C:\Tools\good.exe")
    reply, _ = request(service, first, "planetpdf.hello", name="Good")
    token = reply["result"]["token"]
    assert asked == [("Good", r"C:\Tools\good.exe", "")]
    again = api.Connection(program=r"C:\Tools\good.exe")
    assert "result" in request(service, again, "planetpdf.hello", token=token)[0]
    assert len(asked) == 1  # (the same program: not asked again)
    thief = api.Connection(program=r"C:\Temp\evil.exe")
    reply, _ = request(service, thief, "planetpdf.hello", token=token)
    assert reply["error"]["code"] == api.DENIED and asked[-1] == ("Good", r"C:\Temp\evil.exe", r"C:\Tools\good.exe")
    reply, _ = request(service, api.Connection(program=r"C:\Temp\evil.exe"), "planetpdf.hello", name="Adobe")
    assert reply["error"]["code"] == api.DENIED and len(asked) == 2  # (refused once: not asked again)


def test_a_program_cant_add_itself_by_writing_the_list(tmp_path):
    """Security review 2026-11-25: clients.json was a plain file a program running as the user
    could write itself into. It's sealed with a key the operating system keeps for the user."""
    import json

    from planetpdf.core.api import ClientStore

    store = ClientStore(tmp_path / "api")
    client, token = store.register("Good Program", "Planet")
    assert store.by_token(token) is not None
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["seal"]
    forged = dict(data["clients"][0], id="evil", name="Evil", token_hash="0" * 64, can_sign=True)
    data["clients"].append(forged)
    store.path.write_text(json.dumps(data), encoding="utf-8")
    assert ClientStore(tmp_path / "api").load() == []  # (changed outside PlanetPDF: ignored)
    store.path.write_text(json.dumps({"clients": [forged]}), encoding="utf-8")
    assert ClientStore(tmp_path / "api").load() == []  # (no seal at all)


def test_the_api_is_off_until_turned_on():
    from planetpdf.core.storage.settings import Settings

    assert Settings().api_enabled is False



def test_older_settings_turn_the_api_off_once_unless_plugins_use_it(tmp_path):
    import json

    from planetpdf.core.storage.settings import SETTINGS_FILENAME, load_settings, save_settings

    (tmp_path / SETTINGS_FILENAME).write_text(json.dumps({"api_enabled": True}), encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.api_enabled is False  # (the old default, not a choice)
    settings.api_enabled = True  # (turned on by the user since)
    save_settings(settings, tmp_path)
    assert load_settings(tmp_path).api_enabled is True
    (tmp_path / SETTINGS_FILENAME).write_text(json.dumps({"api_enabled": True, "plugins_enabled": {"x": "y"}}),
                                              encoding="utf-8")
    assert load_settings(tmp_path).api_enabled is True  # (plugins on: they need it)
