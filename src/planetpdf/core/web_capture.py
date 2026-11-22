"""Create ▸ PDF from Web Page / Insert Pages ▸ From Web Page (Acrobat; backlog Phase D,
built 2026-10-02): the page printed to PDF by Microsoft Edge (or Chrome), running
headless and invisible: the page looks the way the browser shows it (scripts, fonts,
styles), with no new library. Found in the usual install folders or on the PATH.

**Don't wait for the browser to exit** (flaky-test hunt 2026-10-02): with a fresh
profile, headless Edge starts its built-in extensions, sync, the work-account sign-in
(Edge identity) and other background services; under load it often kept running long
after it had written the PDF, or hung for minutes when its network service crashed.
So those services are switched off (``QUIET_FLAGS``), and the capture is finished as
soon as the browser reports "<n> bytes written to file" and the file has that size;
then the browser and its child processes are stopped.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report
from planetpdf.core.storage.files import replace_with_retry

TIMEOUT_S = 120
EXIT_GRACE_S = 3  # after the PDF is written, how long the browser may take to close by itself
POLL_S = 0.2

# Background work a throwaway profile would otherwise start (none of it affects the page).
QUIET_FLAGS = ("--disable-extensions", "--disable-component-extensions-with-background-pages",
               "--disable-background-networking", "--disable-sync", "--disable-component-update",
               "--disable-default-apps", "--no-service-autorun", "--disable-breakpad", "--mute-audio")

_WRITTEN = re.compile(rb"(\d+) bytes written to file")


class WebCaptureCanceled(PdfError):
    pass


# On a Mac the browsers are inside their app bundles, not on the PATH (2026-10-14: Chrome was
# installed but "not found"); Brave and Chromium print the same way.
MAC_BROWSERS = ("Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "Google Chrome.app/Contents/MacOS/Google Chrome",
                "Chromium.app/Contents/MacOS/Chromium", "Brave Browser.app/Contents/MacOS/Brave Browser")


def find_browser() -> Path | None:
    configured = os.environ.get("PLANETPDF_BROWSER")
    if configured and Path(configured).is_file():
        return Path(configured)
    for base in filter(None, (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                              os.environ.get("LOCALAPPDATA"))):
        for relative in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            candidate = Path(base, relative)
            if candidate.is_file():
                return candidate
    for base in (Path("/Applications"), Path.home() / "Applications"):
        for relative in MAC_BROWSERS:
            if (base / relative).is_file():
                return base / relative
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def browser_name(browser: Path | None = None) -> str:
    browser = browser or find_browser()
    name = browser.name.lower() if browser else ""
    if "edge" in name:
        return "Microsoft Edge"
    if "chromium" in name:
        return "Chromium"
    if "chrom" in name:
        return "Google Chrome"
    if "brave" in name:
        return "Brave"
    return "the browser"


def normalize_address(text: str) -> str:
    """What the user typed: www.x.com -> https://www.x.com; a file path -> file:///…"""
    text = text.strip().strip('"')
    if not text:
        raise PdfError("Enter the address of the web page.")
    if Path(text).is_file():
        return Path(text).resolve().as_uri()
    scheme = urlparse(text).scheme.lower()
    if scheme in ("http", "https", "file"):
        return text
    if scheme and len(scheme) > 1:
        raise PdfError(f"PlanetPDF can only capture web pages (http, https) and files, not “{scheme}:” addresses.")
    return "https://" + text


def browser_args(browser: Path, profile: Path, out: Path, url: str) -> list[str]:
    return [str(browser), "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}",  # (a throwaway profile: the user's browser is untouched)
            *QUIET_FLAGS,
            "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=8000",  # (lets the page's scripts finish before printing)
            f"--print-to-pdf={out}", url]


# Windows' compatibility-layer setting: a program started with it inherits it, and Edge then
# quits at once (exit code 0, no PDF, not a word in its log) (found 2026-10-17: the Claude app
# that ran the tests had `__COMPAT_LAYER=DetectorsAppHealth`; PlanetPDF itself could be run that way
# by a compatibility setting or Windows' app-health checks).
DROPPED_VARIABLES = ("__COMPAT_LAYER",)


def browser_environment() -> dict[str, str]:
    """PlanetPDF's environment for the browser, without what makes it quit."""
    return {k: v for k, v in os.environ.items() if k.upper() not in DROPPED_VARIABLES}


def web_page_to_pdf(address: str, target: Path, *, timeout: float = TIMEOUT_S,
                    progress: Progress | None = None) -> Path:
    """Print ``address`` to ``target`` with the browser (no header / footer lines).
    ``progress`` hears the seconds waited so far; False from it cancels."""
    browser = find_browser()
    if browser is None:
        raise PdfError("This needs Microsoft Edge or Google Chrome, which wasn't found on this computer.")
    name = browser_name(browser)
    url = normalize_address(address)
    target = Path(target)
    work = Path(tempfile.mkdtemp(prefix="planetpdf-web-"))
    out, log = work / "page.pdf", work / "browser.log"
    try:
        # Output to a file, not pipes: the browser's child processes inherit its handles, and
        # reading a pipe to the end would wait for the last of them.
        with open(log, "wb") as messages:
            process = subprocess.Popen(browser_args(browser, work / "profile", out, url),
                                       stdin=subprocess.DEVNULL, stdout=messages, stderr=messages,
                                       env=browser_environment(), **_new_process_group())
        try:
            _wait_for_pdf(process, out, log, timeout, progress, name)
        finally:
            _stop(process)
        replace_with_retry(out, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def _wait_for_pdf(process: subprocess.Popen, out: Path, log: Path, timeout: float,
                  progress: Progress | None, name: str) -> None:
    start = time.monotonic()
    reported = -1
    while True:
        exited = process.poll() is not None
        if _pdf_written(out, log):
            if not exited:
                try:
                    process.wait(EXIT_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass  # (stopped by the caller)
            return
        if exited:
            if out.is_file() and out.stat().st_size >= 100:
                return  # (a browser that doesn't say "bytes written")
            code = f" ({name} stopped with code {process.returncode}.)" if process.returncode else ""
            raise PdfError("The web page couldn't be captured. Check the address and the network connection."
                           + code)
        waited = int(time.monotonic() - start)
        if waited >= timeout:
            raise PdfError(f"The web page took longer than {int(timeout)} seconds to load.")
        if waited != reported:
            reported = waited
            stage = f"Loading the page in {name}: {waited} of at most {int(timeout)} seconds…"
            if not report(progress, 0, 0, stage):
                raise WebCaptureCanceled("Canceled.")
        time.sleep(POLL_S)


def _pdf_written(out: Path, log: Path) -> bool:
    """The browser said "<n> bytes written to file" and the file has those n bytes."""
    try:
        match = _WRITTEN.search(log.read_bytes())
        return bool(match) and out.is_file() and out.stat().st_size == int(match.group(1))
    except OSError:
        return False


def _new_process_group() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen) -> None:
    """End the browser and every process it started (renderers, GPU, network service…)."""
    if sys.platform == "win32":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
