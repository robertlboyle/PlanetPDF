"""Known security problems in the parts PlanetPDF is built from (security list 2026-11-26, item 9).

Everything a document can reach is read by libraries inside PlanetPDF (MuPDF, Ghostscript, Pillow,
OpenCV, fontTools, pdf2docx…), with the user's rights: a flaw in one of them is a flaw in
PlanetPDF. This lists every pinned part with its version and asks the public advisory databases
about each:

- the Python packages of ``requirements.txt`` (and PyInstaller, whose starter is in the program):
  **OSV** (osv.dev, which carries PyPI's and GitHub's advisories), exactly, by package and version;
- the parts that aren't Python packages (Python itself, the OpenSSL copies, MuPDF inside PyMuPDF,
  Qt, FFmpeg inside OpenCV, Ghostscript): the US **National Vulnerability Database**, by product
  and version. That match is rougher (it knows products, not our builds), so its findings are
  "to look at", and a part it can't answer for is listed with where to look by hand.

    python scripts/check_advisories.py            the report
    python scripts/check_advisories.py --json     the same for a script
    python scripts/check_advisories.py --offline  only the list of parts and versions

Exit code 0: nothing known. 1: something was found (a part to update). 2: nothing was found, but
a database couldn't be asked, so that isn't "all clear".

``scripts\build.ps1`` runs it before every build and stops on 1 or 2 (user 2026-11-26: "Do we pull
the latest up to date security fixed components every time we do a new build?" No: a build takes
exactly the pinned, checked files, never "the latest" by itself; this is what says when a pin has a
published fix, so it is raised on purpose, tested, and pinned again). Run it once a month between
builds too. It sends the databases nothing but package names and versions (all public: PlanetPDF's
source lists them); nothing about this computer or its user.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_PAUSE = 6.5  # (seconds between its answers: it allows 5 questions in 30 seconds without a key)
AGENT = "advisory-check/1.0"
TIMEOUT = 40
BUILD_TOOLS = ("pyinstaller",)  # (from requirements-dev.txt: part of the built program)


def pinned_packages() -> dict[str, str]:
    """name -> version from requirements.txt, and the build tools that end up in the program."""
    found: dict[str, str] = {}
    for file, wanted in (("requirements.txt", None), ("requirements-dev.txt", BUILD_TOOLS)):
        for line in (ROOT / file).read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line.strip())
            if match and (wanted is None or match.group(1).lower() in wanted):
                found.setdefault(match.group(1), match.group(2))
    return found


def native_parts() -> list[dict]:
    """The parts that aren't Python packages, each with the version in this environment, its name
    in the vulnerability database (CPE vendor:product), and its publisher's own page."""
    parts: list[dict] = []

    def add(name: str, version: str, cpe: str, page: str, where: str = "") -> None:
        parts.append({"name": name, "version": version, "cpe": cpe, "page": page, "where": where})

    add("Python", ".".join(map(str, sys.version_info[:3])), "python:python", "https://www.python.org/downloads/")
    try:
        import ssl

        version = re.search(r"\d+\.\d+\.\d+[a-z]?", ssl.OPENSSL_VERSION)
        add("OpenSSL", version.group(0) if version else "", "openssl:openssl",
            "https://openssl-library.org/news/vulnerabilities/", "in Python")
    except ImportError:
        pass
    try:
        from cryptography.hazmat.backends.openssl import backend

        version = re.search(r"\d+\.\d+\.\d+[a-z]?", backend.openssl_version_text())
        add("OpenSSL", version.group(0) if version else "", "openssl:openssl",
            "https://openssl-library.org/news/vulnerabilities/", "in cryptography")
    except Exception:  # noqa: BLE001 (not installed here, or a build without it)
        pass
    try:
        import pymupdf

        add("MuPDF", str(getattr(pymupdf, "mupdf_version", "") or pymupdf.VersionFitz), "artifex:mupdf",
            "https://mupdf.com/releases/history", "in PyMuPDF")
    except Exception:  # noqa: BLE001
        pass
    try:
        from PySide6.QtCore import qVersion

        add("Qt", qVersion(), "qt:qt", "https://www.qt.io/blog/tag/security", "in PySide6")
    except Exception:  # noqa: BLE001
        pass
    try:
        import cv2

        version = re.search(r"avcodec:\s+YES \(([\d.]+)\)", cv2.getBuildInformation())
        add("FFmpeg (libavcodec)", version.group(1) if version else "", "ffmpeg:ffmpeg",
            "https://ffmpeg.org/security.html", "in OpenCV; its own version numbering, so by hand")
    except Exception:  # noqa: BLE001
        pass
    build = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    version = re.search(r"gs\\gs(\d+\.\d+\.\d+)", build)
    add("Ghostscript", version.group(1) if version else "", "artifex:ghostscript",
        "https://ghostscript.com/releases/", "bundled beside the program")
    try:
        rustc = subprocess.run(["rustc", "--version"], capture_output=True, text=True, timeout=20).stdout
        version = re.search(r"\d+\.\d+\.\d+", rustc)
        add("Rust standard library", version.group(0) if version else "", "rust-lang:rust",
            "https://blog.rust-lang.org/", "in the launcher and the print service")
    except (OSError, subprocess.SubprocessError):
        pass
    return parts


