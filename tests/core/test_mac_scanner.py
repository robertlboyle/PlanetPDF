"""Scanning through ImageCaptureCore's helper (Mac, 2026-10-14), with a stand-in helper program."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from planetpdf.core import mac_scanner as M
from planetpdf.core.document import PdfError
from planetpdf.core.escl_scanner import ScanCanceled

FAKE = textwrap.dedent('''\
    import json, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    def opt(name, default=""):
        return args[args.index("--" + name) + 1] if "--" + name in args else default
    def say(**kw):
        print(json.dumps(kw), flush=True)
    if args[0] == "list":
        say(scanner={"id": "USB-1", "name": "Epson Perfection V39", "connection": "usb"})
        say(scanner={"id": "NET-1", "name": "Canon MF741C/743C (ac:6b:a7) (3) (ac:6b:a7) (4)", "connection": "network"})
    elif args[0] == "caps":
        say(caps={"glass": {"dpis": [150, 300, 600], "max": [8.5, 11.7]},
                  "feeder": {"dpis": [300], "max": [8.5, 14], "duplex": True}})
    elif args[0] == "scan":
        if opt("device") == "EMPTY":
            say(error="The document feeder is empty: put the pages in and scan again.")
            sys.exit(1)
        out = Path(opt("out"))
        for n in range(1 if opt("source") == "glass" else 3):
            time.sleep(0.6 if opt("device") == "SLOW" else 0.05)
            page = out / ("scan" + (f" {n + 1}" if n else "") + ".jpg")
            page.write_bytes(b"\\xff\\xd8 fake jpeg " + opt("color").encode())
            say(page=str(page))
        say(done=3)
    ''')


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE)
    runner = tmp_path / "planetpdf-scan"
    runner.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    runner.chmod(0o755)
    monkeypatch.setattr(M, "helper", lambda compile_if_needed=True: runner)
    return runner


def test_names_lose_bonjours_repeats():
    assert M.tidy_name("Canon MF741C/743C (ac:6b:a7) (3) (ac:6b:a7) (3)  (ac:6b:a7) (4)") == "Canon MF741C/743C (ac:6b:a7)"
    assert M.tidy_name("Epson Perfection V39") == "Epson Perfection V39"


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_list_puts_usb_scanners_first(fake_helper):
    found = M.list_scanners(seconds=0)
    assert [(s.name, s.connection) for s in found] == [("Epson Perfection V39 (USB)", "usb"),
                                                       ("Canon MF741C/743C (ac:6b:a7)", "network")]
    assert found[0].url == "ica:USB-1"


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_capabilities_become_sources(fake_helper):
    caps = M.capabilities(M.MacScanner("Epson", "USB-1"))
    assert set(caps.sources) == {"glass", "feeder", "feeder-duplex"}
    assert caps.sources["glass"]["dpis"] == [150, 300, 600] and caps.sources["glass"]["max"] == (2550, 3510)
    assert caps.sources["glass"]["colors"] == ["color", "gray", "bw"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_feeder_pages_are_numbered_in_order(fake_helper, tmp_path):
    stages = []
    pages = M.scan(M.MacScanner("Canon", "NET-1"), tmp_path / "out", source="feeder", color="gray",
                   first_number=2, progress=lambda done, total, stage="": stages.append(stage) or True)
    assert [p.name for p in pages] == ["scan-002.jpg", "scan-003.jpg", "scan-004.jpg"]
    assert pages[0].read_bytes().endswith(b"gray")
    assert stages[0] == "Scanning page 2 from the feeder…"
    assert not list((tmp_path / "out").glob("incoming-*"))  # (the helper's own folder is gone)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_errors_and_cancel(fake_helper, tmp_path):
    with pytest.raises(PdfError, match="feeder is empty"):
        M.scan(M.MacScanner("Canon", "EMPTY"), tmp_path, source="feeder")
    with pytest.raises(ScanCanceled):
        M.scan(M.MacScanner("Canon", "SLOW"), tmp_path, source="feeder", progress=lambda *a: False)


@pytest.mark.skipif(sys.platform != "darwin", reason="the helper is a Mac program")
def test_the_real_helper_builds_and_answers():
    helper = M.helper()
    assert helper.is_file()
    import subprocess

    done = subprocess.run([str(helper), "nonsense"], capture_output=True, text=True)
    assert done.returncode == 2 and "usage" in done.stderr
