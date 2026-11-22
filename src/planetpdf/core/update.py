"""Updates (user 2026-10-04): check a web server for a newer PlanetPDF, download it, install it.

**On the server** (one folder, made by ``scripts/release_update.py publish``):

* ``latest.json``: ``{"app": "PlanetPDF", "version": "1.2.0", "file": "PlanetPDF-1.2.0.zip",
  "size": …, "sha256": "…", "notes": "…", "published": "2026-10-04"}``
* ``latest.json.sig``: an Ed25519 signature of those exact bytes (base64). Only the holder
  of the private key (kept off the server, never in the project) can make one; PlanetPDF
  carries the public key (``resources/update/update_key.pub``). A changed or stolen server
  can't push a program: an unsigned / wrongly signed file is refused, and so is a download
  whose size or SHA-256 differs from the signed file.
* the zip: the one-folder build (``PlanetPDF.exe`` and its files at the top).

**Installed** (per machine, e.g. ``C:\\Program Files\\PlanetPDF``)::

    PlanetPDF.exe          the launcher: starts versions/<current>/PlanetPDF.exe
    current.txt            "1.2.0"
    update.json            optional {"url": "https://…/"}: where to look (set by IT)
    versions/1.1.0/…       still used by anyone who hasn't restarted (terminal server)
    versions/1.2.0/…

A new version goes into a folder of its own, so nothing in use is ever overwritten; old
folders are removed once nobody runs them (a folder in use can't be renamed on Windows).

**Mac app** (2026-10-10): the server folder also holds ``latest-macos.json`` (+ ``.sig``,
``"platform": "macos"``) for a zip of ``PlanetPDF.app`` made with ``ditto`` (it keeps the
app's symbolic links and code signature). The installed copy is the app bundle itself
(e.g. ``/Applications/PlanetPDF.app``): the new app is unpacked beside it and swapped in
whole (a running program on macOS keeps its files when the bundle is renamed).

**Linux** (2026-10-27, user: "make the linux version update itself"): one manifest per processor
and package kind, ``latest-linux-<x86_64|arm64>-<appimage|deb|rpm>.json``, naming that file. The
build writes the kind into ``package-kind`` beside the program. An AppImage replaces its own file
(``$APPIMAGE``; the running copy keeps the old one open, so it's done as PlanetPDF closes); a
.deb / .rpm is installed by the installing step as root (``pkexec``: the system asks for an
administrator's password) with ``dpkg -i`` / ``rpm -U``, after the same signature and checksum
checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform as _machine
import re
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from planetpdf import APP_NAME
from planetpdf.core.progress import report

KIND_FILE = "package-kind"  # Linux: "appimage", "deb" or "rpm", beside the program (scripts/build_linux.py)
LINUX_KINDS = ("appimage", "deb", "rpm")


def package_kind(program_dir: Path | None = None) -> str | None:
    """How this Linux copy was installed (None: from source, or not Linux)."""
    if program_dir is None:
        if not getattr(sys, "frozen", False):
            return None
        program_dir = Path(sys.executable).resolve().parent
    try:
        kind = (program_dir / KIND_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return kind if kind in LINUX_KINDS else None


def platform_name(system: str = sys.platform, machine: str | None = None, kind: str | None = None) -> str:
    """"windows", "macos", or "linux-<x86_64|arm64>[-<appimage|deb|rpm>]": which build fits this copy."""
    if system == "darwin":
        return "macos"
    if system == "win32":
        return "windows"
    machine = (machine if machine is not None else _machine.machine()).lower()
    name = "linux-arm64" if machine in ("aarch64", "arm64") else "linux-x86_64"
    return f"{name}-{kind}" if kind else name


def linux_target(path: Path) -> tuple[str, str]:
    """(processor, kind) of a built Linux package, from its name: PlanetPDF-1.2-aarch64.AppImage,
    planetpdf_1.2_arm64.deb, planetpdf-1.2-1.aarch64.rpm."""
    kind = {".appimage": "appimage", ".deb": "deb", ".rpm": "rpm"}.get(path.suffix.lower())
    if kind is None:
        raise ValueError(f"{path.name} isn't an AppImage, .deb or .rpm")
    stem = path.name[: -len(path.suffix)]
    machine = re.split(r"[-_.]", stem)[-1]
    return platform_name("linux", machine), kind


PLATFORM = platform_name(kind=package_kind()) if sys.platform.startswith("linux") else platform_name()


def manifest_name(platform: str) -> str:
    return {"macos": "latest-macos.json", "windows": "latest.json"}.get(platform, f"latest-{platform}.json")


MANIFEST = manifest_name(PLATFORM)
SIGNATURE = MANIFEST + ".sig"
APP_BUNDLE = f"{APP_NAME}.app"
EXE_NAME = "PlanetPDF.exe" if sys.platform == "win32" else "PlanetPDF"
CURRENT_FILE = "current.txt"
CONFIG_FILE = "update.json"
URL_ENV_VAR = "PLANETPDF_UPDATE_URL"
VERSIONS = "versions"
TIMEOUT = 30  # seconds for the check
CHUNK = 256 * 1024


class UpdateError(Exception):
    """Shown to the user as is."""


@dataclass(frozen=True)
class Release:
    version: str
    file: str
    size: int
    sha256: str
    notes: str = ""
    published: str = ""


@dataclass(frozen=True)
class SignedRelease:
    """The release with the exact manifest bytes and signature, so the elevated helper can
    check them again itself (it trusts nothing it's handed)."""
    release: Release
    manifest: bytes
    signature: bytes


def parse_version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text)
    if not parts:
        raise UpdateError(f"“{text}” isn't a version number.")
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# --- keys and signatures ------------------------------------------------------------------------

