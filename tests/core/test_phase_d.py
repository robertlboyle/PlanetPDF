"""Phase D (2026-10-02): PDF from Web Page (Edge), Check Spelling, Read Out Loud, scanner."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pymupdf
import pytest

from planetpdf.core import markup as K
from planetpdf.core import spelling as S
from planetpdf.core import web_capture as W
from planetpdf.core.document import PdfError, PdfSession

needs_browser = pytest.mark.skipif(W.find_browser() is None, reason="Edge / Chrome not installed")
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


# --- web page -------------------------------------------------------------------------------------


def test_addresses_are_completed(tmp_path):
    assert W.normalize_address("www.planet.net") == "https://www.planet.net"
    page = tmp_path / "page.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    assert W.normalize_address(str(page)).startswith("file:///")
    with pytest.raises(PdfError):
        W.normalize_address("javascript:alert(1)")
    with pytest.raises(PdfError):
        W.normalize_address("  ")


@needs_browser
def test_web_page_to_pdf_with_the_browser_twice_in_a_row(tmp_path):
    """Back to back, as Create then Insert does (the second one used to fail now and then
    in full test runs: Edge kept running after writing the PDF)."""
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1>Fiber Service Agreement</h1><p>Signed copy</p></body></html>", encoding="utf-8")
    for n in (1, 2):
        stages = []
        out = W.web_page_to_pdf(str(page), tmp_path / f"page{n}.pdf",
                                progress=lambda done, total, stage="": stages.append(stage) or True)
        with pymupdf.open(out) as doc:
            text = doc[0].get_text()
            assert "Fiber Service Agreement" in text and "file:///" not in text  # (no header / footer lines)
        assert stages[0] == f"Loading the page in {W.browser_name()}: 0 of at most {W.TIMEOUT_S} seconds…"



@needs_browser
@pytest.mark.skipif(sys.platform != "win32", reason="a Windows compatibility setting")
def test_a_compatibility_setting_doesnt_stop_the_browser(tmp_path, monkeypatch):
    """Found 2026-10-17: with `__COMPAT_LAYER` in PlanetPDF's environment (a Windows
    compatibility setting, inherited by what it starts), Edge quit at once with no PDF."""
    monkeypatch.setenv("__COMPAT_LAYER", "DetectorsAppHealth")
    assert "__COMPAT_LAYER" not in W.browser_environment()
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1>Compatibility</h1></body></html>", encoding="utf-8")
    with pymupdf.open(W.web_page_to_pdf(str(page), tmp_path / "page.pdf")) as doc:
        assert "Compatibility" in doc[0].get_text()


# A stand-in for the browser: writes the PDF (or not) and starts a child process, like Edge.
FAKE_BROWSER = r"""
import subprocess, sys, time
source, out, mode, pids = sys.argv[1:5]
if mode == "crash":
    sys.exit(3)
data = open(source, "rb").read()
if mode == "quiet":
    open(out, "wb").write(data)
    sys.exit(0)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
open(pids, "w").write(f"{child.pid} ")
if mode == "linger":
    open(out, "wb").write(data)
    sys.stderr.write(f"{len(data)} bytes written to file {out}\n")
    sys.stderr.flush()
time.sleep(300)  # (keeps running, like Edge under load; "hang": never writes)
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in found.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def fake_browser(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 72), "Fake capture")
    doc.save(source)
    pids = tmp_path / "pids.txt"
    mode = {"value": "linger"}

    def args(browser, profile, out, url):
        return [sys.executable, "-c", FAKE_BROWSER, str(source), str(out), mode["value"], str(pids)]

    monkeypatch.setattr(W, "find_browser", lambda: Path(sys.executable))
    monkeypatch.setattr(W, "browser_args", args)
    page = tmp_path / "page.html"
    page.write_text("<p>hi</p>", encoding="utf-8")

    def capture(kind, **kw):
        mode["value"] = kind
        return W.web_page_to_pdf(str(page), tmp_path / "out.pdf", **kw)

    capture.children = lambda: [int(p) for p in pids.read_text().split()] if pids.exists() else []
    return capture


