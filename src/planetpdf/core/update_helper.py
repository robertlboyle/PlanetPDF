"""The update helper: PlanetPDF's own program started with one of these flags (handled in
``ui/app.main`` before anything else loads; no Qt).

* ``--finish-update ROOT ZIP MANIFEST SIGNATURE --wait-pid PID [--restart]``: started by
  PlanetPDF as it closes (as the user). Waits for that PlanetPDF to end, runs the
  installing step as administrator (one UAC prompt), then, with ``--restart``, starts the
  new version (as the user, not as administrator).
* ``--apply-update ROOT ZIP MANIFEST SIGNATURE RESULT``: the installing step, as
  administrator. Checks the signature and checksum again itself, installs
  (``update.apply``) and writes what happened to RESULT (JSON), which PlanetPDF shows on
  its next start.
* ``--install-updates``: check, download and install with no questions, for IT (e.g. a
  scheduled task running as SYSTEM on a terminal server). Exit code 0 = up to date or
  updated.

On a Mac (2026-10-10) ROOT is the app bundle; the installing step asks for an
administrator's password (macOS' own prompt) only when the app's folder isn't writable,
and the new app is reopened with ``open``.

On Linux (2026-10-27) ROOT is /opt/planetpdf for a .deb / .rpm: the installing step runs as
root through ``pkexec`` (the desktop asks for an administrator's password) and installs the
package. An AppImage is replaced by PlanetPDF itself as it closes (``install_appimage``): the
helper would run from the AppImage's mount, which goes away with PlanetPDF.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from planetpdf import UPDATE_URL, __version__
from planetpdf.core import update as U

FINISH = "--finish-update"
APPLY = "--apply-update"
INSTALL = "--install-updates"
FLAGS = (FINISH, APPLY, INSTALL)
RESULT_FILE = "result.json"
LOG_FILE = "update.log"  # what the helper did, step by step, beside the downloaded update
STARTED_FILE = "installing"  # there while the installing step runs
INSTALL_TIMEOUT_SECONDS = 900  # (unpacking ~4,800 files: about half a minute)
WAIT_FOR_EXIT_MS = 120_000


def self_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "planetpdf", *args]


def finish_command(root: Path, zip_path: Path, folder: Path, pid: int, restart: bool) -> list[str]:
    command = self_command(FINISH, str(root), str(zip_path), str(folder / U.MANIFEST), str(folder / U.SIGNATURE),
                           "--wait-pid", str(pid))
    return command + (["--restart"] if restart else [])


def start_detached(command: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def log(where: Path, text: str) -> None:
    """One line in the update log (never fails: the log is only for finding out what happened).
    ``where``: the folder holding ``LOG_FILE``, or a ``.log`` file itself."""
    import time

    target = where if where.suffix == ".log" else where / LOG_FILE
    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {text}\n")
    except OSError:
        pass


def _wait_for_result(results, seconds: float, since: float = 0.0) -> dict | None:
    """The installing step's own result (in any of ``results``, written at ``since`` or later), once
    it has written it (or None after ``seconds``)."""
    import time

    results = [results] if isinstance(results, Path) else list(results)
    end = time.monotonic() + seconds
    while True:
        for result in results:
            try:
                if since and result.stat().st_mtime < since:
                    continue  # (left from before)
                data = json.loads(result.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                pass
        if time.monotonic() >= end:
            return None
        time.sleep(0.5)


def _write_result(path: Path, ok: bool, text: str, version: str = "", *, manual: bool = False,
                  target: str = "") -> None:
    """What the install did, for PlanetPDF's next start (``manual``: it offers the download page;
    ``target``: the version a failed install was for)."""
    try:
        path.write_text(json.dumps({"ok": ok, "message": text, "version": version, "manual": manual,
                                    "target": target}), encoding="utf-8")
    except OSError:
        pass


def _target(manifest: Path) -> str:
    """The version an update's manifest offers ("" when it can't be read)."""
    try:
        return str(json.loads(manifest.read_bytes()).get("version", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def read_result(folder: Path, running: str = "", installed_at: float = 0.0) -> dict | None:
    """What the last install did (and forget it), for PlanetPDF's next start. A failure is
    dropped once ``running`` (the version now running) is that update's version or newer: it was
    installed another way since (user 2026-10-27: after installing 0.1.14 with Setup, 0.1.14 said
    an older attempt "wasn't installed"). Older updaters didn't say which version they tried: their
    failure is dropped when it is older than the running program (``installed_at``)."""
    path = folder / RESULT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        written = path.stat().st_mtime
        path.unlink()
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("ok") and running:
        target = str(data.get("target") or "")
        try:
            if target and not U.is_newer(target, running):
                return None
        except U.UpdateError:
            pass
        if not target and installed_at and written < installed_at:
            return None
    return data


def helper_main(argv: list[str]) -> int:
    try:
        if FINISH in argv:
            return _finish(argv[argv.index(FINISH) + 1 :])
        if APPLY in argv:
            return _apply(argv[argv.index(APPLY) + 1 :])
        return _install_updates()
    except IndexError:
        return 2


STATUS_FOLDER = "update-status"


def status_folder(root: Path) -> Path:
    """Where the installing step writes what happened when it runs as administrator: a folder only
    administrators can change (security review 2026-11-21: writing into the user's own folder as
    administrator let a program running as the user turn those writes, through a link, onto any
    file). Windows / Linux packages: inside the installed program's folder; Mac: /Library."""
    if U.is_app_bundle(root):
        return Path("/Library/Application Support/PlanetPDF") / STATUS_FOLDER
    if U.is_appimage(root):
        return root.parent / f".planetpdf-{STATUS_FOLDER}"
    return root / STATUS_FOLDER


