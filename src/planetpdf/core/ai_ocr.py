"""AI text reading for exports: Baidu's **Unlimited-OCR** (MIT, a 3-billion-parameter vision-language
model; user 2026-10-21: "using it for exports like converting to Word is the best use of it").

It reads a whole document in one pass and returns its text in reading order, as blocks (titles,
paragraphs, tables…), flowing across pages: just right for Word, Rich Text, HTML and text exports
of scans. It gives no word positions, so Recognize Text (the searchable layer) stays Tesseract.

**Optional, Windows with an NVIDIA graphics card (about 8 GB)**: the installer's "AI text reading"
component puts its own Python + PyTorch (CUDA) + Transformers + the model in ``<install>/ai-ocr``
(``scripts/build_ai_ocr.py``); updates never carry it. ``status()`` says whether it can run here
and why not.

**The helper** (``resources/ai_ocr/helper.py``, run by that Python) is started in the background on
first use and keeps the model on the card; PlanetPDF talks to it over its standard input and output,
private pipes only these two processes hold (JSON lines: ``read`` → ``progress``… → ``result``). It
quits by itself after ``IDLE_SECONDS`` without work, freeing the card's memory.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from planetpdf.core.document import PdfError
from planetpdf.core.progress import Progress, report

ENV_RUNTIME = "PLANETPDF_AI_OCR"  # (a runtime folder elsewhere, e.g. on a test laptop)
RUNTIME_FOLDER = "ai-ocr"
MODEL_REVISION = "8249d20b2822a7d8a4c22c8d8c9069b5ac36bda3"  # (huggingface.co/baidu/Unlimited-OCR; reviewed code)
# The model's own code (Transformers runs it: trust_remote_code) and the files that choose which
# code runs, at that revision: checked before the helper starts (security review 2026-11-25). The
# weights are safetensors (data, no code); tokenizer.json is data too.
MODEL_CODE_SHA256 = {
    "configuration_deepseek_v2.py": "b8470dd616ba8745fce6e27b093aef73a098863cc891b2477dcf9326a36000f7",
    "conversation.py": "ec7b6ce89bcda643de1f43269ffa66a7b2e65dc3ed30e427958f776546b4ba03",
    "deepencoder.py": "0ae2fb6d1e5ae8cf100fc32f854830acd08c821a0a1f23a94a76588c222ddcf2",
    "modeling_deepseekv2.py": "74e36e6bd0ba7bc565ef76464a99baa8e6bccb710ae9c1007b54ac30b855fa4c",
    "modeling_unlimitedocr.py": "3894696d68197ad00e89f0963880645dfce49b1933a9f27e78fbc05983547a78",
    "config.json": "55e6c5179eea054a3fca99e3e41424649d22f574fba4c5f0737ef9c9095526e4",
    "tokenizer_config.json": "a0cbe8464049da1f891b7a12676de06af4cb54c130995d42f71adc1c30c6e9f3",
    "processor_config.json": "2c82d81788aac5146db01d842138e8f5921daa907d2fd61c3c6c757d41dfb1d7",
    "preprocessor_config.json": "62cef9e2db4ae494ff2ecccaedfeef884d8ba880c25d17f14e81f3d1c3c899cb",
    "generation_config.json": "dc97b67afaad12b72320c4230f292be8cb5a73fcb4caa9072074fe5a341fb003",
    "special_tokens_map.json": "ab4bd57ce17d62e39e0a39e739de1e407484f090f0b2c7e391312bca7a5b061a",
    "chat_template.jinja": "690dcec206a691b4876d70db7092b6b0ae14cb8b51396e4c89a25b777f5a6830",
}


def check_model_code(model_dir: Path) -> None:
    """The model's code and configuration are exactly the reviewed ones, and there's no other
    code beside them; else ``AiOcrError`` (nothing is loaded)."""
    import hashlib

    model_dir = Path(model_dir)
    for name, expected in MODEL_CODE_SHA256.items():
        try:
            actual = hashlib.sha256((model_dir / name).read_bytes()).hexdigest()
        except OSError:
            actual = ""
        if actual != expected:
            raise AiOcrError(f"The AI text reader's model file {name} isn't the reviewed one, so it isn't run. "
                             "Install the AI component again.")
    extra = sorted(p.name for p in model_dir.glob("*.py") if p.name not in MODEL_CODE_SHA256)
    if extra:
        raise AiOcrError(f"The AI text reader's model folder holds code that wasn't reviewed ({', '.join(extra)}), "
                         "so it isn't run. Install the AI component again.")
MIN_MEMORY_GB = 7.5
IDLE_SECONDS = 300
PAGES_PER_PASS = 8  # (its context is 32k tokens: a few pages at a time, joined afterwards)
RENDER_DPI = 150  # (the model looks at pages at 1024 px)
NEEDS = "AI text reading needs PlanetPDF's optional AI component and an NVIDIA graphics card with about 8 GB of memory."


class AiOcrError(PdfError):
    pass


class AiOcrCanceled(AiOcrError):
    pass


# --- where it is, and whether it can run here ---------------------------------------------------------


def runtime_dir() -> Path | None:
    """The optional component: ``PLANETPDF_AI_OCR``, else ``<install>/ai-ocr`` beside the launcher."""
    override = os.environ.get(ENV_RUNTIME, "").strip()
    candidates = [Path(override)] if override else []
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        candidates += [exe.parents[2] / RUNTIME_FOLDER, exe.parent / RUNTIME_FOLDER]
    for folder in candidates:
        if (folder / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")).is_file():
            return folder
    return None


def helper_command(runtime: Path) -> list[str]:
    python = runtime / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    from planetpdf.core.resources import resources_dir

    return [str(python), "-u", str(resources_dir() / "ai_ocr" / "helper.py"), "--model", str(runtime / "model")]


def nvidia_card() -> tuple[str, float] | None:
    """(name, memory in GB) of the first NVIDIA card, from the driver's nvidia-smi; None without one."""
    smi = shutil.which("nvidia-smi")
    if smi is None and sys.platform == "win32" and os.environ.get("SystemRoot"):
        candidate = Path(os.environ["SystemRoot"]) / "System32" / "nvidia-smi.exe"
        smi = str(candidate) if candidate.is_file() else None
    if smi is None:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name, memory = out.strip().splitlines()[0].rsplit(",", 1)
        return name.strip(), float(memory) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