def test_capture_finishes_when_the_pdf_is_written_and_stops_the_browser(fake_browser, monkeypatch):
    monkeypatch.setattr(W, "EXIT_GRACE_S", 0.5)
    started = time.monotonic()
    out = fake_browser("linger", timeout=60)
    assert time.monotonic() - started < 20  # (not the 60 s timeout: the browser is still running)
    with pymupdf.open(out) as doc:
        assert "Fake capture" in doc[0].get_text()
    assert not any(_alive(pid) for pid in fake_browser.children())  # (its child process is gone too)


def test_capture_by_a_browser_that_exits_without_a_message(fake_browser):
    with pymupdf.open(fake_browser("quiet")) as doc:
        assert "Fake capture" in doc[0].get_text()


def test_capture_timeout_and_crash_and_cancel_stop_the_browser(fake_browser):
    with pytest.raises(PdfError, match="longer than 2 seconds"):
        fake_browser("hang", timeout=2)
    assert not any(_alive(pid) for pid in fake_browser.children())
    with pytest.raises(PdfError, match="couldn't be captured.*code 3"):
        fake_browser("crash")
    stages = []
    with pytest.raises(W.WebCaptureCanceled):
        fake_browser("hang", progress=lambda done, total, stage="": stages.append(stage) or len(stages) < 2)
    assert stages[:2] == [f"Loading the page in the browser: {n} of at most {W.TIMEOUT_S} seconds…" for n in (0, 1)]
    assert not any(_alive(pid) for pid in fake_browser.children())


# --- spelling -------------------------------------------------------------------------------------


@pytest.fixture
def speller(tmp_path):
    return S.Speller(tmp_path / "user_words.txt")


def test_misspellings_suggestions_and_the_users_dictionary(speller, tmp_path):
    found = [w for w, _o in speller.misspelled("Thsi is a simple sentense about NASA and fiber at www.plaent.net 4qa")]
    assert found == ["Thsi", "sentense"]  # (acronyms, addresses and words with digits are skipped)
    assert "This" in speller.suggestions("Thsi") and "sentence" in speller.suggestions("sentense")
    assert speller.is_wrong("Gigafibr")
    speller.add_word("Gigafibr")
    assert not speller.is_wrong("gigafibr")
    assert S.Speller(tmp_path / "user_words.txt").user_words == ["gigafibr"]  # (saved)
    speller.ignore_all("teh")
    assert not speller.is_wrong("teh")


