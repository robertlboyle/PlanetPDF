"""AI text reading for exports (core/ai_ocr.py), with a stand-in helper that speaks the real
helper's protocol (the model itself needs an NVIDIA card: tried on another computer)."""

from __future__ import annotations

import sys
import textwrap

import pymupdf
import pytest

from planetpdf.core import ai_ocr as A
from planetpdf.core.export import ExportFormat, ExportOptions, export

SAMPLE = (
    "<|det|>title [10, 10, 500, 40]<|/det|># Quarterly Report\n"
    "<|det|>text [10, 50, 500, 90]<|/det|>Sales grew in every re-\ngion this quarter.\n"
    "<|det|>image [10, 100, 300, 300]<|/det|>\n"
    "<|det|>table [10, 310, 500, 400]<|/det|><table><tr><td>Region</td><td>Sales</td></tr>"
    "<tr><td>East</td><td>1,200</td></tr></table>\n"
    "<|det|>page_footer [10, 780, 500, 790]<|/det|>Page 1\n"
)


def test_the_models_blocks_become_a_document():
    blocks = A.parse(SAMPLE)
    assert [b.kind for b in blocks] == ["heading", "paragraph", "table"]  # (picture and footer left out)
    assert blocks[0].text == "Quarterly Report" and blocks[0].level == 1
    assert blocks[1].text == "Sales grew in every region this quarter."  # (lines joined, hyphen healed)
    assert blocks[2].rows == [["Region", "Sales"], ["East", "1,200"]]


def test_markdown_tables_and_untagged_text():
    blocks = A.parse("<|det|>table<|/det|>| A | B |\n|---|---|\n| 1 | 2 |")
    assert blocks[0].rows == [["A", "B"], ["1", "2"]]
    assert [b.text for b in A.parse("First paragraph.\n\nSecond one.")] == ["First paragraph.", "Second one."]


def test_a_paragraph_cut_between_passes_flows_on():
    first = [A.Block("paragraph", "The meeting was moved to")]
    second = [A.Block("paragraph", "next week because of the holiday."), A.Block("heading", "Notes", level=2)]
    joined = A.join_passes([first, second])
    assert [b.text for b in joined] == ["The meeting was moved to next week because of the holiday.", "Notes"]


FAKE = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("cmd") == "crash":
            sys.exit(3)
        pages = request.get("pages", [])
        print("loading weights...", flush=True)  # (chatter the client must skip)
        text = ""
        for n, page in enumerate(pages, start=1):
            print(json.dumps({"id": request["id"], "page": n}), flush=True)
            name = page.replace("\\\\", "/").rsplit("/", 1)[-1]
            text += f"<|det|>title<|/det|>{name}\\n<|det|>text<|/det|>Words on {name}.\\n"
        print(json.dumps({"id": request["id"], "final": True, "text": text}), flush=True)
''')


@pytest.fixture
def helper(tmp_path):
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE, encoding="utf-8")
    h = A.Helper([sys.executable, "-u", str(script)])
    yield h
    h.stop()


def _doc(pages=3):
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"Scanned page {n + 1}")
    return doc


def test_reading_counts_pages_in_passes(helper, monkeypatch):
    monkeypatch.setattr(A, "PAGES_PER_PASS", 2)
    seen = []
    blocks = A.read_document(_doc(3), [0, 1, 2], lambda d, t, s="": seen.append((s, d, t)) or True, helper=helper)
    assert [b.text for b in blocks if b.kind == "heading"] == ["page-00001.png", "page-00002.png", "page-00003.png"]
    assert ("Reading page", 3, 3) in seen and any(s.startswith("Starting the AI") for s, _d, _t in seen)
    assert helper.process.poll() is None  # (it stays loaded for the next job)


def test_cancel_stops_the_helper(helper):
    with pytest.raises(A.AiOcrCanceled):
        A.read_document(_doc(2), [0, 1], lambda d, t, s="": s != "Reading page", helper=helper)
    assert helper.process is None  # (stopped: the graphics card's memory is freed)


def test_a_helper_that_dies_is_reported(helper):
    with pytest.raises(A.AiOcrError, match="stopped unexpectedly"):
        helper.request({"cmd": "crash", "id": "x"})


def test_export_to_word_and_text_with_ai_reading(helper, tmp_path, monkeypatch):
    import docx

    monkeypatch.setattr(A.Helper, "shared", classmethod(lambda cls, command=None: helper))
    options = ExportOptions(ai_reading=True)
    [word] = export(_doc(2), [0, 1], tmp_path / "out.docx", ExportFormat.DOCX, options)
    document = docx.Document(str(word))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert headings == ["page-00001.png", "page-00002.png"]
    [text] = export(_doc(1), [0], tmp_path / "out.txt", ExportFormat.TXT, options)
    assert "Words on page-00001.png." in text.read_text(encoding="utf-8")
    [page] = export(_doc(1), [0], tmp_path / "out.html", ExportFormat.HTML, options)
    assert "<h1>page-00001.png</h1>" in page.read_text(encoding="utf-8")


def test_status_says_why_it_cant_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(A, "runtime_dir", lambda: None)
    assert not A.status().ready and "component isn't installed" in A.status().reason
    monkeypatch.setattr(A, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(A, "nvidia_card", lambda: None)
    assert "No NVIDIA graphics card" in A.status().reason
    monkeypatch.setattr(A, "nvidia_card", lambda: ("NVIDIA GeForce MX450", 2.0))
    assert "too little memory" in A.status().reason
    monkeypatch.setattr(A, "nvidia_card", lambda: ("NVIDIA RTX 4070", 12.0))
    assert A.status().ready and "RTX 4070" in A.status().reason


def test_the_models_code_is_checked_before_it_runs(tmp_path):
    """Security review 2026-11-25: trust_remote_code ran whatever .py files the model folder had."""
    import pytest

    from planetpdf.core import ai_ocr as A

    folder = tmp_path / "model"
    folder.mkdir()
    with pytest.raises(A.AiOcrError, match="isn't the reviewed one"):
        A.check_model_code(folder)
    for name in A.MODEL_CODE_SHA256:
        (folder / name).write_bytes(b"something else")
    with pytest.raises(A.AiOcrError, match="isn't the reviewed one"):
        A.check_model_code(folder)
