"""The update's installing step starts from a clean environment (security list 2026-11-26, item 10).

That step runs as administrator (``--apply-update``, after Windows' prompt) or as SYSTEM
(``--install-updates``), but its environment variables come from the user: a program running as
that user can set them (for good, under the user's own registry key) and wait for an update. Some
variables tell the libraries PlanetPDF carries what to load, as whoever runs them:
``OPENSSL_CONF`` / ``OPENSSL_MODULES`` make the encryption library read a settings file and load
program files of the caller's choosing; ``PATH``, ``SystemRoot`` and ``windir`` say where Windows
looks for others.

``scrub(argv)`` takes them out, and puts Windows' own folders back in ``PATH``, when the program
was started as one of those steps. It must run before the first library that reads them is
loaded, so it is called first thing: by a PyInstaller run-time hook in the built program
(``packaging/rthook_clean_environment.py``), and again at the top of ``ui/app.main``. It imports
nothing but ``os`` and ``sys`` (and ``ctypes`` on Windows), and loads none of those libraries.

The Mac's and Linux's administrator prompts (``osascript``, ``pkexec``) already start the step
with an environment of their own; the same variables are removed there all the same.
"""

from __future__ import annotations

import os
import sys

# (core/update_helper.py's APPLY, INSTALL, and REFRESH: a test keeps them the same. Not --finish-update: it
# runs as the user and starts the new PlanetPDF, which should get the user's environment.)
HELPER_FLAGS = ("--apply-update", "--install-updates", "--refresh-install")

# what the libraries in the program read to decide which files to load or trust
REMOVED = (
    "OPENSSL_CONF", "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "OPENSSL_ENGINES", "OPENSSL_ia32cap",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH",
    "TESSDATA_PREFIX", "GS_LIB", "GS_OPTIONS", "MAGICK_CONFIGURE_PATH",
)
REMOVED_PREFIXES = ("PYTHON", "PLANETPDF_")
KEPT = ("PLANETPDF_UPDATE_URL",)  # (set for the whole computer by IT, for the unattended update)
_REMOVED = frozenset(name.upper() for name in REMOVED)


def is_helper(argv: list[str]) -> bool:
    return any(flag in argv for flag in HELPER_FLAGS)


def scrub(argv: list[str] | None = None, environ=None) -> list[str]:
    """Clean the environment when this process is one of the update's installing steps. Returns the
    names it removed or reset (for the log). Never raises."""
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    if not is_helper(argv):
        return []
    changed = []
    for name in list(environ):
        upper = name.upper()
        if upper in KEPT:
            continue
        if upper in _REMOVED or upper.startswith(REMOVED_PREFIXES):
            environ.pop(name, None)
            changed.append(name)
    if sys.platform == "win32":
        try:
            from planetpdf.core import windows_elevate as E

            system, windows = E.system_dir(), E.windows_dir()
        except Exception:  # noqa: BLE001 (nothing here may stop the update)
            return changed
        fresh = {
            "PATH": os.pathsep.join([system, windows, os.path.join(system, "Wbem"),
                                     os.path.join(system, "WindowsPowerShell", "v1.0")]),
            "SystemRoot": windows, "windir": windows, "ComSpec": os.path.join(system, "cmd.exe"),
        }
        for name, value in fresh.items():
            if environ.get(name) != value:
                environ[name] = value
                changed.append(name)
    return changed