def test_issues_in_comments_fields_and_page_text(speller, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The contrct is ready", fontname="helv")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "Notes"
    widget.rect = pymupdf.Rect(72, 200, 300, 220)
    widget.field_value = "Plese call me"
    page.add_widget(widget)
    doc.save(tmp_path / "f.pdf")
    session = PdfSession.open(tmp_path / "f.pdf")
    try:
        K.add_markup(session, K.MarkupSpec(K.new_id(), 0, K.Kind.NOTE, (400, 400, 420, 420), text="Chek this"))
        issues = S.find_issues(session, speller)
        assert sorted((i.where, i.word) for i in issues) == [("comment", "Chek"), ("field", "Plese")]
        with_page = S.find_issues(session, speller, page_text=True)
        page_issue = next(i for i in with_page if i.where == "page")
        assert page_issue.word == "contrct" and page_issue.rect is not None
        comment = next(i for i in issues if i.where == "comment")
        assert S.replace_word(comment.text, comment, "Check") == "Check this"
        assert "Chek this" in comment.context
    finally:
        session.close()


def test_word_lists_know_terms_and_names(speller):
    """User 2026-10-02: medical / legal / technical terms and proper nouns aren't mistakes."""
    names = {wl.name for wl in speller.word_lists()}
    assert {"Medical", "Legal", "Business & Finance", "Academic & Science", "Computing & Programming",
            "Networking & Telecom", "Companies & Brands", "People's Names", "Places"} <= names
    for word in ("lisinopril", "Ozempic", "tortfeasor", "certiorari", "EBITDA", "subnet", "Kubernetes", "PostgreSQL",
                 "Calix", "Hackettstown", "Nguyen", "PDFs", "iPhones", "JavaScript's"):
        assert not speller.is_wrong(word), word
    speller.set_off([wl.key for wl in speller.word_lists() if wl.name == "Medical"])
    assert speller.is_wrong("lisinopril") and not speller.is_wrong("tortfeasor")


def test_names_keep_their_capitals(speller):
    """User: "look for capitalization to see if that is the intent"."""
    assert speller.is_wrong("Javascript") and speller.suggestions("Javascript") == ["JavaScript"]
    assert speller.is_wrong("iphone") and speller.suggestions("iphone") == ["iPhone"]
    assert speller.is_wrong("O'brien") and not speller.is_wrong("O'Brien")
    # acronyms and plain capitalized names don't make ordinary words wrong
    for word in ("Basic", "basic", "Maui", "latex", "LaTeX", "python", "Python", "metformin", "Metformin"):
        assert not speller.is_wrong(word), word
    assert "Kubernetes" in speller.suggestions("Kubernetis")  # (suggestions come from the lists too)


def test_import_and_remove_a_word_list(tmp_path):
    folder = tmp_path / "lists"
    source = tmp_path / "words.dic"
    source.write_text("3\nZorblax/S\nquuxify\nFrobNitz\n", encoding="utf-8")  # (Hunspell .dic)
    speller = S.Speller(user_lists=folder)
    assert speller.is_wrong("quuxify")
    S.import_word_list(source, folder, "Our Products")
    speller.reload_lists()
    imported = next(wl for wl in speller.word_lists() if not wl.builtin)
    assert imported.name == "Our Products" and imported.key.startswith("user:")
    assert not speller.is_wrong("quuxify") and not speller.is_wrong("Zorblax") and speller.is_wrong("Frobnitz")
    speller.set_off([imported.key])
    assert speller.is_wrong("quuxify")
    S.remove_word_list(imported)
    speller.reload_lists()
    assert all(wl.builtin for wl in speller.word_lists())
    with pytest.raises(ValueError):
        S.remove_word_list(speller.word_lists()[0])


def test_ocr_text_is_found_and_changeable(speller, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Scaned wrods", render_mode=3)  # (invisible, like an OCR layer)
    page.insert_text((72, 200), "Vsible text", fontname="helv")
    doc.save(tmp_path / "o.pdf")
    session = PdfSession.open(tmp_path / "o.pdf")
    try:
        issues = S.find_issues(session, speller, page_text=True)
        assert sorted((i.where, i.word, i.changeable) for i in issues) == [
            ("ocr", "Scaned", True), ("ocr", "wrods", True), ("page", "Vsible", True)]  # (OCR: core.ocr_correct)
        assert next(i for i in issues if i.where == "page").line is not None
    finally:
        session.close()


# --- read out loud --------------------------------------------------------------------------------


@windows
def test_speaker_speaks_pauses_and_stops():
    from planetpdf.core.windows_speech import Speaker

    speaker = Speaker()
    assert speaker.voices
    speaker.volume = 0  # (silent in tests)
    speaker.rate = 10
    speaker.speak("Testing Read Out Loud in PlanetPDF.")
    assert speaker.speaking
    speaker.pause()
    assert speaker.paused
    speaker.stop()
    assert not speaker.paused
    speaker.speak("Done.")
    assert speaker.wait(10000)


# --- scanner ---------------------------------------------------------------------------------------


@windows
def test_scanner_list_works_without_a_scanner():
    from planetpdf.core import windows_scanner

    assert isinstance(windows_scanner.scanners(), list)  # (this computer may have none)
