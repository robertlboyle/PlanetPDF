"""PlanetPDF's AI text reader (Unlimited-OCR): run by the optional component's own Python, which has
PyTorch (CUDA) and Transformers; PlanetPDF itself doesn't (``core/ai_ocr.py``).

Talks JSON lines over its standard input and output (private pipes between PlanetPDF and this
process):

    in   {"cmd": "read", "id": "pass-0", "pages": ["C:/…/page-00001.png", …]}
    out  {"id": "pass-0", "page": 3}               (the pages read so far)
    out  {"id": "pass-0", "final": true, "text": "<|det|>title [..]<|/det|>…"}
    in   {"cmd": "check"}  →  {"final": true, "card": "NVIDIA …", "memory_gb": 12.0}
    out  {"error": "…"}                             (a request that failed; the helper carries on)

Only replies go to standard output: whatever the model and its libraries print goes to standard
error. The model loads on the first request and stays on the graphics card; after
PLANETPDF_AI_OCR_IDLE seconds (default 300) with no request the helper quits, freeing the card's
memory. The model's own code comes from the model folder (a reviewed copy, pinned when the component
was built), never from the internet: HF_HUB_OFFLINE is set before Transformers loads.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import sys
import tempfile
import threading
import traceback
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
OUT = sys.stdout  # (replies only)


def send(message: dict) -> None:
    OUT.write(json.dumps(message) + "\n")
    OUT.flush()


class Reader:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.model = self.tokenizer = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("no NVIDIA graphics card is available to PyTorch (CUDA)")
        path = str(self.model_dir)
        self.tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True, local_files_only=True)
        model = AutoModel.from_pretrained(path, trust_remote_code=True, use_safetensors=True,
                                          torch_dtype=torch.bfloat16, local_files_only=True)
        self.model = model.eval().cuda()

    def check(self) -> dict:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("no NVIDIA graphics card is available to PyTorch (CUDA)")
        props = torch.cuda.get_device_properties(0)
        return {"card": props.name, "memory_gb": round(props.total_memory / 1024 ** 3, 1)}

    def read(self, pages: list[str]) -> str:
        """The pages in one pass (Unlimited-OCR's multi-page mode: base size, 1024 px)."""
        self.load()
        with tempfile.TemporaryDirectory(prefix="planetpdf-ai-ocr-out-") as out:
            result = self.model.infer_multi(
                self.tokenizer, prompt="<image>Multi page parsing.", image_files=pages, output_path=out,
                image_size=1024, max_length=32768, no_repeat_ngram_size=35, ngram_window=1024,
                save_results=True)
            if isinstance(result, str) and result.strip():
                return result
            if isinstance(result, (list, tuple)) and result and all(isinstance(r, str) for r in result):
                return "\n\n".join(result)
            # (the model writes its result into the output folder: take its text files, in name order)
            texts = [p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(Path(out).rglob("*")) if p.suffix.lower() in (".mmd", ".md", ".txt")]
            if not texts:
                raise RuntimeError("the model returned no text")
            return "\n\n".join(texts)


def handle(reader: Reader, request: dict) -> list[dict]:
    if request.get("cmd") == "check":
        return [{"final": True, **reader.check()}]
    if request.get("cmd") == "read":
        pages = [str(p) for p in request.get("pages") or []]
        text = reader.read(pages)
        return [{"page": len(pages)}, {"final": True, "text": text}]
    return [{"error": f"unknown command {request.get('cmd')!r}"}]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    reader = Reader(Path(args.model))
    idle = float(os.environ.get("PLANETPDF_AI_OCR_IDLE", "300"))
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in sys.stdin:
            lines.put(line)
        lines.put(None)  # (PlanetPDF closed: stop)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=idle)
        except queue.Empty:
            return 0  # (idle: free the graphics card)
        if line is None:
            return 0
        try:
            request = json.loads(line)
        except ValueError:
            continue
        rid = request.get("id")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                replies = handle(reader, request)
            for message in replies:
                send({"id": rid, **message})
        except Exception as exc:  # (reported to PlanetPDF, which shows it; the helper carries on)
            traceback.print_exc(file=sys.stderr)
            send({"id": rid, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    raise SystemExit(main())
