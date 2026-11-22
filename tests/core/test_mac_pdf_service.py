"""The Mac's "Open in PlanetPDF" PDF Service (user 2026-10-18): puts the printed PDF in
PlanetPDF's inbox, named after the document, and opens it with PlanetPDF (which asks where to
save it)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planetpdf.core import mac_pdf_service as M

SH = shutil.which("sh") or (r"C:\Program Files\Git\bin\sh.exe" if Path(r"C:\Program Files\Git\bin\sh.exe").is_file()
                            else None)


def test_install_writes_the_script_once(tmp_path):
    path = M.install("net.planet.planetpdf", home=tmp_path)
    assert path == tmp_path / "Library" / "PDF Services" / "Open in PlanetPDF"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh") and 'open -b "net.planet.planetpdf"' in text
    assert "Library/Application Support/Planet Networks/PlanetPDF/printouts" in text
    if sys.platform != "win32":
        assert os.access(path, os.X_OK)
    before = path.stat().st_mtime_ns
    M.install("net.planet.planetpdf", home=tmp_path)  # (unchanged: not rewritten)
    assert path.stat().st_mtime_ns == before
    assert M.remove(home=tmp_path) and not path.exists()


@pytest.mark.skipif(SH is None, reason="needs a POSIX shell")
def test_the_script_hands_it_to_planetpdf(tmp_path):
    home = tmp_path / "home"
    script = M.install("net.planet.planetpdf", home=home)
    tools = tmp_path / "bin"
    tools.mkdir()
    opened = tmp_path / "opened.txt"
    (tools / "open").write_text(f'#!/bin/sh\nprintf "%s|" "$@" >> "{opened.as_posix()}"\n', encoding="utf-8",
                                newline="\n")
    os.chmod(tools / "open", 0o755)
    printed = tmp_path / "spool.pdf"
    printed.write_bytes(b"%PDF-1.7 printed")
    env = dict(os.environ, HOME=home.as_posix(), PATH=tools.as_posix() + os.pathsep + os.environ.get("PATH", ""))

    def run(title):
        subprocess.run([SH, script.as_posix(), title, "", printed.as_posix()], check=True, env=env)

    run("Microsoft Word - Quote 1043.docx")
    run("Microsoft Word - Quote 1043.docx")
    run("Invoice: March/April?")
    run("   ")
    inbox = home / "Library" / "Application Support" / "Planet Networks" / "PlanetPDF" / "printouts"
    files = sorted(inbox.rglob("*.pdf"), key=lambda p: p.name)
    assert [p.name for p in files] == ["Invoice March April.pdf", "Printout.pdf", "Quote 1043.pdf", "Quote 1043.pdf"]
    assert len({p.parent for p in files}) == 4  # (a folder each: two printouts of one name never meet)
    assert files[2].read_bytes() == b"%PDF-1.7 printed"
    calls = opened.read_text(encoding="utf-8").split("|")
    assert calls[0] == "-b" and calls[1] == "net.planet.planetpdf" and calls[2].endswith("Quote 1043.pdf")
