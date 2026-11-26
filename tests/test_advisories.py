"""The advisory check (scripts/check_advisories.py; security list 2026-11-26, item 9): every pinned
part is listed and asked about. Nothing here goes online: the databases' answers are stand-ins."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_advisories", ROOT / "scripts" / "check_advisories.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_runtime_pin_and_the_program_starter_are_listed(check):
    packages = check.pinned_packages()
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.MULTILINE)
    assert set(pins) <= set(packages) and len(pins) > 40
    for name in ("PyMuPDF", "pypdf", "pillow", "cryptography", "pyHanko", "fonttools", "pdf2docx", "pyinstaller"):
        assert any(p.lower() == name.lower() for p in packages), name  # (what reads documents, and the starter)
    assert "pytest" not in {p.lower() for p in packages}  # (a build tool that isn't in the program)
    names = {part["name"] for part in check.native_parts()}
    assert {"Python", "OpenSSL", "MuPDF", "Ghostscript"} <= names  # (parts that aren't Python packages)
    assert all(part["version"] for part in check.native_parts() if part["name"] in ("Python", "MuPDF", "Ghostscript"))


def test_a_known_problem_is_reported_once_with_the_version_that_fixes_it(check, capsys):
    asked = []

    def ask(url, body=None):
        asked.append(url)
        if url == check.OSV_BATCH:
            assert {"package": {"name": "pypdf", "ecosystem": "PyPI"}, "version": "1.0"} in body["queries"]
            return {"results": [{"vulns": [{"id": "GHSA-aaaa"}, {"id": "PYSEC-1"}]} if q["package"]["name"] == "pypdf"
                                else {} for q in body["queries"]]}
        if url.startswith(check.OSV_VULN):  # (the same problem, as GitHub and as PyPI list it)
            return {"summary": "Endless loop on a broken file", "aliases": ["CVE-2026-1"],
                    "affected": [{"package": {"name": "pypdf"}, "ranges": [{"events": [{"introduced": "0"},
                                                                                         {"fixed": "1.1"}]}]}]}
        assert "isVulnerable" in url and "cpeName=cpe:2.3:a:" in url  # (only the product's own problems)
        return {"vulnerabilities": [{"cve": {"id": "CVE-2026-9", "descriptions": [{"lang": "en", "value": "Bad"}]}}]
                if "ghostscript" in url else []}

    found = check.osv_findings({"pypdf": "1.0", "pillow": "2.0"}, ask)
    assert list(found) == ["pypdf"] and len(found["pypdf"]) == 1
    assert found["pypdf"][0] == {"id": "GHSA-aaaa (CVE-2026-1)", "summary": "Endless loop on a broken file",
                                 "fixed": "1.1"}
    gs = {"name": "Ghostscript", "version": "10.0", "cpe": "artifex:ghostscript", "page": "p", "where": ""}
    assert check.nvd_findings(gs, ask) == [{"id": "CVE-2026-9", "summary": "Bad", "fixed": ""}]
    assert check.nvd_findings({**gs, "cpe": "artifex:mupdf"}, ask) == []
    assert check.nvd_findings({**gs, "version": ""}, ask) is None  # (no version to ask about: by hand)

    def offline(url, body=None):
        raise OSError("no network")

    assert check.nvd_findings(gs, offline) is None  # (couldn't be asked: by hand, never "ok")


def test_the_report_fails_when_something_was_found_or_could_not_be_asked(check, monkeypatch, capsys):
    monkeypatch.setattr(check, "pinned_packages", lambda: {"pypdf": "1.0"})
    monkeypatch.setattr(check, "native_parts", lambda: [{"name": "Ghostscript", "version": "10.0",
                                                         "cpe": "artifex:ghostscript", "page": "page", "where": ""}])
    clean = check.report(ask=lambda url, body=None: {"results": [{}]} if body else {"vulnerabilities": []}, pause=0)
    assert not any(e["advisories"] for e in clean["packages"] + clean["parts"]) and clean["parts"][0]["checked"]

    def down(url, body=None):
        raise OSError("no network")

    broken = check.report(ask=down, pause=0)
    assert broken["errors"] and not broken["parts"][0]["checked"]
    check.show(broken)
    out = capsys.readouterr().out
    assert "by hand   Ghostscript 10.0: page" in out and "couldn't be checked" in out
    assert check.report(offline=True)["errors"] == []
    assert any("Ghostscript 10.0 couldn't be checked" in error for error in broken["errors"])
    monkeypatch.setattr(check, "report", lambda offline=False: broken)
    assert check.main([]) == 2  # (not being able to ask isn't "all clear")
    monkeypatch.setattr(check, "report", lambda offline=False: clean)
    assert check.main([]) == 0
    found = {**clean, "packages": [{"name": "pypdf", "version": "1.0",
                                    "advisories": [{"id": "CVE-1", "summary": "Bad", "fixed": "1.1"}]}]}
    monkeypatch.setattr(check, "report", lambda offline=False: found)
    assert check.main([]) == 1
    assert "ADVISORY  pypdf 1.0" in capsys.readouterr().out


def test_every_build_asks_first(check):
    """User 2026-11-26: builds take the pinned files, never "the latest"; so each build asks whether
    a pinned part has a published fix, and stops when one does."""
    build = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    assert "check_advisories.py" in build and "-SkipAdvisoryCheck" in build
    assert build.index("check_advisories.py") < build.index('Step 1 "')  # (before anything is built)
