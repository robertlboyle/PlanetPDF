"""Download the Read Out Loud voice files into models/ (new machine; they aren't pip
packages and are git-ignored). Skips files that are already there.

    .venv\\Scripts\\python.exe scripts\\download_voices.py

Kokoro: github.com/thewh1teagle/kokoro-onnx release model-files-v1.0 (Apache-2.0).
Piper: huggingface.co/rhasspy/piper-voices, Kristin (public domain) and Joe (CC0) only:
other popular Piper voices are licensed for non-commercial use only.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "models"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/"
FILES = [
    ("kokoro/kokoro-v1.0.onnx", KOKORO + "kokoro-v1.0.onnx"),
    ("kokoro/voices-v1.0.bin", KOKORO + "voices-v1.0.bin"),
]
for voice in ("kristin", "joe"):
    base = f"{PIPER}{voice}/medium/en_US-{voice}-medium"
    FILES += [(f"piper/en_US-{voice}-medium.onnx", base + ".onnx"),
              (f"piper/en_US-{voice}-medium.onnx.json", base + ".onnx.json"),
              (f"piper/en_US-{voice}-medium.MODEL_CARD.txt", f"{PIPER}{voice}/medium/MODEL_CARD")]


def main() -> int:
    for name, url in FILES:
        target = ROOT / name
        if target.is_file() and target.stat().st_size > 0:
            print(f"have  {name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"get   {name}  ({url})")
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.replace(target)
    print(f"Voice files are in {ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
