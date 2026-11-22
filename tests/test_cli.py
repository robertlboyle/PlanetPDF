"""The command line (planetpdf.cli) and the command list behind it (core.commands)."""

from __future__ import annotations

import json

import pymupdf
import pytest

from planetpdf import cli
from planetpdf.core import commands
from tests.fixtures.generate import SSN, make_acroform_pdf, make_text_pdf


def run(capsys, *args):
    code = cli.main(["planetpdf", *args, "--json", "--quiet"] if args and args[0] not in ("help",) else
                    ["planetpdf", *args])
    out = capsys.readouterr()
    try:
        return code, json.loads(out.out) if out.out.strip() else None, out.err
    except json.JSONDecodeError:
        return code, out.out, out.err


@pytest.fixture
def pdf(tmp_path):
    return make_text_pdf(tmp_path / "text.pdf")


def test_every_command_has_a_known_permission_and_parameter_kinds():
    kinds = {"file", "files", "out", "folder", "pages", "text", "texts", "int", "number", "flag", "choice",
             "secret", "json"}
    for cmd in commands.COMMANDS.values():
        assert cmd.permission in commands.PERMISSIONS
        assert all(p.kind in kinds for p in cmd.params), cmd.name
    assert commands.COMMANDS["sign"].permission == commands.SIGN
    assert json.dumps(commands.describe())  # (what the API lists)


def test_is_command_tells_commands_from_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.is_command(["planetpdf", "info", "x.pdf"])
    assert not cli.is_command(["planetpdf", "x.pdf"])
    assert not cli.is_command(["planetpdf"])
    (tmp_path / "info").write_text("a file named like a command")
    assert not cli.is_command(["planetpdf", "info"])


def test_info_and_text(capsys, pdf):
    code, result, _ = run(capsys, "info", str(pdf))
    assert code == 0 and result["pages"] >= 1
    code, result, _ = run(capsys, "text", str(pdf), "--pages", "1")
    assert code == 0 and result["text"].strip()


def test_page_commands_write_new_files_and_never_overwrite(capsys, pdf, tmp_path):
    out = tmp_path / "rotated.pdf"
    code, result, _ = run(capsys, "rotate", str(pdf), "--out", str(out), "--degrees", "90")
    assert code == 0 and out.is_file()
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 90
    code, _result, err = run(capsys, "rotate", str(pdf), "--out", str(out), "--degrees", "180")
    assert code == cli.FAILED and "already exists" in err
    code, _result, _ = run(capsys, "rotate", str(pdf), "--out", str(out), "--degrees", "180", "--overwrite")
    assert code == 0
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 180


def test_combine_extract_and_split(capsys, pdf, tmp_path):
    both = tmp_path / "both.pdf"
    code, result, _ = run(capsys, "combine", str(pdf), str(pdf), "--out", str(both))
    assert code == 0 and result["pages"] == 2 * pymupdf.open(pdf).page_count
    one = tmp_path / "one.pdf"
    code, result, _ = run(capsys, "extract", str(both), "--pages", "1", "--out", str(one))
    assert code == 0 and pymupdf.open(one).page_count == 1
    code, result, _ = run(capsys, "split", str(both), "--folder", str(tmp_path / "parts"), "--every", "1")
    assert code == 0 and len(result["outputs"]) == result_pages(both)


def result_pages(path):
    with pymupdf.open(path) as doc:
        return doc.page_count


def test_redact_removes_the_text_and_verifies(capsys, pdf, tmp_path):
    out = tmp_path / "redacted.pdf"
    code, result, _ = run(capsys, "redact", str(pdf), "--preset", "ssn", "--find", "Public", "--out", str(out))
    assert code == 0, result
    assert result["marks"] >= 1 and result["verified"]
    with pymupdf.open(out) as doc:
        text = "".join(page.get_text() for page in doc)
    assert SSN not in text
    code, result, _ = run(capsys, "search", str(pdf), "--find", "Public")
    assert code == 0 and result["matches"] >= 1


def test_fill_and_list_fields(capsys, tmp_path):
    source = make_acroform_pdf(tmp_path / "form.pdf")
    code, result, _ = run(capsys, "fields", str(source))
    text = next(f for f in result["fields"] if f["kind"] == "text" and not f["read_only"])
    out = tmp_path / "filled.pdf"
    code, result, _ = run(capsys, "fill", str(source), "--set", f"{text['name']}=Jane Doe", "--out", str(out))
    assert code == 0, result
    code, result, _ = run(capsys, "fields", str(out))
    assert next(f for f in result["fields"] if f["name"] == text["name"])["value"] == "Jane Doe"


def test_protect_and_unprotect(capsys, pdf, tmp_path, monkeypatch):
    locked = tmp_path / "locked.pdf"
    monkeypatch.setenv("TEST_PDF_PASSWORD", "s3cret-pass")
    code, _result, _ = run(capsys, "protect", str(pdf), "--open-password", "env:TEST_PDF_PASSWORD",
                           "--out", str(locked))
    assert code == 0
    with pymupdf.open(locked) as doc:
        assert doc.needs_pass
    code, _result, err = run(capsys, "info", str(locked))
    assert code == cli.FAILED
    plain = tmp_path / "plain.pdf"
    code, _result, _ = run(capsys, "unprotect", str(locked), "--password", "s3cret-pass", "--out", str(plain))
    assert code == 0
    with pymupdf.open(plain) as doc:
        assert not doc.needs_pass


def test_usage_errors_exit_2(capsys, pdf):
    assert run(capsys, "rotate", str(pdf))[0] == cli.USAGE  # (no --out / --degrees)
    assert run(capsys, "reorder", str(pdf), "--order", "1", "--out", "x.pdf")[0] in (cli.USAGE, cli.FAILED)
    assert cli.main(["planetpdf", "help", "nonsense"]) == cli.USAGE


def test_signing_needs_approval(capsys, pdf, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(cli, "_approve", lambda description: asked.append(description) or False)
    code, _result, err = run(capsys, "sign", str(pdf), "--pfx", str(pdf), "--out", str(tmp_path / "signed.pdf"))
    assert code == cli.NOT_APPROVED and asked and "Sign" in asked[0]
    assert not (tmp_path / "signed.pdf").exists()


def test_commands_list_as_json(capsys):
    assert cli.main(["planetpdf", "commands", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {c["name"] for c in listed} == set(commands.COMMANDS)


def test_export_with_ai_says_why_it_cant_run(capsys, pdf, tmp_path, monkeypatch):
    from planetpdf.core import ai_ocr

    monkeypatch.setattr(ai_ocr, "status", lambda: ai_ocr.Status(False, "No NVIDIA graphics card was found."))
    code, _result, err = run(capsys, "export", str(pdf), "--format", "docx", "--ai", "--out", str(tmp_path / "a.docx"))
    assert code == cli.FAILED and "NVIDIA" in err
