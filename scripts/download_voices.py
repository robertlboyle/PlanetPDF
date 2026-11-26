"""Download the Read Out Loud voice files into models/ (new machine; they aren't pip
packages and are git-ignored). Files already there are checked, not downloaded again.

    .venv\\Scripts\\python.exe scripts\\download_voices.py
    .venv\\Scripts\\python.exe scripts\\download_voices.py --check   # only check what's there

Kokoro: github.com/thewh1teagle/kokoro-onnx release model-files-v1.0 (Apache-2.0).
Piper: huggingface.co/rhasspy/piper-voices, Kristin (public domain) and Joe (CC0) only:
other popular Piper voices are licensed for non-commercial use only.

Every file is pinned to one version and its SHA-256 (security review 2026-11-25: a model file is
in effect code, and the Piper voices came from the main branch, which moves: Joe was retrained
there on 2026-11-09). Piper is pinned to commit ``PIPER_COMMIT`` (2026-11-24; the files 1.0.6
shipped). A file that doesn't match is refused (deleted, and the script fails). To move to newer
voices, change the commit here and the hashes in planetpdf/core/neural_speech.py
(``MODEL_SHA256``) on purpose, then run scripts/update_notices.py.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "models"
KOKORO_RELEASE = "model-files-v1.0"
KOKORO = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/{KOKORO_RELEASE}/"
PIPER_COMMIT = "5ec43b186ef915a195d3ddbcfd9916e76c503c3f"
PIPER = f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_COMMIT}/en/en_US/"

# (file in models/, where it comes from, SHA-256: PlanetPDF's own list, which it checks before loading)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from planetpdf.core.neural_speech import MODEL_SHA256  # noqa: E402

SOURCES = {
    "kokoro/kokoro-v1.0.onnx": KOKORO + "kokoro-v1.0.onnx",
    "kokoro/voices-v1.0.bin": KOKORO + "voices-v1.0.bin",
}
for _voice in ("kristin", "joe"):
    SOURCES[f"piper/en_US-{_voice}-medium.onnx"] = f"{PIPER}{_voice}/medium/en_US-{_voice}-medium.onnx"
    SOURCES[f"piper/en_US-{_voice}-medium.onnx.json"] = f"{PIPER}{_voice}/medium/en_US-{_voice}-medium.onnx.json"
    SOURCES[f"piper/en_US-{_voice}-medium.MODEL_CARD.txt"] = f"{PIPER}{_voice}/medium/MODEL_CARD"
FILES = [(name, SOURCES[name], digest) for name, digest in MODEL_SHA256.items()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    check_only = "--check" in (argv if argv is not None else sys.argv[1:])
    problems = 0
    for n, (name, url, expected) in enumerate(FILES, 1):
        target = ROOT / name
        if target.is_file():
            if sha256(target) == expected:
                print(f"File {n} of {len(FILES)}: have  {name} (checked)")
                continue
            print(f"File {n} of {len(FILES)}: {name} isn't the pinned file", file=sys.stderr)
            if check_only:
                problems += 1
                continue
            target.unlink()
        elif check_only:
            print(f"File {n} of {len(FILES)}: missing {name}", file=sys.stderr)
            problems += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"File {n} of {len(FILES)}: get   {name}  ({url})")
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        if sha256(partial) != expected:
            partial.unlink()
            print(f"  {name}: the download doesn't match its pinned SHA-256: refused", file=sys.stderr)
            problems += 1
            continue
        partial.replace(target)
    if problems:
        print(f"{problems} file(s) missing or not as pinned.", file=sys.stderr)
        return 1
    print(f"Voice files are in {ROOT}, every one as pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