@dataclass
class Status:
    ready: bool
    reason: str  # why not (shown on the grayed option), or what it will use
    card: str = ""


def status() -> Status:
    from planetpdf.core import policy

    if policy.reason("ai_reader"):
        return Status(False, policy.reason("ai_reader"))
    if sys.platform != "win32" and not os.environ.get(ENV_RUNTIME):
        return Status(False, "AI text reading runs on Windows with an NVIDIA graphics card.")
    runtime = runtime_dir()
    if runtime is None:
        return Status(False, "The optional AI text reading component isn't installed (PlanetPDF Setup ▸ "
                             "Components). It needs an NVIDIA graphics card with about 8 GB of memory.")
    card = nvidia_card()
    if card is None:
        return Status(False, "No NVIDIA graphics card was found: AI text reading needs one, with about 8 GB of memory.")
    name, memory = card
    if memory < MIN_MEMORY_GB:
        return Status(False, f"The graphics card ({name}, {memory:.0f} GB) has too little memory: "
                             "AI text reading needs about 8 GB.", name)
    return Status(True, f"Uses the {name} ({memory:.0f} GB).", name)


# --- the helper ------------------------------------------------------------------------------------------


class Helper:
    """The background helper process: started on first use, shared, one request at a time."""

    _shared: Helper | None = None
    _lock = threading.Lock()

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.process: subprocess.Popen | None = None
        self._busy = threading.Lock()

    @classmethod
    def shared(cls, command: list[str] | None = None) -> Helper:
        with cls._lock:
            if cls._shared is None or (command is not None and cls._shared.command != command):
                if command is None:
                    runtime = runtime_dir()
                    if runtime is None:
                        raise AiOcrError(NEEDS)
                    check_model_code(runtime / "model")  # (before any of its code can run)
                    command = helper_command(runtime)
                cls._shared = Helper(command)
            return cls._shared

    def _start(self) -> subprocess.Popen:
        if self.process is not None and self.process.poll() is None:
            return self.process
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, creationflags=flags,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PLANETPDF_AI_OCR_IDLE": str(IDLE_SECONDS)})
        except OSError as exc:
            raise AiOcrError(f"The AI text reader couldn't start: {exc}") from exc
        return self.process

    def request(self, message: dict, on_message: Callable[[dict], bool] | None = None) -> dict:
        """Send one request; ``on_message`` sees each progress line (False = cancel: the helper
        is stopped, so the card's memory is freed at once). Returns the final message."""
        with self._busy:
            process = self._start()
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except OSError:  # (it had quit while idle: start it again)
                self.process = None
                process = self._start()
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    self.process = None
                    raise AiOcrError("The AI text reader stopped unexpectedly (out of graphics memory?).")
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # (a library's chatter on standard output)
                if "error" in reply:
                    raise AiOcrError(f"The AI text reader: {reply['error']}")
                if reply.get("final"):
                    return reply
                if on_message is not None and on_message(reply) is False:
                    self.stop()
                    raise AiOcrCanceled("Canceled.")

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.kill()