def key_path() -> Path:
    from planetpdf.core.resources import resources_dir

    return resources_dir() / "update" / "update_key.pub"


def public_key(path: Path | None = None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = path or key_path()
    try:
        raw = base64.b64decode(path.read_text(encoding="ascii").strip())
        return Ed25519PublicKey.from_public_bytes(raw)
    except FileNotFoundError:
        raise UpdateError("This copy of PlanetPDF has no update key, so it can't check that an update is genuine.") \
            from None
    except ValueError as exc:
        raise UpdateError(f"PlanetPDF's update key is damaged: {exc}") from exc


def verify(manifest: bytes, signature: bytes, key) -> Release:
    """The release the manifest describes, if its signature is good; else UpdateError."""
    from cryptography.exceptions import InvalidSignature

    try:
        key.verify(base64.b64decode(signature.strip()), manifest)
    except (InvalidSignature, ValueError):
        raise UpdateError("The update on the server isn't signed with PlanetPDF's update key, so it wasn't installed.") from None
    try:
        data = json.loads(manifest.decode("utf-8"))
        if data.get("app") != APP_NAME:
            raise ValueError(f"it's for {data.get('app')!r}")
        if data.get("platform", "windows") != PLATFORM:
            raise ValueError(f"it's for {data.get('platform')}, not {PLATFORM}")
        file = str(data["file"])
        if "/" in file or "\\" in file or file.startswith("."):
            raise ValueError(f"odd file name {file!r}")
        release = Release(str(data["version"]), file, int(data["size"]), str(data["sha256"]).lower(),
                          str(data.get("notes", "")), str(data.get("published", "")))
        parse_version(release.version)
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"The update information on the server can't be read ({exc}).") from exc
    return release


def sign(manifest: bytes, private_key) -> bytes:
    return base64.b64encode(private_key.sign(manifest)) + b"\n"


# --- the server -----------------------------------------------------------------------------------

Opener = Callable[[str, int], "urllib.response.addinfourl"]


def _open(url: str, timeout: int):
    if not url.lower().startswith("https://"):
        raise UpdateError(f"Updates are only fetched over HTTPS (not “{url}”).")
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}-updater", "Cache-Control": "no-cache"})
    return urllib.request.urlopen(request, timeout=timeout)


def _url(base: str, name: str) -> str:
    return base.rstrip("/") + "/" + name


def _read(opener: Opener, url: str, limit: int = 1 << 20) -> bytes:
    try:
        with opener(url, TIMEOUT) as response:
            data = response.read(limit + 1)
    except UpdateError:
        raise
    except Exception as exc:  # (no network, 404, certificate problem…)
        raise UpdateError(f"PlanetPDF couldn't reach the update server ({_reason(exc)}).") from exc
    if len(data) > limit:
        raise UpdateError("The update information on the server is far too large.")
    return data


