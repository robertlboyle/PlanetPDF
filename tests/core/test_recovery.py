from __future__ import annotations

from datetime import datetime, timedelta

import pymupdf

from planetpdf.core.document import PdfSession, Protection
from planetpdf.core.storage.recovery import RecoveryStore


def test_write_list_discard(tmp_path):
    store = RecoveryStore(tmp_path / "recovery")
    assert store.entries() == []
    a, b = store.new_id(), store.new_id()
    now = datetime(2026, 9, 30, 12, 0)
    store.write(a, tmp_path / "docs" / "one.pdf", b"%PDF-a", now)
    store.write(b, tmp_path / "docs" / "two.pdf", b"%PDF-b", now + timedelta(minutes=5))
    store.write(a, tmp_path / "docs" / "one.pdf", b"%PDF-a2", now + timedelta(minutes=10))  # overwritten in place
    entries = store.entries()
    assert [e.name for e in entries] == ["one.pdf", "two.pdf"]  # newest first
    assert entries[0].pdf.read_bytes() == b"%PDF-a2"
    store.discard(a)
    assert [e.name for e in store.entries()] == ["two.pdf"]
    (store.root / f"{b}.pdf").unlink()  # half-written leftovers are cleaned up
    assert store.entries() == []
    assert list(store.root.iterdir()) == []


def test_restore_next_to_original_never_over_it(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    original = docs / "contract.pdf"
    original.write_bytes(b"original")
    store = RecoveryStore(tmp_path / "recovery")
    entry = store.write(store.new_id(), original, b"recovered")
    target = store.restore_to(entry, tmp_path / "fallback")
    assert target == docs / "contract (recovered).pdf" and target.read_bytes() == b"recovered"
    assert original.read_bytes() == b"original" and store.entries() == []
    again = store.write(store.new_id(), original, b"second")
    assert store.restore_to(again, tmp_path) == docs / "contract (recovered 2).pdf"
    gone = store.write(store.new_id(), tmp_path / "missing" / "x.pdf", b"x")
    (tmp_path / "fallback").mkdir()
    assert store.restore_to(gone, tmp_path / "fallback").parent == tmp_path / "fallback"


def test_recovery_bytes_keep_the_password(pdf_copy, tmp_path):
    s = PdfSession.open(pdf_copy("text.pdf"))
    s.set_protection(Protection(open_password="pw"))
    s.save()
    s.close()
    s = PdfSession.open(s.path, password="pw")
    s.doc[0].insert_text((72, 700), "unsaved edit")
    data = s.recovery_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert doc.needs_pass  # not left lying around unprotected
    assert doc.authenticate("pw") and "unsaved edit" in doc[0].get_text()
    s.close()


def test_a_recovered_copy_never_goes_into_a_temporary_folder(tmp_path):
    """Security review 2026-11-27: the original was in %TEMP%, and Restore wrote the recovered copy
    there, where files are deleted without asking."""
    import tempfile
    from pathlib import Path

    temp = Path(tempfile.mkdtemp(prefix="ppdf-recovery-"))
    try:
        store = RecoveryStore(tmp_path / "recovery")
        entry = store.write("a", temp / "opened.pdf", b"%PDF-1.7 recovered")
        target = store.restore_to(entry, tmp_path / "documents")
        assert target.parent == tmp_path / "documents" and target.name == "opened (recovered).pdf"
        chosen = tmp_path / "chosen.pdf"
        again = store.write("b", temp / "opened.pdf", b"%PDF-1.7 recovered")
        assert store.restore_to(again, tmp_path / "documents", chosen) == chosen and chosen.exists()
    finally:
        import shutil

        shutil.rmtree(temp, ignore_errors=True)