def status_files(root: Path, result: Path) -> tuple[Path, Path, Path]:
    """(result, log, marker) of an installing step run as administrator for this ``result``."""
    import hashlib

    name = hashlib.sha256(os.path.normcase(str(result)).encode("utf-8")).hexdigest()[:16]
    folder = status_folder(root)
    return folder / f"{name}.json", folder / f"{name}.log", folder / f"{name}.installing"


def _apply(args: list[str]) -> int:
    root, zip_path, manifest, signature, result = (Path(a) for a in args[:5])
    if _is_admin():  # (never write into the user's folder as administrator)
        result, log_to, started = status_files(root, result)
        try:
            result.parent.mkdir(parents=True, exist_ok=True)
            for old in (result, log_to):
                old.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        log_to, started = result.parent, result.parent / STARTED_FILE
    log(log_to, f"installing step started (administrator: {_is_admin()})")
    try:
        started.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        return _install(root, zip_path, manifest, signature, result, log_to)
    finally:
        try:
            started.unlink()
        except OSError:
            pass


def _install(root: Path, zip_path: Path, manifest: Path, signature: Path, result: Path,
             log_to: Path | None = None) -> int:
    log_to = log_to or result.parent
    try:
        version = U.apply(root, zip_path, manifest.read_bytes(), signature.read_bytes())
    except (U.UpdateError, OSError) as exc:
        log(log_to, f"installing step failed: {exc}")
        _write_result(result, False, f"The update couldn't be installed: {exc}", target=_target(manifest))
        return 1
    log(log_to, f"installing step done: version {version}")
    _write_result(result, True, f"PlanetPDF was updated to version {version}.", version)
    return 0


