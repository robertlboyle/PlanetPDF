import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from planetpdf.core import update as U

BASE = "https://updates.example.com/planetpdf/"


def _zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


@pytest.fixture
def server():
    key = Ed25519PrivateKey.generate()
    files: dict[str, bytes] = {}

    def publish(version: str, payload: bytes | None = None, *, signer=None, sha=None) -> bytes:
        payload = payload if payload is not None else _zip({U.EXE_NAME: b"exe " + version.encode(), "lib/x.dll": b"x"})
        name = f"PlanetPDF-{version}.zip"
        manifest = json.dumps({"app": "PlanetPDF", "platform": U.PLATFORM, "version": version, "file": name, "size": len(payload),
                               "sha256": sha or hashlib.sha256(payload).hexdigest(), "notes": "New things"}).encode()
        files.update({U.MANIFEST: manifest, U.SIGNATURE: U.sign(manifest, signer or key), name: payload})
        return payload

    def opener(url: str, _timeout: int):
        assert url.startswith(BASE)
        name = url[len(BASE):]
        if name not in files:
            raise OSError("404")
        return io.BytesIO(files[name])

    publish.key, publish.opener, publish.files = key.public_key(), opener, files
    return publish


def test_newer_release_found_and_downloaded(server, tmp_path):
    server("1.2.0")
    signed = U.check(BASE, "1.1.9", key=server.key, opener=server.opener)
    assert signed.release.version == "1.2.0" and signed.release.notes == "New things"
    seen = []
    path = U.download(BASE, signed, tmp_path, lambda d, t, s="": seen.append((d, t)), opener=server.opener)
    assert path.read_bytes() == server.files[signed.release.file]
    assert seen[-1][0] == seen[-1][1] == signed.release.size
    again = U.downloaded(tmp_path, "1.1.9", key=server.key)
    assert again is not None and again[1] == path
    assert U.downloaded(tmp_path, "1.2.0", key=server.key) is None  # (installed by now)


def test_same_or_older_is_no_update(server):
    server("1.2.0")
    assert U.check(BASE, "1.2.0", key=server.key, opener=server.opener) is None
    assert U.check(BASE, "1.10", key=server.key, opener=server.opener) is None  # (10 > 2, not text order)


def test_wrong_signature_refused(server):
    server("9.0", signer=Ed25519PrivateKey.generate())
    with pytest.raises(U.UpdateError, match="isn't signed"):
        U.check(BASE, "1.0", key=server.key, opener=server.opener)


def test_tampered_download_thrown_away(server, tmp_path):
    server("2.0", sha="0" * 64)
    signed = U.check(BASE, "1.0", key=server.key, opener=server.opener)
    with pytest.raises(U.UpdateError, match="checksum"):
        U.download(BASE, signed, tmp_path, opener=server.opener)
    assert list(tmp_path.iterdir()) == []


def test_only_https():
    with pytest.raises(U.UpdateError, match="HTTPS"):
        U.check("http://updates.example.com/", "1.0", key=object())


def test_install_side_by_side(server, tmp_path):
    root = tmp_path / "PlanetPDF"
    old = root / U.VERSIONS / "1.0.0"
    old.mkdir(parents=True)
    (old / U.EXE_NAME).write_bytes(b"old")
    (root / U.CURRENT_FILE).write_text("1.0.0")
    assert U.install_root(old / U.EXE_NAME) == root
    server("1.1.0")
    signed = U.check(BASE, "1.0.0", key=server.key, opener=server.opener)
    zip_path = U.download(BASE, signed, tmp_path / "dl", opener=server.opener)
    assert U.apply(root, zip_path, signed.manifest, signed.signature, key=server.key) == "1.1.0"
    assert U.current_version(root) == "1.1.0"
    assert (root / U.VERSIONS / "1.1.0" / "lib" / "x.dll").read_bytes() == b"x"
    assert not old.exists()  # (nobody was running it)
    # a tampered manifest handed to the helper is refused
    with pytest.raises(U.UpdateError):
        U.apply(root, zip_path, signed.manifest.replace(b"1.1.0", b"1.1.1"), signed.signature, key=server.key)


