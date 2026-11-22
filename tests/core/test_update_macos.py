"""Updating the Mac app (2026-10-10): the whole PlanetPDF.app is swapped for the new one."""

from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from planetpdf.core import update as U
from planetpdf.core import update_helper as H

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (ditto, codesign)")


def _fake_app(folder: Path, version: str) -> Path:
    """A tiny real app bundle, ad-hoc signed (like a local build)."""
    app = folder / U.APP_BUNDLE
    (app / "Contents" / "MacOS").mkdir(parents=True)
    shutil.copy("/usr/bin/true", app / "Contents" / "MacOS" / "PlanetPDF")
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleExecutable": "PlanetPDF", "CFBundleIdentifier": "net.planet.planetpdf",
                       "CFBundleName": "PlanetPDF", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": version}, f)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


def _release(tmp: Path, version: str, key) -> tuple[Path, bytes, bytes]:
    build = tmp / f"build-{version}"
    build.mkdir()
    app = _fake_app(build, version)
    zip_path = tmp / f"PlanetPDF-{version}-macos.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(zip_path)], check=True)
    data = zip_path.read_bytes()
    manifest = json.dumps({"app": "PlanetPDF", "platform": "macos", "version": version, "file": zip_path.name,
                           "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}).encode()
    return zip_path, manifest, U.sign(manifest, key)


def test_the_installed_mac_app_is_its_bundle(tmp_path):
    exe = tmp_path / "Applications" / "PlanetPDF.app" / "Contents" / "MacOS" / "PlanetPDF"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert U.install_root(exe) == tmp_path / "Applications" / "PlanetPDF.app"
    assert U.is_app_bundle(U.install_root(exe))


def test_a_windows_update_is_never_installed_on_a_mac_and_back(monkeypatch):
    key = Ed25519PrivateKey.generate()
    manifest = json.dumps({"app": "PlanetPDF", "platform": "windows", "version": "9.0", "file": "x.zip",
                           "size": 1, "sha256": "0" * 64}).encode()
    monkeypatch.setattr(U, "PLATFORM", "macos")
    with pytest.raises(U.UpdateError, match="for windows, not macos"):
        U.verify(manifest, U.sign(manifest, key), key.public_key())
    old_windows = json.dumps({"app": "PlanetPDF", "version": "9.0", "file": "x.zip", "size": 1,
                              "sha256": "0" * 64}).encode()  # (manifests before 2026-10-10 name no platform)
    monkeypatch.setattr(U, "PLATFORM", "windows")
    assert U.verify(old_windows, U.sign(old_windows, key), key.public_key()).version == "9.0"



def test_each_computer_reads_its_own_manifest():
    """Linux (2026-10-26): one manifest per processor, so an ARM64 copy never offers the x86-64 build."""
    assert U.platform_name("win32") == "windows" and U.manifest_name("windows") == "latest.json"
    assert U.platform_name("darwin") == "macos" and U.manifest_name("macos") == "latest-macos.json"
    assert U.platform_name("linux", "aarch64") == "linux-arm64"
    assert U.platform_name("linux", "x86_64") == "linux-x86_64"
    assert U.manifest_name("linux-arm64") == "latest-linux-arm64.json"

@mac_only
def test_apply_swaps_the_whole_app(tmp_path):
    key = Ed25519PrivateKey.generate()
    installed = _fake_app(tmp_path / "Applications", "1.0")
    assert U.current_version(installed) == "1.0"
    zip_path, manifest, signature = _release(tmp_path, "1.1", key)
    assert U.apply(installed, zip_path, manifest, signature, key=key.public_key()) == "1.1"
    assert U.current_version(installed) == "1.1"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["PlanetPDF.app"]  # nothing left behind
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)], check=True)


@mac_only
def test_a_tampered_app_is_refused_and_the_old_one_stays(tmp_path):
    key = Ed25519PrivateKey.generate()
    installed = _fake_app(tmp_path / "Applications", "1.0")
    zip_path, manifest, signature = _release(tmp_path, "1.1", key)
    work = tmp_path / "work"
    subprocess.run(["ditto", "-x", "-k", str(zip_path), str(work)], check=True)
    plist = work / "PlanetPDF.app" / "Contents" / "Info.plist"
    plist.write_bytes(plist.read_bytes().replace(b"1.1", b"6.6"))  # breaks the code signature
    zip_path.unlink()
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(work / "PlanetPDF.app"), str(zip_path)], check=True)
    data = zip_path.read_bytes()
    manifest = json.dumps({"app": "PlanetPDF", "platform": "macos", "version": "1.1", "file": zip_path.name,
                           "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}).encode()
    with pytest.raises(U.UpdateError, match="code signature isn't valid"):
        U.apply(installed, zip_path, manifest, U.sign(manifest, key), key=key.public_key())
    assert U.current_version(installed) == "1.0"
    assert sorted(p.name for p in installed.parent.iterdir()) == ["PlanetPDF.app"]


def test_admin_script_quotes_the_command():
    script = H.admin_script(["/Applications/Planet PDF.app/Contents/MacOS/PlanetPDF", "--apply-update", 'a"b'])
    assert script.startswith('do shell script "') and "with administrator privileges" in script
    assert "'/Applications/Planet PDF.app/Contents/MacOS/PlanetPDF'" in script
    assert '\\"' in script  # the double quote inside an argument is escaped for AppleScript


def test_the_update_message_says_what_installing_needs(tmp_path, monkeypatch):
    from planetpdf.ui.updates import install_note

    assert "Windows administrator" in install_note(None, "win32")
    monkeypatch.setattr(U, "needs_admin", lambda root: False)
    assert install_note(tmp_path / "PlanetPDF.app", "darwin") == "PlanetPDF must close to install it."
    monkeypatch.setattr(U, "needs_admin", lambda root: True)
    assert "administrator's password" in install_note(tmp_path / "PlanetPDF.app", "darwin")