# --- what it returns ---------------------------------------------------------------------------------------

DET = re.compile(r"<\|det\|>\s*([^<\s\[]+)\s*(?:\[[^\]]*\])?\s*<\|/det\|>")
HEADINGS = {"title": 1, "doc_title": 1, "section_title": 2, "section-header": 2, "sub_title": 2, "header_title": 2,
            "paragraph_title": 2, "heading": 2}
SKIPPED = {"image", "figure", "header", "footer", "page_header", "page_footer", "page_number", "number", "seal"}


@dataclass
class Block:
    kind: str  # "heading", "paragraph", "table", "list", "caption", "formula"
    text: str = ""
    level: int = 0  # headings: 1, 2
    rows: list[list[str]] = field(default_factory=list)  # tables


def _table_rows(text: str) -> list[list[str]] | None:
    if "<table" in text.lower():
        rows = []
        for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", text):
            cells = re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)
            rows.append([html.unescape(re.sub(r"(?s)<[^>]+>", " ", c)).strip() for c in cells])
        return rows or None
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) >= 2 and all(ln.startswith("|") for ln in lines):
        rows = []
        for ln in lines:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # (the markdown divider)
            rows.append(cells)
        return rows
    return None


def parse(text: str) -> list[Block]:
    """The model's tagged text (``<|det|>kind [box]<|/det|>words``) as blocks, in order. Text without
    tags becomes paragraphs (one per blank-line-separated run)."""
    blocks: list[Block] = []
    marks = list(DET.finditer(text))
    if not marks:
        for part in re.split(r"\n\s*\n", text.strip()):
            if part.strip():
                blocks.append(_block("text", part))
        return blocks
    for n, mark in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(text)
        kind, words = mark.group(1).lower(), text[mark.end():end].strip()
        if kind in SKIPPED or not words:
            continue
        blocks.append(_block(kind, words))
    return blocks


def _block(kind: str, words: str) -> Block:
    rows = _table_rows(words) if kind in ("table", "text") else None
    if rows:
        return Block("table", rows=rows)
    words = re.sub(r"(?m)^#+\s*", "", words) if kind in HEADINGS else words
    if kind in HEADINGS:
        return Block("heading", " ".join(words.split()), level=HEADINGS[kind])
    if kind in ("list", "list_item"):
        return Block("list", words.strip())
    if kind in ("caption", "figure_caption", "table_caption"):
        return Block("caption", " ".join(words.split()))
    if kind in ("formula", "equation"):
        return Block("formula", words.strip())
    lines = [ln.strip() for ln in words.splitlines()]
    return Block("paragraph", _join_lines(lines))


def _join_lines(lines: list[str]) -> str:
    """A paragraph's lines as flowing text (a word broken with a hyphen at a line end is joined)."""
    out = ""
    for line in (ln for ln in lines if ln):
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return out


def join_passes(parts: list[list[Block]]) -> list[Block]:
    """Several passes (a few pages each) as one document: a paragraph cut at a pass's end (no
    sentence end) continues with the next pass's first paragraph."""
    out: list[Block] = []
    for blocks in parts:
        if out and blocks and out[-1].kind == "paragraph" and blocks[0].kind == "paragraph" \
                and not re.search(r"[.!?:;\"”)]$", out[-1].text) and blocks[0].text[:1].islower():
            out[-1] = Block("paragraph", _join_lines([out[-1].text, blocks[0].text]))
            blocks = blocks[1:]
        out.extend(blocks)
    return out


# --- reading a document ---------------------------------------------------------------------------------