def test_zip_cannot_escape_its_folder(server, tmp_path):
    server("1.1.0", _zip({U.EXE_NAME: b"x", "../evil.txt": b"!"}))
    signed = U.check(BASE, "1.0.0", key=server.key, opener=server.opener)
    zip_path = U.download(BASE, signed, tmp_path / "dl", opener=server.opener)
    with pytest.raises(U.UpdateError, match="outside"):
        U.apply(tmp_path / "root", zip_path, signed.manifest, signed.signature, key=server.key)
    assert not (tmp_path / "root" / "evil.txt").exists()


def test_helper_installs_and_reports(server, tmp_path, monkeypatch):
    from planetpdf.core import update_helper as H

    monkeypatch.setattr(U, "public_key", lambda path=None: server.key)  # (the helper uses the bundled key)
    root = tmp_path / "root"
    server("3.0")
    signed = U.check(BASE, "1.0", key=server.key, opener=server.opener)
    folder = tmp_path / "dl"
    zip_path = U.download(BASE, signed, folder, opener=server.opener)
    args = [H.APPLY, str(root), str(zip_path), str(folder / U.MANIFEST), str(folder / U.SIGNATURE),
            str(folder / H.RESULT_FILE)]
    assert H.helper_main(["planetpdf", *args]) == 0
    assert U.current_version(root) == "3.0"
    assert H.read_result(folder) == {"ok": True, "message": "PlanetPDF was updated to version 3.0.", "version": "3.0",
                                     "manual": False, "target": ""}
    assert H.read_result(folder) is None  # (shown once)
    (folder / U.SIGNATURE).write_bytes(U.sign(signed.manifest, Ed25519PrivateKey.generate()))
    assert H.helper_main(["planetpdf", *args]) == 1
    assert H.read_result(folder)["ok"] is False
    command = H.finish_command(root, zip_path, folder, 1234, restart=True)
    assert H.FINISH in command and command[-3:] == ["--wait-pid", "1234", "--restart"]


def test_configured_url(tmp_path, monkeypatch):
    assert U.configured_url(tmp_path, "https://a/") == ""  # (tests: the env variable blanks it)
    monkeypatch.delenv(U.URL_ENV_VAR)
    assert U.configured_url(tmp_path, "https://a/") == "https://a/"
    (tmp_path / U.CONFIG_FILE).write_text(json.dumps({"url": "https://b/"}))
    assert U.configured_url(tmp_path, "https://a/") == "https://b/"


# --- when Windows' administrator prompt misbehaves (user 2026-10-26) ------------------------


@pytest.fixture
def finishing(server, tmp_path, monkeypatch):
    """A downloaded update and the arguments of the finishing step (as PlanetPDF closes)."""
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows' administrator prompt")
    from planetpdf.core import update_helper as H
    from planetpdf.core import windows_elevate as E

    monkeypatch.setattr(U, "public_key", lambda path=None: server.key)
    monkeypatch.setattr(E, "START_GRACE_SECONDS", 0.3)
    monkeypatch.setattr(E, "is_admin", lambda: False)
    # (the test's folder stands in for an install only administrators can change; the check itself
    # is test_an_install_others_can_change_gets_no_administrator_step)
    monkeypatch.setattr(U, "elevation_refusal", lambda root: "")
    root = tmp_path / "root"
    server("3.0")
    signed = U.check(BASE, "1.0", key=server.key, opener=server.opener)
    folder = tmp_path / "dl"
    zip_path = U.download(BASE, signed, folder, opener=server.opener)
    args = [str(root), str(zip_path), str(folder / U.MANIFEST), str(folder / U.SIGNATURE)]
    return H, E, root, folder, args


def test_a_prompt_reported_as_canceled_that_ran_the_installer_counts_as_installed(finishing, monkeypatch):
    H, E, root, folder, args = finishing

    def broken_prompt(command):  # Windows ran the installing step, then said "canceled"
        H.helper_main(["planetpdf", *command[command.index(H.APPLY):]])
        raise E.ElevationCanceled()

    monkeypatch.setattr(E, "run_elevated", broken_prompt)
    H.helper_main(["planetpdf", H.FINISH, *args])
    assert U.current_version(root) == "3.0"
    assert H.read_result(folder)["ok"] is True  # (not "canceled")
    assert "canceled" in (folder / H.LOG_FILE).read_text(encoding="utf-8")


