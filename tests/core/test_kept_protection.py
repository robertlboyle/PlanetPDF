"""New files made from a password-protected PDF keep its password, and are never written
unprotected first (security review 2026-11-25, finding 7 and "written unprotected first")."""

from __future__ import annotations

import json

import pymupdf
import pytest

from planetpdf import cli
from planetpdf.core import pages as page_ops
from planetpdf.core.document import PdfSession, Protection, save_pdf
from tests.fixtures.generate import make_text_pdf

PASSWORD = "open-sesame"


@pytest.fixture
def protected(tmp_path):
    plain = make_text_pdf(tmp_path / "plain.pdf")
    path = tmp_path / "protected.pdf"
    with pymupdf.open(plain) as doc:
        doc.save(path, **Protection(open_password=PASSWORD).save_args())
    return path


def _run(capsys, *args):
    code = cli.main(["planetpdf", *args, "--json", "--quiet"])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def _needs_password(path) -> bool:
    with pymupdf.open(path) as doc:
        return bool(doc.needs_pass)


@pytest.mark.parametrize("command", [
    ["compress"],
    ["extract", "--pages", "1"],
    ["redact", "--preset", "ssn"],
    ["export", "--format", "image_pdf", "--pages", "1"],
])
def test_command_line_copies_keep_the_password(protected, tmp_path, capsys, command):
    out = tmp_path / "out.pdf"
    code, result = _run(capsys, command[0], str(protected), "--out", str(out), "--password", PASSWORD, *command[1:])
    assert code == 0, result
    assert _needs_password(out)
    with pymupdf.open(out) as doc:
        assert doc.authenticate(PASSWORD)


def test_compress_over_the_input_keeps_it_protected(protected, capsys):
    code, _result = _run(capsys, "compress", str(protected), "--out", str(protected), "--password", PASSWORD)
    assert code == 0 and _needs_password(protected)


def test_no_protect_drops_it_on_purpose(protected, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    code, result = _run(capsys, "extract", str(protected), "--out", str(out), "--password", PASSWORD, "--pages", "1",
                        "--no-protect")
    assert code == 0 and result["protected"] is False and not _needs_password(out)


def test_split_parts_keep_the_password(protected, tmp_path, capsys):
    code, result = _run(capsys, "split", str(protected), "--folder", str(tmp_path / "parts"), "--every", "1",
                        "--password", PASSWORD)
    assert code == 0 and result["outputs"]
    assert all(_needs_password(p) for p in result["outputs"])


def test_a_protected_file_is_encrypted_as_it_is_written(tmp_path, monkeypatch):
    """Whatever lands in the target's folder is already encrypted (a watcher copied the
    unprotected version in the 19 ms before it was encrypted in place)."""
    from planetpdf.core.storage import files

    seen = []
    real = files.replace_with_retry

    def watch(tmp, target):
        seen.append(_needs_password(tmp))
        return real(tmp, target)

    monkeypatch.setattr("planetpdf.core.document.replace_with_retry", watch)
    session = PdfSession.open(make_text_pdf(tmp_path / "plain.pdf"))
    try:
        page_ops.export_pages(session, [0], tmp_path / "out.pdf", Protection(open_password=PASSWORD))
    finally:
        session.close()
    assert seen == [True]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.pdf", "plain.pdf"]  # (nothing else left)


def test_save_pdf_without_protection_is_a_plain_file(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    save_pdf(doc, tmp_path / "plain.pdf")
    assert not _needs_password(tmp_path / "plain.pdf")