def _reason(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    code = getattr(exc, "code", None)
    return f"HTTP {code}" if code else str(reason or exc)


def check(base_url: str, current: str, *, key=None, opener: Opener = _open) -> SignedRelease | None:
    """The newer release on the server, or None when this is the latest."""
    if not base_url.strip():
        raise UpdateError("No update server address is set.")
    manifest = _read(opener, _url(base_url, MANIFEST))
    signature = _read(opener, _url(base_url, SIGNATURE), 4096)
    release = verify(manifest, signature, key or public_key())
    if not is_newer(release.version, current):
        return None
    return SignedRelease(release, manifest, signature)


def download(base_url: str, signed: SignedRelease, folder: Path, progress=None, *,
             opener: Opener = _open) -> Path:
    """The release's zip in ``folder``, checked against the signed size and SHA-256.
    ``progress(done, total)`` counts bytes."""
    release = signed.release
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.file
    if target.is_file() and _matches(target, release):
        report(progress, release.size, release.size)
        return target  # (downloaded already, e.g. by the automatic check)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(_url(base_url, release.file), TIMEOUT) as response, open(part, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > release.size:
                    raise UpdateError("The download is larger than the update information says: it was thrown away.")
                digest.update(chunk)
                out.write(chunk)
                if not report(progress, done, release.size):
                    raise UpdateCanceled()
    except (UpdateError, UpdateCanceled):
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download didn't finish ({_reason(exc)}).") from exc
    if done != release.size or digest.hexdigest() != release.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded update doesn't match its checksum: it was thrown away. Try again later.")
    os.replace(part, target)
    (folder / MANIFEST).write_bytes(signed.manifest)
    (folder / SIGNATURE).write_bytes(signed.signature)
    return target


class UpdateCanceled(Exception):
    pass


def _matches(path: Path, release: Release) -> bool:
    if path.stat().st_size != release.size:
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest() == release.sha256


def downloaded(folder: Path, current: str, *, key=None) -> tuple[SignedRelease, Path] | None:
    """An update downloaded earlier and still good (signed, newer, checksum), else None."""
    try:
        manifest = (folder / MANIFEST).read_bytes()
        signature = (folder / SIGNATURE).read_bytes()
        release = verify(manifest, signature, key or public_key())
    except (OSError, UpdateError):
        return None
    path = folder / release.file
    if not is_newer(release.version, current) or not path.is_file() or not _matches(path, release):
        return None
    return SignedRelease(release, manifest, signature), path


# --- installing -----------------------------------------------------------------------------------

def install_root(executable: Path | None = None) -> Path | None:
    """The folder holding ``versions/`` when this copy runs from one, the Mac app bundle
    when it runs from one, else None (a plain build or the source code: nothing to update
    in place)."""
    exe = Path(executable or sys.executable).resolve()
    version_dir = exe.parent
    kind = package_kind(version_dir) if (getattr(sys, "frozen", False) or executable is not None) else None
    if kind == "appimage":
        image = os.environ.get("APPIMAGE", "")
        return Path(image) if image and Path(image).is_file() else None
    if kind in ("deb", "rpm"):
        return version_dir  # (/opt/planetpdf)
    if getattr(sys, "frozen", False) or executable is not None:
        if version_dir.parent.name == VERSIONS:
            return version_dir.parent.parent
        bundle = version_dir.parent.parent  # PlanetPDF.app/Contents/MacOS/PlanetPDF
        if version_dir.name == "MacOS" and version_dir.parent.name == "Contents" and bundle.suffix == ".app":
            return bundle
    return None


def is_app_bundle(root: Path) -> bool:
    return root.suffix == ".app"


def is_appimage(root: Path) -> bool:
    return root.suffix.lower() == ".appimage" or (root.is_file() and root.name != EXE_NAME)


def linux_package(root: Path) -> str | None:
    """"deb" / "rpm" when ``root`` is a Linux package's program folder."""
    kind = package_kind(root) if root.is_dir() else None
    return kind if kind in ("deb", "rpm") else None


def bundle_version(app: Path) -> str | None:
    """CFBundleShortVersionString from the app's Info.plist."""
    import plistlib

    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            return str(plistlib.load(f).get("CFBundleShortVersionString") or "") or None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def needs_admin(root: Path) -> bool:
    """Mac / AppImage: replacing it needs an administrator when its folder isn't writable. A Linux
    package always does (the package manager installs it)."""
    if linux_package(root):
        return True
    return not (os.access(root.parent, os.W_OK) and os.access(root, os.W_OK))


def configured_url(root: Path | None, default: str) -> str:
    """``PLANETPDF_UPDATE_URL`` (tests set it empty: no test may reach the real server),
    then ``update.json`` next to the launcher (set by IT), then the built-in address."""
    if URL_ENV_VAR in os.environ:
        return os.environ[URL_ENV_VAR].strip()
    if root is not None:
        try:
            data = json.loads((root / CONFIG_FILE).read_text(encoding="utf-8"))
            if isinstance(data, dict) and str(data.get("url", "")).strip():
                return str(data["url"]).strip()
        except (OSError, ValueError):
            pass
    return default


def current_version(root: Path) -> str | None:
    if is_app_bundle(root):
        return bundle_version(root)
    if is_appimage(root) or linux_package(root):
        from planetpdf import __version__

        return __version__
    try:
        text = (root / CURRENT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text and (root / VERSIONS / text / EXE_NAME).is_file():
        return text
    found = [d.name for d in _version_dirs(root)]
    return max(found, key=parse_version) if found else None


def _version_dirs(root: Path) -> Iterator[Path]:
    folder = root / VERSIONS
    if folder.is_dir():
        for d in folder.iterdir():
            if d.is_dir() and re.fullmatch(r"\d+(\.\d+)*", d.name) and (d / EXE_NAME).is_file():
                yield d


def apply(root: Path, zip_path: Path, manifest: bytes, signature: bytes, *, key=None, progress=None) -> str:
    """Install the update: checked again here (the helper trusts nothing it's handed),
    unpacked into ``versions/<version>``, made current; old versions nobody uses removed.
    Returns the version.

    Security review 2026-11-21: the download sits in the user's own folder, which other programs
    running as the user can change, so it is first copied somewhere only this step (as
    administrator) can write, and that copy is checked and installed (no swapping the file between
    the check and the unpacking). And an update never takes PlanetPDF back to an older version (a
    genuinely signed old release could bring back a problem fixed since)."""
    release = verify(manifest, signature, key or public_key())
    installed = current_version(root)
    if installed and is_newer(installed, release.version):
        raise UpdateError(f"PlanetPDF {installed} is installed and this update is the older version "
                          f"{release.version}, so it wasn't installed: updates never go back to an older version.")
    folder = _private_folder(root)
    try:
        private = folder / Path(release.file).name
        shutil.copyfile(zip_path, private)
        if not _matches(private, release):
            raise UpdateError("The downloaded update doesn't match its checksum, so it wasn't installed.")
        return _apply_checked(root, private, release, progress)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


INCOMING = ".incoming"  # (Windows: the update's private copy, inside versions/)


def _private_folder(root: Path) -> Path:
    """An empty folder only this process's user can write: inside the installed program's own
    folder on Windows (Program Files: administrators only), else a new private temporary folder
    (0700, owned by whoever runs this step: root when it runs as administrator)."""
    import tempfile

    if sys.platform == "win32" and not (is_app_bundle(root) or is_appimage(root) or linux_package(root)):
        folder = root / VERSIONS / INCOMING
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        return folder
    return Path(tempfile.mkdtemp(prefix="planetpdf-update-"))


def _apply_checked(root: Path, zip_path: Path, release: Release, progress=None) -> str:
    if is_app_bundle(root):
        return _apply_app(root, zip_path, release, progress)
    if is_appimage(root):
        return _apply_appimage(root, zip_path, release, progress)
    if linux_package(root):
        return _apply_package(linux_package(root), zip_path, release, progress)
    versions = root / VERSIONS
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / release.version
    if not (target / EXE_NAME).is_file():
        staging = versions / f"{release.version}.partial"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            for n, member in enumerate(members):
                destination = (staging / member.filename).resolve()
                if not destination.is_relative_to(staging.resolve()):
                    raise UpdateError(f"The update holds a file outside its folder ({member.filename}).")
                archive.extract(member, staging)
                report(progress, n + 1, len(members))
        if not (staging / EXE_NAME).is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise UpdateError(f"The update has no {EXE_NAME}, so it wasn't installed.")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    tmp = root / (CURRENT_FILE + ".new")
    tmp.write_text(release.version + "\n", encoding="utf-8")
    os.replace(tmp, root / CURRENT_FILE)
    remove_old_versions(root, keep={release.version})
    return release.version


def remove_old_versions(root: Path, keep: set[str]) -> list[str]:
    """Delete version folders other than ``keep`` that nobody is running (a folder whose
    program is running can't be renamed on Windows: it's left for next time)."""
    removed = []
    for folder in list(_version_dirs(root)):
        if folder.name in keep:
            continue
        doomed = folder.with_name(folder.name + ".old")
        try:
            os.replace(folder, doomed)
        except OSError:
            continue  # (in use)
        shutil.rmtree(doomed, ignore_errors=True)
        removed.append(folder.name)
    for leftover in (root / VERSIONS).glob("*.old"):
        shutil.rmtree(leftover, ignore_errors=True)
    return removed


def _apply_app(app: Path, zip_path: Path, release: Release, progress=None) -> str:
    """Mac: unpack the new PlanetPDF.app beside the installed one, check it, swap it in."""
    import subprocess

    staging = app.parent / f".{app.stem}-update-{release.version}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        report(progress, 0, 3, "Unpacking")
        unpacked = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(staging)], capture_output=True, text=True)
        if unpacked.returncode != 0:
            raise UpdateError(f"The update couldn't be unpacked ({unpacked.stderr.strip() or unpacked.returncode}).")
        new = staging / APP_BUNDLE
        if not (new / "Contents" / "MacOS" / EXE_NAME).is_file():
            raise UpdateError(f"The update has no {APP_BUNDLE}, so it wasn't installed.")
        report(progress, 1, 3, "Checking the app's signature")
        team = _team_id(app)
        checked = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(new)], capture_output=True, text=True)
        if checked.returncode != 0:
            raise UpdateError(f"The new app's code signature isn't valid ({checked.stderr.strip()}), so it wasn't installed.")
        if team and _team_id(new) != team:
            raise UpdateError("The new app is signed by someone else, so it wasn't installed.")
        report(progress, 2, 3, "Replacing the app")
        old = app.with_name(f".{app.stem}-old.app")
        shutil.rmtree(old, ignore_errors=True)
        os.replace(app, old)
        try:
            os.replace(new, app)
        except OSError:
            os.replace(old, app)  # (put the old one back)
            raise
        shutil.rmtree(old, ignore_errors=True)
        report(progress, 3, 3, "Replacing the app")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return release.version


