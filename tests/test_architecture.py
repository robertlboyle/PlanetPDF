"""Architecture rules from CLAUDE.md, enforced mechanically."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
CORE = SRC / "planetpdf" / "core"


def _core_modules() -> list[str]:
    modules = []
    for path in CORE.rglob("*.py"):
        parts = path.relative_to(SRC).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        # Windows-only modules refuse to load elsewhere (macOS / Linux development).
        if "windows" in parts[-1] and sys.platform != "win32":
            continue
        modules.append(".".join(parts))
    return sorted(modules)


def test_core_never_imports_qt():
    # Fresh interpreter, so Qt imported by other tests can't mask a violation.
    code = (
        "import importlib, sys\n"
        f"for m in {_core_modules()!r}: importlib.import_module(m)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6'))\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"core imported Qt: {result.stdout}{result.stderr}"


FORBIDDEN = [
    (re.compile(r"\b(import|from)\s+(win32\w*|pywintypes|winreg|_winreg)\b"), "Windows-only module"),
    (re.compile(r"[\"'][A-Za-z]:\\\\"), "hardcoded drive-letter path"),
    (re.compile(r"AppData[\\/]|%APPDATA%|%LOCALAPPDATA%", re.IGNORECASE), "hardcoded AppData location"),
    (re.compile(r"\bQSettings\b"), "QSettings (settings live in core.storage JSON)"),
]


def test_no_platform_specific_code_in_src():
    problems = []
    for path in SRC.rglob("*.py"):
        # Future Windows-only providers live in explicitly named modules.
        if "windows" in path.stem:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, why in FORBIDDEN:
                if pattern.search(line):
                    problems.append(f"{path.relative_to(SRC)}:{lineno}: {why}: {line.strip()}")
    assert not problems, "\n".join(problems)


def test_qstandardpaths_only_in_paths_module():
    users = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "QStandardPaths" in p.read_text(encoding="utf-8")
    ]
    assert users == ["planetpdf/ui/paths.py"]
