"""Settings an administrator makes for everyone on a computer (security list 2026-11-26, item 5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from planetpdf.core import policy as P
from planetpdf.core import update as U
from planetpdf.core.document import PdfError
from planetpdf.core.storage.settings import Settings, load_settings, save_settings


@pytest.fixture(autouse=True)
def no_policy_left_behind():
    P.reset(P.Policy())
    yield
    P.reset(None)


def _file(tmp_path: Path, data) -> Path:
    path = tmp_path / "install" / P.POLICY_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _trusted(path: Path) -> str:
    return ""


def test_a_policy_sets_settings_and_turns_features_off(tmp_path):
    path = _file(tmp_path, {"settings": {"check_updates_on_start": False, "update_every_days": 0,
                                         "check_revocation": True, "timestamp_url": "http://tsa.example"},
                            "off": ["plugins", "web_capture"], "comment": "for the terminal servers"})
    policy = P.load(path, trust=_trusted)
    assert policy.in_force and policy.problems == ()
    assert policy.settings == {"check_updates_on_start": False, "update_every_days": 0, "check_revocation": True,
                               "timestamp_url": "http://tsa.example"}
    assert policy.locks("check_revocation") and not policy.locks("theme")
    assert not policy.allows("plugins") and policy.allows("updates")


def test_what_a_policy_cannot_do_is_left_out_and_said(tmp_path):
    path = _file(tmp_path, {"settings": {"theme": "dark", "update_every_days": "never", "api_enabled": 1,
                                         "remember_text": False},
                            "off": ["redaction", "ai_reader"], "extra": 1})
    policy = P.load(path, trust=_trusted)
    assert policy.settings == {"remember_text": False} and policy.off == {"ai_reader"}
    said = " ".join(policy.problems)
    for word in ('"theme"', '"update_every_days" needs a value of the kind int', '"api_enabled"', '"redaction"',
                 '"extra"'):
        assert word in said
    assert P.load(_file(tmp_path, "{not json"), trust=_trusted).in_force is False
    assert P.load(_file(tmp_path, "[1, 2]"), trust=_trusted).problems
    assert P.load(tmp_path / "none.json", trust=_trusted) == P.Policy()  # (no file: no policy, no problem)


def test_only_an_administrators_file_counts(tmp_path):
    """One anybody else could have written is ignored: a policy can also loosen things."""
    path = _file(tmp_path, {"settings": {"api_enabled": True}})
    ignored = P.load(path, trust=lambda p: "It can be changed by Users (BU), not only by administrators.")
    assert not ignored.in_force and ignored.settings == {} and ignored.found == path
    assert "is ignored" in ignored.problems[0] and "Users (BU)" in ignored.problems[0]
    # ...and that is how the real check judges a file the test's own user made
    real = P.load(path)
    assert not real.in_force and real.problems and "is ignored" in real.problems[0]
    assert P.untrusted(path) != ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_a_file_only_administrators_can_change_is_trusted():
    from planetpdf.core import windows_elevate as E

    assert P.untrusted(Path(E.system_dir()) / "kernel32.dll") == ""


def test_a_setting_under_policy_keeps_the_policys_value_and_the_users_own_is_kept_for_later(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    own = Settings(check_revocation=False, update_every_days=7, user_name="Pat")
    save_settings(own, profile)
    P.reset(P.Policy(path=tmp_path / "policy.json", settings={"check_revocation": True, "update_every_days": 0,
                                                              "remember_text": False}))
    s = load_settings(profile)
    assert s.check_revocation is True and s.update_every_days == 0 and s.remember_text is False
    assert s.locked("check_revocation") and not s.locked("user_name") and s.user_name == "Pat"
    s.check_revocation = False  # (Preferences, a plugin being turned on, anything: the policy's value stays)
    s.update_every_days = 30
    s.user_name = "Pat B."
    assert s.check_revocation is True and s.update_every_days == 0 and s.user_name == "Pat B."
    save_settings(s, profile)
    saved = json.loads((profile / "settings.json").read_text(encoding="utf-8"))
    assert saved["check_revocation"] is False and saved["update_every_days"] == 7  # (the user's own, untouched)
    assert saved["remember_text"] is True and saved["user_name"] == "Pat B."
    assert "_locked" not in saved and "_own" not in saved
    P.reset(P.Policy())  # (the policy is gone: their own choices are back)
    back = load_settings(profile)
    assert back.check_revocation is False and back.update_every_days == 7 and back.remember_text is True
    assert not back.locked("check_revocation")


def test_a_policy_applies_to_someone_with_no_settings_yet(tmp_path):
    P.reset(P.Policy(path=tmp_path / "policy.json", settings={"api_enabled": False, "autosave_minutes": 2}))
    profile = tmp_path / "new-profile"
    s = load_settings(profile)
    assert s.api_enabled is False and s.autosave_minutes == 2 and s.locked("autosave_minutes")
    save_settings(s, profile)
    saved = json.loads((profile / "settings.json").read_text(encoding="utf-8"))
    assert "autosave_minutes" not in saved and "api_enabled" not in saved  # (never theirs: the defaults later)
    P.reset(P.Policy())
    assert load_settings(profile).autosave_minutes == Settings().autosave_minutes


def test_checks_for_updates_can_be_turned_off_for_everyone(tmp_path, monkeypatch):
    monkeypatch.delenv(U.URL_ENV_VAR, raising=False)
    root = tmp_path / "install"
    root.mkdir()
    assert U.configured_url(root, "https://ours/") == "https://ours/" and U.checks_off(root) == ""
    (root / U.CONFIG_FILE).write_text(json.dumps({"check": False, "url": "https://it.example/pdf/"}), encoding="utf-8")
    assert U.configured_url(root, "https://ours/") == "" and "administrator" in U.checks_off(root)
    # IT's own update (--install-updates) and the voices' download still get the address
    assert U.configured_url(root, "https://ours/", even_if_off=True) == "https://it.example/pdf/"
    (root / U.CONFIG_FILE).write_text(json.dumps({"url": "https://it.example/pdf/"}), encoding="utf-8")
    assert U.configured_url(root, "https://ours/") == "https://it.example/pdf/"
    P.reset(P.Policy(path=root / "policy.json", off=frozenset({"updates"})))
    assert U.configured_url(root, "https://ours/") == "" and U.checks_off(root)
    assert U.configured_url(root, "https://ours/", even_if_off=True) == "https://it.example/pdf/"


def test_features_turned_off_say_so(tmp_path):
    from planetpdf.core import ai_ocr, office, plugins, voice_download, web_capture

    P.reset(P.Policy(path=tmp_path / "policy.json", off=frozenset(P.FEATURES)))
    by = P.BY_ADMINISTRATOR
    with pytest.raises(PdfError, match=by):
        web_capture.web_page_to_pdf("https://example.com", tmp_path / "page.pdf")
    with pytest.raises(PdfError, match=by):
        office.to_pdf(tmp_path / "a.docx", tmp_path / "a.pdf")
    with pytest.raises(PdfError, match=by):
        office.word_save_as(tmp_path / "a.docx", tmp_path / "a.doc")
    status = ai_ocr.status()
    assert not status.ready and by in status.reason
    with pytest.raises(U.UpdateError, match=by):
        voice_download.fetch_manifest("https://example.com/", opener=lambda *a: pytest.fail("no download"))
    plugin = plugins.Plugin(folder=tmp_path, id="p", name="P")
    assert by in plugins.turned_off() and not plugins.is_enabled(plugin, {"p": plugin.fingerprint()})
    with pytest.raises(plugins.PluginError, match=by):
        plugins.start(plugin, plugins.MenuItem("Tools", "Go", "go"), address="x", token="t", log_folder=tmp_path)
    P.reset(P.Policy())
    assert P.reason("plugins") == "" and plugins.is_enabled(plugin, {"p": plugin.fingerprint()})


def test_the_command_line_shows_the_policy(tmp_path, capsys):
    from planetpdf import cli

    P.reset(P.Policy(path=tmp_path / "policy.json", settings={"check_revocation": True}, off=frozenset({"plugins"}),
                     found=tmp_path / "policy.json"))
    assert cli.is_command(["planetpdf", "policy"])
    assert cli.main(["planetpdf", "policy"]) == cli.OK
    out = capsys.readouterr().out
    assert "Policy in force" in out and "check_revocation = true" in out and "off: plugins" in out
    assert cli.main(["planetpdf", "policy", "--json"]) == cli.OK
    data = json.loads(capsys.readouterr().out)
    assert data["in_force"] and data["settings"] == {"check_revocation": True} and "updates" in data["can_turn_off"]
    P.reset(P.Policy(found=tmp_path / "policy.json", problems=("policy.json is ignored. It can be changed by Users.",)))
    assert cli.main(["planetpdf", "policy"]) == cli.CHECK_FAILED  # (IT sees that their file isn't taken, and why)
    assert "Problem: policy.json is ignored" in capsys.readouterr().out
    P.reset(P.Policy(path=tmp_path / "policy.json", settings={"api_enabled": False}))
    assert cli.main(["planetpdf", "serve"]) == cli.FAILED
    assert "turned off on this computer by your administrator" in capsys.readouterr().err


def test_every_setting_a_policy_can_set_is_a_real_setting():
    defaults = Settings()
    for name in P.SETTINGS:
        assert hasattr(defaults, name), name


def test_the_policy_wins_over_the_users_own_variables(tmp_path, monkeypatch):
    """Security review 2026-11-27, finding 14: a user's PLANETPDF_UPDATE_URL made checks happen with
    updates turned off, and PLANETPDF_PRINT_FOLDER saved printouts where the policy said "ask"."""
    import json

    from planetpdf.core import policy as POL
    from planetpdf.core import printouts as PR
    from planetpdf.core import update as U

    (tmp_path / U.CONFIG_FILE).write_text(json.dumps({"check": False}), encoding="utf-8")
    monkeypatch.setenv(U.URL_ENV_VAR, "https://users-own.example.com/")
    assert U.configured_url(tmp_path, "https://software.example.com/") == ""
    assert U.configured_url(tmp_path, "https://software.example.com/", even_if_off=True) == \
        "https://users-own.example.com/"  # (IT's own update, run as the administrator, may)
    monkeypatch.setenv(PR.ENV_FOLDER, str(tmp_path / "users-folder"))
    monkeypatch.setenv(PR.ENV_NAME, "{user} x")
    try:
        POL.reset(POL.Policy(path=tmp_path / "policy.json", settings={"print_folder": "", "print_name_template": ""}))
        assert PR.default_folder("") is None and PR.template("") == PR.DEFAULT_TEMPLATE
        POL.reset(POL.Policy())
        assert PR.default_folder("") == tmp_path / "users-folder"  # (no policy: the variable counts)
    finally:
        POL.reset()