def read_document(doc, pages: list[int], progress: Progress | None = None, *, helper: Helper | None = None) -> list[Block]:
    """The pages (0-based) read by the model, as one flowing document. ``progress`` counts pages."""
    import pymupdf

    helper = helper or Helper.shared()
    work = Path(tempfile.mkdtemp(prefix="planetpdf-ai-ocr-"))
    try:
        files = []
        for n, index in enumerate(pages):
            if not report(progress, n, len(pages), "Preparing page"):
                raise AiOcrCanceled("Canceled.")
            path = work / f"page-{index + 1:05d}.png"
            doc[index].get_pixmap(matrix=pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)).save(str(path))
            files.append(str(path))
        if not report(progress, 0, 0, "Starting the AI text reader (loading the model onto the graphics card)…"):
            raise AiOcrCanceled("Canceled.")
        parts, done = [], 0
        for start in range(0, len(files), PAGES_PER_PASS):
            chunk = files[start:start + PAGES_PER_PASS]

            def seen(reply: dict, base=done) -> bool:
                if "page" in reply:
                    return report(progress, base + int(reply["page"]), len(files), "Reading page")
                return True

            reply = helper.request({"cmd": "read", "id": f"pass-{start}", "pages": chunk}, seen)
            parts.append(parse(reply.get("text", "")))
            done += len(chunk)
            if not report(progress, done, len(files), "Reading page"):
                raise AiOcrCanceled("Canceled.")
        return join_passes(parts)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- writing it out ---------------------------------------------------------------------------------------


def to_text(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind == "table":
            out.append("\n".join("\t".join(row) for row in b.rows))
        elif b.kind == "list":
            out.append("\n".join(f"• {ln.lstrip('-*• ').strip()}" for ln in b.text.splitlines() if ln.strip()))
        else:
            out.append(b.text)
    return "\n\n".join(out) + "\n"


def to_html(blocks: list[Block], title: str = "") -> str:
    out = [f"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title></head><body>"]
    for b in blocks:
        if b.kind == "heading":
            out.append(f"<h{b.level}>{html.escape(b.text)}</h{b.level}>")
        elif b.kind == "table":
            rows = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>" for row in b.rows)
            out.append(f"<table border=\"1\">{rows}</table>")
        elif b.kind == "list":
            items = "".join(f"<li>{html.escape(ln.lstrip('-*• ').strip())}</li>" for ln in b.text.splitlines() if ln.strip())
            out.append(f"<ul>{items}</ul>")
        elif b.kind == "caption":
            out.append(f"<p><i>{html.escape(b.text)}</i></p>")
        else:
            out.append(f"<p>{html.escape(b.text)}</p>")
    out.append("</body></html>\n")
    return "\n".join(out)


def to_docx(blocks: list[Block], target: Path) -> Path:
    """A Word document: headings as Word's heading styles, tables as Word tables, lists as bullets."""
    import docx

    document = docx.Document()
    for b in blocks:
        if b.kind == "heading":
            document.add_heading(b.text, level=b.level)
        elif b.kind == "table" and b.rows:
            width = max(len(r) for r in b.rows)
            table = document.add_table(rows=len(b.rows), cols=width)
            table.style = "Table Grid"
            for r, row in enumerate(b.rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell
        elif b.kind == "list":
            for ln in (ln for ln in b.text.splitlines() if ln.strip()):
                document.add_paragraph(ln.lstrip("-*• ").strip(), style="List Bullet")
        elif b.kind == "caption":
            document.add_paragraph(b.text, style="Caption")
        else:
            document.add_paragraph(b.text)
    document.save(str(target))
    return Path(target)


def to_rtf(blocks: list[Block]) -> str:
    def esc(text: str) -> str:
        out = []
        for ch in text:
            if ch in "\\{}":
                out.append("\\" + ch)
            elif ord(ch) > 127:
                out.append(f"\\u{ord(ch) if ord(ch) < 32768 else ord(ch) - 65536}?")
            else:
                out.append(ch)
        return "".join(out)

    parts = [r"{\rtf1\ansi\deff0{\fonttbl{\f0 Calibri;}}\fs22"]
    for b in blocks:
        if b.kind == "heading":
            parts.append(rf"\pard\sa120\b\fs{32 if b.level == 1 else 26} {esc(b.text)}\b0\fs22\par")
        elif b.kind == "table":
            for row in b.rows:
                parts.append(r"\trowd" + "".join(rf"\cellx{1800 * (i + 1)}" for i in range(len(row)))
                             + "".join(rf"\intbl {esc(c)}\cell" for c in row) + r"\row")
            parts.append(r"\pard\par")
        elif b.kind == "list":
            for ln in (ln for ln in b.text.splitlines() if ln.strip()):
                parts.append(rf"\pard\li360\sa60 \u8226? {esc(ln.lstrip('-*• ').strip())}\par")
        else:
            parts.append(rf"\pard\sa120 {esc(b.text)}\par")
    parts.append("}")
    return "\n".join(parts)
