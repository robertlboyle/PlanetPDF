"""Printouts from the PlanetPDF printer (user 2026-10-18): where they go and what they're called."""

from datetime import datetime
from pathlib import Path

from planetpdf.core import printouts as P


def test_file_names_from_the_template():
    when = datetime(2026, 10, 18, 14, 5, 33)
    assert P.file_name("{document}", "Quote 1043", now=when) == "Quote 1043"
    assert P.file_name("{date} {document} ({user})", "Quote 1043", now=when, user="robert") == \
        "2026-10-18 Quote 1043 (robert)"
    assert P.file_name("{document}_{time}_{computer}", "Map", now=when, computer="PN-LAPTOP") == "Map_14-05-33_PN-LAPTOP"
    assert P.file_name("{Document} {unknown}", "Map", now=when) == "Map {unknown}"  # (a typo stays visible)
    assert P.file_name("{document}", "Invoice: March/April?") == "Invoice March April"
    assert P.file_name("{document}", "") == "Printout"
    assert P.file_name("  ..  ", "x") == "Printout"
    assert P.safe_name("CON") == "Printout" and len(P.safe_name("x" * 300)) == 120


def test_folder_and_template_settings_win_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(P.ENV_FOLDER, raising=False)
    monkeypatch.delenv(P.ENV_NAME, raising=False)
    assert P.default_folder("") is None and P.template("") == "{document}"  # (ask, by default)
    monkeypatch.setenv(P.ENV_FOLDER, str(tmp_path / "it"))
    monkeypatch.setenv(P.ENV_NAME, "{date} {document}")
    assert P.default_folder("") == tmp_path / "it" and P.template("") == "{date} {document}"
    assert P.default_folder(str(tmp_path / "mine")) == tmp_path / "mine" and P.template("{document}") == "{document}"


def test_inbox_and_unique_names(tmp_path):
    inbox = tmp_path / "printouts"
    job = inbox / "job-7-1"
    job.mkdir(parents=True)
    pdf = job / "Quote.pdf"
    pdf.write_bytes(b"%PDF")
    assert P.is_printout(pdf, inbox) and not P.is_printout(tmp_path / "other.pdf", inbox)
    assert P.waiting(inbox) == [pdf]
    out = tmp_path / "out"
    out.mkdir()
    assert P.unique_path(out, "Quote").name == "Quote.pdf"
    (out / "Quote.pdf").write_bytes(b"1")
    assert P.unique_path(out, "Quote").name == "Quote (2).pdf"
    pdf.unlink()
    P.done_with(pdf, inbox)
    assert not job.exists() and inbox.exists()  # (the job's folder goes, the inbox stays)
