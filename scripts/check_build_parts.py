"""The parts a build takes from the build computer are the pinned ones (security list 2026-11-26,
item 9).

The Python packages and the voices are pinned by checksum. Two parts weren't: **Ghostscript** (its
whole folder is copied from the copy installed on the build computer) and **Tesseract's language
files** (eng and osd). Whatever was there went into the installer, so a changed file on that
computer would have been shipped to everyone.

    python scripts/check_build_parts.py --ghostscript "C:\\Program Files\\gs\\gs10.08.0" --tessdata "C:\\Program Files\\Tesseract-OCR\\tessdata"

compares them with ``packaging/pinned-parts.json`` and fails (exit 1) on any difference;
``scripts\\build.ps1`` runs it before anything is built. After installing a new Ghostscript or new
language files on purpose (from their publishers: check the download first), ``--pin`` writes the
new checksums; read ``git diff packaging/pinned-parts.json`` before committing.

Ghostscript is pinned as one digest over every file of the folders the installer takes (their
names and contents): its ``lib`` folder holds PostScript the interpreter runs, so every file counts.
The pins are per system (Windows' Ghostscript is Artifex's build; the Mac and Linux builds compile
it from a pinned source archive themselves).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "packaging" / "pinned-parts.json"
GHOSTSCRIPT_FOLDERS = ("bin", "lib", "Resource", "iccprofiles")  # (what build.ps1 copies; plus doc/COPYING)
LANGUAGES = ("eng", "osd")
SYSTEM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(home: Path, folders: tuple[str, ...] = GHOSTSCRIPT_FOLDERS) -> tuple[str, int]:
    """One SHA-256 over every file under ``folders`` of ``home`` (each file's path, with forward
    slashes, and its own SHA-256, in sorted order), and how many files that was."""
    files = sorted(p for folder in folders for p in (home / folder).rglob("*") if p.is_file())
    files.append(home / "doc" / "COPYING")
    digest = hashlib.sha256()
    for path in files:
        digest.update(f"{path.relative_to(home).as_posix()}\n{file_sha256(path)}\n".encode("utf-8"))
    return digest.hexdigest(), len(files)


def measure(ghostscript: Path | None, tessdata: Path | None) -> dict:
    found: dict = {}
    if ghostscript is not None:
        digest, count = tree_digest(ghostscript)
        found["ghostscript"] = {"tree_sha256": digest, "files": count}
    if tessdata is not None:
        found["tessdata"] = {f"{lang}.traineddata": file_sha256(tessdata / f"{lang}.traineddata") for lang in LANGUAGES}
    return found


def compare(pinned: dict, found: dict) -> list[str]:
    """What differs from the pins (empty: nothing)."""
    problems = []
    for part, values in found.items():
        want = pinned.get(part, {}).get(SYSTEM)
        if want is None:
            problems.append(f"{part}: no pin is recorded for {SYSTEM} (after checking where it came from, run "
                            "this with --pin)")
            continue
        for name, value in values.items():
            if want.get(name) != value:
                problems.append(f"{part}: {name} is {value}, pinned {want.get(name)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ghostscript", type=Path, help="Ghostscript's folder (with bin, lib, Resource, iccprofiles)")
    parser.add_argument("--tessdata", type=Path, help="Tesseract's language files' folder")
    parser.add_argument("--pin", action="store_true", help="Write what is found as the new pins")
    parser.add_argument("--pins", type=Path, default=PINS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.ghostscript is None and args.tessdata is None:
        parser.error("give --ghostscript, --tessdata, or both")
    try:
        found = measure(args.ghostscript, args.tessdata)
    except OSError as exc:
        print(f"check_build_parts: {exc}", file=sys.stderr)
        return 1
    pinned = json.loads(args.pins.read_text(encoding="utf-8")) if args.pins.is_file() else {}
    if args.pin:
        for part, values in found.items():
            pinned.setdefault(part, {})[SYSTEM] = values
        args.pins.write_text(json.dumps(pinned, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Pinned for {SYSTEM}: {', '.join(found)} (read git diff {args.pins.relative_to(ROOT).as_posix()})")
        return 0
    problems = compare(pinned, found)
    for problem in problems:
        print(f"NOT AS PINNED  {problem}", file=sys.stderr)
    if problems:
        print("The build stops here: a part on this computer isn't the one that was checked and pinned. If it was "
              "replaced on purpose, check where the new one came from, then run this with --pin.", file=sys.stderr)
        return 1
    for part, values in found.items():
        print(f"as pinned      {part} ({', '.join(values)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
