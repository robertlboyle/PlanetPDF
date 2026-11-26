"""The parts a build takes from the build computer are the pinned ones (security list 2026-11-26,
item 9): Ghostscript's folder and Tesseract's language files, as the voices already were."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def parts():
    spec = importlib.util.spec_from_file_location("check_build_parts", ROOT / "scripts" / "check_build_parts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installed(tmp_path):
    gs, tess = tmp_path / "gs", tmp_path / "tessdata"
    for name, data in (("bin/gs.exe", b"program"), ("lib/PDFA_def.ps", b"%!PS"), ("Resource/Init/gs_init.ps", b"init"),
                       ("iccprofiles/srgb.icc", b"icc"), ("doc/COPYING", b"AGPL"), ("doc/other.txt", b"not copied")):
        (gs / name).parent.mkdir(parents=True, exist_ok=True)
        (gs / name).write_bytes(data)
    tess.mkdir()
    for lang in ("eng", "osd"):
        (tess / f"{lang}.traineddata").write_bytes(lang.encode())
    return gs, tess


def run(parts, installed, pins: Path, *extra: str) -> int:
    gs, tess = installed
    return parts.main(["--ghostscript", str(gs), "--tessdata", str(tess), "--pins", str(pins), *extra])


def test_what_was_pinned_passes_and_any_change_stops_the_build(parts, installed, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(parts, "ROOT", tmp_path)
    gs, tess = installed
    pins = tmp_path / "pins.json"
    assert run(parts, installed, pins) == 1 and "no pin is recorded" in capsys.readouterr().err  # (nothing pinned yet)
    assert run(parts, installed, pins, "--pin") == 0
    assert run(parts, installed, pins) == 0
    (gs / "doc" / "other.txt").write_bytes(b"changed, but the installer doesn't take it")
    assert run(parts, installed, pins) == 0
    capsys.readouterr()
    (gs / "lib" / "PDFA_def.ps").write_bytes(b"%!PS (changed)")  # (PostScript the interpreter runs)
    assert run(parts, installed, pins) == 1
    assert "ghostscript: tree_sha256" in capsys.readouterr().err
    (gs / "lib" / "PDFA_def.ps").write_bytes(b"%!PS")
    assert run(parts, installed, pins) == 0
    (gs / "bin" / "extra.dll").write_bytes(b"planted")  # (a file that wasn't there)
    assert run(parts, installed, pins) == 1
    (gs / "bin" / "extra.dll").unlink()
    (tess / "eng.traineddata").write_bytes(b"other")
    assert run(parts, installed, pins) == 1 and "eng.traineddata" in capsys.readouterr().err
    (tess / "osd.traineddata").unlink()
    assert run(parts, installed, pins) == 1  # (a missing file is said, not a traceback)


def test_pinning_again_keeps_the_other_systems_pins(parts, installed, tmp_path, monkeypatch):
    monkeypatch.setattr(parts, "ROOT", tmp_path)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"tessdata": {"some-other-system": {"eng.traineddata": "x"}, "source": {"a": "b"}}}),
                    encoding="utf-8")
    assert run(parts, installed, pins, "--pin") == 0
    data = json.loads(pins.read_text(encoding="utf-8"))
    assert data["tessdata"]["some-other-system"] == {"eng.traineddata": "x"} and data["tessdata"]["source"] == {"a": "b"}
    assert set(data["tessdata"][parts.SYSTEM]) == {"eng.traineddata", "osd.traineddata"}
    assert data["ghostscript"][parts.SYSTEM]["files"] == 5  # (bin, lib, Resource, iccprofiles, and doc/COPYING)


def test_the_windows_build_has_pins_and_checks_them_first():
    pins = json.loads((ROOT / "packaging" / "pinned-parts.json").read_text(encoding="utf-8"))
    assert len(pins["ghostscript"]["windows"]["tree_sha256"]) == 64
    assert set(pins["tessdata"]["windows"]) == {"eng.traineddata", "osd.traineddata"}
    build = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    assert "check_build_parts.py" in build
    assert build.index("check_build_parts.py") < build.index("Step 1 ")  # (before anything is built)