def test_windows_failing_to_start_the_installer_offers_the_download_page(finishing, monkeypatch):
    H, E, root, folder, args = finishing

    def failing(command):
        raise E.ElevationFailed(1, "File system error")

    monkeypatch.setattr(E, "run_elevated", failing)
    assert H.helper_main(["planetpdf", H.FINISH, *args]) == 1
    result = H.read_result(folder)
    assert result["ok"] is False and result["manual"] is True and "download page" in result["message"]
    assert U.current_version(root) is None


def test_an_install_others_can_change_gets_no_administrator_step(finishing, monkeypatch):
    """Security review 2026-11-27, finding 3: in a folder other users can change, a program of
    theirs could stand in for PlanetPDF's, and the administrator step ran it. Now nothing is run as
    administrator there, and the user is told to have the installer run."""
    H, E, root, folder, args = finishing
    monkeypatch.undo()  # (the real check: this test's folder is the user's own, so others' to change)
    monkeypatch.setattr(E, "is_admin", lambda: False)
    asked = []
    monkeypatch.setattr(E, "run_elevated", lambda command: asked.append(command) or 0)
    root.mkdir(exist_ok=True)
    assert H.helper_main(["planetpdf", H.FINISH, *args]) == 1
    assert asked == []
    result = H.read_result(folder)
    assert result["ok"] is False and result["manual"] is True
    assert "won't run one there as administrator" in result["message"]


def test_a_real_cancel_says_so(finishing, monkeypatch):
    H, E, root, folder, args = finishing

    def canceled(command):
        raise E.ElevationCanceled()

    monkeypatch.setattr(E, "run_elevated", canceled)
    assert H.helper_main(["planetpdf", H.FINISH, *args]) == 1
    result = H.read_result(folder)
    assert "canceled" in result["message"] and result["manual"] is False


def test_a_failure_says_which_version_it_was_for(finishing, monkeypatch):
    H, E, root, folder, args = finishing
    monkeypatch.setattr(E, "run_elevated", lambda command: (_ for _ in ()).throw(E.ElevationCanceled()))
    H.helper_main(["planetpdf", H.FINISH, *args])
    assert json.loads((folder / H.RESULT_FILE).read_text(encoding="utf-8"))["target"] == "3.0"


# --- a failure that a later install has made stale (user 2026-10-27) ----------------------------


def _failure(folder, target=""):
    from planetpdf.core import update_helper as H

    folder.mkdir(exist_ok=True)
    H._write_result(folder / H.RESULT_FILE, False, "The update wasn't installed.", target=target)
    return H


def test_a_failure_for_the_version_now_running_is_not_shown(tmp_path):
    H = _failure(tmp_path / "dl", target="0.1.14")
    assert H.read_result(tmp_path / "dl", running="0.1.14") is None
    assert not (tmp_path / "dl" / H.RESULT_FILE).exists()  # (and forgotten)


def test_a_failure_for_a_newer_version_is_shown(tmp_path):
    H = _failure(tmp_path / "dl", target="0.1.15")
    assert H.read_result(tmp_path / "dl", running="0.1.14")["ok"] is False


def test_an_old_updaters_failure_from_before_this_install_is_not_shown(tmp_path):
    import os
    import time

    H = _failure(tmp_path / "dl")  # (no target: written by 0.1.13 or older)
    result = tmp_path / "dl" / H.RESULT_FILE
    os.utime(result, (time.time() - 3600, time.time() - 3600))
    assert H.read_result(tmp_path / "dl", running="0.1.14", installed_at=time.time() - 60) is None
    H = _failure(tmp_path / "dl")
    assert H.read_result(tmp_path / "dl", running="0.1.14", installed_at=time.time() - 60)["ok"] is False


