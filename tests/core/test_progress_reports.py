"""Long jobs report real numbers (user rule 2026-10-02): "Writing page 3 of 10",
"Applying redactions: marked page 2 of 5", never only "Working…"."""

from __future__ import annotations

import pymupdf
import pytest

from planetpdf.core import compare as C
from planetpdf.core import pages as P
from planetpdf.core.document import PdfSession
from planetpdf.core.export import ExportCanceled, ExportFormat, ExportOptions, export
from planetpdf.core.optimize import ReduceOptions, reduce_size
from planetpdf.core.progress import report, stage_text
from planetpdf.core.redaction import marks as M
from planetpdf.core.redaction.apply import RedactionCanceled, RedactionOptions, redact_to_file
from planetpdf.core.redaction.patterns import PRESETS
from planetpdf.core.redaction.sanitize import SanitizeOptions
from planetpdf.core.redaction.search import search_pages


class Recorder:
    """A progress callback that keeps "<stage> x of y" lines (as the dialog shows them)."""

    def __init__(self, stop_at: str | None = None) -> None:
        self.lines: list[str] = []
        self.stop_at = stop_at

    def __call__(self, done: int, total: int, stage: str = "") -> bool:
        line = stage_text(stage, done, total) if stage else f"{done} of {total}"
        self.lines.append(line)
        return not (self.stop_at and line.startswith(self.stop_at))

    def having(self, start: str) -> list[str]:
        return [line for line in self.lines if line.startswith(start)]


def test_report_and_stage_text():
    assert stage_text("Reading page", 2, 10) == "Reading page 3 of 10"
    assert stage_text("Reading page", 10, 10) == "Reading page 10 of 10"
    assert stage_text("Saving…", 0, 0) == "Saving…"
    two_args = []
    assert report(lambda d, t: two_args.append((d, t)) or True, 1, 4)  # (old-style callbacks still work)
    assert two_args == [(1, 4)]
    assert not report(lambda d, t, s: False, 0, 1, "Reading page")
    assert report(None, 0, 1, "anything")


def test_word_export_counts_pages_read_and_written(fixture_dir, tmp_path):
    rec = Recorder()
    with pymupdf.open(fixture_dir / "text.pdf") as doc:
        export(doc, [0, 1, 2], tmp_path / "out.docx", ExportFormat.DOCX, ExportOptions(), rec)
    assert rec.having("Reading page") == ["Reading page 1 of 3", "Reading page 2 of 3", "Reading page 3 of 3"]
    assert rec.having("Writing page")[:3] == ["Writing page 1 of 3", "Writing page 2 of 3", "Writing page 3 of 3"]
    assert "Analyzing the page layout…" in rec.lines
    assert (tmp_path / "out.docx").exists()


def test_word_export_can_be_canceled_between_pages(fixture_dir, tmp_path):
    with pymupdf.open(fixture_dir / "text.pdf") as doc, pytest.raises(ExportCanceled):
        export(doc, [0, 1, 2], tmp_path / "out.docx", ExportFormat.DOCX, ExportOptions(), Recorder("Reading page 2"))
    assert not (tmp_path / "out.docx").exists() and not list(tmp_path.glob(".~planetpdf-*"))
    import logging

    assert not any(type(f).__name__ == "_Pdf2docxProgress" for f in logging.getLogger().filters)


def test_pdf2docx_leaves_the_console_and_logging_alone():
    """User report 2026-10-02: "The `fitz` API is deprecated" and "[WARNING] Ignore Line
    ... due to overlap" in the console. pdf2docx's import sets up root logging (and its
    `import fitz` prints PyMuPDF's notice); its conversion warnings went to the console."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import logging
        from planetpdf.core import export as E
        root = logging.getLogger()
        before = (list(root.handlers), root.level)
        E._import_pdf2docx()
        assert (list(root.handlers), root.level) == before, (root.handlers, root.level)
        with E._Pdf2docxProgress(None, 1) as watch:
            logging.warning('Ignore Line "%s" due to overlap', "CONTENTS")  # (what pdf2docx does)
            logging.error("Ignore page %d due to parsing page error: %s", 2, "bad table")
        assert watch.problems == ["Ignore page 2 due to parsing page error: bad table"], watch.problems
        assert root.level == before[1] and not root.filters
        print("ok")
    """)
    run = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "ok", run.stdout  # (no "fitz API is deprecated")
    assert "WARNING" not in run.stderr and "fitz" not in run.stderr, run.stderr


