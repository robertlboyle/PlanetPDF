"""The update's installing step starts from a clean environment (security list 2026-11-26, item 10)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import planetpdf
from planetpdf.core import clean_environment as C
from planetpdf.core import update_helper as H

SRC = Path(planetpdf.__file__).parents[1]


def test_only_the_installing_steps_are_cleaned():
    # every step that runs as administrator or SYSTEM (not --finish-update: it starts the new PlanetPDF)
    assert set(C.HELPER_FLAGS) == {H.APPLY, H.INSTALL, H.REFRESH} == set(H.FLAGS) - {H.FINISH}
    env = {"OPENSSL_CONF": "x", "PATH": "user-folder"}
    assert C.scrub(["planetpdf", "file.pdf"], env) == [] and env == {"OPENSSL_CONF": "x", "PATH": "user-folder"}
    assert C.scrub(["planetpdf", H.FINISH, "a"], env) == []


def test_what_steers_the_libraries_is_taken_out():
    env = {"OPENSSL_CONF": "evil.cnf", "openssl_modules": "evil-folder", "SSL_CERT_FILE": "evil.pem",
           "PYTHONPATH": "evil", "PYTHONSTARTUP": "evil.py", "PLANETPDF_GHOSTSCRIPT": "evil.exe",
           "PLANETPDF_UPDATE_URL": "https://updates.example/", "HTTPS_PROXY": "http://proxy:8080", "USERNAME": "pat"}
    changed = C.scrub(["PlanetPDF.exe", H.APPLY, "root"], env)
    for name in ("OPENSSL_CONF", "openssl_modules", "SSL_CERT_FILE", "PYTHONPATH", "PYTHONSTARTUP",
                 "PLANETPDF_GHOSTSCRIPT"):
        assert name not in env and name in changed
    # IT's update address, the proxy, and everything ordinary stay
    assert env["PLANETPDF_UPDATE_URL"] and env["HTTPS_PROXY"] and env["USERNAME"] == "pat"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows: where it looks for programs and libraries")
def test_windows_own_folders_come_from_windows_not_from_the_environment():
    from planetpdf.core import windows_elevate as E

    system, windows = E.system_dir(), E.windows_dir()
    assert (Path(system) / "kernel32.dll").is_file() and Path(system).parent == Path(windows)
    env = {"PATH": r"C:\Users\pat\evil;C:\Windows\System32", "SystemRoot": r"C:\Users\pat\fake-windows",
           "windir": r"C:\Users\pat\fake-windows", "ComSpec": r"C:\Users\pat\cmd.exe"}
    changed = C.scrub(["PlanetPDF.exe", H.INSTALL], env)
    assert env["SystemRoot"] == windows and env["windir"] == windows
    assert env["PATH"].split(os.pathsep)[:2] == [system, windows] and "evil" not in env["PATH"]
    assert env["ComSpec"] == os.path.join(system, "cmd.exe")
    assert {"PATH", "SystemRoot", "windir", "ComSpec"} <= set(changed)


def test_cleaning_loads_nothing_that_reads_those_variables():
    """It must run before the encryption library is loaded: importing and running it loads neither
    that library's Python modules nor Qt."""
    code = ("import sys; from planetpdf.core.clean_environment import scrub; "
            f"scrub(['planetpdf', {H.APPLY!r}]); "
            "bad = sorted(m for m in sys.modules if m in ('ssl', '_ssl', 'hashlib', '_hashlib', 'hmac', "
            "'cryptography', 'secrets', 'random') or m.startswith('PySide6')); print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(SRC)!r}); " + code],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_program_cleans_before_it_does_anything_else():
    """``app.main`` cleans first, and the built program does it even earlier (a run-time hook that
    is first in the spec's list)."""
    source = (SRC / "planetpdf" / "ui" / "app.py").read_text(encoding="utf-8")
    body = source[source.index("def main("):]
    assert body.index("scrub(argv)") < body.index("PROBE_FLAG") < body.index("helper_main")
    spec = (SRC.parent / "packaging" / "planetpdf.spec").read_text(encoding="utf-8")
    assert "rthook_clean_environment.py" in spec
    hooks = spec[spec.index("runtime_hooks="):]
    assert hooks.index("rthook_clean_environment") < hooks.index("rthook_{name}")  # (before the Linux ones)