# --- security review 2026-11-21 -----------------------------------------------------------------------


def test_an_update_never_goes_back_to_an_older_version(server, tmp_path):
    root = tmp_path / "PlanetPDF"
    (root / U.VERSIONS / "2.0.0").mkdir(parents=True)
    (root / U.VERSIONS / "2.0.0" / U.EXE_NAME).write_bytes(b"new")
    (root / U.CURRENT_FILE).write_text("2.0.0")
    server("1.5.0")  # (genuinely signed, but older than what's installed)
    signed = U.check(BASE, "1.0.0", key=server.key, opener=server.opener)
    zip_path = U.download(BASE, signed, tmp_path / "dl", opener=server.opener)
    with pytest.raises(U.UpdateError, match="older version"):
        U.apply(root, zip_path, signed.manifest, signed.signature, key=server.key)
    assert U.current_version(root) == "2.0.0" and not (root / U.VERSIONS / "1.5.0").exists()


def test_the_download_is_checked_and_installed_from_a_private_copy(server, tmp_path, monkeypatch):
    """Swapping the downloaded file after it was checked changes nothing: the copy was checked."""
    root = tmp_path / "PlanetPDF"
    server("1.1.0")
    signed = U.check(BASE, "1.0.0", key=server.key, opener=server.opener)
    zip_path = U.download(BASE, signed, tmp_path / "dl", opener=server.opener)
    real_matches = U._matches

    def swap_after_checking(path, release):
        ok = real_matches(path, release)
        zip_path.write_bytes(_zip({U.EXE_NAME: b"evil"}))  # (another program, just after the check)
        return ok

    monkeypatch.setattr(U, "_matches", swap_after_checking)
    assert U.apply(root, zip_path, signed.manifest, signed.signature, key=server.key) == "1.1.0"
    assert (root / U.VERSIONS / "1.1.0" / U.EXE_NAME).read_bytes() == b"exe 1.1.0"
    assert not (root / U.VERSIONS / U.INCOMING).exists()


def test_as_administrator_nothing_is_written_into_the_users_folder(server, tmp_path, monkeypatch):
    from planetpdf.core import update_helper as H

    monkeypatch.setattr(U, "public_key", lambda path=None: server.key)
    monkeypatch.setattr(H, "_is_admin", lambda: True)
    root = tmp_path / "root"
    monkeypatch.setattr(U, "install_root", lambda executable=None: root)  # (running from that install)
    monkeypatch.setattr(H, "_registered_install_dir", lambda: root)
    server("3.0")
    signed = U.check(BASE, "1.0", key=server.key, opener=server.opener)
    folder = tmp_path / "dl"
    zip_path = U.download(BASE, signed, folder, opener=server.opener)
    before = sorted(p.name for p in folder.iterdir())
    result = folder / H.RESULT_FILE
    assert H.helper_main(["planetpdf", H.APPLY, str(root), str(zip_path), str(folder / U.MANIFEST),
                          str(folder / U.SIGNATURE), str(result)]) == 0
    assert sorted(p.name for p in folder.iterdir()) == before  # (the user's folder: untouched)
    status, status_log, _marker = H.status_files(root, result)
    assert status.parent == root / H.STATUS_FOLDER and json.loads(status.read_text())["ok"] is True
    H._collect(root, result, since=0)  # (the user's own finishing step copies it over)
    assert H.read_result(folder)["version"] == "3.0"
    assert "installing step done" in (folder / H.LOG_FILE).read_text(encoding="utf-8")


def test_the_appimage_is_checked_again_as_root_before_it_is_installed(tmp_path):
    import shutil
    import subprocess

    from planetpdf.core import update_helper as H

    sh = shutil.which("sh")
    if sh is None or shutil.which("sha256sum") is None:
        pytest.skip("needs sh and sha256sum")
    new, image = tmp_path / "new.AppImage", tmp_path / "PlanetPDF.AppImage"
    new.write_bytes(b"good")
    image.write_bytes(b"old")
    good = hashlib.sha256(b"good").hexdigest()
    command = H.appimage_install_command(new, good, image)
    assert command[0] == "/bin/sh"
    assert subprocess.run([sh, *command[1:]]).returncode == 0 and image.read_bytes() == b"good"
    new.write_bytes(b"swapped")  # (changed after PlanetPDF checked it)
    image.write_bytes(b"old")
    assert subprocess.run([sh, *H.appimage_install_command(new, good, image)[1:]]).returncode != 0
    assert image.read_bytes() == b"old"
    with pytest.raises(U.UpdateError):
        H.appimage_install_command(new, "x; rm -rf /", image)


