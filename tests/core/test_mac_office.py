"""Office for Mac through AppleScript (2026-10-14). The real Word / Excel / PowerPoint only with
PLANETPDF_TEST_OFFICE=1 (they show on screen, and macOS asks to let them be controlled)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from planetpdf.core import mac_office as M
from planetpdf.core import office
from planetpdf.core.document import PdfError


class FakeOsascript:
    """Stands in for osascript: records the scripts, "saves" the file the script names."""

    def __init__(self, error: str = "", running: bool = False):
        self.scripts, self.error, self.running, self.quit = [], error, running, []

    def __call__(self, args, **kwargs):
        if args[0] == "pgrep":
            return subprocess.CompletedProcess(args, 0 if self.running else 1)
        script = args[2]
        if script.endswith("to quit saving no"):
            self.quit.append(script)
            return subprocess.CompletedProcess(args, 0, "", "")
        self.scripts.append(script)
        if self.error:
            return subprocess.CompletedProcess(args, 1, "", self.error)
        out = script.split('(POSIX file "')[-1].split('")')[0]  # (the last POSIX file: where it saves)
        Path(out).write_bytes(b"%PDF-1.7 saved by the stand-in")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    runner = FakeOsascript()
    monkeypatch.setattr(M.subprocess, "run", runner)
    monkeypatch.setattr(M, "WORK", tmp_path / "work")
    monkeypatch.setattr(M, "OWN_WORK", tmp_path / "own")
    monkeypatch.setattr(M, "available", lambda app: True)
    return runner


@pytest.mark.parametrize("name, app, words", [
    ("letter.docx", "word", ["Microsoft Word", "format PDF", "read only true"]),
    ("sheet.xlsx", "excel", ["Microsoft Excel", "PDF file format", "open workbook"]),
    ("deck.pptx", "powerpoint", ["Microsoft PowerPoint", "save as PDF"]),
])
@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_each_office_app_saves_a_pdf_and_is_quit_after(fake, tmp_path, name, app, words):
    source = tmp_path / name
    source.write_bytes(b"office file")
    M.to_pdf(source, tmp_path / "out.pdf")
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")
    [script] = fake.scripts
    assert all(w in script for w in words)
    assert str(M.WORK) in script and str(tmp_path / name) not in script  # (a copy in PlanetPDF's folder)
    assert len(fake.quit) == 1  # (PlanetPDF started it)
    assert not list(M.WORK.iterdir())  # (its workspace is gone)


def test_an_office_app_already_open_is_left_open(fake, tmp_path):
    fake.running = True
    source = tmp_path / "letter.docx"
    source.write_bytes(b"x")
    M.to_pdf(source, tmp_path / "out.pdf")
    assert fake.quit == []


def test_word_97_2003(fake, tmp_path):
    source = tmp_path / "document.docx"
    source.write_bytes(b"x")
    M.word_save_as(source, tmp_path / "document.doc")
    assert "format document97" in fake.scripts[0] and (tmp_path / "document.doc").is_file()


def test_permission_refused_says_where_to_allow_it(fake, tmp_path):
    fake.error = "execution error: Not authorized to send Apple events to Microsoft Word. (-1743)"
    source = tmp_path / "letter.docx"
    source.write_bytes(b"x")
    with pytest.raises(PdfError, match="Automation ▸ PlanetPDF ▸ Microsoft Word"):
        M.to_pdf(source, tmp_path / "out.pdf")


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_planetpdf_s_own_folder_when_macos_refuses_office_s(fake, monkeypatch, tmp_path):
    def refused(self, *args, **kwargs):
        if M.WORK in (self, *self.parents):
            raise PermissionError(1, "Operation not permitted")
        return real_mkdir(self, *args, **kwargs)
    real_mkdir = Path.mkdir
    monkeypatch.setattr(Path, "mkdir", refused)
    source = tmp_path / "letter.docx"
    source.write_bytes(b"x")
    M.to_pdf(source, tmp_path / "out.pdf")
    assert str(M.OWN_WORK) in fake.scripts[0] and (tmp_path / "out.pdf").is_file()
    fake.error = "execution error: missing value doesn’t understand the “save as” message. (-1708)"
    with pytest.raises(PdfError, match="click Select or Grant Access"):
        M.to_pdf(source, tmp_path / "again.pdf")


@pytest.mark.parametrize("app", ["word", "excel"])
def test_macros_are_off_while_word_and_excel_open_the_file(app):
    script = M._script(app, Path("/tmp/in.docx"), Path("/tmp/out.pdf"), "pdf")
    off = script.index("set automation security to msoAutomationSecurityForceDisable")
    assert script.index("set oldSecurity to automation security") < off < script.index("open ")
    assert script.count("set automation security to oldSecurity") == 2  # (after success and on error)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac only: POSIX paths and the Mac helper")
def test_paths_with_quotes_are_escaped():
    script = M._script("word", Path('/tmp/a "b".docx'), Path("/tmp/out.pdf"), "pdf")
    assert 'file name "/tmp/a \\"b\\".docx"' in script


def test_the_office_entry_point_picks_this_system():
    assert office.app_for(Path("x.PPTX")) == "powerpoint" and office.app_for(Path("x.pdf")) is None
    if sys.platform == "darwin":
        assert office._backend() is M


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("PLANETPDF_TEST_OFFICE")
                    or not M.available("word"), reason="the real Office for Mac: PLANETPDF_TEST_OFFICE=1")
def test_real_word_excel_powerpoint(tmp_path):
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation

    word = tmp_path / "letter.docx"
    document = Document()
    document.add_heading("Hello from PlanetPDF", 1)
    document.save(word)
    sheet = tmp_path / "sheet.xlsx"
    book = Workbook()
    book.active["A1"] = "Hello from PlanetPDF"
    book.save(sheet)
    deck = tmp_path / "deck.pptx"
    show = Presentation()
    show.slides.add_slide(show.slide_layouts[0]).shapes.title.text = "Hello from PlanetPDF"
    show.save(deck)
    for source in (word, sheet, deck):
        out = tmp_path / f"{source.stem}.pdf"
        M.to_pdf(source, out)
        with pymupdf.open(out) as pdf:
            assert "Hello from PlanetPDF" in pdf[0].get_text()
    M.word_save_as(word, tmp_path / "letter.doc")
    assert (tmp_path / "letter.doc").read_bytes()[:4] == b"\xd0\xcf\x11\xe0"  # (the old binary format)
    for app in ("Microsoft Word", "Microsoft Excel"):  # (the user's own setting is put back)
        subprocess.run(["open", "-g", "-a", app], check=True)
        before = _security(app)
        M.to_pdf(word if "Word" in app else sheet, tmp_path / "again.pdf")
        assert _security(app) == before != "msoAutomationSecurityForceDisable"


def _security(app: str) -> str:
    for _ in range(30):  # (Office may still be starting)
        done = subprocess.run(["osascript", "-e", f'tell application "{app}" to get automation security'],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return done.stdout.strip()
        import time
        time.sleep(1)
    raise AssertionError(done.stderr)