def _ask(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def osv_findings(packages: dict[str, str], ask=_ask) -> dict[str, list[dict]]:
    """package -> its known advisories at the pinned version (id, summary, fixed in)."""
    names = list(packages)
    queries = [{"package": {"name": name, "ecosystem": "PyPI"}, "version": packages[name]} for name in names]
    answer = ask(OSV_BATCH, {"queries": queries})
    found: dict[str, list[dict]] = {}
    for name, result in zip(names, answer.get("results", [])):
        seen: dict[str, dict] = {}  # (one line per problem: the same one is listed by GitHub and by PyPI)
        for vuln in result.get("vulns", []) or []:
            detail = {"id": vuln.get("id", ""), "summary": "", "fixed": ""}
            key = detail["id"]
            try:
                full = ask(OSV_VULN + urllib.parse.quote(detail["id"]))
                detail["summary"] = (full.get("summary") or full.get("details", "")).strip().splitlines()[0][:200]
                aliases = sorted(a for a in full.get("aliases", []) if a.startswith("CVE-"))
                if aliases:
                    key = aliases[0]
                    detail["id"] += f" ({aliases[0]})"
                else:
                    key = min([detail["id"], *full.get("aliases", [])])
                fixed = sorted({event["fixed"] for affected in full.get("affected", [])
                                if affected.get("package", {}).get("name", "").lower() == name.lower()
                                for rng in affected.get("ranges", []) for event in rng.get("events", [])
                                if "fixed" in event})
                detail["fixed"] = ", ".join(fixed)
            except (urllib.error.URLError, OSError, ValueError, IndexError):
                pass
            if key not in seen or (seen[key]["id"].startswith("PYSEC") and not detail["id"].startswith("PYSEC")):
                seen[key] = detail
        if seen:
            found[name] = list(seen.values())
    return found


def nvd_findings(part: dict, ask=_ask) -> list[dict] | None:
    """What the vulnerability database lists for this product at this version (None: it couldn't
    answer for it, so look at the publisher's page)."""
    if not part["version"] or "by hand" in part["where"]:
        return None
    name = f"cpe:2.3:a:{part['cpe']}:{part['version']}:*:*:*:*:*:*:*"
    try:
        # isVulnerable: only problems of this product itself at this version, not every problem
        # of something else that merely runs with it (asked without it, Python brought back
        # other programs' problems from 2009)
        answer = ask(f"{NVD}?cpeName={urllib.parse.quote(name, safe=':*')}&isVulnerable")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    found = []
    for item in answer.get("vulnerabilities", []):
        cve = item.get("cve", {})
        text = next((d.get("value", "") for d in cve.get("descriptions", []) if d.get("lang") == "en"), "")
        found.append({"id": cve.get("id", ""), "summary": " ".join(text.split())[:200], "fixed": ""})
    return found


def report(offline: bool = False, ask=_ask, pause: float = NVD_PAUSE) -> dict:
    packages = pinned_packages()
    parts = native_parts()
    result = {"packages": [{"name": n, "version": v, "advisories": []} for n, v in packages.items()],
              "parts": [{**p, "advisories": [], "checked": False} for p in parts], "errors": []}
    if offline:
        return result
    try:
        findings = osv_findings(packages, ask)
        for entry in result["packages"]:
            entry["advisories"] = findings.get(entry["name"], [])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result["errors"].append(f"The Python packages couldn't be checked (osv.dev: {exc}).")
    for n, entry in enumerate(result["parts"]):
        if n and pause:
            time.sleep(pause)
        found = nvd_findings(entry, ask)
        entry["checked"] = found is not None
        entry["advisories"] = found or []
        if found is None and entry["version"] and "by hand" not in entry["where"]:
            result["errors"].append(f"{entry['name']} {entry['version']} couldn't be checked (the vulnerability "
                                    f"database didn't answer): {entry['page']}")
    return result


def show(result: dict, offline: bool = False) -> None:
    if offline:
        print(f"Python packages ({len(result['packages'])}; nothing was asked):")
    else:
        print(f"Python packages ({len(result['packages'])}, checked exactly against osv.dev):")
    for entry in result["packages"]:
        flag = "listed  " if offline else "ADVISORY" if entry["advisories"] else "ok      "
        print(f"  {flag}  {entry['name']} {entry['version']}")
        for adv in entry["advisories"]:
            print(f"              {adv['id']}: {adv['summary']}" + (f" Fixed in {adv['fixed']}." if adv["fixed"] else ""))
    print("\nOther parts (the vulnerability database, by product and version: to look at, not exact):")
    for entry in result["parts"]:
        where = f" ({entry['where']})" if entry["where"] else ""
        if not entry["checked"]:
            print(f"  by hand   {entry['name']} {entry['version']}{where}: {entry['page']}")
            continue
        flag = "LOOK AT " if entry["advisories"] else "ok      "
        print(f"  {flag}  {entry['name']} {entry['version']}{where}")
        for adv in entry["advisories"][:12]:
            print(f"              {adv['id']}: {adv['summary']}")
        if len(entry["advisories"]) > 12:
            print(f"              and {len(entry['advisories']) - 12} more: {entry['page']}")
    for error in result["errors"]:
        print(f"\n{error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="The report as JSON")
    parser.add_argument("--offline", action="store_true", help="Only list the parts and versions; ask nothing")
    args = parser.parse_args(argv)
    result = report(offline=args.offline)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        show(result, args.offline)
    found = any(e["advisories"] for e in result["packages"] + result["parts"])
    return 1 if found else 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