def test_the_administrator_step_installs_only_into_its_own_folder(tmp_path, monkeypatch):
    """Security review 2026-11-25 (finding 10): the folder came from its command line."""
    from planetpdf.core import update_helper as H

    root = tmp_path / "PlanetPDF"
    monkeypatch.setattr(U, "install_root", lambda executable=None: root)
    monkeypatch.setattr(H, "_registered_install_dir", lambda: root)
    assert H.trusted_root(root) == root
    with pytest.raises(U.UpdateError, match="isn't this PlanetPDF's"):
        H.trusted_root(tmp_path / "Windows" / "System32")
    monkeypatch.setattr(H, "_registered_install_dir", lambda: tmp_path / "Elsewhere")
    with pytest.raises(U.UpdateError, match="installer registered"):
        H.trusted_root(root)
    monkeypatch.setattr(U, "install_root", lambda executable=None: None)
    with pytest.raises(U.UpdateError, match="installed PlanetPDF"):
        H.trusted_root(root)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows install folder")
def test_updates_replace_the_launchers_and_the_print_service(tmp_path, monkeypatch):
    """Security review 2026-11-25 (finding 6): updates never replaced them."""
    root = tmp_path / "PlanetPDF"
    (root / "printer").mkdir(parents=True)
    (root / "PlanetPDF.exe").write_bytes(b"old launcher")
    (root / "PlanetPDF.com").write_bytes(b"same console")
    (root / "printer" / "planetpdf-print-service.exe").write_bytes(b"old service")
    new = tmp_path / "install-root"
    (new / "printer").mkdir(parents=True)
    (new / "PlanetPDF.exe").write_bytes(b"new launcher")
    (new / "PlanetPDF.com").write_bytes(b"same console")
    (new / "printer" / "planetpdf-print-service.exe").write_bytes(b"new service")
    calls = []
    monkeypatch.setattr(U, "_service_state", lambda: "STOPPED" if ("stop",) in calls else "RUNNING")
    monkeypatch.setattr(U, "_sc", lambda *args: calls.append(args[:1]) or "")
    changed = U.update_root_files(root, new)
    assert sorted(changed) == ["PlanetPDF.exe", r"printer\planetpdf-print-service.exe"]
    assert (root / "PlanetPDF.exe").read_bytes() == b"new launcher"
    assert (root / "printer" / "planetpdf-print-service.exe").read_bytes() == b"new service"
    # stopped while replaced, set to start again by itself if it ever ends, started again
    assert calls == [("stop",), ("failure",), ("start",)]
    assert not list(root.glob("*.old")) and not list(root.glob("*.new"))
    assert U.update_root_files(root, new) == []  # (nothing to change the second time)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows install folder")