def test_compare_counts_both_documents_then_the_changes(fixture_dir, tmp_path):
    with pymupdf.open(fixture_dir / "text.pdf") as old, pymupdf.open(fixture_dir / "text.pdf") as new:
        new[0].insert_text((72, 700), "A brand new line", fontname="helv", fontsize=11)
        rec = Recorder()
        result = C.compare(old, new, rec)
        pages = old.page_count
        assert rec.having("Reading the old document: page") == [
            f"Reading the old document: page {n} of {pages}" for n in range(1, pages + 1)]
        assert rec.having("Reading the new document: page")[-1] == f"Reading the new document: page {pages} of {pages}"
        assert rec.having("Comparing changed passage") and result.changes
        rec = Recorder()
        C.comparison_report(old, new, result, tmp_path / "report.pdf", progress=rec)
        assert rec.having("Drawing page pair")[-1] == f"Drawing page pair {pages} of {pages}"
        assert rec.having("Saving the report")
        with pytest.raises(C.ReportCanceled):
            C.comparison_report(old, new, result, tmp_path / "stopped.pdf", progress=Recorder("Drawing page pair 2"))
        assert not (tmp_path / "stopped.pdf").exists()


def test_reduce_size_numbers_its_steps_with_real_counts(fixture_dir, tmp_path):
    rec = Recorder()
    with pymupdf.open(fixture_dir / "text.pdf") as doc:
        reduce_size(doc, tmp_path / "small.pdf", ReduceOptions(), 1000, rec)
    assert [line.split(":")[0] for line in rec.lines] == ["Step 1 of 4", "Step 2 of 4", "Step 3 of 4", "Step 4 of 4"]
    assert "scanned" in rec.lines[0] and "image" in rec.lines[1] and "font" in rec.lines[2] and "page" in rec.lines[3]


def test_compressing_scans_counts_the_scanned_pictures(tmp_path):
    from tests.core.test_optimize import scanned_pdf

    rec = Recorder()
    with pymupdf.open(scanned_pdf(tmp_path / "scans.pdf")) as doc:
        reduce_size(doc, tmp_path / "small.pdf", ReduceOptions(), 1000, rec)
    assert rec.having("Step 1 of 4: checking scanned picture") == [
        "Step 1 of 4: checking scanned picture 1 of 3", "Step 1 of 4: checking scanned picture 2 of 3",
        "Step 1 of 4: checking scanned picture 3 of 3"]


@pytest.fixture
def marked(pdf_copy):
    session = PdfSession.open(pdf_copy("text.pdf"))
    for hit in search_pages(session, PRESETS["ssn"].pattern):
        M.add_mark(session, M.make_spec(hit.page, hit.rects, "SSN"))
    yield session
    session.close()


def test_redaction_counts_marks_pages_and_checks(marked, tmp_path):
    marks = len(M.list_marks(marked))
    pages = len({m.page for m in M.list_marks(marked)})
    rec = Recorder()
    result = redact_to_file(marked, tmp_path / "out.pdf", RedactionOptions(), SanitizeOptions(), progress=rec)
    assert result.report.passed
    assert rec.having("Reading the text under redaction mark")[-1] == \
        f"Reading the text under redaction mark {marks} of {marks}"
    assert rec.having("Applying redactions: marked page") == [
        f"Applying redactions: marked page {n} of {pages}" for n in range(1, pages + 1)]
    assert rec.having(f"Removing hidden information ({len(SanitizeOptions.__dataclass_fields__)} kinds")
    assert rec.having("Writing the new file")
    assert rec.having("Verifying: reading page")[-1] == f"Verifying: reading page {marked.page_count} of {marked.page_count}"
    assert rec.having("Verifying: searching for removed text")
    assert rec.having("Verifying: checking the redacted areas, marked page")[-1].endswith(f"{pages} of {pages}")


def test_redaction_cancel_writes_nothing(marked, tmp_path):
    with pytest.raises(RedactionCanceled):
        redact_to_file(marked, tmp_path / "out.pdf", RedactionOptions(), progress=Recorder("Applying redactions"))
    assert not (tmp_path / "out.pdf").exists()


def test_split_counts_files_and_stops_on_cancel(pdf_copy, tmp_path):
    session = PdfSession.open(pdf_copy("text.pdf"))
    try:
        groups = [("", [i]) for i in range(session.page_count)]
        rec = Recorder()
        paths = P.split_document(session, groups, tmp_path / "all", "part", rec)
        assert rec.lines == [f"Writing file {n} of {len(groups)}" for n in range(1, len(groups) + 1)]
        assert len(paths) == len(groups)
        paths = P.split_document(session, groups, tmp_path / "some", "part", Recorder("Writing file 2"))
        assert len(paths) == 1  # (stopped before the second file)
    finally:
        session.close()