def _collect(root: Path, result: Path, since: float) -> None:
    """The administrator step's result and log, copied into the user's folder (as the user)."""
    status, status_log, _marker = status_files(root, result)
    try:
        if status.stat().st_mtime >= since and not result.exists():
            result.write_text(status.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    try:
        if status_log.stat().st_mtime >= since:
            with (result.parent / LOG_FILE).open("a", encoding="utf-8") as f:
                f.write(status_log.read_text(encoding="utf-8"))
    except OSError:
        pass


def _is_admin() -> bool:
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    from planetpdf.core import windows_elevate as E

    return E.is_admin()


def _finish(args: list[str]) -> int:
    import time

    root, zip_path, manifest, signature = (Path(a) for a in args[:4])
    pid = int(args[args.index("--wait-pid") + 1]) if "--wait-pid" in args else 0
    restart = "--restart" in args
    result = zip_path.parent / RESULT_FILE
    folder = zip_path.parent
    try:  # (a result left from before must not pass for this one's)
        result.unlink()
    except OSError:
        pass
    since = time.time() - 2
    results = (result, status_files(root, result)[0])
    apply_command = self_command(APPLY, str(root), str(zip_path), str(manifest), str(signature), str(result))
    log(folder, f"finishing: {zip_path.name}, restart {restart}")
    if sys.platform == "win32":
        from planetpdf.core import windows_elevate as E

        if pid:
            E.wait_for_process(pid, WAIT_FOR_EXIT_MS)
        try:
            code = subprocess.call(apply_command) if E.is_admin() else E.run_elevated(apply_command)
            log(folder, f"installing step ended: exit code {code}")
            if code == -1:  # (started, with nothing to wait on: its result says how it went)
                done = _wait_for_result(results, INSTALL_TIMEOUT_SECONDS, since)
                code = 0 if done and done.get("ok") else 1
        except E.ElevationCanceled:
            # Windows also says "canceled" after its own prompt failed: the installing step may
            # have run all the same, and its result is the truth (user 2026-10-26)
            log(folder, "Windows reported the administrator prompt as canceled")
            code = _settle(root, result, since, "The update wasn't installed: Windows' administrator prompt was "
                           "canceled. PlanetPDF will offer it again.", manual=False, target=_target(manifest))
        except OSError as exc:
            log(folder, f"Windows couldn't start the installing step as administrator: {exc}")
            code = _settle(root, result, since,
                           f"Windows couldn't start the update's installer as administrator ({exc}). The update "
                           "is downloaded; you can also install it with the full installer from the download page.",
                           manual=True, target=_target(manifest))
    elif sys.platform == "darwin":
        if pid:
            _wait_for_exit(pid, WAIT_FOR_EXIT_MS)
        code = _run_as_admin_mac(apply_command, result) if U.needs_admin(root) else subprocess.call(apply_command)
    else:
        if pid:
            _wait_for_exit(pid, WAIT_FOR_EXIT_MS)
        code = _run_as_admin_linux(apply_command, result) if U.needs_admin(root) else subprocess.call(apply_command)
    _collect(root, result, since)
    if restart and U.is_app_bundle(root):
        start_detached(["/usr/bin/open", str(root)])
    elif restart:
        launcher = root / U.EXE_NAME
        version = U.current_version(root)
        program = launcher if launcher.is_file() else (root / U.VERSIONS / str(version) / U.EXE_NAME)
        if program.is_file():
            start_detached([str(program)])
    return code


def _settle(root: Path, result: Path, since: float, message: str, *, manual: bool, target: str = "") -> int:
    """After Windows said the installing step didn't start: if it ran after all (its marker or
    its result shows up, in the user's folder or the administrators' status folder), its own result
    stands; otherwise ``message`` (``manual``: PlanetPDF offers the download page)."""
    import time

    from planetpdf.core import windows_elevate as E

    status, _log, status_marker = status_files(root, result)
    results, markers = (result, status), (result.parent / STARTED_FILE, status_marker)

    def signs() -> bool:
        for path in (*results, *markers):
            try:
                if path.stat().st_mtime >= since:
                    return True
            except OSError:
                pass
        return False

    end = time.monotonic() + E.START_GRACE_SECONDS
    while time.monotonic() < end and not signs():
        time.sleep(0.5)
    done = _wait_for_result(results, INSTALL_TIMEOUT_SECONDS, since) if signs() else None
    if done is not None:
        log(result.parent, f"the installing step ran after all: {done.get('message', '')}")
        return 0 if done.get("ok") else 1
    _write_result(result, False, message, manual=manual, target=target)
    return 1


def _wait_for_exit(pid: int, timeout_ms: int) -> None:
    import time

    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass  # (alive, someone else's)
        time.sleep(0.2)


def admin_script(command: list[str]) -> str:
    """AppleScript that runs ``command`` as administrator (macOS asks for the password)."""
    shell = " ".join(shlex.quote(part) for part in command)
    quoted = shell.replace("\\", "\\\\").replace('"', '\\"')
    return (f'do shell script "{quoted}" with administrator privileges '
            'with prompt "PlanetPDF needs an administrator to install its update."')


def _run_as_admin_mac(command: list[str], result: Path) -> int:
    done = subprocess.run(["/usr/bin/osascript", "-e", admin_script(command)], capture_output=True, text=True)
    if done.returncode != 0 and "-128" in done.stderr:  # (User canceled.)
        _write_result(result, False, "The update wasn't installed: the administrator password was canceled. "
                                     "PlanetPDF will offer it again.")
    elif done.returncode != 0 and not result.exists():
        _write_result(result, False, f"The update couldn't be installed as administrator: {done.stderr.strip()}")
    return done.returncode


PKEXEC = "pkexec"
PKEXEC_CANCELED = 126  # (pkexec: the user dismissed the password window)
PKEXEC_NOT_ALLOWED = 127


def _run_as_admin_linux(command: list[str], result: Path) -> int:
    """The installing step as root: pkexec asks for an administrator's password in the desktop's
    own window."""
    import shutil

    pkexec = shutil.which(PKEXEC)
    if pkexec is None:
        _write_result(result, False, "The update couldn't be installed: this computer has no pkexec to ask for an "
                                     "administrator's password. Install the new package from the download page.",
                      manual=True)
        return 1
    log(result.parent, "asking for an administrator's password (pkexec)")
    code = subprocess.call([pkexec, *command])
    if code in (PKEXEC_CANCELED, PKEXEC_NOT_ALLOWED) and not result.exists():
        _write_result(result, False, "The update wasn't installed: the administrator password was canceled. "
                                     "PlanetPDF will offer it again.")
    elif code != 0 and not result.exists():
        _write_result(result, False, f"The update couldn't be installed as administrator (pkexec: {code}).")
    return code


APPIMAGE_INSTALL = ('set -e; d=$(mktemp -d); trap \'rm -rf "$d"\' EXIT; cp -- "$1" "$d/new"; '
                    'echo "$2  $d/new" | sha256sum -c --status; install -m 755 -- "$d/new" "$3"')


def appimage_install_command(new: Path, sha256: str, image: Path) -> list[str]:
    """As root: copy the new AppImage somewhere private, check its checksum there, then install that
    copy (security review 2026-11-21: checking the user's file and then installing it by name let
    it be swapped in between)."""
    if not all(c in "0123456789abcdef" for c in sha256) or len(sha256) != 64:
        raise U.UpdateError("The update's checksum isn't readable.")
    return ["/bin/sh", "-c", APPIMAGE_INSTALL, "planetpdf-update", str(new), sha256, str(image)]


def install_appimage(root: Path, zip_path: Path, folder: Path, restart: bool) -> int:
    """An AppImage's update, done by PlanetPDF itself as it closes: checked, then the new AppImage
    takes the old one's place (pkexec only when its folder isn't the user's); with ``restart`` the
    new one is started."""
    result = folder / RESULT_FILE
    manifest, signature = folder / U.MANIFEST, folder / U.SIGNATURE
    log(folder, f"replacing the AppImage {root}")
    if U.needs_admin(root):
        try:
            release = U.verify(manifest.read_bytes(), signature.read_bytes(), U.public_key())
            if not U._matches(zip_path, release):
                raise U.UpdateError("The downloaded update doesn't match its checksum, so it wasn't installed.")
        except (U.UpdateError, OSError) as exc:
            _write_result(result, False, f"The update couldn't be installed: {exc}")
            return 1
        code = _run_as_admin_linux(appimage_install_command(zip_path, release.sha256, root), result)
        if code == 0:
            _write_result(result, True, f"PlanetPDF was updated to version {release.version}.", release.version)
    else:
        code = _install(root, zip_path, manifest, signature, result)
    if code == 0 and restart:
        start_detached([str(root)])
    return code


def _install_updates() -> int:
    root = U.install_root()
    if root is None:
        print("This copy of PlanetPDF isn't installed in a versions folder: nothing to update.")
        return 2
    current = U.current_version(root) or __version__
    url = U.configured_url(root, UPDATE_URL)
    try:
        signed = U.check(url, current)
        if signed is None:
            print(f"PlanetPDF {current} is the latest version.")
            return 0
        with tempfile.TemporaryDirectory(prefix="planetpdf-update-") as tmp:
            zip_path = U.download(url, signed, Path(tmp))
            version = U.apply(root, zip_path, signed.manifest, signed.signature)
    except (U.UpdateError, OSError) as exc:
        print(f"Update failed: {exc}")
        return 1
    print(f"PlanetPDF was updated to version {version}.")
    return 0
