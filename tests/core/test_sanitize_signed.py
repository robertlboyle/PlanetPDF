"""Sanitize on signed files and on layers listed by reference (security review 2026-11-25).

- A signed file is saved by adding the changes to its end, so "removed" items stayed in its
  earlier, signed version: Sanitize now removes the signatures and writes a whole new file (the
  command line only with --remove-signatures).
- A signed document put back from a snapshot (undo, or a command that failed) could no longer be
  saved at all ("incremental needs original file").
- Hidden layers whose list was its own object (``/OFF 5 0 R``) were read as "nothing hidden", and
  Sanitize showed them.
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from planetpdf import cli
from planetpdf.core.document import PdfSession
from planetpdf.core.redaction import ocg
from planetpdf.core.redaction.ocg import LayerError, remove_hidden_layers
from planetpdf.core.redaction.sanitize import SanitizeOptions, sanitize, signed_fields
from planetpdf.core.signing.digital import SignOptions, sign_pdf, validate_signatures
from planetpdf.core.signing.providers.pkcs12 import FileTrustStore, Pkcs12Provider
from tests.fixtures.certs import PFX_PASSWORD
from tests.fixtures.generate import make_text_pdf

SECRET = b"ATTACHED-SECRET-42"
HIDDEN = "HIDDEN-LAYER-WORDS"


def _with_hidden_information(path):
    make_text_pdf(path)
    doc = pymupdf.open(path)
    doc.set_metadata({"title": "Secret title", "author": "Somebody"})
    doc.embfile_add("note.txt", SECRET, filename="note.txt")
    doc[0].add_text_annot((300, 300), "A private comment")
    off = doc.add_ocg("Hidden", on=False)
    doc[0].insert_text((72, 700), HIDDEN, fontsize=12, oc=off)
    doc.save(path.with_name("tmp.pdf"))
    doc.close()
    path.with_name("tmp.pdf").replace(path)
    return path


@pytest.fixture
def signed(tmp_path, test_certs):
    source = _with_hidden_information(tmp_path / "source.pdf")
    target = tmp_path / "signed.pdf"
    sign_pdf(source, target, Pkcs12Provider(test_certs.pfx, PFX_PASSWORD), 0, (72, 600, 300, 680),
             SignOptions(reason="Approval"))
    return target


def _all_bytes(path) -> bytes:
    """The file's bytes, plus every stream decompressed (what someone digging in the file finds)."""
    data = path.read_bytes()
    return data + pymupdf.open(path).tobytes(expand=255)


def test_cli_refuses_a_signed_file_without_remove_signatures(signed, capsys):
    before = signed.read_bytes()
    code = cli.main(["planetpdf", "sanitize", str(signed), "--out", str(signed), "--quiet"])
    assert code == cli.USAGE
    assert "--remove-signatures" in capsys.readouterr().err
    assert signed.read_bytes() == before


def test_cli_sanitize_with_remove_signatures_writes_a_whole_new_file(signed, tmp_path, capsys):
    assert SECRET in _all_bytes(signed)
    out = tmp_path / "clean.pdf"
    code = cli.main(["planetpdf", "sanitize", str(signed), "--out", str(out), "--remove-signatures", "--json",
                     "--quiet"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["removed"]["signatures"] == 1
    data = _all_bytes(out)
    assert SECRET not in data and b"A private comment" not in data and b"Secret title" not in data
    assert HIDDEN.encode() not in data
    assert out.read_bytes().count(b"%%EOF") == 1  # (one revision: nothing earlier kept)
    with pymupdf.open(out) as doc:
        assert signed_fields(doc) == []
        assert HIDDEN not in doc[0].get_text()


def test_window_sanitize_in_place_then_save_rewrites_without_the_old_revision(signed):
    session = PdfSession.open(signed)
    assert session.has_digital_signatures
    sanitize(session.doc, SanitizeOptions(), remove_signatures=True)
    session.signatures_changed()
    assert not session.has_digital_signatures
    session.save()
    session.close()
    data = _all_bytes(signed)
    assert SECRET not in data and signed.read_bytes().count(b"%%EOF") == 1


def test_a_signed_document_put_back_from_a_snapshot_still_saves_and_keeps_its_signature(signed, tmp_path):
    original = signed.read_bytes()
    session = PdfSession.open(signed)
    before = session.snapshot()
    assert before.startswith(original)  # (the signed file, with any changes appended)
    session.doc[0].insert_text((72, 100), "A change")
    session.restore_snapshot(before)  # (undo of a whole-document change)
    session.doc[0].insert_text((72, 120), "Kept change")
    session.mark_modified()
    session.save()  # (failed: "incremental needs original file")
    session.close()
    assert signed.read_bytes().startswith(original)
    [status] = validate_signatures(signed, FileTrustStore(tmp_path / "no-trust"))
    assert status.intact  # (the signed bytes untouched; the page change itself is reported as a change)
    with pymupdf.open(signed) as doc:
        assert "Kept change" in doc[0].get_text() and "A change" not in doc[0].get_text()


def _off_list_by_reference(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Visible words", fontsize=12)
    off = doc.add_ocg("Hidden", on=False)
    page.insert_text((72, 200), HIDDEN, fontsize=12, oc=off)
    catalog = doc.pdf_catalog()
    listed = doc.get_new_xref()
    doc.update_object(listed, f"[{off} 0 R]")
    doc.xref_set_key(catalog, "OCProperties/D/OFF", f"{listed} 0 R")
    doc.save(path)
    doc.close()
    return pymupdf.open(path)


def test_hidden_layers_listed_by_reference_are_removed_not_shown(tmp_path):
    doc = _off_list_by_reference(tmp_path / "layers.pdf")
    assert HIDDEN not in doc[0].get_text()  # (hidden to begin with)
    assert ocg.hidden_ocgs(doc)  # (was read as "nothing hidden")
    assert remove_hidden_layers(doc) >= 1
    again = pymupdf.open("pdf", doc.tobytes(garbage=4))
    assert HIDDEN not in again[0].get_text() and "Visible words" in again[0].get_text()


def test_sanitize_stops_rather_than_show_hidden_text(tmp_path, monkeypatch):
    doc = _off_list_by_reference(tmp_path / "layers.pdf")
    monkeypatch.setattr(ocg, "hidden_ocgs", lambda _doc: set())  # (a layer it can't read as hidden)
    with pytest.raises(LayerError, match="page 1"):
        remove_hidden_layers(doc)


def test_content_in_a_layer_the_file_doesnt_define_stops_sanitize(tmp_path):
    doc = _off_list_by_reference(tmp_path / "layers.pdf")
    page = doc[0]
    page.insert_text((72, 300), "x", fontsize=12)  # (a stream to add to)
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n/OC /NoSuchLayer BDC EMC\n")
    with pytest.raises(LayerError, match="NoSuchLayer"):
        remove_hidden_layers(doc)