def test_an_install_whose_own_programs_are_older_is_noticed_and_brought_up_to_date(tmp_path, monkeypatch):
    """Security list 2026-11-26, item 6: updated by a version that replaced only the program, an
    install kept its old print service and launcher. PlanetPDF can tell, and an administrator's
    step (--refresh-install) replaces them."""
    from planetpdf.core import update_helper as H

    root = tmp_path / "PlanetPDF"
    version = root / U.VERSIONS / "2.0.0"
    (root / "printer").mkdir(parents=True)
    (version / U.ROOT_FILES / "printer").mkdir(parents=True)
    (version / U.EXE_NAME).write_bytes(b"program")
    (root / U.CURRENT_FILE).write_text("2.0.0\n", encoding="utf-8")
    for folder, age in ((root, b"old"), (version / U.ROOT_FILES, b"new")):
        (folder / "PlanetPDF.exe").write_bytes(age + b" launcher")
        (folder / "PlanetPDF.com").write_bytes(b"same console")
        (folder / "printer" / "planetpdf-print-service.exe").write_bytes(age + b" service")
    assert U.stale_root_files(root) == [r"printer\planetpdf-print-service.exe", "PlanetPDF.exe"]
    assert U.stale_root_files(tmp_path / "nothing-here") == []
    calls = []
    monkeypatch.setattr(U, "_service_state", lambda: "STOPPED")
    monkeypatch.setattr(U, "_sc", lambda *args: calls.append(args[0]) or "")
    monkeypatch.setattr(U, "install_root", lambda executable=None: root)
    monkeypatch.setattr(H, "_registered_install_dir", lambda: root)
    monkeypatch.setattr(H, "_is_admin", lambda: False)
    assert H.helper_main(["planetpdf", H.REFRESH, str(root)]) == 1  # (only as administrator)
    assert U.stale_root_files(root)
    monkeypatch.setattr(H, "_is_admin", lambda: True)
    assert H.helper_main(["planetpdf", H.REFRESH, str(tmp_path / "Elsewhere")]) == 1  # (only its own install)
    assert H.helper_main(["planetpdf", H.REFRESH, str(root)]) == 0
    assert U.stale_root_files(root) == [] and "failure" in calls
    assert (root / "printer" / "planetpdf-print-service.exe").read_bytes() == b"new service"
    log = (H.status_folder(root) / H.REFRESH_LOG).read_text(encoding="utf-8")
    assert "PlanetPDF.exe" in log and "planetpdf-print-service.exe" in log
    assert H.REFRESH in H.FLAGS


def test_unattended_updates_refuse_a_folder_others_can_change(tmp_path, monkeypatch, capsys):
    """Security review 2026-11-27, finding 3: IT's --install-updates (as SYSTEM or an administrator)
    installs nothing into a folder other users can change, and says why (exit code 3)."""
    import sys

    from planetpdf.core import update_helper as H

    if sys.platform != "win32":
        pytest.skip("Windows permissions")
    root = tmp_path / "PlanetPDF"
    (root / U.VERSIONS / "1.0").mkdir(parents=True)  # (this user's own folder: others' to change)
    monkeypatch.setattr(U, "install_root", lambda executable=None: root)
    monkeypatch.setattr(U, "check", lambda *a, **k: pytest.fail("nothing may be asked of the server"))
    assert H._install_updates() == 3
    assert "Not updated" in capsys.readouterr().out


def test_after_an_update_the_old_version_folder_goes_and_apps_says_the_new_version(tmp_path, monkeypatch):
    """Review 2026-11-27: after an in-app update, Apps & features still named the old version and
    the old version's folder stayed (the installing step runs from it, so it can't remove it)."""
    import sys

    from planetpdf.core import update_helper as H

    if sys.platform != "win32":
        pytest.skip("Windows' installing step")
    from planetpdf.core import windows_elevate as E

    root = tmp_path / "PlanetPDF"
    for version in ("1.0.8", "1.0.9"):
        (root / U.VERSIONS / version).mkdir(parents=True)
        (root / U.VERSIONS / version / U.EXE_NAME).write_bytes(b"exe")
    (root / U.CURRENT_FILE).write_text("1.0.9", encoding="utf-8")
    named, started = [], []
    monkeypatch.setattr(E, "set_installed_version", lambda version: named.append(version) or True)
    monkeypatch.setattr(H, "start_detached", lambda command: started.append(command))
    H._after_install_windows(root, "1.0.9", tmp_path)
    assert named == ["1.0.9"]
    assert started and started[0][1] == H.TIDY and Path(started[0][0]) == root / U.VERSIONS / "1.0.9" / U.EXE_NAME
    monkeypatch.setattr(H, "_is_admin", lambda: True)
    monkeypatch.setattr(H, "trusted_root", lambda claimed: root)
    assert H._tidy([str(root)]) == 0
    assert sorted(p.name for p in (root / U.VERSIONS).iterdir()) == ["1.0.9"]
