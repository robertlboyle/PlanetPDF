"""Linux updates itself (2026-10-27): the AppImage replaces its own file; a .deb / .rpm is
installed as root (pkexec) by the installing step. Nothing here runs pkexec, dpkg or rpm."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from planetpdf.core import update as U
from planetpdf.core import update_helper as H


def _signed(tmp: Path, name: str, payload: bytes, version: str, platform: str, key) -> tuple[Path, bytes, bytes]:
    path = tmp / "download" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)
    manifest = json.dumps({"app": "PlanetPDF", "platform": platform, "version": version, "file": name,
                           "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}).encode()
    return path, manifest, U.sign(manifest, key)


def _program(folder: Path, kind: str) -> Path:
    folder.mkdir(parents=True)
    (folder / U.EXE_NAME).write_bytes(b"")
    (folder / U.KIND_FILE).write_text(kind + "\n", encoding="utf-8")
    return folder / U.EXE_NAME


def test_each_package_reads_its_own_manifest():
    assert U.linux_target(Path("PlanetPDF-1.2-aarch64.AppImage")) == ("linux-arm64", "appimage")
    assert U.linux_target(Path("planetpdf_1.2_amd64.deb")) == ("linux-x86_64", "deb")
    assert U.linux_target(Path("planetpdf-1.2-1.aarch64.rpm")) == ("linux-arm64", "rpm")
    assert U.platform_name("linux", "aarch64", "deb") == "linux-arm64-deb"
    assert U.manifest_name("linux-arm64-deb") == "latest-linux-arm64-deb.json"
    with pytest.raises(ValueError):
        U.linux_target(Path("PlanetPDF.zip"))


def test_where_each_kind_is_installed(tmp_path, monkeypatch):
    deb = _program(tmp_path / "opt" / "planetpdf", "deb")
    assert U.install_root(deb) == deb.parent and U.linux_package(deb.parent) == "deb"
    assert U.needs_admin(deb.parent)
    image = tmp_path / "Apps" / "PlanetPDF-1.0-x86_64.AppImage"
    image.parent.mkdir()
    image.write_bytes(b"old")
    mounted = _program(tmp_path / "mount" / "usr" / "lib" / "planetpdf", "appimage")
    monkeypatch.setenv("APPIMAGE", str(image))
    assert U.install_root(mounted) == image and U.is_appimage(image)
    assert not U.needs_admin(image)  # (the user's own folder)
    monkeypatch.delenv("APPIMAGE")
    assert U.install_root(mounted) is None
    assert U.package_kind(tmp_path) is None


def test_an_appimage_replaces_itself(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(U, "PLATFORM", "linux-x86_64-appimage")
    image = tmp_path / "PlanetPDF-1.0-x86_64.AppImage"
    image.write_bytes(b"old program")
    new, manifest, signature = _signed(tmp_path, "PlanetPDF-1.1-x86_64.AppImage", b"new program", "1.1",
                                       "linux-x86_64-appimage", key)
    assert U.apply(image, new, manifest, signature, key=key.public_key()) == "1.1"
    assert image.read_bytes() == b"new program"  # (same name: shortcuts keep working)
    assert sys.platform == "win32" or image.stat().st_mode & 0o111  # (Windows has no execute bit)
    assert not list(tmp_path.glob(".*update"))


def test_a_changed_download_is_never_installed(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(U, "PLATFORM", "linux-x86_64-appimage")
    image = tmp_path / "PlanetPDF.AppImage"
    image.write_bytes(b"old program")
    new, manifest, signature = _signed(tmp_path, "PlanetPDF-1.1.AppImage", b"new program", "1.1",
                                       "linux-x86_64-appimage", key)
    new.write_bytes(b"evil program")
    with pytest.raises(U.UpdateError, match="checksum"):
        U.apply(image, new, manifest, signature, key=key.public_key())
    assert image.read_bytes() == b"old program"


@pytest.mark.parametrize("kind, tool", [("deb", ["dpkg", "-i"]), ("rpm", ["rpm", "-U", "--replacepkgs"])])
def test_a_package_is_installed_with_the_system_tool(tmp_path, monkeypatch, kind, tool):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(U, "PLATFORM", f"linux-arm64-{kind}")
    root = _program(tmp_path / "opt" / "planetpdf", kind).parent
    new, manifest, signature = _signed(tmp_path, f"planetpdf.{kind}", b"package", "1.1", f"linux-arm64-{kind}", key)
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    seen = []

    def run(command, **kw):
        ran.append(command)
        seen.append(Path(command[-1]).read_bytes())  # (the private copy, there while it installs)
        return Done()

    monkeypatch.setattr("subprocess.run", run)
    assert U.apply(root, new, manifest, signature, key=key.public_key()) == "1.1"
    # installed from a private copy, not from the download the user's programs can change
    assert ran[0][:-1] == tool and Path(ran[0][-1]).name == new.name and Path(ran[0][-1]) != new
    assert seen == [b"package"] and not Path(ran[0][-1]).exists()
    Done.returncode, Done.stderr = 1, "dpkg: error: requested operation requires superuser privilege"
    with pytest.raises(U.UpdateError, match="superuser"):
        U.apply(root, new, manifest, signature, key=key.public_key())


def test_the_installing_step_runs_through_pkexec(tmp_path, monkeypatch):
    root = _program(tmp_path / "opt" / "planetpdf", "deb").parent
    folder = tmp_path / "updates"
    folder.mkdir()
    called = []
    monkeypatch.setattr(H.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(H.subprocess, "call", lambda command: called.append(command) or 0)
    monkeypatch.setattr(H, "start_detached", lambda command: called.append(("started", command)))
    code = H._finish([str(root), str(folder / "planetpdf.deb"), str(folder / U.MANIFEST),
                      str(folder / U.SIGNATURE), "--restart"])
    assert code == 0
    assert called[0][0] == "/usr/bin/pkexec" and H.APPLY in called[0]
    assert called[1] == ("started", [str(root / U.EXE_NAME)])


def test_a_canceled_password_is_reported(tmp_path, monkeypatch):
    folder = tmp_path / "updates"
    folder.mkdir()
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(H.subprocess, "call", lambda command: H.PKEXEC_CANCELED)
    assert H._run_as_admin_linux(["x"], folder / H.RESULT_FILE) == H.PKEXEC_CANCELED
    assert "canceled" in H.read_result(folder)["message"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    H._run_as_admin_linux(["x"], folder / H.RESULT_FILE)
    assert H.read_result(folder)["manual"]  # (no pkexec: the download page)


def test_planetpdf_replaces_its_appimage_as_it_closes(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(U, "PLATFORM", "linux-x86_64-appimage")
    monkeypatch.setattr(U, "public_key", lambda path=None: key.public_key())
    image = tmp_path / "PlanetPDF.AppImage"
    image.write_bytes(b"old program")
    new, manifest, signature = _signed(tmp_path, "PlanetPDF-1.1.AppImage", b"new program", "1.1",
                                       "linux-x86_64-appimage", key)
    (new.parent / U.MANIFEST).write_bytes(manifest)
    (new.parent / U.SIGNATURE).write_bytes(signature)
    started = []
    monkeypatch.setattr(H, "start_detached", started.append)
    assert H.install_appimage(image, new, new.parent, restart=True) == 0
    assert image.read_bytes() == b"new program" and started == [[str(image)]]
    assert H.read_result(new.parent)["version"] == "1.1"