def _apply_appimage(image: Path, new: Path, release: Release, progress=None) -> str:
    """Linux AppImage: the new one takes the old one's place (same name, so shortcuts keep
    working); the running copy keeps the old file open until it ends."""
    report(progress, 0, 1, "Replacing the AppImage")
    staging = image.with_name(f".{image.name}.update")
    shutil.copyfile(new, staging)
    staging.chmod(0o755)
    os.replace(staging, image)
    report(progress, 1, 1, "Replacing the AppImage")
    return release.version


PACKAGE_COMMANDS = {"deb": ["dpkg", "-i"], "rpm": ["rpm", "-U", "--replacepkgs"]}


def _apply_package(kind: str, package: Path, release: Release, progress=None) -> str:
    """Linux .deb / .rpm: installed by the system's package tool (the installing step runs as root)."""
    import subprocess

    report(progress, 0, 1, "Installing the package")
    command = [*PACKAGE_COMMANDS[kind], str(package)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise UpdateError(f"{command[0]} couldn't install it ({detail[-1] if detail else done.returncode}).")
    report(progress, 1, 1, "Installing the package")
    return release.version


def _team_id(app: Path) -> str | None:
    """The Apple Developer Team ID that signed the app (None: ad-hoc or unsigned)."""
    import subprocess

    info = subprocess.run(["codesign", "-dv", str(app)], capture_output=True, text=True)
    match = re.search(r"^TeamIdentifier=(\S+)$", info.stderr, re.MULTILINE)
    return match.group(1) if match and match.group(1) != "not" else None
