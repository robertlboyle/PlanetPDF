"""Assemble the optional AI text reading component (Unlimited-OCR) for the add-on installer
``packaging/PlanetPDF-AI.iss`` (user 2026-10-21: optional, "put in the notes that an NVidia graphics
card is needed"; Windows only).

    python scripts/build_ai_ocr.py            (into %LOCALAPPDATA%\\PlanetPDF-build\\ai-ocr; skips what's there)

Steps, each counted: (1) Python's embeddable package, (2) pip, (3) PyTorch with CUDA and the model's
libraries, pinned, (4) the model's files at the reviewed revision ``core.ai_ocr.MODEL_REVISION``
(its code and weights; downloads checked against Hugging Face's sizes), (5) a manifest. About 10 GB.
Outside Dropbox (size, file locks), like the rest of the build.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from planetpdf.core.ai_ocr import MODEL_REVISION, check_model_code  # noqa: E402

PYTHON_VERSION = "3.12.10"  # (the model card tested Python 3.12)
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"  # (CUDA 12.8: NVIDIA drivers from 570 on)
TORCH = ["torch==2.10.0", "torchvision==0.25.0"]  # (the model card's tested versions)
LIBRARIES = ["transformers==4.57.1", "einops==0.8.2", "Pillow==12.1.1", "addict==2.4.0", "easydict==1.13",
             "psutil==7.1.0", "matplotlib==3.10.7", "safetensors==0.6.2", "accelerate==1.10.1"]
MODEL = "baidu/Unlimited-OCR"
SKIP = ("assets/", "wheel/", ".pdf", "README.md", ".gitattributes")


def step(n: int, text: str) -> None:
    print(f"\n=== Step {n} of 5: {text} ===", flush=True)


def download(url: str, target: Path, size: int | None = None) -> None:
    if target.exists() and (size is None or target.stat().st_size == size):
        print(f"  {target.name}: already here")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as out:
        total = int(response.headers.get("Content-Length") or size or 0)
        done, last = 0, -1
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            mb = done // (1 << 20)
            if total and mb // 100 != last:
                last = mb // 100
                print(f"  {target.name}: {mb:,} of {total // (1 << 20):,} MB", flush=True)
    if size is not None and part.stat().st_size != size:
        raise SystemExit(f"{target.name}: got {part.stat().st_size} bytes, expected {size}")
    part.replace(target)


def main() -> int:
    out = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PlanetPDF-build" / "ai-ocr"
    python_dir, model_dir = out / "python", out / "model"
    python = python_dir / "python.exe"

    step(1, f"Python {PYTHON_VERSION} (embeddable)")
    if not python.exists():
        archive = out / "python-embed.zip"
        download(PYTHON_URL, archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(python_dir)
        archive.unlink()
        pth = next(python_dir.glob("python3*._pth"))  # (let it use site-packages: pip's libraries)
        pth.write_text(pth.read_text().replace("#import site", "import site") + "Lib\\site-packages\n")
    print(f"  {python}")

    step(2, "pip")
    if subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode:
        get_pip = out / "get-pip.py"
        download(GET_PIP, get_pip)
        subprocess.run([str(python), str(get_pip), "--no-warn-script-location"], check=True)
        get_pip.unlink()

    step(3, "PyTorch (CUDA 12.8) and the model's libraries")
    pip = [str(python), "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check"]
    subprocess.run(pip + ["--index-url", TORCH_INDEX] + TORCH, check=True)
    subprocess.run(pip + LIBRARIES, check=True)

    step(4, f"the model ({MODEL} at {MODEL_REVISION[:7]})")
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL}/revision/{MODEL_REVISION}?blobs=true",
                                timeout=60) as response:
        info = json.load(response)
    files = [(s["rfilename"], s.get("size")) for s in info["siblings"]
             if not any(s["rfilename"].startswith(x) or s["rfilename"].endswith(x) for x in SKIP)]
    index = next((f for f, _ in files if f == "model.safetensors.index.json"), None)
    if index:  # (only the weight files the index names: the repository also holds a second copy)
        download(f"https://huggingface.co/{MODEL}/resolve/{MODEL_REVISION}/{index}", model_dir / index)
        wanted = set(json.loads((model_dir / index).read_text())["weight_map"].values())
        files = [(f, s) for f, s in files if not f.endswith(".safetensors") or f in wanted]
    for n, (name, size) in enumerate(files, start=1):
        print(f"  file {n} of {len(files)}: {name}", flush=True)
        download(f"https://huggingface.co/{MODEL}/resolve/{MODEL_REVISION}/{name}", model_dir / name, size)
    check_model_code(model_dir)  # (the reviewed code, byte for byte: security review 2026-11-25)

    step(5, "manifest")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    (out / "ai-ocr.json").write_text(json.dumps({
        "model": MODEL, "revision": MODEL_REVISION, "python": PYTHON_VERSION, "torch": TORCH,
        "libraries": LIBRARIES, "bytes": total}, indent=2), encoding="utf-8")
    print(f"Done: {out} ({total / (1 << 30):.1f} GB)")
    shutil.rmtree(out / "python" / "Scripts", ignore_errors=True)  # (pip's launchers: not needed to run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
