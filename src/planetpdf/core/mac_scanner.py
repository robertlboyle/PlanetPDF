"""Scanning on the Mac through Apple's ImageCaptureCore (2026-10-14; user: "Can you work with any
USB scanner attached to the mac?").

ImageCaptureCore is how Image Capture and Preview scan: USB scanners (Apple's drivers or the
maker's), network scanners (AirScan) and ones another Mac shares. Python can't call it, so a
small Swift program does (``mac/planetpdf-scan.swift``; in the app it's
``Contents/MacOS/planetpdf-scan``, from source it's compiled into ``build/mac-helper`` with the
command-line tools' ``swiftc``). It answers in JSON lines; this module runs it and turns its
answers into what the scan window and ``ExtrasUi`` use (the same shapes as
``core/escl_scanner.py``, which Linux uses and which also reaches scanners by address).
"""

from __future__ import annotations

import json
import re
import selectors
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.escl_scanner import COLORS, Capabilities, ScanCanceled
from planetpdf.core.progress import report

HELPER = "planetpdf-scan"
SOURCE = Path(__file__).resolve().parents[3] / "mac" / f"{HELPER}.swift"  # (in the project)
BUILT = Path(__file__).resolve().parents[3] / "build" / "mac-helper" / HELPER
CHANGES = {"glass": "the glass", "feeder": "the feeder", "feeder-duplex": "the feeder"}


@dataclass(frozen=True)
class MacScanner:
    name: str
    id: str  # ImageCaptureCore's persistent ID
    connection: str = "network"  # "usb" / "network"

    @property
    def url(self) -> str:  # (the key the scan window and settings use)
        return f"ica:{self.id}"


def tidy_name(name: str) -> str:
    """ "Canon MF741C/743C (ac:6b:a7) (3) (ac:6b:a7) (3)  (ac:6b:a7) (4)" -> "Canon MF741C/743C (ac:6b:a7)":
    devices that clash on the network keep adding "(n)" to their Bonjour names."""
    first = re.match(r"^(.*?\([^)]*\))", name.strip())
    return first.group(1) if first else name.strip()


def helper(compile_if_needed: bool = True) -> Path:
    """The helper program: in the app beside PlanetPDF; from source, compiled when missing or
    older than its Swift source."""
    if getattr(sys, "frozen", False):
        built = Path(sys.executable).resolve().parent / HELPER
        if built.is_file():
            return built
        raise PdfError("PlanetPDF's scanner helper is missing from the app: reinstall PlanetPDF.")
    stale = not BUILT.is_file() or (SOURCE.is_file() and SOURCE.stat().st_mtime > BUILT.stat().st_mtime)
    if stale and compile_if_needed:
        swiftc = shutil.which("swiftc")
        if swiftc is None:
            raise PdfError("Scanning needs Apple's command-line tools to build its helper "
                           "(xcode-select --install).")
        BUILT.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([swiftc, "-O", "-swift-version", "5", "-o", str(BUILT), str(SOURCE),
                               "-framework", "ImageCaptureCore"], capture_output=True, text=True)
        if done.returncode != 0:
            raise PdfError(f"The scanner helper didn't build:\n{done.stderr[-800:]}")
    return BUILT


def _lines(args: list[str], timeout: float) -> list[dict]:
    try:
        done = subprocess.run([str(helper()), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PdfError("The scanner didn't answer in time.") from exc
    answers = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    for answer in answers:
        if "error" in answer:
            raise PdfError(answer["error"])
    return answers


def list_scanners(seconds: float = 4) -> list[MacScanner]:
    """Every scanner ImageCaptureCore sees within ``seconds``: USB, network, shared."""
    found = {}
    for answer in _lines(["list", "--seconds", str(seconds)], seconds + 20):
        s = answer.get("scanner") or {}
        if s.get("id"):
            found[s["id"]] = MacScanner(tidy_name(s.get("name", "Scanner")), s["id"], s.get("connection", "network"))
    labeled = [MacScanner(f"{s.name} (USB)" if s.connection == "usb" else s.name, s.id, s.connection)
               for s in found.values()]
    return sorted(labeled, key=lambda s: (s.connection != "usb", s.name.lower()))  # (USB first)


def capabilities(scanner: MacScanner) -> Capabilities:
    [answer] = [a for a in _lines(["caps", "--device", scanner.id], 75) if "caps" in a] or [{}]
    caps = Capabilities(make_model=scanner.name)
    for key, info in (answer.get("caps") or {}).items():
        unit = {"colors": list(COLORS), "dpis": sorted(info.get("dpis") or [300]), "formats": {"image/jpeg"},
                "max": tuple(int(v * 300) for v in info.get("max", (8.5, 11)))}
        caps.sources[key] = unit
        if key == "feeder" and info.get("duplex"):
            caps.sources["feeder-duplex"] = dict(unit)
    if not caps.sources:
        raise PdfError(f"The scanner “{scanner.name}” didn't say what it can do.")
    return caps


def scan(scanner: MacScanner, folder: Path, *, source: str = "glass", color: str = "color", dpi: int = 300,
         caps: Capabilities | None = None, progress=None, first_number: int = 1) -> list[Path]:
    """Scan into ``folder``: one page from the glass, every page in the feeder. Pages are named
    ``scan-NNN.jpg`` from ``first_number`` on. ``progress`` returning False cancels."""
    folder.mkdir(parents=True, exist_ok=True)
    incoming = folder / f"incoming-{first_number}"
    incoming.mkdir(exist_ok=True)
    process = subprocess.Popen([str(helper()), "scan", "--device", scanner.id, "--source", source, "--color", color,
                                "--dpi", str(dpi), "--out", str(incoming)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    pages: list[Path] = []
    error = ""
    with selectors.DefaultSelector() as waiting:
        waiting.register(process.stdout, selectors.EVENT_READ)
        while True:
            number = first_number + len(pages)
            if not report(progress, 0, 0, f"Scanning page {number} from {CHANGES[source]}…"):
                process.terminate()
                process.wait(10)
                raise ScanCanceled("Canceled.")
            if not waiting.select(timeout=0.5):
                if process.poll() is not None:
                    break
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            if "page" in answer:
                arrived = Path(answer["page"])
                page = folder / f"scan-{number:03d}{arrived.suffix.lower() or '.jpg'}"
                shutil.move(str(arrived), page)
                pages.append(page)
            elif "error" in answer:
                error = answer["error"]
            elif "done" in answer:
                break
    process.wait(30)
    shutil.rmtree(incoming, ignore_errors=True)
    if error and not pages:
        raise PdfError(error)
    if not pages:
        raise PdfError(f"The scanner “{scanner.name}” sent no pages.")
    return pages
