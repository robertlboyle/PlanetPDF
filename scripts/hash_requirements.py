"""Add (or refresh) the SHA-256 of every file PyPI publishes for each pinned package in
requirements.txt and requirements-dev.txt, so pip installs exactly those files and nothing else
(``--require-hashes``; security review 2026-11-25: versions were pinned, files weren't).

    .venv\\Scripts\\python.exe scripts\\hash_requirements.py          # rewrite both files
    .venv\\Scripts\\python.exe scripts\\hash_requirements.py --check  # say what's missing, change nothing

Every file of the release is listed (wheels for Windows, the Mac, and Linux, and the source), so
the same files work on every build computer. Run it again after changing a pin. Asks only PyPI's
JSON API (https://pypi.org/pypi/<name>/<version>/json); no new library.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("requirements.txt", "requirements-dev.txt")
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)\s*(;[^\\]*)?")


def hashes(name: str, version: str) -> tuple[list[str], bool]:
    """(sha256 of every file, whether a wheel exists) for ``name==version`` on PyPI."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
        data = json.load(response)
    files = data.get("urls", [])
    return sorted({f["digests"]["sha256"] for f in files}), any(f["packagetype"] == "bdist_wheel" for f in files)


def logical_lines(text: str) -> list[str]:
    """The file's lines with continuations (`` \\``) joined and old ``--hash`` options dropped."""
    out, current = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            current += line[:-1].rstrip() + " "
            continue
        out.append(current + line)
        current = ""
    if current:
        out.append(current.rstrip())
    return [re.sub(r"\s+--hash=\S+", "", line).rstrip() for line in out]


def rewrite(path: Path, check: bool) -> list[str]:
    lines = logical_lines(path.read_text(encoding="utf-8"))
    pins = [line for line in lines if PIN.match(line.strip())]
    out, problems = [], []
    for n, line in enumerate(lines):
        match = PIN.match(line.strip())
        if not match:
            out.append(line)
            continue
        name, version, marker = match.group(1), match.group(2), (match.group(3) or "").strip()
        done = sum(1 for x in lines[:n] if PIN.match(x.strip())) + 1
        print(f"{path.name}: package {done} of {len(pins)}: {name} {version}", flush=True)
        try:
            found, wheel = hashes(name, version)
        except OSError as exc:
            problems.append(f"{name}=={version}: PyPI didn't answer ({exc})")
            out.append(line)
            continue
        if not found:
            problems.append(f"{name}=={version}: PyPI lists no files")
        if not wheel:
            problems.append(f"{name}=={version}: no wheel (built from source: its build tools aren't pinned)")
        out.append(" \\\n    ".join([f"{name}=={version}{' ' + marker if marker else ''}"]
                                     + [f"--hash=sha256:{h}" for h in found]))
    if not check:
        path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return problems


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    problems = []
    for name in FILES:
        problems += rewrite(ROOT / name, check)
    for problem in problems:
        print("note: " + problem)
    print("Checked." if check else "Hashes written: install with pip --require-hashes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
